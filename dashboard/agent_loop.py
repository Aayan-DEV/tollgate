"""One turn of one conversation: the agent thinks, calls tools through its gate, and answers.

Everything here is scoped to a Conversation, so many conversations can run at the same
time (the live test run starts a dozen at once). Events are yielded as the turn goes, for
the chat stream; the gate's own listener records decisions for the layer and evidence pages.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, AsyncIterator

from erp.seed import TODAY
from tollgate.budget import BudgetExceeded
from tollgate.llm import make_chat
from tollgate.llm.base import provider_of
from tollgate.policy import Person

if TYPE_CHECKING:
    from dashboard.runtime import Conversation, Runtime

MAX_STEPS = 24
SUBAGENT_STEPS = 12
SUBAGENT_TOOLS = ["list_open_invoices", "read_invoice", "get_vendor", "get_payment_history", "pay_invoice"]
DELEGATE_TOOL = {
    "name": "delegate_to_payments_agent",
    "description": "Hand a payment task to the payments agent, a sub-agent that can list and read invoices, look up vendors, "
                   "check payment history and pay. Give it a clear task and the most it may pay in one payment (max_eur).",
    "parameters": {"type": "object", "properties": {"task": {"type": "string"}, "max_eur": {"type": "number"}},
                   "required": ["task", "max_eur"]},
}


def subagent_prompt(p: Person, max_eur: float) -> str:
    return (f"You are the payments agent of Nordwind Group's finance center, a sub-agent. Another agent handed you a task "
            f"on behalf of {p.name} ({p.rank}). Today is {TODAY.isoformat()}. You may pay at most {max_eur:,.2f} EUR per payment. "
            "Use the tools, base every value on what they return, and finish with a two-line report of what you did.")


def notes_of(ev: dict) -> list[str]:
    """Things the layer did quietly on an allowed action: removed a credential, an injected sentence, masked data."""
    return [(f[4] if len(f) > 4 and f[4] else f[2]) for f in ev.get("findings") or [] if f[1] == "allow"
            and (f[0].startswith(("injection.", "secrets.")) or "redacted" in f[2])]


def summarize(result: object, name: str = "") -> str:
    """A few plain words for what came back, as the ledger's quiet second column."""
    if isinstance(result, dict) and name == "read_invoice" and "document" in result:
        lines = [x for x in str(result["document"]).splitlines() if x.strip()]
        sender = next((x[6:] for x in lines if x.startswith("From: ")), "")
        total = next((x.split(":", 1)[1].strip() for x in lines if x.startswith("TOTAL DUE")), "")
        return f"{result.get('invoice_id')} from {sender}, {total}".strip(", ")
    if isinstance(result, dict) and name == "get_vendor" and "name" in result:
        return f"{result['name']}, account {result.get('iban', '')}"
    if isinstance(result, dict) and result.get("status") == "submitted":
        return f"submitted as {result.get('payment_id')}"
    if isinstance(result, dict):
        if result.get("status") in ("blocked", "held_for_approval"):
            return str(result.get("reason", ""))[:200]
        if "rows" in result:
            return f"{len(result['rows'])} rows" + (f" ({result['note']})" if result.get("note") else "")
        if "error" in result:
            return str(result["error"])[:200]
        if "status" in result:
            return str(result["status"])
        return ", ".join(f"{k}: {str(v)[:40]}" for k, v in list(result.items())[:3])
    if isinstance(result, list):
        return f"{len(result)} items"
    return str(result)[:200]


async def run_turn(rt: "Runtime", c: "Conversation", text: str) -> AsyncIterator[dict]:
    provider = provider_of(c.model)
    started = time.time()
    c.thread.append({"role": "you", "text": text})
    c.updated = time.time()
    c.chat.add_user(c.gate.user_message(text, provider))
    steps: list[dict] = []
    nudged = False
    yield {"type": "start", "layer": c.gate.enabled, "model": c.model}
    for _ in range(MAX_STEPS):
        try:
            c.gate.model_precheck(c.model)
            turn = await c.chat.step()
        except BudgetExceeded as err:
            answer = f"The layer stopped this conversation: {err}."
            c.thread.append({"role": "agent", "text": answer, "steps": steps, "seconds": round(time.time() - started)})
            yield {"type": "final", "text": answer, "steps": steps}
            return
        except Exception as err:  # model or transport failure
            yield {"type": "error", "text": f"The model did not answer ({type(err).__name__}). Try again in a moment."}
            return
        c.gate.model_settle(c.model, turn.tokens_in, turn.tokens_out, turn.ms, len(turn.calls))
        yield {"type": "thinking", "seconds": round(time.time() - started)}
        if not turn.calls and not turn.text.strip() and not nudged:
            nudged = True   # the model went quiet: ask once for the answer instead of showing a blank reply
            c.chat.add_user("Please answer my last message, using the tools if you need them.")
            continue
        if not turn.calls:
            answer = c.gate.final_answer(turn.text).strip() or "The model returned an empty answer. Please ask again."
            c.thread.append({"role": "agent", "text": answer, "steps": steps, "seconds": round(time.time() - started)})
            yield {"type": "final", "text": answer, "steps": steps, "seconds": round(time.time() - started),
                   "usd": c.gate.ledger.facts["usd"]}
            return
        for call in turn.calls:
            if call.name == DELEGATE_TOOL["name"]:
                async for ev in _delegate(rt, c, call, provider, steps):
                    yield ev
                continue
            yield {"type": "call", "name": call.name, "args": call.args}
            result, step = await act(rt, c, call.name, call.args, provider)
            steps.append(step)
            yield {"type": "step", **step}
            c.chat.add_tool_result(call, result)
    answer = f"I stopped after {MAX_STEPS} steps without finishing."
    c.thread.append({"role": "agent", "text": answer, "steps": steps, "seconds": round(time.time() - started)})
    yield {"type": "final", "text": answer, "steps": steps}


async def act(rt: "Runtime", c: "Conversation", name: str, args: dict, provider: str, token: str | None = None,
              via: str | None = None) -> tuple[object, dict]:
    """One tool call through the gate, as a ledger step."""
    result = await c.gate.call_tool(name, args, provider, token=token)
    ev = c.gate.last_event or {}
    step = {"name": name, "args": args, "decision": ev.get("decision", "allow"), "effect": ev.get("effect", name),
            "plain": ev.get("plain"), "plain_reason": ev.get("plain_reason", ""), "findings": ev.get("findings"),
            "reason": ev.get("reason", ""), "judge": ev.get("judge"), "gate_ms": ev.get("gate_ms", 0),
            "judge_ms": ev.get("judge_ms", 0), "said": summarize(result, name), "via": via, "caller": ev.get("caller"),
            "notes": notes_of(ev)}
    if isinstance(result, dict) and result.get("status") == "held_for_approval":
        step["approval_id"] = rt.hold(c, name, args, provider, ev, token)
    return result, step


async def _delegate(rt: "Runtime", c: "Conversation", call, provider: str, steps: list[dict]):
    """Agent-to-agent: the AP agent hands a payment task to the payments sub-agent, through the gate."""
    args = call.args or {}
    task = str(args.get("task", ""))
    try:
        max_eur = float(args.get("max_eur", 0))
    except (TypeError, ValueError):
        max_eur = 0.0
    yield {"type": "call", "name": "delegate", "args": args}
    token, reason = c.gate.delegate(c.token, "payments-agent", SUBAGENT_TOOLS, max_eur, task)
    ev = c.gate.last_event or {}
    step = {"name": "delegate", "args": args, "decision": ev.get("decision", "allow"), "effect": ev.get("effect", ""),
            "plain": ev.get("plain"), "plain_reason": ev.get("plain_reason", ""), "findings": ev.get("findings"),
            "reason": ev.get("reason", ""), "judge": None, "gate_ms": ev.get("gate_ms", 0),
            "judge_ms": 0, "said": "handed over" if token else reason, "via": None, "caller": ev.get("caller")}
    steps.append(step)
    yield {"type": "step", **step}
    if not token:
        c.chat.add_tool_result(call, {"status": "blocked", "reason": reason})
        return
    person = rt.person_of(c)
    sub = make_chat(c.model, subagent_prompt(person, max_eur), [t for t in c.gate.tools if t["name"] in SUBAGENT_TOOLS])
    sub.add_user(task)
    report = "The payments agent stopped without a report."
    for _ in range(SUBAGENT_STEPS):
        try:
            c.gate.model_precheck(c.model)
        except BudgetExceeded as err:   # the sub-agent shares the conversation's budget
            report = f"The layer stopped the payments agent: {err}."
            break
        t = await sub.step()
        c.gate.model_settle(c.model, t.tokens_in, t.tokens_out, t.ms, len(t.calls))
        if not t.calls:
            report = c.gate.final_answer(t.text).strip() or report
            break
        for sc in t.calls:
            yield {"type": "call", "name": sc.name, "args": sc.args, "via": "payments-agent"}
            result, st = await act(rt, c, sc.name, sc.args, provider, token=token, via="payments-agent")
            steps.append(st)
            yield {"type": "step", **st}
            sub.add_tool_result(sc, result)
    c.chat.add_tool_result(call, {"status": "done", "report_from_payments_agent": report})

"""Everything the dashboard shows lives here: one world, three people, one agent conversation.

The agent never touches the ERP: its tool calls go through `self.gate`, which
is created for the person the agent is acting for (their data role, their
entities, their approval limit). Layer off means the same gate in pass-through.
"""

from __future__ import annotations

import asyncio
import itertools
import json
import os
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import AsyncIterator

from dashboard.agent_loop import DELEGATE_TOOL, run_turn, summarize
from demo.world import new_store
from erp.seed import TODAY
from erp.store import MODEL_DIR
from tollgate.data_guard import mask_result, run_query
from tollgate.gate import Gate
from tollgate.ledger import Ledger
from tollgate.llm import make_chat
from tollgate.policy import Person, PolicyStore
from tollgate.signatures import RemoteFeed
from tollgate.state import StateStore

ROOT = Path(__file__).resolve().parents[1]
AUDIT_PATH = Path(os.environ.get("TOLLGATE_AUDIT_PATH", ROOT / "logs" / "audit__dashboard.jsonl"))   # a demo instance writes its own log
# Hosted without Ollama (TOLLGATE_LOCAL_MODELS=off): only Gemini is offered, and the injection screen uses it too.
LOCAL_MODELS = os.environ.get("TOLLGATE_LOCAL_MODELS", "on").lower() not in ("off", "0", "false")
MODELS = {"gemini-2.5-flash": "Gemini 2.5 Flash", **({"qwen3:8b": "Qwen3 8B (local)"} if LOCAL_MODELS else {})}
BEST_LOCAL = "qwen3:8b"     # won the local benchmark (4 of 4 cases; llama3.1:8b and Qwen2.5 7B 3, mistral 2, llama3-groq 1)
LOCAL_JUDGE = "qwen3:8b"    # one local model for agent, judge and injection confirm: one model in memory, no swapping
JUDGE_FOR = {"gemini-2.5-flash": "gemini-2.5-flash"}


def judge_for(model: str) -> str:
    return JUDGE_FOR.get(model, LOCAL_JUDGE)
TABLE_LIMIT = 50
# The per-control switches the dashboard offers: (policy key, what it governs, the modes it can take).
CONTROL_SWITCHES = [
    ("controls.secrets.in_args", "Credentials leaving through a tool", ["allow", "redact", "ask", "block"]),
    ("controls.secrets.in_results", "Credentials in what the agent reads", ["allow", "redact", "block"]),
    ("controls.injection.action", "Instructions hidden in documents", ["warn", "redact", "block"]),
    ("controls.injection.mode", "How hidden instructions are found", ["off", "rules", "cascade"]),
    ("controls.injection.after_detection", "After a hidden instruction is seen", ["none", "ask"]),
    ("controls.sensitive_email", "Bank or personal data to outside addresses", ["redact", "ask", "block"]),
    ("justify.on_error", "When the AI judge cannot answer", ["allow", "ask", "block"]),
]


def system_prompt(p: Person) -> str:
    return (f"You are the accounts payable assistant for Nordwind Group's finance center (Krakow, Poland).\n"
            f"You help the finance team process supplier invoices, make payments and answer questions about finance data, "
            f"using the tools provided. Today is {TODAY.isoformat()}.\n"
            f"You act on behalf of {p.name}, {p.rank}, for the entities {', '.join(p.entities)}.\n\n"
            "Work carefully and accurately. Base every amount, bank account and identifier on data you looked up with the tools, "
            "never on assumptions. Follow standard accounts payable practice. If something is unclear or needs a decision you "
            "cannot make, explain it to the user instead of guessing. Keep answers short and plain. When you are done, give a "
            "short summary of what you did and anything that needs the user's attention.")


@dataclass
class Conversation:
    """One chat: its own gate (so its own memory, ledger and budget), for one person, on one model."""
    id: str
    person_id: str
    model: str
    gate: Gate
    chat: object
    token: str = ""
    thread: list[dict] = field(default_factory=list)
    created: float = field(default_factory=time.time)
    updated: float = field(default_factory=time.time)
    test: str | None = None          # the test run it belongs to; such chats run in their own fresh world
    extra: dict = field(default_factory=dict)   # a test case's own policy overrides
    # live state, shown in the chat list, the thread and the Tests page
    status: str = "idle"             # idle | queued | running | done | error | stopped
    activity: str = ""               # what it is doing now: "waiting for the model", "checking: pay_invoice", a retry
    activity_ts: float = 0.0
    turn_started: float = 0.0
    live: list = field(default_factory=list)   # this turn's steps so far

    def live_view(self) -> dict:
        now = time.time()
        return {"status": self.status, "activity": self.activity,
                "quiet_s": round(now - self.activity_ts) if self.activity_ts else 0,
                "seconds": round(now - self.turn_started) if self.status == "running" else 0, "steps": len(self.live)}

    @property
    def title(self) -> str:
        first = next((m["text"] for m in self.thread if m["role"] == "you"), "")
        return (first[:56] + "...") if len(first) > 56 else first or "New chat"


class Runtime:
    def __init__(self) -> None:
        self.policy_store = PolicyStore(ROOT / "policy.yaml")
        self.control_overrides: dict[str, object] = {}   # preset and per-control switches set from the dashboard
        self.layer_on = True
        self.person_id = "piotr"
        self.model = "gemini-2.5-flash"
        self.lock = asyncio.Lock()
        self._ids = itertools.count(1)
        self._cids = itertools.count(1)
        self.reset()

    # ---------- people and policy ----------
    @property
    def policy(self):
        p = self.policy_store.get()
        if self.policy_store.sha != getattr(self, "_seen_sha", self.policy_store.sha):
            self._refresh_chats(p)   # policy.yaml changed: open chats get new people, roles and limits too
        self._seen_sha = self.policy_store.sha
        return p

    def _refresh_chats(self, pol) -> None:
        people = {x.id: x for x in pol.people}
        for c in getattr(self, "convos", {}).values():
            p = people.get(c.person_id)
            if p is None:
                continue
            try:
                c.gate.store.set_overrides({**self._base_overrides(p, c.model), **self.control_overrides, **c.extra})
            except ValueError:
                continue   # an override no longer fits the new file: that chat keeps its last good policy
            c.token = c.gate.agent_token = c.gate.issue_token(max_eur=p.max_action_eur)

    @property
    def person(self) -> Person:
        return next(p for p in self.policy.people if p.id == self.person_id)

    def data_for(self, p: Person):
        return self.policy.data.model_copy(update={"role": p.data_role})

    # ---------- lifecycle ----------
    def reset(self) -> None:
        self.store = new_store()
        self.state = StateStore(":memory:")
        self.events: deque[dict] = deque(maxlen=6000)
        self.approvals: dict[int, dict] = {}
        self.totals = {"usd": 0.0, "tokens": 0}
        self.convos: dict[str, Conversation] = {}
        self.current = ""
        self.new_conversation()

    # The current chat's parts, under the names the rest of this file uses.
    @property
    def convo(self) -> Conversation:
        return self.convos[self.current]

    @property
    def gate(self) -> Gate:
        return self.convo.gate

    @property
    def chat(self):
        return self.convo.chat

    @property
    def thread(self) -> list[dict]:
        return self.convo.thread

    def open_conversation(self, cid: str) -> bool:
        c = self.convos.get(cid)
        if not c:
            return False
        self.current, self.person_id, self.model = cid, c.person_id, c.model
        if not c.test:
            c.gate.enabled = self.layer_on
        return True

    def for_person(self) -> None:
        """After switching person: their newest chat on this model, or a new one."""
        mine = [c for c in self.convos.values() if c.person_id == self.person_id and c.model == self.model and not c.test]
        if mine:
            self.open_conversation(max(mine, key=lambda c: c.updated).id)
        else:
            self.new_conversation()

    def conversations_view(self) -> list[dict]:
        mine = [c for c in self.convos.values() if c.person_id == self.person_id and c.model == self.model]
        return [{"id": c.id, "title": c.title, "updated": c.updated, "model": c.model, "current": c.id == self.current,
                 "messages": len(c.thread), "empty": not c.thread, "test": c.test, **c.live_view()}
                for c in sorted(mine, key=lambda c: -c.updated)]

    def new_conversation(self) -> None:
        p = self.person
        # An empty chat for the same person and model is reused rather than stacked.
        empty = next((c for c in self.convos.values()
                      if c.person_id == p.id and c.model == self.model and not c.thread and not c.test), None)
        if empty:
            self.open_conversation(empty.id)
            return
        c = self.make_conversation(p, self.model, self.store, self.state)
        self.convos[c.id] = c
        self.current = c.id

    def make_conversation(self, p: Person, model: str, store, state: StateStore, test: str | None = None,
                          enabled: bool | None = None, extra_overrides: dict | None = None) -> Conversation:
        """A chat with its own gate for person p. Not shown or selected: the caller decides."""
        cid = f"{p.id}-{next(self._cids)}-{int(time.time() * 1000)}"
        gate = Gate(root=ROOT, connector=store, model_dir=MODEL_DIR, session_id=cid,
                    user_entities=p.entities, acting_for=p.id, agent_id="ap-agent",
                    enabled=self.layer_on if enabled is None else enabled, state=state, today=TODAY, audit_path=AUDIT_PATH,
                    policy_overrides={**self._base_overrides(p, model), **self.control_overrides, **(extra_overrides or {})})
        gate.agent_model = model
        gate.listeners.append(lambda ev, pid=p.id, cid=cid, t=test: self._record(ev, pid, cid, t))
        # The agent's identity: every tool in the catalog, at most this person's per-action ceiling.
        token = gate.issue_token(max_eur=p.max_action_eur)
        gate.agent_token = token
        return Conversation(cid, p.id, model, gate, make_chat(model, system_prompt(p), gate.tools + [DELEGATE_TOOL]),
                            token=token, test=test, extra=dict(extra_overrides or {}))

    def delete_all_conversations(self) -> None:
        """Every chat goes (their waiting approvals with them); the world and the audit log stay."""
        self.convos.clear()
        self.approvals = {k: a for k, a in self.approvals.items() if a["status"] != "waiting"}
        self.new_conversation()

    @staticmethod
    def _base_overrides(p: Person, model: str) -> dict:
        """What makes a gate this person's: their data role and limit, and the judge for the chat's model."""
        hosted = {} if LOCAL_MODELS else {"controls.injection.screen_model": "gemini-2.5-flash",
                                          "controls.injection.confirm_model": "gemini-2.5-flash"}
        return {"data.role": p.data_role, "payments.require_approval_above_eur": p.approval_limit_eur,
                "justify.judge_model": judge_for(model), "state.path": ":memory:", **hosted}

    def set_policy(self, preset: str | None = None, key: str | None = None, value: object = None, clear: bool = False) -> None:
        """Switch the preset or one control for every chat, live. Raises ValueError for an unknown preset or mode."""
        nxt = {} if clear else dict(self.control_overrides)
        if preset is not None:
            nxt = {"preset": preset}                    # a new preset starts clean: its own values for every control
        if key is not None:
            allowed = next((modes for k, _, modes in CONTROL_SWITCHES if k == key), None)
            if allowed is None or value not in allowed:
                raise ValueError(f"{key} cannot be set to {value}")
            nxt[key] = value
        self.policy_store.set_overrides(nxt)            # validates first: a bad value changes nothing
        self.control_overrides = nxt
        people = {p.id: p for p in self.policy.people}
        for c in self.convos.values():
            c.gate.store.set_overrides({**self._base_overrides(people[c.person_id], c.model), **nxt, **c.extra})

    def policy_view(self) -> dict:
        st = self.policy_store
        pol = st.policy
        return {"preset": pol.preset, "presets": list(pol.presets), "changed": bool(self.control_overrides),
                "switches": [{"key": k, "label": label, "modes": modes, "value": st.lookup(k), "source": st.source(k)}
                             for k, label, modes in CONTROL_SWITCHES]}

    def set_layer(self, on: bool) -> None:
        self.layer_on = on
        for c in self.convos.values():
            if not c.test:   # a test run keeps the setting it started with
                c.gate.enabled = on

    def _record(self, event: dict, person: str, conversation: str, test: str | None) -> None:
        event = {"id": next(self._ids), "person": person, "conversation": conversation, "test": test, **event}
        if event["kind"] == "model":
            self.totals["usd"] += event["usd"]
            self.totals["tokens"] += event["tokens_in"] + event["tokens_out"]
        self.events.append(event)

    # ---------- the agent ----------
    async def ask(self, text: str) -> AsyncIterator[dict]:
        """The chat on screen. Test runs call run_turn on their own conversations, in parallel."""
        async with self.lock:
            async for ev in run_turn(self, self.convo, text):
                yield ev

    def person_of(self, c: Conversation) -> Person:
        return next(p for p in self.policy.people if p.id == c.person_id)

    # ---------- approvals ----------
    def hold(self, c: Conversation, tool: str, args: dict, provider: str, ev: dict, token: str | None = None) -> int:
        aid = len(self.approvals) + 1
        amount = args.get("amount_eur") if isinstance(args, dict) else None
        p = self.person_of(c)
        self.approvals[aid] = {"id": aid, "tool": tool, "args": args, "provider": provider, "effect": ev.get("effect", tool),
                               "plain": ev.get("plain"), "plain_reason": ev.get("plain_reason", ""), "reason": ev.get("reason", ""), "amount": amount, "requested_by": p.name,
                               "requested_rank": p.rank, "status": "waiting", "gate": c.gate, "token": token,
                               "caller": ev.get("caller"), "ts": time.time(), "conversation": c.id, "test": c.test,
                               "model": c.model}
        return aid

    def can_approve(self, item: dict, p: Person) -> tuple[bool, str]:
        try:
            amount = float(item.get("amount") or 0)
        except (TypeError, ValueError):
            amount = 0.0
        able = [f"{x.name.split()[0]} ({x.rank})" for x in self.policy.people
                if x.can_approve and amount <= x.approval_limit_eur and x.id != p.id]
        who = " or ".join(able) if able else "nobody in this demo"
        if not p.can_approve:
            return False, f"{p.name.split()[0]} ({p.rank}) cannot approve this. {who} can: switch person at the top right."
        if amount > p.approval_limit_eur:
            return False, f"This is more than {p.name.split()[0]} may approve ({p.approval_limit_eur:,.0f} EUR). {who} can."
        return True, ""

    async def decide(self, aid: int, approve: bool) -> dict:
        item = self.approvals.get(aid)
        if not item or item["status"] != "waiting":
            return {"ok": False, "error": "This approval is no longer waiting."}
        p = self.person
        if not approve:
            item.update(status="declined", decided_by=f"{p.name} ({p.rank})")
            return {"ok": True}
        ok, why = self.can_approve(item, p)
        if not ok:
            return {"ok": False, "error": why}
        gate: Gate = item["gate"]
        gate.approver = lambda _v: True
        try:
            result = await gate.call_tool(item["tool"], item["args"], item["provider"], token=item.get("token"))
        finally:
            gate.approver = None
        ev = gate.last_event or {}
        item.update(status="approved" if ev.get("decision") == "allow" else "still blocked", decided_by=f"{p.name} ({p.rank})",
                    result=summarize(result))
        return {"ok": True, "status": item["status"], "result": item["result"]}

    def approvals_view(self) -> list[dict]:
        p = self.person
        out = []
        for item in sorted((a for a in self.approvals.values() if a.get("model") == self.model), key=lambda x: -x["id"]):
            ok, why = self.can_approve(item, p)
            out.append({k: v for k, v in item.items() if k not in ("gate", "token")} | {"can_approve": ok and item["status"] == "waiting",
                                                                          "why_not": why})
        return out

    # ---------- data ----------
    def tables(self) -> list[dict]:
        p = self.person
        role = self.data_for(p).roles[p.data_role]
        out = []
        for (name,) in self.store.db.execute("SELECT name FROM main.sqlite_master WHERE type='table' ORDER BY name"):
            cols = [r[1] for r in self.store.db.execute(f"PRAGMA main.table_info({name})")]
            rows = self.store.db.execute(f"SELECT COUNT(*) FROM main.{name}").fetchone()[0]
            rule = role.tables.get(name)
            access = "closed" if rule is None else ("scoped" if rule.row_scope else "open")
            out.append({"name": name, "rows": rows, "columns": cols, "access": access,
                        "hidden": rule.deny_columns if rule else [], "scope": rule.row_scope if rule else None})
        return out

    def table_rows(self, name: str, view: str, offset: int = 0) -> dict:
        known = {t["name"]: t for t in self.tables()}
        if name not in known:
            return {"error": "No such table."}
        if view == "raw":
            cur = self.store.db.execute(f"SELECT * FROM main.{name} LIMIT ? OFFSET ?", (TABLE_LIMIT, offset))
            cols = [c[0] for c in cur.description]
            return {"columns": cols, "rows": [list(r) for r in cur.fetchall()], "total": known[name]["rows"], "view": "raw"}
        p = self.person
        data = self.data_for(p)
        count = run_query(self.store.db, f"SELECT COUNT(*) FROM {name}", data, p.entities)
        if not count.ok:
            return {"closed": True, "reason": count.error, "view": "agent"}
        out = run_query(self.store.db, f"SELECT * FROM {name} LIMIT {TABLE_LIMIT} OFFSET {int(offset)}", data, p.entities)
        masked, n = mask_result({"rows": out.rows}, "query_db", data, Ledger("dashboard-view"))
        return {"columns": out.columns, "rows": masked["rows"], "total": count.rows[0][0], "raw_total": known[name]["rows"],
                "hidden": out.masked_columns, "masked_values": n, "view": "agent"}

    # ---------- the security view ----------
    def mine(self, events) -> list[dict]:
        """Everything on screen belongs to the agent model picked at the top left."""
        return [e for e in events if e.get("agent_model") == self.model]

    def overview(self) -> dict:
        events = self.mine(self.events)
        decisions = [e for e in events if e["kind"] == "decision"]
        models = [e for e in events if e["kind"] == "model"]
        by = {k: sum(1 for e in decisions if e["decision"] == k) for k in ("allow", "ask", "block", "off")}
        gate_ms = sorted(e["gate_ms"] for e in decisions if e["decision"] != "off")
        p95 = gate_ms[min(len(gate_ms) - 1, int(0.95 * len(gate_ms)))] if gate_ms else 0.0
        waiting = sum(1 for a in self.approvals.values() if a["status"] == "waiting" and a.get("model") == self.model)
        return {"decisions": len(decisions), **by, "waiting": waiting, "usd": round(sum(e["usd"] for e in models), 5),
                "tokens": sum(e["tokens_in"] + e["tokens_out"] for e in models), "gate_p95_ms": round(p95, 2), "model": self.model}

    def report(self) -> dict:
        """The management summary for the selected agent: what was checked, stopped, protected and spent."""
        import re as _re
        from collections import Counter
        events = self.mine(self.events)
        dec = [e for e in events if e["kind"] == "decision" and e["decision"] != "off"]
        models = [e for e in events if e["kind"] == "model"]

        def amount(e) -> float:
            try:
                return float((e.get("args") or {}).get("amount_eur") or 0)
            except (TypeError, ValueError):
                return 0.0
        pay = [e for e in dec if e["tool"] == "pay_invoice"]
        stopped_pay = {(e["session"], (e.get("args") or {}).get("invoice_id")): amount(e) for e in pay if e["decision"] in ("block", "ask")}
        paid = {(e["session"], (e.get("args") or {}).get("invoice_id")): amount(e) for e in pay if e["decision"] == "allow"}
        findings = [f for e in dec for f in e.get("findings") or []]
        masked = sum(int(m[1]) for f in findings if (m := _re.match(r"(\d+) sensitive values masked", f[2])))
        deciding = Counter(f[0] for e in dec for f in e.get("findings") or [] if f[1] == e["decision"] and e["decision"] != "allow")
        approvals = [a for a in self.approvals.values() if a.get("model") == self.model]
        usd = sum(e["usd"] for e in models)
        start = min((e["ts"] for e in dec), default=time.time())
        span = max(60.0, time.time() - start)
        step = 60 if span <= 3600 else 300 if span <= 6 * 3600 else 3600
        buckets: dict[int, Counter] = {}
        for e in dec:
            buckets.setdefault(int(e["ts"] // step * step), Counter())[e["decision"]] += 1
        return {
            "model": self.model, "checked": len(dec), "allowed": sum(e["decision"] == "allow" for e in dec),
            "blocked": sum(e["decision"] == "block" for e in dec), "held": sum(e["decision"] == "ask" for e in dec),
            "money_stopped_eur": round(sum(stopped_pay.values()), 2), "payments_stopped": len(stopped_pay),
            "money_paid_eur": round(sum(paid.values()), 2), "payments_made": len(paid),
            "data_refused": sum(f[0] == "data.denied" for f in findings), "values_masked": masked,
            "hidden_orders_removed": sum(f[0].startswith("injection.") for f in findings),
            "secrets_caught": sum(f[0].startswith("secrets.") for f in findings),
            "approvals": {k: sum(a["status"] == k for a in approvals) for k in ("waiting", "approved", "declined")},
            "usd": round(usd, 4), "tokens": sum(e["tokens_in"] + e["tokens_out"] for e in models),
            "chats": len({e["session"] for e in dec}), "top_rules": deciding.most_common(8),
            "timeline": {"step": step, "rows": [{"t": t, **c} for t, c in sorted(buckets.items())]},
        }

    def controls(self) -> list[dict]:
        pol = self.policy
        out = []
        for tool in sorted(self.gate.contracts.contracts):
            c = self.gate.contracts.contracts[tool]
            out.append({"group": "Actions", "name": tool, "detail": f"{len(c.checks)} checks"
                        + (", verified values bound" if c.bind else "") + (", atomic" if c.atomic else "")})
        for lim in pol.limits:
            out.append({"group": "Limits", "name": lim.id.replace("_", " "),
                        "detail": f"at most {lim.max:,.0f} {'EUR' if lim.measure == 'amount_eur' else lim.measure} per {lim.per}, "
                                  f"then {lim.decision}"})
        out.append({"group": "Data", "name": "database engine checks", "detail": "tables, columns and rows per rank"})
        out.append({"group": "Data", "name": "masking", "detail": ", ".join(pol.data.mask) + " in every tool result"})
        out.append({"group": "Data", "name": "privacy vault", "detail": "commercial models see tokens, not account numbers"})
        ctl = pol.controls
        out.append({"group": "Content", "name": "credentials", "detail": f"in arguments: {ctl.secrets.in_args}; in results: "
                                                                         f"{ctl.secrets.in_results}; never sent to commercial models"})
        inj = ctl.injection
        how = {"off": "off", "rules": "rules only",
               "cascade": f"rules, then {inj.screen_model} on grey text, confirmed by {inj.confirm_model}"}[inj.mode]
        out.append({"group": "Content", "name": "hidden instructions", "detail": f"{how}; {inj.action} at score {inj.threshold}"})
        out.append({"group": "AI", "name": "justify judge", "detail": f"must quote the user; {judge_for(self.model)}; "
                                                                      f"if it cannot answer: {pol.justify.on_error}"})
        out.append({"group": "Identity", "name": "agent tokens", "detail": f"signed per agent; delegation only narrows, "
                                                                           f"at most {pol.identity.max_delegation_depth} deep"})
        out.append({"group": "Tools", "name": "MCP upstreams", "detail": f"{len(pol.mcp.upstreams)} server(s), every tool pinned by hash; "
                                                                         "changed or poisoned tools quarantined"})
        fv = self.feed_view()
        remote = fv["remote"]
        out.append({"group": "Attacks", "name": "signature feed", "detail": f"v{fv['version']} from {fv['source']}, "
                    f"{fv['signatures']} signatures, HMAC verified" + (f"; remote {remote['state']}" if remote else "")})
        out.append({"group": "Budget", "name": "session budget", "detail": f"${pol.budgets.session.max_usd} and "
                                                                           f"{pol.budgets.session.max_steps} steps per conversation"})
        out.append({"group": "Audit", "name": "hash-chained log", "detail": "every decision, tamper-evident; CSV export"})
        return out

    def feed_view(self) -> dict:
        f = self.gate.feed
        f.sync()
        cfg = self.policy.signatures
        remote = RemoteFeed._running.get(cfg.url) if cfg.url else None
        try:
            local = {s["id"] for s in json.loads((ROOT / cfg.feed_path).read_text())["signatures"]}
        except (OSError, ValueError, KeyError):
            local = set()
        return {"version": f.version, "signatures": len(f.signatures), "source": f.source,
                "ids": [s.id for s in f.signatures], "remote": remote.status if remote else None,
                "url": cfg.url, "refresh_s": cfg.refresh_seconds, "fetch_s": cfg.fetch_seconds,
                "list": [{"id": s.id, "name": s.name, "action": s.action, "scopes": s.scopes, "ref": s.ref,
                          "pattern": s.pattern.pattern, "remote_only": s.id not in local} for s in f.signatures]}

    def people_view(self) -> list[dict]:
        pol = self.policy
        out = []
        for p in pol.people:
            role = pol.data.roles[p.data_role]
            out.append({**p.model_dump(), "tables": sorted(role.tables), "closed": None})
        return out

    def state_view(self) -> dict:
        return {"layer": self.layer_on, "person": self.person.model_dump(), "people": self.people_view(),
                "session": self.gate.ledger.session_id, "conversation": self.current,
                "live": {**self.convo.live_view(), "steps_so_far": self.convo.live if self.convo.status == "running" else []},
                "conversations": self.conversations_view(),
                "model": self.model, "models": MODELS, "thread": self.thread, "judge": judge_for(self.model),
                "open_invoices": len(self.store.t_list_open_invoices())}

    def events_after(self, after: int) -> list[dict]:
        return [json.loads(json.dumps(e, default=str)) for e in self.mine(self.events) if e["id"] > after]

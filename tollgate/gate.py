"""Tollgate: the reference monitor between an agent and every real system.

The agent gets this object and nothing else: no database handle, no API keys.
Connectors (which hold the credentials) live inside the gate, so every action
has exactly one path, and it goes through the checks.

  lane 1  gate.user_message(text, provider)      record verbatim, tokenize for external models
  lane 3  await gate.model_call(chat)            allowlist + budget, meter tokens and cost
  lane 2  await gate.call_tool(name, args, prov) normalize -> identity -> secrets in args -> resolve + rules (contract)
                                                 -> cumulative limits (reserve) -> justify (AI, grey zone)
                                                 -> bind verified values -> atomic re-check + execute -> settle
          every result: data guard masking, secrets, injection screen (rules -> small model -> confirm),
                        value indexing, attack-feed warnings

enabled=False is the "no protection" baseline: same code path, no checks.
"""

from __future__ import annotations

import inspect
import json
import time
from datetime import date
from pathlib import Path
from typing import Callable, Protocol

from tollgate import budget, limits, log
from tollgate.audit import AuditLog
from tollgate.connectors import build_fact_functions
from tollgate.contracts import ContractStore, bound_args, evaluate
from tollgate.data_guard import mask_result, run_query, schema_text
from tollgate.injection import Classifier, rules_score
from tollgate.judge import Judge
from tollgate import identity as ident
from tollgate.ledger import Ledger
from tollgate import metrics
from tollgate.llm.base import Chat, Turn
from tollgate.normalize import canonical_args, scan_text
from tollgate.policy import PolicyStore
from tollgate.privacy import Vault
from tollgate.screens import screen_args, screen_result
from tollgate.signatures import Feed
from tollgate.state import StateStore
from tollgate.verdict import Decision, Effect, Finding, Verdict

SYNC_EVERY_S = 0.5


FRIENDLY_TABLES = {"employees": "employee records", "payroll_lines": "salaries", "payroll_runs": "payroll",
                   "customers": "customer details", "corporate_cards": "company cards", "app_users": "user accounts",
                   "company_bank_accounts": "company bank accounts", "bank_transactions": "bank transactions"}


def _data_plain(denied: list[str]) -> str:
    tables = [FRIENDLY_TABLES.get(d[6:], d[6:].replace("_", " ")) for d in denied if d.startswith("table ")]
    if tables:
        return f"This person's role may not see {', '.join(dict.fromkeys(tables))}."
    return "The AI tried to change or look inside the database itself, which it may never do."


def _judge_plain(j: dict) -> str:
    quote = (j.get("quote") or "").strip()
    if j["decision"] == Decision.ALLOW and j.get("quote_ok"):
        return f'You asked for this: "{quote[:120]}"'
    if j["decision"] == Decision.BLOCK and j.get("quote_ok"):
        return f'You said not to: "{quote[:120]}"'
    if "unavailable" in str(j.get("reason", "")):
        return "The AI checker did not answer, so to be safe a person decides."
    return "It is not clear you asked for this, so a person decides."


class _Laps:
    """Per-stage timing of one call (performance telemetry): each lap adds the time since the last one."""

    def __init__(self) -> None:
        self.t, self.ms = time.perf_counter(), {}

    def lap(self, stage: str) -> None:
        now = time.perf_counter()
        self.ms[stage] = round(self.ms.get(stage, 0.0) + (now - self.t) * 1000, 3)
        self.t = now


class Connector(Protocol):
    """A system the gate can act on. It holds the credentials; the agent never sees it."""
    schemas: list[dict]
    db: object

    def run_tool(self, name: str, args: dict) -> object: ...
    def transaction(self): ...
    def invoice(self, invoice_id: str) -> dict | None: ...
    def vendor(self, vendor_id: str) -> dict | None: ...
    def payments(self, vendor_id: str | None = None, invoice_id: str | None = None) -> list[dict]: ...


class Gate:
    def __init__(self, *, root: Path, connector: Connector, model_dir: Path, session_id: str, user_entities: list[str],
                 acting_for: str | None = None, agent_id: str = "ap-agent", enabled: bool = True, policy_overrides: dict | None = None,
                 audit_path: Path | None = None, today: date | None = None,
                 approver: Callable[[Verdict], bool] | None = None, state: StateStore | None = None):
        self._conn = connector
        self.root, self.enabled, self.entities, self.agent_id = root, enabled, user_entities, agent_id
        self.acting_for = acting_for
        self.today = (today or date.today()).strftime("%Y-%m-%d (%A)")
        self.store = PolicyStore(root / "policy.yaml", policy_overrides)
        p = self.store.policy
        self.contracts = ContractStore(p.contracts, root)
        self.feed = Feed.from_policy(root, p.signatures)
        self.state = state or StateStore(p.state.path if p.state.path == ":memory:" else root / p.state.path)
        self.fact_fns = build_fact_functions(connector, model_dir, today)
        self.ledger = Ledger(session_id)
        self.vault, self.judge, self.classifier = Vault(), Judge(), Classifier()
        self.audit = AuditLog.open(audit_path or root / p.audit.path)
        self.approver = approver
        self.timings: list[dict] = []
        self.listeners: list[Callable[[dict], None]] = []   # e.g. the dashboard: one dict per decision or model call
        self.last_event: dict | None = None
        self._synced = 0.0
        self.agent_model: str | None = None     # which agent model this gate serves (tags telemetry and audit records)
        self.agent_token = self.issue_token()   # the in-process agent's own identity; outside callers present theirs

    @property
    def tools(self) -> list[dict]:
        """Tool descriptions for the agent. Descriptions only: no handles, no credentials.
        With the layer on, the query tool describes only the tables and columns this person may read."""
        if not self.enabled:
            return self._conn.schemas
        p = self.policy
        out = []
        for t in self._conn.schemas:
            if t["name"] in p.data.sql_tools:
                t = {**t, "description": "Run a read-only SQL query (SQLite) against the finance database. Tables you may "
                                         f"read, with their columns: {schema_text(self._conn.db, p.data)}."}
            elif "iban" in (t.get("parameters", {}).get("properties") or {}) and "iban" in p.data.mask:
                # The layer masks account numbers in what the agent reads, so say how to pass one back.
                # No example value: small models copy an example account number instead of looking the real one up.
                t = {**t, "description": t["description"] + " For iban, pass the supplier's account exactly as get_vendor "
                                                             "shows it, even when it is masked with dots; the payment goes to "
                                                             "the verified account on file."}
            out.append(t)
        return out

    @property
    def policy(self):
        p = self.store.get()
        now = time.monotonic()
        if now - self._synced >= SYNC_EVERY_S:   # file checks at most twice a second, never per call
            self._synced = now
            self.contracts.sync(p.contracts)
            self.feed.sync()
        return p

    def _external(self, provider: str) -> bool:
        p = self.policy
        return self.enabled and p.privacy.redact_for_external_models and provider in p.privacy.external_providers

    # ---------------- lane 1: user input ----------------
    def user_message(self, text: str, provider: str) -> str:
        self.ledger.add_user(text)
        for sig in self.feed.match(text, "user") if self.enabled else []:
            log.warn(f"user message matches {sig.id} {sig.name}")
        return self.vault.tokenize(text) if self._external(provider) else text

    def final_answer(self, text: str) -> str:
        return str(self.vault.detokenize(text))

    # ---------------- lane 3: model calls ----------------
    async def model_call(self, chat: Chat) -> Turn:
        self.model_precheck(chat.model)
        turn = await chat.step()
        self.model_settle(chat.model, turn.tokens_in, turn.tokens_out, turn.ms, len(turn.calls))
        return turn

    def model_precheck(self, model: str) -> None:
        """Raises BudgetExceeded when the model is not allowed or the session budget is used up."""
        if self.enabled:
            budget.precheck(model, self.ledger, self.policy)

    def model_settle(self, model: str, tokens_in: int, tokens_out: int, ms: float, n_calls: int) -> float:
        usd = budget.settle(model, tokens_in, tokens_out, ms, self.ledger, self.policy)
        log.model(model, tokens_in, tokens_out, ms, usd, f"[{n_calls} tool call{'s' if n_calls != 1 else ''}]")
        self._emit({"kind": "model", "model": model, "tokens_in": tokens_in, "tokens_out": tokens_out,
                    "ms": round(ms), "usd": usd, "calls": n_calls})
        return usd

    def _emit(self, event: dict) -> None:
        event = {"ts": time.time(), "session": self.ledger.session_id, "layer": self.enabled, "agent_model": self.agent_model, **event}
        self.last_event = event
        metrics.observe(event)
        for fn in self.listeners:
            fn(event)

    # ---------------- lane 2: tool calls ----------------
    def issue_token(self, tools: list[str] | None = None, max_eur: float = 1e12, agent: str | None = None) -> str:
        """The token for this gate's own agent: every tool in the catalog unless narrowed."""
        p = self.policy
        catalog = tools or (p.tools.read + p.tools.irreversible)
        return ident.issue(agent or self.agent_id, self.acting_for or "", catalog, max_eur, p.identity.token_ttl_s)

    def delegate(self, parent_token: str | None, child: str, tools: list[str], max_eur: float, task: str) -> tuple[str | None, str]:
        """Agent-to-agent: hand a task to a sub-agent with a narrower token. Returns (token or None, reason)."""
        t0 = time.perf_counter()
        p = self.policy
        parent = parent_token or self.agent_token
        what = f'hand "{" ".join(task.split())[:90]}" to {child}, at most {max_eur:,.2f} EUR per payment'
        shown = {"child": child, "task": task, "max_eur": max_eur, "tools": tools}
        if not self.enabled:   # layer off: the sub-agent gets whatever was asked, nothing is checked
            parent_claims = ident.verify(parent)
            token = ident.issue(child, parent_claims.acting_for, tools, max_eur, p.identity.token_ttl_s,
                                chain=parent_claims.chain + (child,))
            self.caller = " > ".join(parent_claims.chain)
            self._record(Verdict(Decision.ALLOW, Effect("delegate", "unguarded", f"{what} (no protection)"), []), shown, t0, quiet=True)
            return token, "delegated without checks (layer off)"
        findings: list[Finding] = []
        token = None
        try:
            token = ident.delegate(parent, child, tools, max_eur, p.identity.max_delegation_depth)
        except ident.IdentityError as err:
            findings.append(Finding("identity.delegation", Decision.BLOCK, str(err), plain=(
                f"A helper agent can never get more power than the one handing over the job. This would let it pay up to "
                f"{max_eur:,.0f} EUR at once; the limit here is lower." if "amount" in str(err) else
                "A helper agent can never get more power than the one handing over the job.")))
        for sig in self.feed.match(scan_text(task), "tool_result"):   # the hand-off message is untrusted text too
            findings.append(Finding(f"feed.{sig.id}", Decision.BLOCK, f"delegated task contains {sig.name}",
                                    plain=f"The job handed over matches a known attack ({sig.name})."))
        inj = p.controls.injection
        if inj.mode != "off":
            score, feats = rules_score(task, p.email.internal_domains)
            if score >= inj.threshold:
                findings.append(Finding("injection.delegation", Decision.BLOCK,
                                        f"delegated task reads like an injected instruction (score {score:.2f}: {', '.join(feats)})",
                                        plain="The job handed over reads like a hidden instruction planted in a document."))
        decision = Decision.strictest([f.decision for f in findings])
        if decision != Decision.ALLOW:
            token = None
        effect = Effect("delegate", "delegation", what,
                        plain=f'Hand the job "{" ".join(task.split())[:90]}" to the payments helper, at most {max_eur:,.0f} EUR per payment')
        self.caller = " > ".join(ident.verify(parent).chain)
        self._record(Verdict(decision, effect, findings or [Finding("identity.delegation", Decision.ALLOW, "narrower rights, same person")]),
                     shown, t0)
        return token, Verdict(decision, effect, findings).reason if findings else "delegated"

    def _identity(self, token: str | None, name: str, args: dict, p) -> tuple[list[Finding], str]:
        """Checks the caller: signed, acting for this session's person, allowed this tool and this amount."""
        if token is None:   # the session's own in-process agent
            if not p.identity.required:
                return [], self.agent_id
            token = self.agent_token
        try:
            c = ident.verify(token or "")
        except ident.IdentityError as err:
            return [Finding("identity.token", Decision.BLOCK, str(err),
                            plain="The request did not come with a valid ID for the AI, so nothing runs.")], "unknown agent"
        who = " > ".join(c.chain)
        out = []
        if self.acting_for and c.acting_for != self.acting_for:
            out.append(Finding("identity.impersonation", Decision.BLOCK,
                               f"{who} claims to act for '{c.acting_for}' but this session belongs to '{self.acting_for}'",
                               plain="The AI tried to act as a different person than the one it works for."))
        if name not in c.tools:
            out.append(Finding("identity.tool_scope", Decision.BLOCK, f"{who} was not given the tool {name}",
                               plain="This AI was never given permission to do that."))
        try:
            amount = float(args.get("amount_eur")) if isinstance(args, dict) and args.get("amount_eur") is not None else None
        except (TypeError, ValueError):
            amount = None
        if amount is not None and amount > c.max_eur:
            out.append(Finding("identity.amount_scope", Decision.BLOCK,
                               f"{who} may move at most {c.max_eur:,.2f} EUR per action, not {amount:,.2f}",
                               plain=f"This AI may move at most {c.max_eur:,.0f} EUR in one go; this was {amount:,.2f} EUR."))
        if c.depth > p.identity.max_delegation_depth:
            out.append(Finding("identity.depth", Decision.BLOCK, f"delegation chain {who} is deeper than allowed",
                               plain="Too many agents handing the job down to each other."))
        return out, who

    async def call_tool(self, name: str, args: dict, provider: str, token: str | None = None) -> object:
        t0 = time.perf_counter()
        p = self.policy
        if self._external(provider):
            args = self.vault.detokenize(args)
        self.ledger.facts["tool_calls"] += 1
        if not self.enabled:
            result = await self._run(name, args)
            self._record(Verdict(Decision.ALLOW, Effect(name, "unguarded", f"{name} (no protection)"), []), args, t0, quiet=True)
            return result

        laps = _Laps()
        args = canonical_args(args)
        laps.lap("normalize")
        kind = p.tools.kind(name)
        findings, caller = self._identity(token, name, args, p)
        laps.lap("identity")
        self.caller = caller
        result: object = None
        judged: dict | None = None
        contract, ctx, held = None, {}, limits.Reservations()

        n = self.ledger.count_call(name, args)
        if n > p.budgets.session.max_identical_calls:
            findings.append(Finding("loop.identical_call", Decision.BLOCK, f"same call repeated {n} times; stop and summarize",
                                    plain="The AI kept repeating the same step, so it was stopped."))
        for sig in self.feed.match(scan_text(json.dumps(args, default=str)), "tool_args"):
            findings.append(Finding(f"feed.{sig.id}", Decision(sig.action if sig.action != "warn" else "allow"),
                                    f"{sig.name} ({sig.ref})", plain=f"This matches a known attack: {sig.name}."))

        args, sf = screen_args(args, p.controls)
        findings += sf
        quarantined = getattr(self._conn, "quarantined", {}).get(name)   # e.g. an upstream MCP tool whose description changed
        if quarantined:
            findings.append(Finding("mcp.quarantine", Decision.BLOCK, f"tool {name} is quarantined: {quarantined}",
                                    plain="This outside tool changed or hides instructions, so it is locked until a person checks it."))
        laps.lap("known_bad")

        if any(f.decision == Decision.BLOCK for f in findings):
            effect = Effect(name, kind, f"{name} by {caller}")
        elif name in p.data.sql_tools:
            effect = Effect(name, "read", f"query_db: {' '.join(str(args.get('sql', '')).split())[:90]}")
            lim, _ = limits.reserve(p.limits, name, args, self.state, self.agent_id, self.ledger.session_id)
            findings += lim
            laps.lap("limits")
            if not any(f.decision == Decision.BLOCK for f in findings):
                result = self._query(args, p, findings)
            laps.lap("data_guard")
        else:
            contract = self.contracts.get(name)
            effect, cf, ctx = evaluate(name, args, kind, contract, self.fact_fns, self.store.lookup)
            findings += cf
            args = ctx.get("args", args)   # a contract control may have redacted arguments
            if kind == "deny":
                findings.append(Finding("tools.deny", Decision.BLOCK, f"{name} is denied by policy",
                                        plain="This action is switched off for the AI."))
            elif kind == "unknown":
                findings.append(Finding("tools.unknown", Decision(p.controls.unknown_tool), f"{name} is not in the tool catalog",
                                        plain="The AI tried an action nobody has approved for it."))
            tainted = self.ledger.facts.get("tainted")
            if kind == "irreversible" and tainted and p.controls.injection.after_detection == "ask":
                findings.append(Finding("injection.after_detection", Decision.ASK,
                                        f"this session read text that tried to instruct the agent ({tainted}); "
                                        "irreversible actions need a human",
                                        plain="Earlier in this chat a document tried to give the AI orders, so a person checks "
                                              "anything that cannot be undone."))
            laps.lap("contract")
            if kind == "irreversible" and not any(f.decision != Decision.ALLOW for f in findings):
                lim, held = limits.reserve(p.limits, name, args, self.state, self.agent_id, self.ledger.session_id)
                findings += lim
            laps.lap("limits")
            if kind == "irreversible" and p.justify.enabled and not any(f.decision != Decision.ALLOW for f in findings):
                judged = await self.judge.justify(effect, self.ledger, p.justify, p.justify.judge_model, self.today)
                if not judged.get("cached") and judged["tokens"] != (0, 0):
                    budget.settle(p.justify.judge_model, *judged["tokens"], judged["ms"], self.ledger, p, judge=True)
                findings.append(Finding("justify", judged["decision"], judged["reason"], deterministic=False,
                                        plain=_judge_plain(judged)))
            laps.lap("judge")

        decision = Decision.strictest([f.decision for f in findings])
        verdict = Verdict(decision, effect, findings, judged)
        if decision == Decision.ASK and self.approver and self.approver(verdict):
            decision = verdict.decision = Decision.ALLOW
            findings.append(Finding("human.approved", Decision.ALLOW, "approved by a human"))

        laps.lap("decide")
        executed = False
        if decision == Decision.ALLOW and result is None:
            result, executed = await self._execute(name, args, kind, contract, ctx, findings, verdict)
            decision = verdict.decision
        limits.settle(held, executed, self.state)
        laps.lap("execute")
        if decision == Decision.ASK:
            self.ledger.facts["held"] += 1
            result = {"status": "held_for_approval", "reason": verdict.reason,
                      "note": "A human must approve this in the approval queue. Do not retry it; tell the user it is waiting."}
        elif decision == Decision.BLOCK:
            self.ledger.facts["blocked"] += 1
            result = {"status": "blocked", "reason": verdict.reason}
            if all(f.fixable for f in findings if f.decision == Decision.BLOCK):
                # Only the agent's own values were wrong (a guessed id or amount). The checks stay the same on the retry.
                result["note"] = ("The values you passed do not match the company's records. Look them up with the tools "
                                  "and call again with those exact values, or explain the problem to the user.")

        result, masked = mask_result(result, name, p.data, self.ledger)
        if masked:
            findings.append(Finding("data.masked", Decision.ALLOW, f"{masked} sensitive values masked"))
        screened = None
        if executed or (decision == Decision.ALLOW and kind != "irreversible"):
            rs = await screen_result(name, result, p.controls, self.classifier, p.email.internal_domains)
            result, screened = rs.result, rs.event(p.controls.injection.threshold)
            findings += rs.findings
            if rs.injection and rs.injection.flagged(p.controls.injection.threshold):
                self.ledger.facts["tainted"] = f"{name} {next(iter(args.values()), '')}".strip()
                self.ledger.warnings.append(f"injection in {name} result")
            if rs.withheld:
                verdict.decision = decision = Decision.BLOCK
                self.ledger.facts["blocked"] += 1
        laps.lap("screen")
        text = json.dumps(result, default=str)
        self.ledger.index(text, f"tool {name}")
        for sig in self.feed.match(scan_text(text), "tool_result"):
            self.ledger.warnings.append(f"{sig.id} in {name} result")
            log.warn(f"{name} result matches {sig.id} {sig.name} (untrusted content; agent may be steered)")
        self._record(verdict, args, t0, screen=screened, laps=laps)
        return self.vault.tokenize(result) if self._external(provider) else result

    def _query(self, args: dict, p, findings: list[Finding]) -> object:
        out = run_query(self._conn.db, str(args.get("sql", "")), p.data, self.entities)
        if not out.ok and out.policy_denied:
            findings.append(Finding("data.denied", Decision.BLOCK, f"data guard: {out.error}", plain=_data_plain(out.denied)))
            return None
        if not out.ok:
            findings.append(Finding("data.sql_error", Decision.ALLOW, f"SQL error (not a policy issue): {out.error}"))
            return {"error": f"SQL error: {out.error}"}
        limits.record_rows(p.limits, "query_db", len(out.rows), self.state, self.agent_id, self.ledger.session_id)
        result: dict = {"columns": out.columns, "rows": out.rows}
        notes = ([f"columns hidden by policy: {', '.join(out.masked_columns)}"] if out.masked_columns else []) + \
                ([f"truncated to {p.data.max_rows_per_query} rows (policy limit)"] if out.truncated else [])
        if notes:
            result["note"] = "; ".join(notes)
            findings.append(Finding("data.limited", Decision.ALLOW, "; ".join(notes)))
        return result

    async def _run(self, name: str, args: dict) -> object:
        """Connectors may be sync (the ERP) or async (an upstream MCP server)."""
        result = self._conn.run_tool(name, args)
        return await result if inspect.isawaitable(result) else result

    async def _execute(self, name: str, args: dict, kind: str, contract, ctx: dict, findings: list[Finding],
                       verdict: Verdict) -> tuple[object, bool]:
        """Bind verified values, then re-check and execute in one transaction (no time-of-check gap)."""
        if contract is not None and contract.bind:
            args = bound_args(contract, args, ctx, self.store.lookup)
        if contract is not None and contract.atomic:
            with self._conn.transaction():
                _, recheck, _ = evaluate(name, args, kind, contract, self.fact_fns, self.store.lookup)
                stale = [f for f in recheck if f.decision != Decision.ALLOW]
                if stale:
                    findings.append(Finding("atomic.recheck", Decision.BLOCK,
                                            "state changed between check and execution: " + "; ".join(f.message for f in stale),
                                            plain="Something changed at the last moment, so it was not done."))
                    verdict.decision = Decision.BLOCK
                    return None, False
                result = self._conn.run_tool(name, args)   # inside the transaction: the ERP is synchronous by design
        else:
            result = await self._run(name, args)
        self._account(name, args, result)
        if kind == "irreversible":
            self.ledger.calls.clear()   # state changed: re-reading the same record is progress, not a loop
        return result, True

    def _account(self, name: str, args: dict, result: object) -> None:
        f = self.ledger.facts
        if name == "pay_invoice" and isinstance(result, dict) and result.get("status") in ("submitted", "error"):
            f["payments"] += 1
            try:
                f["paid_total_eur"] += float(args.get("amount_eur", 0))
            except (TypeError, ValueError):
                pass
        if name == "send_email":
            f["emails_sent"] += 1

    def _record(self, v: Verdict, args: dict, t0: float, quiet: bool = False, screen: dict | None = None,
                laps: _Laps | None = None) -> None:
        v.latency_ms = (time.perf_counter() - t0) * 1000
        judge_ms = (v.judge["ms"] if v.judge else 0.0) + (screen["ms"] if screen and screen["model_calls"] else 0.0)
        self.timings.append({"tool": v.effect.tool, "decision": v.decision.value,
                             "gate_ms": round(v.latency_ms - judge_ms, 3), "judge_ms": round(judge_ms, 1)})
        stages = dict(laps.ms) if laps else {}
        tw = time.perf_counter()
        self.audit.write({
            "session": self.ledger.session_id, "agent_model": self.agent_model, "caller": getattr(self, "caller", self.agent_id),
            "acting_for": self.acting_for, "tool": v.effect.tool, "kind": v.effect.kind, "decision": v.decision.value,
            "effect": v.effect.summary, "findings": [[f.control, f.decision.value, f.message] for f in v.findings],
            "judge": {k: v.judge[k] for k in ("decision", "quote", "quote_ok", "model") if k in v.judge} if v.judge else None,
            "policy_sha": self.store.sha, "feed_version": self.feed.version, "latency_ms": round(v.latency_ms, 2),
            "args_keys": sorted(args or {}), "stages_ms": stages,
        })
        stages["record"] = round((time.perf_counter() - tw) * 1000, 3)
        self._emit({"kind": "decision", "caller": getattr(self, "caller", self.agent_id), "tool": v.effect.tool, "decision": "off" if quiet else v.decision.value,
                    "effect": v.effect.summary, "reason": "" if quiet else v.reason,
                    "plain": v.effect.plain, "plain_reason": "" if quiet else v.plain_reason,
                    "findings": [[f.control, f.decision.value, f.message, f.deterministic, f.plain] for f in v.findings],
                    "judge": {k: v.judge[k] for k in ("decision", "quote", "quote_ok", "model", "reason") if k in v.judge} if v.judge else None,
                    "gate_ms": round(v.latency_ms - judge_ms, 3), "judge_ms": round(judge_ms), "args": args, "screen": screen,
                    "stages_ms": stages})
        if quiet:
            log.passthrough(v.effect.tool, json.dumps(args, default=str)[:110])
            return
        timing = f"{v.latency_ms - judge_ms:.1f} ms" + (f" + judge {judge_ms / 1000:.1f}s" if judge_ms else "")
        log.decision(v.effect.tool, v.effect.summary, v.decision.value, v.reason, timing,
                     judge=(f"{v.judge['model']}: {v.judge['reason']}" if v.judge else ""))

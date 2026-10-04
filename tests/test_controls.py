"""Controls beyond single calls: hot reload, signed feed, audit chain, privacy,
budgets, loops, data budget, and the judge's quote verification."""

from __future__ import annotations

import asyncio
import json
import time

import pytest

from tests.conftest import make_gate, make_store
from tollgate import budget, llm
from tollgate.audit import AuditLog, verify
from tollgate.judge import Judge
from tollgate.ledger import Ledger
from tollgate.policy import Justify, PolicyStore
from tollgate.privacy import Vault
from tollgate.signatures import Feed, sign
from tollgate.verdict import Decision, Effect

PAY_12500 = {"invoice_id": "INV-T2", "vendor_id": "V-103", "amount_eur": 12500, "iban": "DE89370400440532013000"}


def call(gate, store, tool, args):
    asyncio.run(gate.call_tool(tool, args, "ollama"))
    return gate.timings[-1]["decision"]


def _touch(path, text):
    path.write_text(text)
    t = time.time() + 2
    import os
    os.utime(path, (t, t))


def test_policy_hot_reload_changes_decision(sandbox, record):
    store = make_store()
    gate = make_gate(store, root=sandbox, tmp=sandbox)
    assert call(gate, store, "pay_invoice", PAY_12500) == "ask"
    policy = sandbox / "policy.yaml"
    _touch(policy, policy.read_text().replace("require_approval_above_eur: 10000", "require_approval_above_eur: 20000")
           .replace("window_minutes: 1440, max: 10000", "window_minutes: 1440, max: 20000"))
    gate.store._checked = 0
    assert asyncio.run(gate.call_tool("pay_invoice", PAY_12500, "ollama"))["status"] == "submitted"
    record("policy", "positive", True)


def test_invalid_policy_is_rejected_and_last_good_stays(sandbox, record):
    store = make_store()
    gate = make_gate(store, root=sandbox, tmp=sandbox)
    _touch(sandbox / "policy.yaml", (sandbox / "policy.yaml").read_text().replace("max_steps: 60", "max_steps: -5"))
    gate.store._checked = 0
    assert gate.policy.budgets.session.max_steps == 60
    record("policy", "negative", True)


def test_contract_hot_reload(sandbox, record):
    store = make_store()
    gate = make_gate(store, root=sandbox, tmp=sandbox)
    pack = sandbox / "contracts" / "finance_ap.yaml"
    _touch(pack, pack.read_text().replace("type: never, decision: block", "type: never, decision: ask"))
    gate.store._checked = gate._synced = 0   # skip the twice-a-second throttle
    assert call(gate, store, "update_vendor_bank_details", {"vendor_id": "V-101", "new_iban": "PL10105000997603123456789123"}) == "ask"
    record("contracts", "positive", True)


def test_tampered_feed_is_rejected(sandbox, record):
    feed_path = sandbox / "signatures" / "feed.json"
    feed = Feed(feed_path, True, 0.001)
    v0, n0 = feed.version, len(feed.signatures)
    data = json.loads(feed_path.read_text())
    data["version"], data["signatures"] = v0 + 1, []
    _touch(feed_path, json.dumps(data))           # changed without re-signing
    feed.sync(force=True)
    assert feed.version == v0 and len(feed.signatures) == n0
    sign(feed_path)                                # properly signed update is accepted
    feed._mtime = 0
    feed.sync(force=True)
    assert feed.version == v0 + 1
    record("feed", "negative", True)


def test_audit_chain_detects_tampering(tmp_path, record):
    log = AuditLog(tmp_path / "a.jsonl")
    for i in range(5):
        log.write({"n": i})
    assert verify(tmp_path / "a.jsonl")[0]
    lines = (tmp_path / "a.jsonl").read_text().splitlines()
    lines[2] = lines[2].replace('"n": 2', '"n": 9')
    (tmp_path / "a.jsonl").write_text("\n".join(lines) + "\n")
    ok, line, _ = verify(tmp_path / "a.jsonl")
    assert not ok and line == 3
    record("audit", "negative", True)


def test_privacy_vault_round_trip(record):
    v = Vault()
    text = "Pay PL61109010140000071219812874 and mail billing@balticpaper.example, id SYN12345678"
    tok = v.tokenize(text)
    assert "PL61" not in tok and "@" not in tok and "SYN1" not in tok
    assert v.detokenize({"iban": "[IBAN_1]"}) == {"iban": "PL61109010140000071219812874"}
    record("privacy", "positive", True)


def test_budget_blocks_step_limit_and_unknown_model(record):
    p = PolicyStore("policy.yaml").policy
    led = Ledger("t")
    led.facts["steps"] = p.budgets.session.max_steps
    with pytest.raises(budget.BudgetExceeded):
        budget.precheck("qwen3:8b", led, p)
    with pytest.raises(budget.BudgetExceeded):
        budget.precheck("gpt-unknown", Ledger("t2"), p)
    assert budget.cost("gemini-2.5-flash", 1_000_000, 0, 0, p) == pytest.approx(0.30)
    record("budget", "negative", True)


def test_identical_call_loop_is_stopped(gate, store, record):
    args = {"vendor_id": "V-101"}
    decisions = [call(gate, store, "get_payment_history", args) for _ in range(3)]
    assert decisions == ["allow", "allow", "block"]
    record("loops", "negative", True)


def test_data_budget_masks_after_limit(gate, store, record):
    seen_full = 0
    for vid in ["V-101", "V-102", "V-103", "V-104"] + [f"V-{200 + i}" for i in range(12)]:
        res = asyncio.run(gate.call_tool("get_vendor", {"vendor_id": vid}, "ollama"))
        seen_full += "..." not in res["iban"]
    d = gate.policy.data
    assert seen_full == (d.max_full_ibans_per_session if "get_vendor" in d.reveal_full_iban_tools else 0)
    record("data", "negative", True)


def test_bulk_vendor_list_is_masked(gate, store, record):
    res = asyncio.run(gate.call_tool("list_vendors", {}, "ollama"))
    assert all("..." in r["iban"] for r in res)
    record("data", "negative", True)


def test_guessed_values_get_a_retry_note_but_scam_signals_do_not(gate, store, record):
    """A guessed vendor id or amount is the agent's own mistake: the block says to look the real values up.
    A different bank account is a fraud signal: blocked with no invitation to try again."""
    guessed = asyncio.run(gate.call_tool("pay_invoice", {**PAY_12500, "vendor_id": "CLOUDHOST", "amount_eur": 999}, "ollama"))
    assert guessed["status"] == "blocked" and "Look them up" in guessed["note"]
    other_account = asyncio.run(gate.call_tool("pay_invoice", {**PAY_12500, "iban": "PL10105000997603123456789123"}, "ollama"))
    assert other_account["status"] == "blocked" and "note" not in other_account
    assert store.payments(invoice_id="INV-T2") == []
    record("payments", "negative", True)


def test_vendor_lookup_by_name(gate, store, record):
    """Agents often know only the supplier's name; reads accept it (one clear match only)."""
    res = asyncio.run(gate.call_tool("get_vendor", {"vendor_id": "CloudHost"}, "ollama"))
    assert res["vendor_id"] == "V-103"
    found = asyncio.run(gate.call_tool("list_vendors", {"search": "Vistula"}, "ollama"))
    assert [r["vendor_id"] for r in found] == ["V-104"]
    assert "error" in asyncio.run(gate.call_tool("get_vendor", {"vendor_id": "o"}, "ollama"))   # several match: no guess
    record("data", "positive", True)


def test_row_scope_hides_other_entities(gate, store, record):
    res = asyncio.run(gate.call_tool("query_db", {"sql": "SELECT COUNT(*) FROM invoices WHERE entity_id = 'DE01'"},
                                     "ollama"))
    assert res["rows"] == [[0]]
    record("data", "negative", True)


# ---------------- judge: quote verification (model faked, deterministic) ----------------
def _fake(answer):
    async def fake_complete_json(model, system, prompt, schema, timeout):
        from tollgate.llm.base import Turn
        return answer, Turn(json.dumps(answer), [], 10, 5, 3.0)
    return fake_complete_json


@pytest.mark.parametrize("answer,want", [
    ({"decision": "allow", "quote": "pay all approved invoices", "reason": "asked"}, Decision.ALLOW),
    ({"decision": "allow", "quote": "the CFO approved this payment", "reason": "invented"}, Decision.ASK),
    ({"decision": "block", "quote": "do NOT pay anything to Krakow Logistics", "reason": "forbidden"}, Decision.BLOCK),
    ({"decision": "allow", "quote": "pay", "reason": "too short"}, Decision.ASK),
    ({"decision": "allow", "quote": "[U2] Month-end run: please pay all approved invoices.", "reason": "asked"}, Decision.ALLOW),
    ({"decision": "allow", "quote": "[U2] the CFO approved this payment", "reason": "invented"}, Decision.ASK),
], ids=["real quote allows", "invented quote held", "forbidding quote blocks", "too-short quote held",
        "labelled real quote allows", "labelled invented quote held"])
def test_judge_quote_verification(monkeypatch, answer, want, record):
    monkeypatch.setattr(llm, "complete_json", _fake(answer))
    led = Ledger("j")
    led.add_user("Heads up: do NOT pay anything to Krakow Logistics until I tell you.")
    led.add_user("Month-end run: please pay all approved invoices.")
    effect = Effect("pay_invoice", "irreversible", "pay 2050 EUR to Krakow Logistics S.A.", {"checks": {}}, 2050, ["V-102"])
    out = asyncio.run(Judge().justify(effect, led, Justify(judge_model="fake"), "fake"))
    assert out["decision"] == want
    record("judge", "positive" if want == Decision.ALLOW else "negative", True)


def test_judge_failure_fails_closed(monkeypatch, record):
    async def broken(*a, **k):
        raise TimeoutError
    monkeypatch.setattr(llm, "complete_json", broken)
    led = Ledger("j")
    led.add_user("please pay all approved invoices")
    out = asyncio.run(Judge().justify(Effect("pay_invoice", "irreversible", "pay"), led, Justify(judge_model="x"), "x"))
    assert out["decision"] == Decision.ASK
    record("judge", "negative", True)


def _write_many(path, tag):
    log = AuditLog(path)   # a separate instance, as in a separate process
    for i in range(150):
        log.write({"tool": tag, "i": i})


def test_audit_chain_survives_several_processes(tmp_path, record):
    """Two processes (say the dashboard and a CLI test run) append to one log at the same time: the chain stays valid."""
    import multiprocessing as mp
    path = tmp_path / "shared.jsonl"
    procs = [mp.get_context("spawn").Process(target=_write_many, args=(path, t)) for t in ("a", "b")]
    for p in procs:
        p.start()
    for p in procs:
        p.join()
    ok, n, msg = verify(path)
    assert ok and n == 300, msg
    record("audit", "negative", True)


def test_audit_chain_survives_many_gates_on_one_file(tmp_path, record):
    path = tmp_path / "shared.jsonl"
    store = make_store()
    gates = [make_gate(store, tmp=tmp_path) for _ in range(3)]
    for g in gates:
        g.audit = AuditLog.open(path)
    for g in gates + gates[::-1]:   # interleaved writers, like chats in the dashboard
        asyncio.run(g.call_tool("list_open_invoices", {}, "ollama"))
    ok, n, _ = verify(path)
    assert ok and n == 6
    record("audit", "positive", True)

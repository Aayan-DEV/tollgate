"""Agent identity: forged tokens, impersonation, scope, delegation that tries to widen, depth, expiry."""

from __future__ import annotations

import asyncio
import time

import pytest

from erp.seed import TODAY
from erp.store import MODEL_DIR
from tests.conftest import ROOT, make_store
from tollgate import identity as ident
from tollgate.gate import Gate

PAY_OK = {"invoice_id": "INV-T1", "vendor_id": "V-101", "amount_eur": 4800, "iban": "PL61109010140000071219812874"}


def gate_for(person: str, tmp_path) -> Gate:
    return Gate(root=ROOT, connector=make_store(), model_dir=MODEL_DIR, session_id="id-test", user_entities=["PL01"],
                acting_for=person, policy_overrides={"justify.enabled": False, "state.path": ":memory:", "controls.injection.mode": "rules",
                                                   "signatures.url": None},
                audit_path=tmp_path / "a.jsonl", today=TODAY)


def call(gate, tool, args, token):
    res = asyncio.run(gate.call_tool(tool, args, "ollama", token=token))
    return gate.timings[-1]["decision"], res


def test_own_agent_passes(tmp_path, record):
    g = gate_for("piotr", tmp_path)
    assert call(g, "pay_invoice", PAY_OK, g.agent_token)[0] == "allow"
    record("identity", "positive", True)


@pytest.mark.parametrize("token", ["", "garbage", "abc.def"], ids=["missing", "garbage", "malformed"])
def test_missing_or_bad_token_is_blocked(tmp_path, token, record):
    g = gate_for("piotr", tmp_path)
    decision, res = call(g, "list_open_invoices", {}, token)
    assert decision == "block" and "token" in res["reason"]
    record("identity", "negative", True)


def test_forged_token_is_blocked(tmp_path, record):
    g = gate_for("piotr", tmp_path)
    payload, sig = g.agent_token.split(".")
    tampered = payload[:-2] + ("AA" if payload[-2:] != "AA" else "BB")
    decision, res = call(g, "list_open_invoices", {}, f"{tampered}.{sig}")
    assert decision == "block" and "signature" in res["reason"]
    record("identity", "negative", True)


def test_impersonating_another_person_is_blocked(tmp_path, record):
    g = gate_for("piotr", tmp_path)
    anna = ident.issue("ap-agent", "anna", ["pay_invoice"], 250000)   # a real token, but for the CFO's session
    decision, res = call(g, "pay_invoice", PAY_OK, anna)
    assert decision == "block" and "claims to act for 'anna'" in res["reason"]
    record("identity", "negative", True)


def test_tool_and_amount_scope(tmp_path, record):
    g = gate_for("piotr", tmp_path)
    reader = ident.issue("report-agent", "piotr", ["list_open_invoices", "read_invoice"], 0)
    assert call(g, "pay_invoice", PAY_OK, reader)[0] == "block"
    small = ident.issue("petty-cash-agent", "piotr", ["pay_invoice"], 1000)
    decision, res = call(g, "pay_invoice", PAY_OK, small)
    assert decision == "block" and "at most 1,000.00 EUR" in res["reason"]
    record("identity", "negative", True)


def test_delegation_only_narrows(record):
    parent = ident.issue("ap-agent", "piotr", ["read_invoice", "pay_invoice"], 10000)
    child = ident.delegate(parent, "payments-subagent", ["pay_invoice"], 5000, max_depth=2)
    c = ident.verify(child)
    assert c.chain == ("ap-agent", "payments-subagent") and c.max_eur == 5000 and c.acting_for == "piotr"
    with pytest.raises(ident.IdentityError, match="add tools"):
        ident.delegate(parent, "x", ["send_email"], 100, max_depth=2)
    with pytest.raises(ident.IdentityError, match="raise the amount"):
        ident.delegate(parent, "x", ["pay_invoice"], 50000, max_depth=2)
    grandchild = ident.delegate(child, "sub-sub", ["pay_invoice"], 100, max_depth=2)
    with pytest.raises(ident.IdentityError, match="deep"):
        ident.delegate(grandchild, "too-deep", ["pay_invoice"], 10, max_depth=2)
    record("identity", "negative", True)


def test_sub_agent_cannot_exceed_its_delegated_amount(tmp_path, record):
    g = gate_for("piotr", tmp_path)
    child = ident.delegate(g.agent_token, "payments-subagent", ["pay_invoice"], 1000, max_depth=2)
    decision, res = call(g, "pay_invoice", PAY_OK, child)
    assert decision == "block" and "ap-agent > payments-subagent" in res["reason"]
    record("identity", "negative", True)


def test_expired_token_is_blocked(tmp_path, record):
    g = gate_for("piotr", tmp_path)
    old = ident.issue("ap-agent", "piotr", ["list_open_invoices"], 0, ttl_s=-1)
    decision, res = call(g, "list_open_invoices", {}, old)
    assert decision == "block" and "expired" in res["reason"]
    record("identity", "negative", True)
    assert time.time() > 0


def test_layer_off_delegation_is_not_checked(tmp_path, record):
    g = gate_for("piotr", tmp_path)
    g.enabled = False
    token, reason = g.delegate(None, "payments-subagent", ["pay_invoice"], 10**7, "Pay invoice INV-T1")
    assert token and "layer off" in reason and g.timings[-1]["decision"] == "allow"
    assert ident.verify(token).max_eur == 10**7   # nothing narrowed it: that is what "off" means
    record("identity", "positive", True)

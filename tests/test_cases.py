"""Runs every case in tests/cases.yaml through the real gate and the real policy.yaml.

Expectations marked expect_by_policy are computed from the policy at test time,
so editing a limit in policy.yaml changes what the test expects instead of
breaking it. Each case also checks WHICH control decided (`because`).
"""

from __future__ import annotations

import asyncio

import pytest

from tests.conftest import CASES, make_gate, make_store


def expected(case: dict, gate) -> str:
    rule = case.get("expect_by_policy")
    if not rule:
        return case["expect"]
    limit = float(gate.store.lookup(rule["limit"]))
    return rule["above"] if float(rule["value"]) > limit else rule["otherwise"]


@pytest.mark.parametrize("case", CASES["cases"], ids=[f"{c['id']} {c['title']}" for c in CASES["cases"]])
def test_case(case, tmp_path, record):
    store = make_store()
    gate = make_gate(store, tmp=tmp_path)
    asyncio.run(gate.call_tool(case["tool"], case["args"], "ollama"))
    last = gate.timings[-1]
    want = expected(case, gate)
    audit = (tmp_path / "audit__tests.jsonl").read_text().strip().splitlines()[-1]
    fired = case.get("because")
    passed = last["decision"] == want and (fired is None or want == "allow" or f'"{fired}"' in audit)
    record(case["group"], case["polarity"], passed)
    assert last["decision"] == want, f"{case['id']}: got {last['decision']}, policy expects {want}"
    if fired and want != "allow":
        assert f'"{fired}"' in audit, f"{case['id']}: expected control {fired} to decide; audit: {audit[:300]}"
    assert last["gate_ms"] < 50, f"deterministic path too slow: {last['gate_ms']} ms"

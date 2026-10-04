"""Incident -> regression test pipeline.

Every harmful run is broken into single tool calls. Each call is replayed alone
on a fresh copy of its scenario with no protection; if that one call causes
harm by itself, it is a minimal reproducer and becomes a permanent test case
in tests/incidents/<source>.json. The gate must never allow it again.

Calls only the AI judge can stop (e.g. 'pay only X' scope) are tagged
needs_judge, so the fast deterministic suite stays deterministic.

  uv run python -m evals.incidents results/evals/baseline__qwen3_8b.jsonl ...
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

from erp.seed import TODAY
from erp.store import MODEL_DIR, ApStore
from evals.harm import judge
from evals.scenarios import SCENARIOS
from tollgate import log
from tollgate.gate import Gate

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "tests" / "incidents"
REPLAYED = {"pay_invoice", "send_email", "update_vendor_bank_details", "load_forecast_model", "query_db"}


def gate_decision(scenario: dict, tool: str, args: dict) -> str:
    store = ApStore(scenario["fixture"])
    gate = Gate(root=ROOT, connector=store, model_dir=MODEL_DIR, session_id="incident", user_entities=["PL01"],
                policy_overrides={"justify.enabled": False, "state.path": ":memory:", "signatures.url": None,
                                  "controls.injection.mode": "rules"},
                audit_path=Path("/dev/null"), today=TODAY)
    for user_text, _ in scenario.get("history", []):
        gate.ledger.add_user(user_text)
    gate.ledger.add_user(scenario["user"])
    asyncio.run(gate.call_tool(tool, args, "ollama"))
    return gate.timings[-1]["decision"]


def extract(path: Path) -> list[dict]:
    by_id = {s["id"]: s for s in SCENARIOS}
    cases, seen = [], set()
    for line in path.read_text().splitlines():
        run = json.loads(line)
        if not run["harmful"] or run["scenario"] not in by_id:
            continue
        s = by_id[run["scenario"]]
        for call in run["calls"]:
            if call["name"] not in REPLAYED:
                continue
            key = (s["id"], call["name"], json.dumps(call["args"], sort_keys=True))
            if key in seen:
                continue
            seen.add(key)
            store = ApStore(s["fixture"])
            result = store.run_tool(call["name"], call["args"])
            alone = judge(s, store, [{"name": call["name"], "args": call["args"], "result": result}], "")
            if not alone["harmful"]:
                continue   # legitimate step inside a harmful run, not a reproducer
            decision = gate_decision(s, call["name"], call["args"])
            cases.append({"id": f"{path.stem}:{s['id']}:{len(cases) + 1}", "scenario": s["id"], "tool": call["name"],
                          "args": call["args"], "harms": alone["harms"], "deterministic_decision": decision,
                          "needs_judge": decision == "allow"})
    return cases


if __name__ == "__main__":
    log.LEVEL = "quiet"
    OUT.mkdir(parents=True, exist_ok=True)
    for arg in sys.argv[1:]:
        p = Path(arg)
        cases = extract(p)
        (OUT / f"{p.stem}.json").write_text(json.dumps(cases, indent=1))
        judge_only = sum(c["needs_judge"] for c in cases)
        print(f"{p.name}: {len(cases)} incident reproducers -> tests/incidents/{p.stem}.json "
              f"({len(cases) - judge_only} stopped deterministically, {judge_only} need the judge)")

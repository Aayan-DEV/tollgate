"""Run scenarios with or without the gate and record what happened.

  uv run python -m evals.run --agent qwen3:8b --mode baseline  --runs 2
  uv run python -m evals.run --agent qwen3:8b --mode protected --judge qwen3:14b --runs 2
  uv run python -m evals.run --agent gemini-2.5-flash --mode protected --judge gemini-2.5-flash --scenarios S01,D01

Writes results/evals/<label>.jsonl (one line per run) and prints compact live logs.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Callable

from erp.seed import TODAY
from erp.store import MODEL_DIR, ApStore
from evals.agent import run_agent
from evals.harm import judge
from evals.scenarios import by_id, ensure_model_files
from tollgate import log
from tollgate.gate import Gate

ROOT = Path(__file__).resolve().parents[1]


async def run(agent: str, mode: str, judge_model: str | None = None, *, runs: int = 1, scenarios: str | None = None,
              label: str | None = None, concurrency: int = 1, retries: int = 2, fresh: bool = False,
              on_record: Callable[[dict], None] | None = None) -> Path:
    """Run the scenarios for one agent and mode; returns the results file. on_record is called after each run."""
    a = SimpleNamespace(agent=agent, mode=mode, judge=judge_model, runs=runs, scenarios=scenarios, label=label,
                        concurrency=concurrency, retries=retries, fresh=fresh)

    ensure_model_files()
    label = a.label or f"{a.mode}__{a.agent}" + (f"__judge-{a.judge}" if a.mode == "protected" and a.judge else "")
    out = ROOT / "results" / "evals" / f"{label.replace(':', '_')}.jsonl"
    out.parent.mkdir(parents=True, exist_ok=True)
    if a.fresh and out.exists():
        archive = ROOT / "results" / "archive" / f"{out.stem}__{time.strftime('%Y%m%d-%H%M%S')}.jsonl"
        archive.parent.mkdir(parents=True, exist_ok=True)
        out.rename(archive)   # kept, never deleted
    overrides = {"state.path": ":memory:", **({"justify.judge_model": a.judge} if a.judge else {})}  # each run isolated

    sem = asyncio.Semaphore(max(1, a.concurrency))
    lock = asyncio.Lock()

    async def one(s: dict, r: int) -> None:
        async with sem:
            log.session_label.set(f"{s['id'][:9]}#{r}")
            for attempt in range(1, a.retries + 2):   # a model outage mid-run is retried, so every scenario finishes
                store = ApStore(s["fixture"])
                gate = Gate(root=ROOT, connector=store, model_dir=MODEL_DIR, session_id=f"{label}/{s['id']}/{r}",
                            user_entities=["PL01"], enabled=a.mode == "protected", policy_overrides=overrides,
                            audit_path=ROOT / "logs" / f"audit__{label.replace(':', '_')}.jsonl", today=TODAY)
                gate.agent_model = a.agent
                decisions: list[dict] = []
                gate.listeners.append(lambda e: decisions.append(
                    {k: e.get(k) for k in ("tool", "decision", "effect", "plain", "plain_reason", "reason")}) if e["kind"] == "decision" else None)
                t0 = time.perf_counter()
                error = None
                try:
                    final, calls = await run_agent(s, a.agent, gate)
                except Exception as err:  # model or transport failure: retry, then record
                    final, calls, error = "", [], f"{type(err).__name__}: {err}"
                if not error:
                    break
                print(f"     retry {s['id']} #{r} after {error[:80]} (attempt {attempt})", flush=True)
            verdict = judge(s, store, calls, final)
            f = gate.ledger.facts
            rec = {"label": label, "mode": a.mode, "agent": a.agent, "judge": a.judge, "scenario": s["id"], "kind": s["kind"],
                   "run": r, "seconds": round(time.perf_counter() - t0, 1), "error": error, **verdict,
                   "blocked": f["blocked"], "held": f["held"], "tokens": f["tokens"], "usd": round(f["usd"], 6),
                   "steps": f["steps"], "timings": gate.timings, "final": final[:1500], "decisions": decisions,
                   "calls": [{"name": c["name"], "args": c["args"], "result": json.dumps(c["result"], default=str)[:600]} for c in calls],
                   "payments": store.made_payments, "emails": store.emails, "sql_writes": store.sql_writes,
                   "bank_changes": store.bank_changes, "models_loaded": store.models_loaded}
            async with lock:
                with out.open("a") as fh:
                    fh.write(json.dumps(rec, default=str) + "\n")
            tag = "ERROR" if error else ("HARM " if verdict["harmful"] else "SAFE ")
            util = "" if verdict["utility"] is None else f" useful={verdict['utility']:.0%}"
            if on_record:
                on_record(rec)
            print(f"===> {tag} {s['id']} #{r} {label} {rec['seconds']}s blocked={f['blocked']} held={f['held']}{util} "
                  f"{','.join(verdict['harms'])}{(' ' + error[:120]) if error else ''}", flush=True)

    await asyncio.gather(*(one(s, r) for s in by_id(a.scenarios) for r in range(1, a.runs + 1)))
    return out

async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--agent", default="qwen3:8b")
    ap.add_argument("--mode", choices=["baseline", "protected"], default="protected")
    ap.add_argument("--judge", default=None, help="judge model (protected mode); default from policy.yaml")
    ap.add_argument("--runs", type=int, default=1)
    ap.add_argument("--scenarios", default=None, help="comma list of ids, prefixes (S01) or groups (S, D)")
    ap.add_argument("--label", default=None)
    ap.add_argument("--concurrency", type=int, default=1, help="scenarios running at the same time")
    ap.add_argument("--retries", type=int, default=2, help="re-run a scenario whose model call failed")
    ap.add_argument("--fresh", action="store_true", help="replace the label's previous results instead of adding to them")
    a = ap.parse_args()
    await run(a.agent, a.mode, a.judge, runs=a.runs, scenarios=a.scenarios, label=a.label, concurrency=a.concurrency,
              retries=a.retries, fresh=a.fresh)


if __name__ == "__main__":
    asyncio.run(main())

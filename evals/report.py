"""Before/after report from results/evals/*.jsonl.

  uv run python -m evals.report
"""

from __future__ import annotations

import json
import statistics
from collections import Counter
from pathlib import Path

from evals.scenarios import SCENARIOS

DIR = Path(__file__).resolve().parents[1] / "results" / "evals"
ORDER = ["baseline", "protected"]


def pct(n: float, d: float) -> str:
    return f"{round(100 * n / d)}%" if d else "-"


def p(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    values = sorted(values)
    return values[min(len(values) - 1, int(q * len(values)))]


def main() -> None:
    rows = [json.loads(line) for f in sorted(DIR.glob("*.jsonl")) if f.stem != "smoke" for line in f.read_text().splitlines() if line]
    labels = sorted({r["label"] for r in rows}, key=lambda l: (ORDER.index(l.split("__")[0]) if l.split("__")[0] in ORDER else 9, l))
    short = {l: l.replace("protected__", "P:").replace("baseline__", "B:").replace("__judge-", "+J:") for l in labels}
    w = 20
    print("\nHarmful runs per scenario (harmful / completed)\n")
    print("scenario".ljust(22) + "".join(short[l][:w - 1].ljust(w) for l in labels))
    for s in SCENARIOS:
        cells = []
        for l in labels:
            rs = [r for r in rows if r["label"] == l and r["scenario"] == s["id"] and not r["error"]]
            errs = sum(1 for r in rows if r["label"] == l and r["scenario"] == s["id"] and r["error"])
            h = sum(r["harmful"] for r in rs)
            cells.append((f"{h}/{len(rs)}" + (f" ({errs}e)" if errs else "")).ljust(w))
        print(s["id"].ljust(22) + "".join(cells))

    print("\nSummary\n")
    head = ["setup", "harm (risky)", "harm money", "harm data", "useful", "blocked", "held", "$ / run", "gate p50/p95 ms", "judge p50 s"]
    print("".join(h.ljust(17) for h in head))
    for l in labels:
        rs = [r for r in rows if r["label"] == l and not r["error"]]
        risky = [r for r in rs if r["kind"] not in ("control", "control_data")]
        money = [r for r in risky if r["scenario"].startswith("S")]
        data = [r for r in risky if r["scenario"].startswith("D")]
        useful = [r["utility"] for r in rs if r["utility"] is not None]
        gate_ms = [t["gate_ms"] for r in rs for t in r["timings"] if r["mode"] == "protected"]
        judge_ms = [t["judge_ms"] for r in rs for t in r["timings"] if t["judge_ms"]]
        cells = [short[l], f"{sum(r['harmful'] for r in risky)}/{len(risky)} {pct(sum(r['harmful'] for r in risky), len(risky))}",
                 pct(sum(r["harmful"] for r in money), len(money)), pct(sum(r["harmful"] for r in data), len(data)),
                 pct(sum(useful), len(useful)), f"{statistics.mean(r['blocked'] for r in rs):.1f}" if rs else "-",
                 f"{statistics.mean(r['held'] for r in rs):.1f}" if rs else "-",
                 f"{statistics.mean(r['usd'] for r in rs):.4f}" if rs else "-",
                 f"{p(gate_ms, .5):.1f}/{p(gate_ms, .95):.1f}" if gate_ms else "-",
                 f"{p(judge_ms, .5) / 1000:.1f}" if judge_ms else "-"]
        print("".join(str(c)[:16].ljust(17) for c in cells))

    print("\nHarm types\n")
    for l in labels:
        c = Counter(h for r in rows if r["label"] == l for h in r["harms"])
        print(f"{short[l]}: " + (", ".join(f"{k} x{v}" for k, v in c.most_common()) or "none"))


if __name__ == "__main__":
    main()

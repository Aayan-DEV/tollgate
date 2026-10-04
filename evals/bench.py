"""Hot-path benchmark: deterministic gate decisions, no model calls.

  uv run python -m evals.bench [--n 3000]
"""

from __future__ import annotations

import argparse
import asyncio
import statistics
import tempfile
import time
from pathlib import Path

from erp.seed import TODAY
from erp.store import MODEL_DIR, ApStore
from tollgate import log
from tollgate.gate import Gate

ROOT = Path(__file__).resolve().parents[1]
CALLS = [
    ("list_open_invoices", {}),
    ("read_invoice", {"invoice_id": "INV-B1"}),
    ("get_vendor", {"vendor_id": "V-101"}),
    ("pay_invoice", {"invoice_id": "INV-B1", "vendor_id": "V-101", "amount_eur": 12000, "iban": "PL10105000997603123456789123"}),
    ("query_db", {"sql": "SELECT COUNT(*) FROM invoices WHERE vendor_id = 'V-101'"}),
    ("query_db", {"sql": "SELECT name FROM vendors UNION SELECT national_id FROM employees"}),
    ("send_email", {"to": "x@other.example", "subject": "list", "body": "PL61109010140000071219812874"}),
    ("update_vendor_bank_details", {"vendor_id": "V-101", "new_iban": "PL10105000997603123456789123"}),
]


async def main(n: int) -> None:
    log.LEVEL = "quiet"
    store = ApStore({"invoices": [{"invoice_id": "INV-B1", "vendor_id": "V-101", "vendor_name": "Baltic Paper Sp. z o.o.",
                                   "amount": 4800, "status": "approved", "document": "INVOICE INV-B1"}]})
    tmp = Path(tempfile.mkdtemp())
    gate = Gate(root=ROOT, connector=store, model_dir=MODEL_DIR, session_id="bench", user_entities=["PL01"],
                policy_overrides={"justify.enabled": False, "state.path": ":memory:", "signatures.url": None,
                                  "controls.injection.mode": "rules",
                                  "budgets.session.max_identical_calls": 10**9, "data.max_full_ibans_per_session": 10**9},
                audit_path=tmp / "audit.jsonl", today=TODAY)
    per_tool: dict[str, list[float]] = {}
    t0 = time.perf_counter()
    for i in range(n):
        name, args = CALLS[i % len(CALLS)]
        await gate.call_tool(name, args, "ollama")
        per_tool.setdefault(name, []).append(gate.timings[-1]["gate_ms"])
    wall = time.perf_counter() - t0
    allms = sorted(t["gate_ms"] for t in gate.timings)
    q = lambda v, p: v[min(len(v) - 1, int(p * len(v)))]  # noqa: E731
    print(f"{n} decisions in {wall:.2f}s -> {n / wall:,.0f} decisions/s on one core (includes audit writes)")
    print(f"all     p50 {q(allms, .5):.2f} ms   p95 {q(allms, .95):.2f} ms   p99 {q(allms, .99):.2f} ms")
    for name, v in per_tool.items():
        v.sort()
        print(f"  {name:<28} p50 {q(v, .5):.2f} ms  p99 {q(v, .99):.2f} ms  mean {statistics.mean(v):.2f} ms")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=3000)
    asyncio.run(main(ap.parse_args().n))

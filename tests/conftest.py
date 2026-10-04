"""Shared fixtures and the coverage matrix printed at the end of the run."""

from __future__ import annotations

import shutil
from collections import defaultdict
from pathlib import Path

import pytest
import yaml

from erp.seed import DB_PATH, TODAY, build
from erp.store import MODEL_DIR, ApStore
from evals.scenarios import ensure_model_files
from tollgate.gate import Gate

ROOT = Path(__file__).resolve().parents[1]
CASES = yaml.safe_load((ROOT / "tests" / "cases.yaml").read_text())
RESULTS: list[tuple[str, str, bool]] = []   # (group, polarity, passed)


def pytest_sessionstart(session):
    if not DB_PATH.exists():
        build()
    ensure_model_files()


def make_store() -> ApStore:
    vendors = {"V-101": "Baltic Paper Sp. z o.o.", "V-102": "Krakow Logistics S.A.", "V-103": "CloudHost GmbH", "V-104": "Vistula Office Supplies"}
    invoices = [{"invoice_id": i["invoice_id"], "vendor_id": i["vendor"], "vendor_name": vendors[i["vendor"]], "amount": i["amount"],
                 "status": i["status"], "document": i.get("document", f"INVOICE {i['invoice_id']}")} for i in CASES["open_invoices"]]
    return ApStore({"invoices": invoices, "history": CASES["history"]})


def make_gate(store: ApStore, root: Path = ROOT, tmp: Path | None = None, **overrides) -> Gate:
    return Gate(root=root, connector=store, model_dir=MODEL_DIR, session_id="test", user_entities=["PL01"],
                policy_overrides={"justify.enabled": False, "state.path": ":memory:",
                                  "controls.injection.mode": "rules", "signatures.url": None,   # offline and deterministic
                                  **overrides},
                audit_path=(tmp or root / "logs") / "audit__tests.jsonl", today=TODAY)


@pytest.fixture
def store() -> ApStore:
    return make_store()


@pytest.fixture
def gate(store, tmp_path) -> Gate:
    return make_gate(store, tmp=tmp_path)


@pytest.fixture
def sandbox(tmp_path) -> Path:
    """A private copy of policy, contracts and feed that a test may edit."""
    for rel in ("policy.yaml", "contracts", "signatures"):
        src = ROOT / rel
        (shutil.copytree if src.is_dir() else shutil.copy)(src, tmp_path / rel)
    return tmp_path


@pytest.fixture
def record():
    def _record(group: str, polarity: str, passed: bool) -> None:
        RESULTS.append((group, polarity, passed))
    return _record


def pytest_terminal_summary(terminalreporter):
    if not RESULTS:
        return
    table: dict[str, dict[str, list[int]]] = defaultdict(lambda: {"positive": [0, 0], "negative": [0, 0]})
    for group, polarity, passed in RESULTS:
        cell = table[group][polarity]
        cell[0] += int(passed)
        cell[1] += 1
    w = terminalreporter.write_line
    w("")
    w("Control coverage (passed / total)")
    w(f"{'control':<12}{'allowed (positive)':<22}{'stopped (negative)':<22}")
    for group in sorted(table):
        p, n = table[group]["positive"], table[group]["negative"]
        w(f"{group:<12}{f'{p[0]}/{p[1]}':<22}{f'{n[0]}/{n[1]}':<22}")

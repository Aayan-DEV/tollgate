"""Append-only, hash-chained audit log (JSON Lines).

Each record stores the SHA-256 of the previous record, so editing or deleting
any line breaks the chain. Raw prompts are never stored; effects, findings and
decisions are.
  verify:  uv run python -m tollgate.audit logs/audit.jsonl
  export:  uv run python -m tollgate.audit csv logs/audit.jsonl > audit.csv
"""

from __future__ import annotations

import csv
import fcntl
import hashlib
import io
import json
import os
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

GENESIS = "0" * 64


class AuditLog:
    """One chain per file. Use AuditLog.open(path): every gate writing a file shares one instance (and its last hash)."""

    _open: dict[Path, "AuditLog"] = {}
    _registry_lock = threading.Lock()

    @classmethod
    def open(cls, path: str | Path) -> "AuditLog":
        key = Path(path).resolve()
        with cls._registry_lock:
            if key not in cls._open:
                cls._open[key] = cls(key)
            return cls._open[key]

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._prev = GENESIS
        if self.path.exists():
            last = ""
            with self.path.open() as fh:
                for line in fh:
                    last = line or last
            if last.strip():
                self._prev = json.loads(last)["hash"]

    def write(self, event: dict) -> None:
        """Append one record. Several processes may share the file (more gate processes, a CLI test run next to the
        dashboard): an exclusive file lock serialises them and the previous hash is read from the file itself."""
        with self._lock, self.path.open("a+b") as fh:
            fcntl.flock(fh, fcntl.LOCK_EX)
            try:
                self._prev = _last_hash(fh) or self._prev
                record = {"ts": round(time.time(), 3), **event, "prev": self._prev}
                body = json.dumps(record, sort_keys=True, default=str)
                record["hash"] = hashlib.sha256(body.encode()).hexdigest()
                fh.write((json.dumps(record, sort_keys=True, default=str) + "\n").encode())
                fh.flush()
                self._prev = record["hash"]
            finally:
                fcntl.flock(fh, fcntl.LOCK_UN)


def _last_hash(fh) -> str | None:
    """The hash of the file's last record, reading backwards from the end (records are a few KB at most)."""
    end = fh.seek(0, os.SEEK_END)
    if end == 0:
        return None
    chunk, pos = b"", end
    while pos > 0:
        step = min(65536, pos)
        pos -= step
        fh.seek(pos)
        chunk = fh.read(step) + chunk
        lines = chunk.rstrip(b"\n").split(b"\n")
        if len(lines) > 1 or pos == 0:
            return json.loads(lines[-1])["hash"]
    return None


def verify(path: str | Path) -> tuple[bool, int, str]:
    prev, n = GENESIS, 0
    with Path(path).open() as fh:
        for n, line in enumerate(fh, 1):
            rec = json.loads(line)
            stored = rec.pop("hash")
            if rec["prev"] != prev or hashlib.sha256(json.dumps(rec, sort_keys=True, default=str).encode()).hexdigest() != stored:
                return False, n, f"chain broken at line {n}"
            prev = stored
    return True, n, "chain intact"


def recent(path: str | Path, limit: int = 100) -> list[dict]:
    """The newest records first (for a viewer; the file itself is never rewritten)."""
    if not Path(path).exists():
        return []
    with Path(path).open() as fh:
        lines = fh.readlines()
    return [json.loads(line) for line in reversed(lines[-limit:])]


CSV_COLUMNS = ["time_utc", "agent_model", "caller", "acting_for", "session", "tool", "kind", "decision", "effect", "deciding_controls", "reason", "quiet_actions",
               "judge_decision", "judge_quote", "judge_quote_verified", "latency_ms", "policy_sha", "feed_version", "hash"]


def to_csv(path: str | Path, model: str | None = None) -> str:
    """The audit log as CSV for a spreadsheet or a GRC tool: one row per decision, newest last."""
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(CSV_COLUMNS)
    if not Path(path).exists():
        return buf.getvalue()
    with Path(path).open() as fh:
        for line in fh:
            r = json.loads(line)
            if model and r.get("agent_model") != model:
                continue
            deciding = [f for f in r.get("findings", []) if f[1] == r["decision"] and r["decision"] != "allow"]
            quiet = [f[2] for f in r.get("findings", []) if f[1] == "allow" and f[0].startswith(("injection.", "secrets.", "data."))]
            j = r.get("judge") or {}
            w.writerow([datetime.fromtimestamp(r["ts"], timezone.utc).isoformat(timespec="seconds"), r.get("agent_model"),
                        r.get("caller"), r.get("acting_for"), r.get("session"), r.get("tool"),
                        r.get("kind"), r.get("decision"), r.get("effect"), " ".join(f[0] for f in deciding),
                        "; ".join(f[2] for f in deciding), "; ".join(quiet), j.get("decision", ""), j.get("quote", ""),
                        j.get("quote_ok", ""), r.get("latency_ms"), r.get("policy_sha"), r.get("feed_version"), r.get("hash")])
    return buf.getvalue()


if __name__ == "__main__":
    args = sys.argv[1:]
    if args[:1] == ["csv"]:
        sys.stdout.write(to_csv(args[1] if len(args) > 1 else "logs/audit.jsonl"))
        sys.exit(0)
    ok, n, msg = verify(args[0] if args else "logs/audit.jsonl")
    print(f"{'OK' if ok else 'FAIL'}: {n} records, {msg}")
    sys.exit(0 if ok else 1)

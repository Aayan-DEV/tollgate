"""Shared state for cumulative limits: atomic reserve, then commit or release.

Per-call checks miss splitting (ten payments of 999 under a 10,000 limit).
Limits here are sums over a time window, keyed by vendor, agent or session.
SQLite in WAL mode lets several gateway processes share one file; the
reserve step runs in BEGIN IMMEDIATE so two gateways cannot both take the
last euro. A Redis implementation would expose the same three methods.
"""

from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path


class StateStore:
    def __init__(self, path: str | Path = ":memory:"):
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path), isolation_level=None, check_same_thread=False, timeout=5)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("CREATE TABLE IF NOT EXISTS usage (id INTEGER PRIMARY KEY, limit_id TEXT, key TEXT, amount REAL,"
                        " ts REAL, status TEXT)")
        self.db.execute("CREATE INDEX IF NOT EXISTS usage_k ON usage (limit_id, key, ts)")
        self._lock = threading.Lock()

    def used(self, limit_id: str, key: str, window_s: float) -> float:
        row = self.db.execute("SELECT COALESCE(SUM(amount), 0) FROM usage WHERE limit_id=? AND key=? AND ts>=? AND status!='released'",
                              (limit_id, key, time.time() - window_s)).fetchone()
        return float(row[0])

    def reserve(self, limit_id: str, key: str, amount: float, window_s: float, maximum: float) -> tuple[int | None, float]:
        """Atomically add `amount` if the window total stays <= maximum. Returns (reservation id or None, used before)."""
        with self._lock:
            self.db.execute("BEGIN IMMEDIATE")
            try:
                used = self.used(limit_id, key, window_s)
                if used + amount > maximum:
                    self.db.execute("ROLLBACK")
                    return None, used
                cur = self.db.execute("INSERT INTO usage (limit_id, key, amount, ts, status) VALUES (?,?,?,?, 'reserved')",
                                      (limit_id, key, amount, time.time()))
                self.db.execute("COMMIT")
                return cur.lastrowid, used
            except Exception:
                self.db.execute("ROLLBACK")
                raise

    def commit(self, reservation: int) -> None:
        self.db.execute("UPDATE usage SET status='committed' WHERE id=?", (reservation,))

    def release(self, reservation: int) -> None:
        self.db.execute("UPDATE usage SET status='released' WHERE id=?", (reservation,))

    def record(self, limit_id: str, key: str, amount: float) -> None:
        self.db.execute("INSERT INTO usage (limit_id, key, amount, ts, status) VALUES (?,?,?,?, 'committed')",
                        (limit_id, key, amount, time.time()))

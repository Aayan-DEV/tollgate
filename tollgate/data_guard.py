"""Data guard: what the agent may READ, enforced by the database engine itself.

query_db runs with SQLite's authorizer installed. The engine asks it about
every table and column it touches, after aliases, subqueries, CTEs and UNIONs
are resolved, so SQL tricks cannot route around it:
  - table not granted to the role   -> DENY (whole statement refused)
  - column denied                   -> IGNORE (comes back as NULL)
  - row-scoped table read directly  -> DENY (must go through the scoped view)
  - PRAGMA / ATTACH / writes / risky functions -> DENY
Row scope uses TEMP views (e.g. invoices WHERE entity_id IN the user's entities).

Every tool result also passes mask_result(): full IBANs and national IDs are
masked unless the tool is allowed to reveal one, and reveals are capped per
session (a data budget against bulk extraction).
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass, field

from tollgate.connectors import IBAN_RE, NATIONAL_ID_RE
from tollgate.ledger import Ledger
from tollgate.policy import Data, DataRole

BLOCKED_FUNCTIONS = {"load_extension", "readfile", "writefile", "edit", "fts3_tokenizer"}
SAFE_ID = re.compile(r"^[A-Z0-9_-]+$")


@dataclass
class QueryOutcome:
    ok: bool
    columns: list[str] = field(default_factory=list)
    rows: list[list] = field(default_factory=list)
    denied: list[str] = field(default_factory=list)
    masked_columns: list[str] = field(default_factory=list)
    truncated: bool = False
    error: str | None = None
    policy_denied: bool = False      # False = the SQL itself was wrong (typo, unknown column)


class _Authorizer:
    def __init__(self, role: DataRole):
        self.role = role
        self.denied: set[str] = set()
        self.masked: set[str] = set()

    def __call__(self, action, arg1, arg2, dbname, source):
        if action in (sqlite3.SQLITE_SELECT, sqlite3.SQLITE_RECURSIVE):
            return sqlite3.SQLITE_OK
        if action == sqlite3.SQLITE_READ:
            table, column = arg1, arg2
            if dbname == "temp" or table.startswith("sqlite_temp"):
                return sqlite3.SQLITE_OK
            rule = self.role.tables.get(table)
            if rule is None:
                self.denied.add(f"table {table}")
                return sqlite3.SQLITE_DENY
            if rule.row_scope and source is None:
                self.denied.add(f"direct read of main.{table} (use the scoped table name)")
                return sqlite3.SQLITE_DENY
            if column in rule.deny_columns:
                self.masked.add(f"{table}.{column}")
                return sqlite3.SQLITE_IGNORE
            return sqlite3.SQLITE_OK
        if action == sqlite3.SQLITE_FUNCTION:
            if str(arg2).lower() in BLOCKED_FUNCTIONS:
                self.denied.add(f"function {arg2}")
                return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK
        names = {v: k for k, v in vars(sqlite3).items() if k.startswith("SQLITE_") and isinstance(v, int)}
        self.denied.add(f"operation {names.get(action, action)}")
        return sqlite3.SQLITE_DENY


def run_query(conn: sqlite3.Connection, sql: str, data: Data, entities: list[str]) -> QueryOutcome:
    role = data.roles[data.role]
    auth = _Authorizer(role)
    scoped = [(t, r.row_scope) for t, r in role.tables.items() if r.row_scope]
    ents = ",".join(f"'{e}'" for e in entities if SAFE_ID.match(e)) or "''"
    conn.set_authorizer(None)
    for table, col in scoped:
        conn.execute(f"DROP VIEW IF EXISTS temp.{table}")
        conn.execute(f"CREATE TEMP VIEW {table} AS SELECT * FROM main.{table} WHERE {col} IN ({ents})")
    try:
        conn.set_authorizer(auth)
        cur = conn.execute(str(sql).strip().rstrip(";"))
        cols = [c[0] for c in cur.description or []]
        rows = [list(r) for r in cur.fetchmany(data.max_rows_per_query + 1)]
        truncated = len(rows) > data.max_rows_per_query
        return QueryOutcome(True, cols, rows[: data.max_rows_per_query], sorted(auth.denied), sorted(auth.masked), truncated)
    except sqlite3.Error as err:
        if not auth.denied:
            # The query failed before the engine reached a table it would refuse (e.g. a misspelt column).
            # Name the refusal anyway, so a query aimed at a closed table never reads as allowed.
            conn.set_authorizer(None)
            known = {r[0] for r in conn.execute("SELECT name FROM main.sqlite_master WHERE type='table'")}
            named = {m.split(".")[-1] for m in re.findall(r"\b(?:from|join)\s+([A-Za-z_][\w.]*)", str(sql), re.I)}
            auth.denied.update(f"table {t}" for t in named & known if t not in role.tables)
        reason = "; ".join(sorted(auth.denied)) or str(err)
        return QueryOutcome(False, denied=sorted(auth.denied), masked_columns=sorted(auth.masked), error=reason,
                            policy_denied=bool(auth.denied))
    finally:
        conn.set_authorizer(None)
        for table, _ in scoped:
            conn.execute(f"DROP VIEW IF EXISTS temp.{table}")


def schema_text(conn: sqlite3.Connection, data: Data) -> str:
    """The tables and columns this role may read, for the query tool's description. Hidden columns are left out,
    so the agent neither guesses names nor learns that they exist."""
    role = data.roles[data.role]
    parts = []
    for table in sorted(role.tables):
        spec = role.tables[table]
        cols = [r[1] for r in conn.execute(f"PRAGMA main.table_info({table})") if r[1] not in spec.deny_columns]
        if cols:
            parts.append(f"{table}({', '.join(cols)})" + (" [only your entities' rows]" if spec.row_scope else ""))
    return "; ".join(parts)


def _mask_iban(m: re.Match) -> str:
    raw = re.sub(r"\s+", "", m.group(0))
    return f"{raw[:4]}...{raw[-4:]}"


def mask_text(text: str) -> tuple[str, int]:
    """Mask every IBAN, national ID and credential in free text (used to redact outgoing arguments)."""
    from tollgate import secrets
    text, a = IBAN_RE.subn(_mask_iban, text)
    text, b = NATIONAL_ID_RE.subn("SYN********", text)
    text, kinds = secrets.redact(text)
    return text, a + b + len(kinds)


def mask_result(result: object, tool: str, data: Data, ledger: Ledger) -> tuple[object, int]:
    """Mask IBANs and national IDs in a tool result. Returns (masked result, values masked)."""
    may_reveal = tool in data.reveal_full_iban_tools
    count = 0

    def scrub(text: str) -> str:
        nonlocal count

        def iban(m: re.Match) -> str:
            nonlocal count
            raw = re.sub(r"\s+", "", m.group(0))
            if may_reveal and (raw in ledger.revealed or len(ledger.revealed) < data.max_full_ibans_per_session):
                ledger.revealed.add(raw)
                return m.group(0)
            count += 1
            return _mask_iban(m)

        if "iban" in data.mask:
            text = IBAN_RE.sub(iban, text)
        if "national_id" in data.mask:
            text, n = NATIONAL_ID_RE.subn("SYN********", text)
            count += n
        return text

    def walk(v: object) -> object:
        if isinstance(v, str):
            return scrub(v)
        if isinstance(v, list):
            return [walk(x) for x in v]
        if isinstance(v, dict):
            return {k: walk(x) for k, x in v.items()}
        return v

    return walk(result), count

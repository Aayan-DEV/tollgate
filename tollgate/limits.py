"""Cumulative limits from policy.yaml, enforced through the shared StateStore.

  amount_eur / count : reserved before execution, committed after success,
                       released if the action did not run
  rows               : only known after a query, so checked before (is the
                       budget already used up?) and recorded after
"""

from __future__ import annotations

from dataclasses import dataclass, field

from tollgate.policy import Limit
from tollgate.state import StateStore
from tollgate.verdict import Decision, Finding


@dataclass
class Reservations:
    ids: list[int] = field(default_factory=list)
    over: list[tuple[str, str, float]] = field(default_factory=list)   # (limit, key, amount) a human may approve past


def _key(limit: Limit, args: dict, agent: str, session: str) -> str | None:
    if limit.per == "agent":
        return agent
    if limit.per == "session":
        return session
    value = args.get(limit.per)
    return str(value) if value not in (None, "") else None


def _amount(limit: Limit, args: dict) -> float | None:
    if limit.measure == "count":
        return 1.0
    if limit.measure == "amount_eur":
        try:
            return float(args.get("amount_eur"))
        except (TypeError, ValueError):
            return None
    return None


def reserve(limits: list[Limit], tool: str, args: dict, state: StateStore, agent: str, session: str
            ) -> tuple[list[Finding], Reservations]:
    findings, held = [], Reservations()
    for lim in limits:
        if tool not in lim.tools:
            continue
        key = _key(lim, args, agent, session)
        if key is None:
            continue
        window = lim.window_minutes * 60
        if lim.measure == "rows":
            used = state.used(lim.id, key, window)
            if used >= lim.max:
                findings.append(Finding(f"limits.{lim.id}", Decision(lim.decision),
                                        f"{lim.id}: {used:,.0f} rows already read (limit {lim.max:,.0f} per {lim.per})",
                                        plain=f"The assistant has already read {used:,.0f} rows of data in this chat; "
                                              f"the limit is {lim.max:,.0f}."))
            continue
        amount = _amount(lim, args)
        if amount is None:
            continue
        rid, used = state.reserve(lim.id, key, amount, window, lim.max)
        if rid is None:
            held.over.append((lim.id, key, amount))
            findings.append(Finding(f"limits.{lim.id}", Decision(lim.decision),
                                    f"{lim.id}: {used:,.2f} + {amount:,.2f} would exceed {lim.max:,.0f} per {lim.per} "
                                    f"in {lim.window_minutes // 60 or lim.window_minutes}{'h' if lim.window_minutes >= 60 else 'min'}",
                                    plain=_plain(lim, used, amount)))
        else:
            held.ids.append(rid)
    return findings, held


def _plain(lim: Limit, used: float, amount: float) -> str:
    window = "today" if lim.window_minutes >= 1440 else "this hour" if lim.window_minutes == 60 else f"in the last {lim.window_minutes} minutes"
    if lim.measure == "count":
        return f"The assistant has already done this {used:,.0f} times {window}; the limit is {lim.max:,.0f}."
    who = "This supplier has" if lim.per == "vendor_id" else "The assistant has" if lim.per == "agent" else "This chat has"
    return (f"{who} already been paid {used:,.2f} EUR {window}. Another {amount:,.2f} EUR would go over the limit of "
            f"{lim.max:,.0f} EUR, so a person decides." if lim.per == "vendor_id" else
            f"{who} already paid out {used:,.2f} EUR {window}. Another {amount:,.2f} EUR would go over the limit of {lim.max:,.0f} EUR.")


def settle(held: Reservations, executed: bool, state: StateStore) -> None:
    for rid in held.ids:
        (state.commit if executed else state.release)(rid)
    if executed:   # approved past a limit by a person: it still counts toward the window
        for limit_id, key, amount in held.over:
            state.record(limit_id, key, amount)


def record_rows(limits: list[Limit], tool: str, rows: int, state: StateStore, agent: str, session: str) -> None:
    for lim in limits:
        if tool in lim.tools and lim.measure == "rows":
            state.record(lim.id, _key(lim, {}, agent, session) or session, float(rows))

"""Model guard: allowlist and per-session budgets for commercial and local models.

Commercial APIs are priced per token from the policy's price table; local
models are priced per second of compute, so a runaway local loop is just as
visible as a runaway API bill.
"""

from __future__ import annotations

from tollgate.ledger import Ledger
from tollgate.policy import Policy


class BudgetExceeded(Exception):
    pass


def precheck(model: str, ledger: Ledger, policy: Policy) -> None:
    if model not in policy.models.allowed:
        raise BudgetExceeded(f"model {model} is not on the allowlist")
    s, f = policy.budgets.session, ledger.facts
    if f["steps"] >= s.max_steps:
        raise BudgetExceeded(f"step limit reached ({s.max_steps} model turns)")
    if f["tokens"] >= s.max_tokens:
        raise BudgetExceeded(f"token budget used ({f['tokens']:,}/{s.max_tokens:,})")
    if f["usd"] >= s.max_usd:
        raise BudgetExceeded(f"cost budget used (${f['usd']:.4f}/${s.max_usd})")


def cost(model: str, tokens_in: int, tokens_out: int, ms: float, policy: Policy) -> float:
    price = policy.models.pricing_usd_per_1m.get(model)
    if price:
        return (tokens_in * price.input + tokens_out * price.output) / 1_000_000
    return ms / 1000 * policy.models.local_usd_per_compute_second


def settle(model: str, tokens_in: int, tokens_out: int, ms: float, ledger: Ledger, policy: Policy, judge: bool = False) -> float:
    usd = cost(model, tokens_in, tokens_out, ms, policy)
    f = ledger.facts
    f["tokens"] += tokens_in + tokens_out
    f["usd"] += usd
    if not judge:
        f["steps"] += 1
    return usd

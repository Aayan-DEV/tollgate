"""Effect contracts: declarative Resolve + Rules, loaded from YAML resolver packs.

A contract turns one tool call into an Effect (what would really happen,
looked up through read-only fact functions) and a list of Findings (checks
that failed). Evaluation is deterministic: no code from the YAML is ever
executed, only a fixed set of check types over looked-up values.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Callable, Literal

import yaml
from pydantic import BaseModel

from tollgate import log
from tollgate.verdict import Decision, Effect, Finding

MISSING = object()
CheckType = Literal["present", "equals", "same_iban", "number_equals", "empty", "at_most", "is_true", "never"]


class FactSpec(BaseModel):
    fn: str
    args: list = []


class Check(BaseModel):
    id: str
    type: CheckType
    decision: str                    # ask | block, or @controls.x whose value may also be "redact"
    message: str
    redact: list[str] = []           # with decision "redact": mask sensitive values in these args, then check again
    plain: str = ""                  # the message in everyday words (same {placeholders})
    path: str | None = None
    left: object = None
    right: object = None
    value: object = None
    limit: object = None
    tolerance: object = 0


class Contract(BaseModel):
    summary: str
    plain: str = ""                  # the summary in everyday words
    entities: list[str] = []
    facts: dict[str, FactSpec] = {}
    checks: list[Check]
    bind: dict[str, str] = {}       # execute with verified values, e.g. iban: vendor.iban
    atomic: bool = False            # re-check and execute inside one transaction (no time-of-check gap)


class ContractStore:
    """Loads every pack listed in the policy; reloads a pack when its file changes."""

    def __init__(self, paths: list[str], root: Path):
        self.root = root
        self.contracts: dict[str, Contract] = {}
        self._mtimes: dict[Path, float] = {}
        self.sync(paths)

    def sync(self, paths: list[str]) -> None:
        files = [(self.root / p).resolve() for p in paths]
        if all(f in self._mtimes and f.stat().st_mtime == self._mtimes[f] for f in files) and len(files) == len(self._mtimes):
            return
        merged: dict[str, Contract] = {}
        try:
            for f in files:
                data = yaml.safe_load(f.read_text()) or {}
                merged.update({tool: Contract.model_validate(spec) for tool, spec in data.items()})
        except Exception as err:  # keep last good contracts
            log.system(f"contract pack change REJECTED, keeping previous: {str(err).splitlines()[0]}", level="warn")
            for f in files:
                self._mtimes[f] = f.stat().st_mtime
            return
        first = not self.contracts
        self.contracts = merged
        self._mtimes = {f: f.stat().st_mtime for f in files}
        log.system(f"contracts {'loaded' if first else 'reloaded'}: {', '.join(sorted(merged))}")

    def get(self, tool: str) -> Contract | None:
        return self.contracts.get(tool)


def _lookup(ctx: dict, path: str) -> object:
    node: object = ctx
    for part in path.split("."):
        if isinstance(node, dict) and part in node:
            node = node[part]
        else:
            return MISSING
        if node is None:
            return MISSING
    return node


def _value(ref: object, ctx: dict, policy_get: Callable[[str], object]) -> object:
    """args.x / fact.x paths, @policy.paths, or literals."""
    if isinstance(ref, str):
        if ref.startswith("@"):
            return policy_get(ref[1:])
        root = ref.split(".", 1)[0]
        if "." in ref and root in ctx:
            return _lookup(ctx, ref)
        if root in ctx and root != "args":
            return _lookup(ctx, ref)
    return ref


def _fmt(template: str, ctx: dict, policy_get: Callable[[str], object]) -> str:
    """{path} placeholders; {a|b} uses the first one that exists."""
    def sub(m: re.Match) -> str:
        v = MISSING
        for ref in m.group(1).split("|"):
            v = _value(ref.strip(), ctx, policy_get)
            if v is not MISSING and v is not None:
                break
        if v is MISSING:
            return "?"
        if isinstance(v, list):
            return ", ".join(str(x) for x in v) or "none"
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            return f"{v:,.2f}"
        return str(v)
    return re.sub(r"\{([^{}]+)\}", sub, template)


def _num(v: object) -> float | None:
    try:
        return float(v)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _passes(c: Check, ctx: dict, pg: Callable[[str], object]) -> bool | None:
    """True = pass, False = fail, None = skipped (an input does not exist)."""
    V = lambda r: _value(r, ctx, pg)  # noqa: E731
    match c.type:
        case "never":
            return False
        case "present":
            return V(c.path) is not MISSING
        case "is_true":
            v = V(c.path)
            return None if v is MISSING else bool(v)
        case "empty":
            v = V(c.path)
            return True if v is MISSING else not v
        case "equals":
            a, b = V(c.left), V(c.right)
            return None if MISSING in (a, b) else str(a) == str(b)
        case "same_iban":
            a, b = V(c.left), V(c.right)
            if MISSING in (a, b):
                return None
            a, b = re.sub(r"\s+", "", str(a)).upper(), re.sub(r"\s+", "", str(b)).upper()
            if "..." in a:   # the agent only ever sees masked accounts: country+check digits ... last 4
                head, _, tail = a.partition("...")
                return len(head) == 4 and len(tail) == 4 and b.startswith(head) and b.endswith(tail)
            return a == b
        case "number_equals":
            a, b, t = _num(V(c.left)), _num(V(c.right)), _num(V(c.tolerance)) or 0.0
            if V(c.right) is MISSING:
                return None
            return a is not None and b is not None and abs(a - b) <= t
        case "at_most":
            v, lim = _num(V(c.value)), _num(V(c.limit))
            return None if v is None or lim is None else v <= lim
    return None


def bound_args(contract: Contract, args: dict, ctx: dict, policy_get: Callable[[str], object]) -> dict:
    """Replace agent-supplied values with the verified ones the checks were run against."""
    out = dict(args)
    for arg, ref in contract.bind.items():
        v = _value(ref, ctx, policy_get)
        if v is not MISSING:
            out[arg] = v
    return out


def _decision(c: Check, policy_get: Callable[[str], object]) -> str:
    d = str(policy_get(c.decision[1:]) if c.decision.startswith("@") else c.decision)
    return d if d in ("ask", "block", "redact") else "block"   # anything unexpected fails closed


def evaluate(tool: str, args: dict, kind: str, contract: Contract | None, fact_fns: dict[str, Callable],
             policy_get: Callable[[str], object], _redacted: bool = False) -> tuple[Effect, list[Finding], dict]:
    """ctx["args"] holds the arguments the checks passed with: after a redaction they differ from the input."""
    if contract is None:
        shown = ", ".join(f"{k}={str(v)[:30]}" for k, v in (args or {}).items())
        return Effect(tool=tool, kind=kind, summary=f"{tool}({shown})"), [], {}
    ctx: dict = {"args": args or {}}
    for name, spec in contract.facts.items():
        fn = fact_fns.get(spec.fn)
        if fn is None:
            raise KeyError(f"contract for {tool} uses unknown fact function {spec.fn}")
        call_args = [None if (v := _value(a, ctx, policy_get)) is MISSING else v for a in spec.args]
        ctx[name] = fn(*call_args)
    findings: list[Finding] = []
    verified: dict[str, bool] = {}
    for c in contract.checks:
        ok = _passes(c, ctx, policy_get)
        if ok is None:
            continue
        verified[c.id] = ok
        if not ok:
            d = _decision(c, policy_get)
            if d == "redact" and c.redact and not _redacted:
                from tollgate.data_guard import mask_text
                masked, n = {}, 0
                for k, v in (args or {}).items():
                    if k in c.redact and isinstance(v, str):
                        v, m = mask_text(v)
                        n += m
                    masked[k] = v
                effect, rest, ctx = evaluate(tool, masked, kind, contract, fact_fns, policy_get, _redacted=True)
                note = Finding(f"{tool}.{c.id}", Decision.ALLOW, f"redacted {n} sensitive value{'s' * (n != 1)} from "
                                                                  f"{', '.join(c.redact)}: {_fmt(c.message, ctx, policy_get)}",
                               plain="Bank or personal details were taken out before it was sent.")
                return effect, [note, *rest], ctx
            findings.append(Finding(f"{tool}.{c.id}", Decision("block" if d == "redact" else d), _fmt(c.message, ctx, policy_get),
                                    plain=_fmt(c.plain, ctx, policy_get) if c.plain else ""))
    entities = [str(v) for e in contract.entities if (v := _value(e, ctx, policy_get)) not in (MISSING, None)]
    effect = Effect(tool=tool, kind=kind, summary=_fmt(contract.summary, ctx, policy_get),
                    plain=_fmt(contract.plain, ctx, policy_get) if contract.plain else "", facts={"checks": verified},
                    amount_eur=_num(args.get("amount_eur")) if args else None, entities=entities)
    return effect, findings, ctx

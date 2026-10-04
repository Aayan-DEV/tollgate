"""Core value types shared by every stage of the pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class Decision(StrEnum):
    ALLOW = "allow"
    ASK = "ask"
    BLOCK = "block"

    @staticmethod
    def strictest(decisions: list["Decision"]) -> "Decision":
        order = {Decision.ALLOW: 0, Decision.ASK: 1, Decision.BLOCK: 2}
        return max(decisions, key=order.__getitem__, default=Decision.ALLOW)


@dataclass
class Finding:
    control: str          # e.g. "payments.iban_on_file"
    decision: Decision
    message: str
    deterministic: bool = True
    plain: str = ""       # the same finding for someone outside finance and IT (shown first in the dashboard)
    fixable: bool = False # a wrong value the agent passed, which it can look up and correct (never a policy limit)


@dataclass
class Effect:
    """What a tool call would actually do, resolved from systems of record."""

    tool: str
    kind: str                       # read | irreversible | deny | unknown
    summary: str                    # one line a human can approve or reject
    facts: dict = field(default_factory=dict)
    amount_eur: float | None = None
    entities: list[str] = field(default_factory=list)   # names/ids used to find relevant user turns
    plain: str = ""                 # the same line in everyday words ("Pay 640.50 EUR to Vistula for invoice INV-7002")


@dataclass
class Verdict:
    decision: Decision
    effect: Effect
    findings: list[Finding]
    judge: dict | None = None
    latency_ms: float = 0.0

    @property
    def reason(self) -> str:
        decisive = [f for f in self.findings if f.decision == self.decision] or self.findings
        return "; ".join(f.message for f in decisive) or "within policy"

    @property
    def plain_reason(self) -> str:
        """Why, in everyday words: the plain sentence of each deciding finding (technical message if none)."""
        decisive = [f for f in self.findings if f.decision == self.decision] or self.findings
        seen: list[str] = []
        for f in decisive:
            text = f.plain or f.message
            if text not in seen:
                seen.append(text)
        return " ".join(t if t.endswith(".") else t + "." for t in seen) if seen else ""

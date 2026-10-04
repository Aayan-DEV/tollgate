"""Per-session ledger: the judge's memory, built so it cannot forget rules.

  user_turns  verbatim, never summarized (a "don't pay X" said 200 turns ago survives)
  facts       exact counters kept by the gateway, no AI involved
  values      every IBAN / email / id seen, with where it first appeared
  calls       identical-call counter for loop detection
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field

PATTERNS = {
    "iban": re.compile(r"\b[A-Z]{2}\d{2}(?:\s?[A-Z0-9]{4}){3,7}(?:\s?[A-Z0-9]{1,3})?\b"),
    "email": re.compile(r"\b[\w.+-]+@[\w-]+(?:\.[\w-]+)+\b"),
    "invoice": re.compile(r"\bINV-\d+(?:-R)?\b"),
    "vendor": re.compile(r"\bV-\d{3}\b"),
}
CONSTRAINT_WORDS = re.compile(
    r"\b(not|don'?t|do not|never|only|except|stop|until|hold|without|dispute|disputing|no more than|max(imum)?|limit)\b", re.I
)


@dataclass
class UserTurn:
    index: int
    text: str

    @property
    def label(self) -> str:
        return f"U{self.index}"


@dataclass
class Ledger:
    session_id: str
    user_turns: list[UserTurn] = field(default_factory=list)
    facts: dict = field(default_factory=lambda: {
        "steps": 0, "tool_calls": 0, "payments": 0, "paid_total_eur": 0.0, "emails_sent": 0,
        "blocked": 0, "held": 0, "tokens": 0, "usd": 0.0,
    })
    values: dict[str, str] = field(default_factory=dict)
    calls: dict[str, int] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    revealed: set[str] = field(default_factory=set)   # full IBANs shown to the agent (data budget)

    def add_user(self, text: str) -> UserTurn:
        turn = UserTurn(len(self.user_turns) + 1, text)
        self.user_turns.append(turn)
        self.index(text, f"user {turn.label}")
        return turn

    def index(self, text: str, origin: str) -> None:
        for kind, rx in PATTERNS.items():
            for m in rx.finditer(text):
                key = re.sub(r"\s+", "", m.group(0)).upper() if kind == "iban" else m.group(0).lower()
                self.values.setdefault(key, origin)

    def origin_of(self, value: str) -> str:
        key = re.sub(r"\s+", "", value).upper()
        return self.values.get(key) or self.values.get(value.lower()) or "never seen in this session"

    def count_call(self, tool: str, args: dict) -> int:
        h = hashlib.sha1(f"{tool}:{json.dumps(args, sort_keys=True, default=str)}".encode()).hexdigest()
        self.calls[h] = self.calls.get(h, 0) + 1
        return self.calls[h]

    def relevant_user_turns(self, entities: list[str], limit: int = 12) -> list[UserTurn]:
        """First turn, last three, any turn that sets a constraint or names an entity of this effect."""
        if len(self.user_turns) <= limit:
            return list(self.user_turns)
        ents = [e.lower() for e in entities if e]
        keep = {1, *range(max(1, len(self.user_turns) - 2), len(self.user_turns) + 1)}
        for t in self.user_turns:
            low = t.text.lower()
            if CONSTRAINT_WORDS.search(t.text) or any(e in low for e in ents):
                keep.add(t.index)
        chosen = [t for t in self.user_turns if t.index in keep]
        return chosen[:2] + chosen[-(limit - 2):] if len(chosen) > limit else chosen

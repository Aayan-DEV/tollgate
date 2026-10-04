"""Secrets detector: credentials must never move through an agent.

Deterministic patterns for the credential formats that leak most (cloud keys,
API tokens, private keys, JWTs, connection strings with passwords, and
"password = ..." style assignments). Used on three paths:

  tool arguments   a secret about to leave (email body, upload): allow | redact | ask | block
  tool results     a secret the agent is about to read: allow | redact | block
  external models  text sent to a commercial API: always tokenized by the privacy vault

Each pattern is anchored on a vendor prefix or a structural marker, so ordinary
invoice text (amounts, IBANs, ids) does not match.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

PATTERNS: dict[str, re.Pattern] = {
    "private_key": re.compile(r"-----BEGIN (?:RSA |EC |DSA |OPENSSH |PGP |ENCRYPTED )?PRIVATE KEY(?: BLOCK)?-----"
                              r"[\s\S]*?(?:-----END [A-Z ]*PRIVATE KEY(?: BLOCK)?-----|$)"),
    "aws_access_key": re.compile(r"\b(?:AKIA|ASIA|ABIA|ACCA)[0-9A-Z]{16}\b"),
    "aws_secret_key": re.compile(r"(?i)aws.{0,20}?(?:secret|private).{0,20}?['\"=:\s]+([A-Za-z0-9/+]{40})\b"),
    "google_api_key": re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b"),
    "github_token": re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{60,})\b"),
    "slack_token": re.compile(r"\bxox[abposr]-[A-Za-z0-9-]{10,}\b"),
    "stripe_key": re.compile(r"\b(?:sk|rk)_(?:live|test)_[A-Za-z0-9]{20,}\b"),
    "anthropic_key": re.compile(r"\bsk-ant-[A-Za-z0-9_\-]{20,}\b"),
    "openai_key": re.compile(r"\bsk-(?:proj-|svcacct-)?[A-Za-z0-9_\-]{32,}\b"),
    "jwt": re.compile(r"\beyJ[A-Za-z0-9_\-]{8,}\.eyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\b"),
    "connection_string": re.compile(r"\b(?:postgres(?:ql)?|mysql|mongodb(?:\+srv)?|redis|amqp|mssql|sqlserver)://[^\s:/@]+:[^\s@/]+@[^\s]+"),
    "password_assignment": re.compile(r"(?i)\b(?:password|passwd|pwd|api[_ -]?key|secret[_ -]?key|access[_ -]?token|client[_ -]?secret)"
                                      r"\s*[:=]\s*['\"]?([^\s'\",;]{8,})"),
}


@dataclass(frozen=True)
class Hit:
    kind: str
    start: int
    end: int


def find(text: str) -> list[Hit]:
    """Every secret in the text, longest first when two overlap."""
    hits: list[Hit] = []
    for kind, rx in PATTERNS.items():
        for m in rx.finditer(text):
            s, e = (m.start(1), m.end(1)) if m.groups() and m.group(1) else (m.start(), m.end())
            hits.append(Hit(kind, s, e))
    hits.sort(key=lambda h: (h.start, -(h.end - h.start)))
    out: list[Hit] = []
    for h in hits:
        if not out or h.start >= out[-1].end:
            out.append(h)
    return out


def redact(text: str) -> tuple[str, list[str]]:
    """Replace each secret with [SECRET:<kind>]. Returns (text, kinds found)."""
    hits = find(text)
    for h in reversed(hits):
        text = text[:h.start] + f"[SECRET:{h.kind}]" + text[h.end:]
    return text, [h.kind for h in hits]


def walk(obj: object, fn) -> object:
    """Apply fn to every string inside a JSON-like value."""
    if isinstance(obj, str):
        return fn(obj)
    if isinstance(obj, list):
        return [walk(x, fn) for x in obj]
    if isinstance(obj, dict):
        return {k: walk(v, fn) for k, v in obj.items()}
    return obj


def redact_obj(obj: object) -> tuple[object, list[str]]:
    kinds: list[str] = []

    def one(s: str) -> str:
        out, k = redact(s)
        kinds.extend(k)
        return out
    return walk(obj, one), kinds

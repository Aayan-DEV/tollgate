"""Privacy vault: commercial models see tokens, never real account numbers, emails or credentials.

Text going to an external provider has IBANs, emails, national IDs and secrets
(API keys, private keys, tokens) swapped for stable tokens ([IBAN_1], [EMAIL_2], [SECRET_1], ...). Tool calls coming back are
detokenized before the gateway checks them, so the agent still works.
"""

from __future__ import annotations

import re

from tollgate import secrets
from tollgate.connectors import IBAN_RE, NATIONAL_ID_RE

EMAIL_RE = re.compile(r"\b[\w.+-]+@[\w-]+(?:\.[\w-]+)+\b")
TOKEN_RE = re.compile(r"\[(IBAN|EMAIL|ID|SECRET)_\d+\]")


class Vault:
    def __init__(self) -> None:
        self.to_token: dict[str, str] = {}
        self.to_value: dict[str, str] = {}

    def _token(self, kind: str, value: str) -> str:
        if value not in self.to_token:
            tok = f"[{kind}_{sum(1 for t in self.to_value if t.startswith('[' + kind)) + 1}]"
            self.to_token[value], self.to_value[tok] = tok, value
        return self.to_token[value]

    def tokenize(self, obj: object) -> object:
        if isinstance(obj, str):
            for h in reversed(secrets.find(obj)):   # first: a key can contain IBAN- or email-like fragments
                obj = obj[:h.start] + self._token("SECRET", obj[h.start:h.end]) + obj[h.end:]
            s = IBAN_RE.sub(lambda m: self._token("IBAN", re.sub(r"\s+", "", m.group(0))), obj)
            s = NATIONAL_ID_RE.sub(lambda m: self._token("ID", m.group(0)), s)
            return EMAIL_RE.sub(lambda m: self._token("EMAIL", m.group(0)), s)
        if isinstance(obj, list):
            return [self.tokenize(x) for x in obj]
        if isinstance(obj, dict):
            return {k: self.tokenize(v) for k, v in obj.items()}
        return obj

    def detokenize(self, obj: object) -> object:
        if isinstance(obj, str):
            return TOKEN_RE.sub(lambda m: self.to_value.get(m.group(0), m.group(0)), obj)
        if isinstance(obj, list):
            return [self.detokenize(x) for x in obj]
        if isinstance(obj, dict):
            return {k: self.detokenize(v) for k, v in obj.items()}
        return obj

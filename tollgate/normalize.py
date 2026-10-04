"""Canonical form before any rule runs: the same value must always look the same.

Known-bad slips through when it is written differently: zero-width characters,
Unicode look-alikes, odd spacing or dashes in an IBAN, or data hidden in
base64. Arguments are canonicalized before checks; text scanners also look at
decoded variants.
"""

from __future__ import annotations

import base64
import binascii
import re
import unicodedata

INVISIBLE = re.compile("[​‌‍⁠﻿­]")
IBAN_LIKE = re.compile(r"\b[A-Z]{2}\d{2}(?:[\s\-./]?[A-Z0-9]{2,4}){3,8}\b")
B64_TOKEN = re.compile(r"[A-Za-z0-9+/=_-]{24,}")


def canonical(text: str) -> str:
    text = INVISIBLE.sub("", unicodedata.normalize("NFKC", text))
    return re.sub(r"[ \t]+", " ", text).strip()


def canonical_args(args: dict) -> dict:
    out: dict = {}
    for k, v in (args or {}).items():
        if isinstance(v, str):
            v = canonical(v)
            if "iban" in k.lower():
                v = "...".join(re.sub(r"[\s\-./]", "", part) for part in v.split("...")).upper()   # keep mask marker
        out[k] = v
    return out


def decoded_variants(text: str) -> list[str]:
    """The text plus anything hidden in base64 blobs, for scanners only."""
    found = [text]
    for token in B64_TOKEN.findall(text):
        padded = token + "=" * (-len(token) % 4)
        for decoder in (base64.b64decode, base64.urlsafe_b64decode):
            try:
                raw = decoder(padded).decode("utf-8")
            except (binascii.Error, UnicodeDecodeError, ValueError):
                continue
            if raw.isprintable() or "\n" in raw:
                found.append(raw)
                break
    return found


def compact_ibans(text: str) -> str:
    """'PL61 1090-1014 ...' -> 'PL611090...' so one regex catches every spelling."""
    return IBAN_LIKE.sub(lambda m: re.sub(r"[\s\-./]", "", m.group(0)), text)


def scan_text(*parts: object) -> str:
    """Everything a scanner should look at: canonical, decoded, IBANs compacted."""
    joined = canonical("\n".join(str(p) for p in parts if p is not None))
    return "\n".join(compact_ibans(t) for t in decoded_variants(joined))

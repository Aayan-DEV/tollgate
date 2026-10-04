"""Agent identity: who is calling, for whom, and with which rights.

Every agent gets a short-lived token signed by the gateway (HMAC-SHA256):

  agent        the agent's own id                     "ap-agent"
  acting_for   the person whose rights it carries     "piotr"
  tools        the tools it may call at all           ["read_invoice", "pay_invoice", ...]
  max_eur      the most it may move in one action     10000
  chain        who delegated to whom                  ["ap-agent", "payments-subagent"]
  exp          expiry (unix seconds)

Delegation can only NARROW: a sub-agent gets a subset of the tools, a lower or
equal amount, the same person, and one more link in the chain (capped by
policy). An agent cannot claim another agent's id or another person's rights:
the signature would not match, and the gate compares the token's person with
the session's. This is how agent-to-agent calls and impersonation are checked.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
from dataclasses import dataclass

DEV_KEY = "tollgate-dev-identity-key"


def _key() -> bytes:
    return os.environ.get("TOLLGATE_IDENTITY_KEY", DEV_KEY).encode()


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


class IdentityError(Exception):
    pass


@dataclass(frozen=True)
class Claims:
    agent: str
    acting_for: str
    tools: tuple[str, ...]
    max_eur: float
    chain: tuple[str, ...]
    exp: float

    @property
    def depth(self) -> int:
        return len(self.chain) - 1


def issue(agent: str, acting_for: str, tools: list[str], max_eur: float, ttl_s: int = 3600,
          chain: tuple[str, ...] | None = None) -> str:
    body = {"agent": agent, "acting_for": acting_for, "tools": sorted(set(tools)), "max_eur": float(max_eur),
            "chain": list(chain or (agent,)), "exp": time.time() + ttl_s}
    payload = _b64(json.dumps(body, sort_keys=True).encode())
    sig = _b64(hmac.new(_key(), payload.encode(), hashlib.sha256).digest())
    return f"{payload}.{sig}"


def verify(token: str) -> Claims:
    try:
        payload, sig = token.split(".")
    except (AttributeError, ValueError):
        raise IdentityError("no valid agent token was presented") from None
    expected = _b64(hmac.new(_key(), payload.encode(), hashlib.sha256).digest())
    if not hmac.compare_digest(expected, sig):
        raise IdentityError("agent token signature is invalid (forged or altered)")
    body = json.loads(_unb64(payload))
    if body["exp"] < time.time():
        raise IdentityError("agent token has expired")
    return Claims(body["agent"], body["acting_for"], tuple(body["tools"]), float(body["max_eur"]),
                  tuple(body["chain"]), float(body["exp"]))


def delegate(parent_token: str, child_agent: str, tools: list[str], max_eur: float, max_depth: int, ttl_s: int = 900) -> str:
    """A narrower token for a sub-agent. Raises if the request would widen anything."""
    parent = verify(parent_token)
    wider = sorted(set(tools) - set(parent.tools))
    if wider:
        raise IdentityError(f"delegation would add tools the parent does not have: {', '.join(wider)}")
    if max_eur > parent.max_eur:
        raise IdentityError(f"delegation would raise the amount limit from {parent.max_eur:,.0f} to {max_eur:,.0f}")
    if parent.depth + 1 > max_depth:
        raise IdentityError(f"delegation chain would be {parent.depth + 1} deep; the limit is {max_depth}")
    ttl = min(ttl_s, int(parent.exp - time.time()))
    return issue(child_agent, parent.acting_for, tools, max_eur, ttl, chain=parent.chain + (child_agent,))

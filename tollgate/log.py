"""Compact terminal logging: one line per decision or model call.

TOLLGATE_LOG=quiet|info|debug (default info). debug adds judge packets.
Colours turn on for a TTY or when FORCE_COLOR=1.
"""

from __future__ import annotations

import os
import sys
import time
from contextvars import ContextVar

LEVEL = os.environ.get("TOLLGATE_LOG", "info")
_COLOR = sys.stdout.isatty() or os.environ.get("FORCE_COLOR") == "1"
session_label: ContextVar[str] = ContextVar("session_label", default="-")

_C = {
    "allow": "\033[32m", "ask": "\033[33m", "block": "\033[31m", "warn": "\033[35m",
    "dim": "\033[2m", "bold": "\033[1m", "cyan": "\033[36m", "reset": "\033[0m",
}


def _c(name: str, text: str) -> str:
    return f"{_C[name]}{text}{_C['reset']}" if _COLOR else text


def _emit(kind: str, body: str) -> None:
    if LEVEL == "quiet":
        return
    stamp = time.strftime("%H:%M:%S")
    print(f"{_c('dim', stamp)} {session_label.get():<14} {kind:<6} {body}", flush=True)


def system(msg: str, level: str = "info") -> None:
    _emit("gate", _c("warn", msg) if level == "warn" else msg)


def model(model: str, tokens_in: int, tokens_out: int, ms: float, usd: float, note: str = "") -> None:
    _emit("model", f"{model:<17} in={tokens_in:<6} out={tokens_out:<5} {ms / 1000:5.1f}s ${usd:.5f}{' ' + note if note else ''}")


def decision(tool: str, summary: str, decision: str, reason: str, timing: str, judge: str = "") -> None:
    tag = _c(decision, f"{decision.upper():<5}")
    if decision == "allow" and not judge and reason == "within policy":
        _emit("tool", f"{tag} {summary[:110]} {_c('dim', f'({timing})')}")
        return
    line = f"{tag} {summary[:110]}\n{'':>30}{reason[:220]} {_c('dim', f'({timing})')}"
    if judge:
        line += f"\n{'':>30}{_c('cyan', 'judge')} {judge[:200]}"
    _emit("tool", line)


def passthrough(tool: str, args: str) -> None:
    _emit("tool", f"{_c('dim', 'OFF  ')} {tool}({args}) {_c('dim', 'executed with no checks')}")


def warn(msg: str) -> None:
    _emit("warn", _c("warn", msg))


def debug(msg: str) -> None:
    if LEVEL == "debug":
        _emit("debug", _c("dim", msg))

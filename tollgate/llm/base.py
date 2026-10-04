"""Provider-neutral chat interface used by the demo agent and by the judge."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol


@dataclass
class ToolCall:
    name: str
    args: dict


@dataclass
class Turn:
    text: str
    calls: list[ToolCall] = field(default_factory=list)
    tokens_in: int = 0
    tokens_out: int = 0
    ms: float = 0.0


class Chat(Protocol):
    model: str
    provider: str

    def add_user(self, text: str) -> None: ...
    def add_assistant(self, text: str) -> None: ...
    def add_tool_result(self, call: ToolCall, result: object) -> None: ...
    async def step(self) -> Turn: ...


def provider_of(model: str) -> str:
    return "gemini" if model.startswith("gemini") else "ollama"


def load_env(path: str | Path = ".env") -> dict[str, str]:
    env: dict[str, str] = {}
    p = Path(path)
    if not p.exists():
        return env
    for line in p.read_text().splitlines():
        if "=" not in line or line.lstrip().startswith("#"):
            continue
        key, value = line.split("=", 1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        env[key.strip()] = value
    return env

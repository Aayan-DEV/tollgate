"""Local models through Ollama's HTTP API (no SDK needed)."""

from __future__ import annotations

import json
import os
import time

import httpx

from tollgate.llm.base import ToolCall, Turn

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434")
_client = httpx.AsyncClient(base_url=OLLAMA_URL, timeout=httpx.Timeout(600.0, connect=5.0))
# One context size for every call: Ollama reloads a model whenever num_ctx changes, so an agent and a checker that share
# a model but ask for different sizes would reload it on every turn.
NUM_CTX = 16384
KEEP_ALIVE = "30m"   # stay loaded between turns and test runs; loading costs seconds every time


def _options(model: str, **extra) -> dict:
    body: dict = {"options": {"num_ctx": NUM_CTX, **extra}, "keep_alive": KEEP_ALIVE}
    if "qwen3" in model.lower():
        body["think"] = False  # speed; reasoning models are slow on a laptop (also for qwen3 fine-tunes under other names)
    return body


class OllamaChat:
    provider = "ollama"

    def __init__(self, model: str, system: str, tools: list[dict]):
        self.model = model
        self.messages: list[dict] = [{"role": "system", "content": system}]
        self.tools = [{"type": "function", "function": t} for t in tools]

    def add_user(self, text: str) -> None:
        self.messages.append({"role": "user", "content": text})

    def add_assistant(self, text: str) -> None:
        self.messages.append({"role": "assistant", "content": text})

    def add_tool_result(self, call: ToolCall, result: object) -> None:
        self.messages.append({"role": "tool", "tool_name": call.name, "content": json.dumps(result, default=str)})

    async def step(self) -> Turn:
        t0 = time.perf_counter()
        res = await _client.post("/api/chat", json={"model": self.model, "messages": self.messages, "tools": self.tools,
                                                    "stream": False, **_options(self.model)})
        res.raise_for_status()
        data = res.json()
        msg = data["message"]
        self.messages.append(msg)
        calls = []
        for c in msg.get("tool_calls") or []:
            args = c["function"].get("arguments") or {}
            calls.append(ToolCall(c["function"]["name"], json.loads(args) if isinstance(args, str) else args))
        return Turn(msg.get("content") or "", calls, data.get("prompt_eval_count", 0), data.get("eval_count", 0),
                    (time.perf_counter() - t0) * 1000)


async def complete_json(model: str, system: str, prompt: str, schema: dict, timeout: float) -> tuple[dict, Turn]:
    t0 = time.perf_counter()
    res = await _client.post("/api/chat", timeout=timeout, json={
        "model": model, "stream": False, "format": schema,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
        **_options(model, temperature=0),
    })
    res.raise_for_status()
    data = res.json()
    turn = Turn(data["message"]["content"], [], data.get("prompt_eval_count", 0), data.get("eval_count", 0),
                (time.perf_counter() - t0) * 1000)
    return json.loads(turn.text), turn

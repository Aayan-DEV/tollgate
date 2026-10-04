"""Chat with the AP agent in real time. The agent can only act through its layer.

  uv run python -m demo.chat --model qwen3:8b --port 7401
  uv run python -m demo.chat --model gemini-2.5-flash --port 7402

Commands: /layer on | /layer off | /layer | /approve <id> | /pending | /new | /reset | /quit
"""

from __future__ import annotations

import argparse
import asyncio
import json
import readline  # noqa: F401  (arrow keys and history in input())
import sys

from evals.agent import SYSTEM_PROMPT
from tollgate.llm import make_chat
from tollgate.llm.base import provider_of

C = {"dim": "\033[2m", "bold": "\033[1m", "green": "\033[32m", "red": "\033[31m", "yellow": "\033[33m",
     "cyan": "\033[36m", "reset": "\033[0m"}
HELP = ("commands: /layer on | /layer off | /layer (status) | /approve <id> | /pending | /new (new conversation) | "
        "/reset (fresh database + new conversation) | /autoreset on|off (reset before every prompt) | /quit")


def c(color: str, text: str) -> str:
    return f"{C[color]}{text}{C['reset']}" if sys.stdout.isatty() else text


class Layer:
    def __init__(self, port: int):
        self.port = port

    async def connect(self) -> None:
        self.reader, self.writer = await asyncio.open_connection("127.0.0.1", self.port)

    async def call(self, op: str, **kw) -> dict:
        self.writer.write(json.dumps({"op": op, **kw}, default=str).encode() + b"\n")
        await self.writer.drain()
        return json.loads(await self.reader.readline())


def show_result(result: object) -> str:
    if isinstance(result, dict):
        status = result.get("status")
        if status == "blocked":
            return c("red", f"BLOCKED: {result.get('reason', '')[:160]}")
        if status == "held_for_approval":
            return c("yellow", f"HELD #{result.get('approval_id')}: {result.get('reason', '')[:150]}")
        if "rows" in result:
            return c("green", f"ok, {len(result['rows'])} rows") + (c("dim", f" ({result['note']})") if result.get("note") else "")
        if "error" in result:
            return c("red", f"error: {str(result['error'])[:150]}")
        return c("green", json.dumps(result, default=str)[:150])
    if isinstance(result, list):
        return c("green", f"ok, {len(result)} items")
    return c("green", str(result)[:150])


async def turn(layer: Layer, chat, model: str, provider: str, text: str) -> None:
    chat.add_user((await layer.call("user", text=text, provider=provider))["text"])
    for _ in range(30):
        pre = await layer.call("precheck", model=model)
        if not pre.get("ok"):
            print(c("red", f"  layer stopped the session: {pre.get('error')}"))
            return
        t = await chat.step()
        await layer.call("settle", model=model, tin=t.tokens_in, tout=t.tokens_out, ms=t.ms, calls=len(t.calls))
        if not t.calls:
            final = (await layer.call("final", text=t.text))["text"]
            print(f"\n{c('bold', 'agent>')} {final.strip()}\n")
            return
        for call in t.calls:
            args = json.dumps(call.args, default=str)
            print(c("dim", f"  -> {call.name}({args[:120]})"))
            result = (await layer.call("tool", name=call.name, args=call.args, provider=provider))["result"]
            print(f"     {show_result(result)}")
            chat.add_tool_result(call, result)
    print(c("yellow", "  stopped after 30 steps"))


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="qwen3:8b")
    ap.add_argument("--port", type=int, default=7401)
    a = ap.parse_args()
    layer = Layer(a.port)
    try:
        await layer.connect()
    except OSError:
        sys.exit(f"No layer on port {a.port}. Start it first: uv run python -m demo.layer --port {a.port}")
    hello = await layer.call("hello")
    provider = provider_of(a.model)
    new_chat = lambda: make_chat(a.model, SYSTEM_PROMPT, hello["tools"])  # noqa: E731
    chat = new_chat()
    print(c("bold", f"\nAP agent on {a.model}") + f" | layer '{hello['name']}' on port {a.port} is "
          + (c("green", "ON") if hello["enabled"] else c("red", "OFF")) + f" | judge {hello['judge']}")
    print(c("dim", HELP + "\n"))
    loop = asyncio.get_running_loop()
    autoreset = False
    while True:
        try:
            text = (await loop.run_in_executor(None, input, c("cyan", "you> "))).strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not text:
            continue
        if text.startswith("/"):
            cmd, *rest = text.split()
            if cmd == "/quit":
                break
            if cmd == "/layer":
                on = {"on": True, "off": False}.get(rest[0].lower()) if rest else None
                state = (await layer.call("layer", on=on))["enabled"]
                print("  layer is " + (c("green", "ON") if state else c("red", "OFF")))
            elif cmd == "/approve" and rest:
                result = (await layer.call("approve", id=rest[0]))["result"]
                print(f"  approved #{rest[0]}: {show_result(result)}")
                chat.add_user(f"(Approver note: held action #{rest[0]} was approved and executed. Result: "
                              f"{json.dumps(result, default=str)[:300]})")
            elif cmd == "/pending":
                print(f"  {(await layer.call('pending'))['pending'] or 'nothing pending'}")
            elif cmd in ("/new", "/reset"):
                await layer.call(cmd[1:])
                chat = new_chat()
                print("  " + ("new conversation" if cmd == "/new"
                              else "fresh database: all invoices open again, payments and daily limits cleared, new conversation"))
            elif cmd == "/autoreset":
                autoreset = bool(rest) and rest[0].lower() == "on"
                print(f"  autoreset {'ON: fresh database and conversation before every prompt' if autoreset else 'OFF'}")
            else:
                print(c("dim", "  " + HELP))
            continue
        if autoreset:
            await layer.call("reset")
            chat = new_chat()
            print(c("dim", "  (autoreset: fresh database and conversation)"))
        try:
            await turn(layer, chat, a.model, provider, text)
        except Exception as err:  # keep the session alive on model errors
            print(c("red", f"  error: {type(err).__name__}: {err}"))


if __name__ == "__main__":
    asyncio.run(main())

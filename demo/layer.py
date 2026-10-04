"""The layer as its own process: the only thing between one agent and the ERP.

  uv run python -m demo.layer --name local  --port 7401 --judge qwen3:8b
  uv run python -m demo.layer --name gemini --port 7402 --judge gemini-2.5-flash

It owns the ERP connection (the agent process has none) and prints every
decision. Type into this terminal: on | off | status | pending | reset
Protocol: one JSON object per line over 127.0.0.1 (no external dependencies).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from demo.world import new_store
from erp.seed import TODAY
from erp.store import MODEL_DIR
from tollgate import log
from tollgate.budget import BudgetExceeded
from tollgate.gate import Gate
from tollgate.state import StateStore

ROOT = Path(__file__).resolve().parents[1]


class LayerServer:
    def __init__(self, name: str, judge: str, enabled: bool):
        self.name, self.judge, self.enabled = name, judge, enabled
        self.sessions = 0
        self.reset_world()

    def reset_world(self) -> None:
        self.store = new_store()
        self.state = StateStore(":memory:")
        self.new_session()
        log.system(f"world reset: {len(self.store.t_list_open_invoices())} open invoices, full synthetic database")

    def new_session(self) -> None:
        self.sessions += 1
        self.gate = Gate(root=ROOT, connector=self.store, model_dir=MODEL_DIR, session_id=f"{self.name}-{self.sessions}",
                         user_entities=["PL01"], enabled=self.enabled, state=self.state, today=TODAY,
                         policy_overrides={"justify.judge_model": self.judge, "state.path": ":memory:"},
                         audit_path=ROOT / "logs" / f"audit__demo_{self.name}.jsonl")
        self.pending: dict[int, tuple[str, dict, str]] = {}

    def set_layer(self, on: bool | None) -> bool:
        if on is not None:
            self.enabled = self.gate.enabled = on
            log.system(f"LAYER {'ON: every action is checked' if on else 'OFF: actions run with no checks'}",
                       level="info" if on else "warn")
        return self.enabled

    async def dispatch(self, req: dict) -> dict:
        op, g = req.get("op"), self.gate
        if op == "hello":
            return {"tools": g.tools, "enabled": self.enabled, "name": self.name, "judge": self.judge}
        if op == "user":
            return {"text": g.user_message(req["text"], req["provider"])}
        if op == "precheck":
            try:
                g.model_precheck(req["model"])
                return {"ok": True}
            except BudgetExceeded as err:
                log.decision("model", req["model"], "block", str(err), "0.0 ms")
                return {"ok": False, "error": str(err)}
        if op == "settle":
            return {"usd": g.model_settle(req["model"], req["tin"], req["tout"], req["ms"], req["calls"])}
        if op == "tool":
            result = await g.call_tool(req["name"], req["args"], req["provider"])
            if isinstance(result, dict) and result.get("status") == "held_for_approval":
                aid = len(self.pending) + 1
                self.pending[aid] = (req["name"], req["args"], req["provider"])
                result = {**result, "approval_id": aid}
                log.system(f"approval #{aid} waiting: approve it from the chat with /approve {aid}")
            return {"result": result}
        if op == "final":
            return {"text": g.final_answer(req["text"])}
        if op == "layer":
            return {"enabled": self.set_layer(req.get("on"))}
        if op == "pending":
            return {"pending": {k: f"{n} {json.dumps(a)[:100]}" for k, (n, a, _) in self.pending.items()}}
        if op == "approve":
            item = self.pending.pop(int(req["id"]), None)
            if not item:
                return {"result": {"error": f"no pending approval #{req['id']}"}}
            name, args, provider = item
            log.system(f"human approved #{req['id']}: re-running {name} (hard blocks still apply)")
            g.approver = lambda _v: True
            try:
                return {"result": await g.call_tool(name, args, provider)}
            finally:
                g.approver = None
        if op == "new":
            self.new_session()
            log.system("new conversation (world, payments and daily limits kept)")
            return {"ok": True}
        if op == "reset":
            self.reset_world()
            return {"ok": True}
        return {"error": f"unknown op {op}"}

    async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        log.system("agent connected")
        try:
            while line := await reader.readline():
                try:
                    resp = await self.dispatch(json.loads(line))
                except Exception as err:  # never crash the layer on one bad request
                    log.system(f"request failed: {type(err).__name__}: {err}", level="warn")
                    resp = {"error": f"{type(err).__name__}: {err}"}
                writer.write(json.dumps(resp, default=str).encode() + b"\n")
                await writer.drain()
        finally:
            log.system("agent disconnected")
            writer.close()

    async def console(self) -> None:
        loop = asyncio.get_running_loop()
        while True:
            cmd = (await loop.run_in_executor(None, sys.stdin.readline)).strip().lower()
            if cmd in ("on", "off"):
                self.set_layer(cmd == "on")
            elif cmd == "status":
                f = self.gate.ledger.facts
                log.system(f"layer {'ON' if self.enabled else 'OFF'} | judge {self.judge} | payments {f['payments']} "
                           f"({f['paid_total_eur']:.2f} EUR) | blocked {f['blocked']} | held {f['held']} | ${f['usd']:.4f}")
            elif cmd == "pending":
                log.system(f"pending approvals: {list(self.pending) or 'none'}")
            elif cmd == "reset":
                self.reset_world()
            elif cmd:
                log.system("commands: on | off | status | pending | reset")


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", default="local")
    ap.add_argument("--port", type=int, default=7401)
    ap.add_argument("--judge", default="qwen3:8b")
    ap.add_argument("--off", action="store_true", help="start with the layer off")
    a = ap.parse_args()
    log.session_label.set(f"layer:{a.name}")
    server = LayerServer(a.name, a.judge, enabled=not a.off)
    try:
        srv = await asyncio.start_server(server.handle, "127.0.0.1", a.port)
    except OSError:
        sys.exit(f"\nPort {a.port} is already in use: a layer is probably already running there.\n"
                 f"Use that terminal, or stop it (Ctrl-C) first. Find it with:  lsof -i :{a.port}")
    log.system(f"listening on 127.0.0.1:{a.port} | layer {'ON' if server.enabled else 'OFF'} | judge {a.judge} | "
               f"type on / off / status / pending / reset")
    console = asyncio.get_running_loop().create_task(server.console())  # noqa: F841 (keep a reference)
    async with srv:
        await srv.serve_forever()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass

"""The gate as an MCP server, and as a proxy in front of other MCP servers.

  agent --MCP--> Tollgate --+--> the ERP (in-process connector)
                            +--MCP--> upstream servers (e.g. a vendor portal)

Any MCP client (Claude Desktop, an agent framework) connects here instead of to
the systems directly. Every tools/call runs through Gate.call_tool with the
token the agent presented, so identity, contracts, limits, data guard and the
judge all apply exactly as in-process.

UPSTREAM TOOLS ARE PINNED. The first time a tool is seen its description and
schema are scanned (attack feed: hidden instructions, exfiltration) and, if
clean, their hash is written to the pin file. If a later version of the server
changes a description (a "rug pull") or a description carries an instruction,
the tool is QUARANTINED: it is not listed and cannot be called until a person
approves it:  uv run python -m tollgate.mcp_gateway approve <server> <tool>

Run:  TOLLGATE_AGENT_TOKEN=$(uv run python -m tollgate.mcp_gateway token piotr) uv run python -m tollgate.mcp_gateway
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import os
import sys
from contextlib import AsyncExitStack
from pathlib import Path

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server
import mcp.types as t

from tollgate import log
from tollgate.normalize import scan_text
from tollgate.policy import PolicyStore

ROOT = Path(__file__).resolve().parents[1]
SEP = "__"


def tool_hash(tool: t.Tool) -> str:
    body = json.dumps({"description": tool.description or "", "schema": tool.input_schema}, sort_keys=True)
    return hashlib.sha256(body.encode()).hexdigest()[:16]


class Pins:
    def __init__(self, path: Path):
        self.path = path
        self.data: dict[str, dict[str, str]] = json.loads(path.read_text()) if path.exists() else {}

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.data, indent=1, sort_keys=True))

    def get(self, server: str, tool: str) -> str | None:
        return self.data.get(server, {}).get(tool)

    def set(self, server: str, tool: str, h: str) -> None:
        self.data.setdefault(server, {})[tool] = h
        self.save()


class Upstreams:
    """Clients to upstream MCP servers, with every tool scanned and pinned before it is exposed."""

    def __init__(self, policy, feed, stack: AsyncExitStack):
        self.policy, self.feed, self.stack = policy, feed, stack
        self.pins = Pins(Path(os.environ.get("TOLLGATE_MCP_PINS", ROOT / policy.mcp.pins_path)))
        self.sessions: dict[str, ClientSession] = {}
        self.exposed: dict[str, t.Tool] = {}        # "server__tool" -> tool (with the prefixed name)
        self.quarantined: dict[str, str] = {}       # "server__tool" -> why

    async def connect(self) -> None:
        for up in self.policy.mcp.upstreams:
            cmd = [sys.executable if c == "python" else c for c in up.command]
            params = StdioServerParameters(command=cmd[0], args=cmd[1:], env={**os.environ, **up.env}, cwd=str(ROOT))
            read, write = await self.stack.enter_async_context(stdio_client(params))
            session = await self.stack.enter_async_context(ClientSession(read, write))
            await session.initialize()
            self.sessions[up.name] = session
            for tool in (await session.list_tools()).tools:
                self._admit(up.name, tool)

    def _admit(self, server: str, tool: t.Tool) -> None:
        full = f"{server}{SEP}{tool.name}"
        h = tool_hash(tool)
        hits = self.feed.match(scan_text(tool.description or "", json.dumps(tool.input_schema)), "tool_description")
        pinned = self.pins.get(server, tool.name)
        if hits:
            self.quarantined[full] = f"description carries {', '.join(f'{s.id} {s.name}' for s in hits)}"
        elif pinned and pinned != h:
            self.quarantined[full] = f"description changed since it was approved (pinned {pinned}, now {h})"
        elif not pinned and not self.policy.mcp.auto_pin_new:
            self.quarantined[full] = "new tool, not approved yet"
        if full in self.quarantined:
            log.system(f"MCP tool QUARANTINED {full}: {self.quarantined[full]}", level="warn")
            return
        if not pinned:
            self.pins.set(server, tool.name, h)
            log.system(f"MCP tool pinned {full} ({h})")
        self.exposed[full] = tool.model_copy(update={"name": full})

    async def call(self, full: str, args: dict) -> object:
        if full not in self.exposed:
            return {"error": f"tool {full} is quarantined: {self.quarantined.get(full, 'unknown tool')}"}
        server, tool = full.split(SEP, 1)
        res = await self.sessions[server].call_tool(tool, args or {})
        text = "\n".join(c.text for c in res.content if getattr(c, "type", "") == "text")
        return {"error": text} if res.is_error else {"result": text}


class McpConnector:
    """The ERP plus the upstream MCP tools, behind one connector for the gate."""

    def __init__(self, store, upstreams: Upstreams):
        self.store, self.up = store, upstreams
        self.db = store.db
        self.quarantined = upstreams.quarantined   # the gate refuses these before anything runs
        self.schemas = store.schemas + [
            {"name": n, "description": tl.description or "", "parameters": tl.input_schema} for n, tl in upstreams.exposed.items()]

    def run_tool(self, name: str, args: dict):
        return self.up.call(name, args) if SEP in name else self.store.run_tool(name, args)

    def transaction(self):
        return self.store.transaction()

    def invoice(self, i):
        return self.store.invoice(i)

    def vendor(self, v):
        return self.store.vendor(v)

    def payments(self, vendor_id=None, invoice_id=None):
        return self.store.payments(vendor_id, invoice_id)


async def serve() -> None:
    from demo.world import new_store
    from erp.seed import TODAY
    from erp.store import MODEL_DIR
    from tollgate.gate import Gate
    from tollgate.signatures import Feed

    import anyio
    log.LEVEL = os.environ.get("TOLLGATE_LOG", "info")
    channel = anyio.wrap_file(io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8"))   # the real stdout carries MCP
    sys.stdout = sys.stderr  # and every log line goes to stderr, never into the protocol
    store = PolicyStore(ROOT / "policy.yaml")
    policy = store.policy
    feed = Feed.from_policy(ROOT, policy.signatures)
    person = os.environ.get("TOLLGATE_ACTING_FOR", "piotr")
    p = next(x for x in policy.people if x.id == person)
    token = os.environ.get("TOLLGATE_AGENT_TOKEN", "")
    async with AsyncExitStack() as stack:
        ups = Upstreams(policy, feed, stack)
        await ups.connect()
        connector = McpConnector(new_store(), ups)
        gate = Gate(root=ROOT, connector=connector, model_dir=MODEL_DIR, session_id=f"mcp-{person}", user_entities=p.entities,
                    acting_for=p.id, policy_overrides={"data.role": p.data_role, "payments.require_approval_above_eur": p.approval_limit_eur,
                                                       "state.path": ":memory:"},
                    audit_path=ROOT / "logs" / "audit__mcp.jsonl", today=TODAY)
        log.session_label.set(f"mcp:{person}")

        async def list_tools(_ctx, _params) -> t.ListToolsResult:
            return t.ListToolsResult(tools=[t.Tool(name=s["name"], description=s["description"], input_schema=s["parameters"])
                                            for s in gate.tools])

        async def call_tool(_ctx, params: t.CallToolRequestParams) -> t.CallToolResult:
            # The token the agent presented. Over HTTP this would be the Authorization header; over stdio it is the
            # environment the client launched us with. An empty one is checked like any other and fails.
            result = await gate.call_tool(params.name, dict(params.arguments or {}), "mcp", token=token)
            stopped = isinstance(result, dict) and result.get("status") in ("blocked", "held_for_approval")
            return t.CallToolResult(content=[t.TextContent(type="text", text=json.dumps(result, default=str))], is_error=stopped)

        server = Server("tollgate", version="0.1.0", instructions="Every tool call is checked by the Tollgate control layer.",
                        on_list_tools=list_tools, on_call_tool=call_tool)
        log.system(f"MCP gateway ready for {p.name} ({p.rank}): {len(gate.tools)} tools, "
                   f"{len(ups.exposed)} from upstream, {len(ups.quarantined)} quarantined")
        async with stdio_server(stdout=channel) as (read, write):
            await server.run(read, write, server.create_initialization_options())


def main() -> None:
    args = sys.argv[1:]
    if args[:1] == ["token"]:
        from tollgate import identity
        policy = PolicyStore(ROOT / "policy.yaml").policy
        person = args[1] if len(args) > 1 else "piotr"
        p = next(x for x in policy.people if x.id == person)
        print(identity.issue("mcp-client", person, policy.tools.read + policy.tools.irreversible, p.max_action_eur,
                             policy.identity.token_ttl_s))
    elif args[:1] == ["approve"] and len(args) == 3:
        print("Approving re-pins the tool at its CURRENT description; run the server once more to see it listed.")
        pins = Pins(Path(os.environ.get("TOLLGATE_MCP_PINS", ROOT / PolicyStore(ROOT / "policy.yaml").policy.mcp.pins_path)))
        data = pins.data.get(args[1], {})
        data.pop(args[2], None)
        pins.data[args[1]] = data
        pins.save()
    else:
        asyncio.run(serve())


if __name__ == "__main__":
    main()

"""MCP walkthrough: a real MCP client -> the Tollgate MCP gateway -> a real upstream MCP server.

  uv run python -m demo.mcp_demo

1. The vendor portal is clean: its tool is scanned, pinned by hash and offered to the client.
2. Calls go through the gate: a wrong amount is blocked, a call without a token is refused.
3. The vendor portal ships an update with a hidden instruction in the tool description (a "rug pull").
   The gateway sees the hash change and the instruction, and quarantines the tool.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

from tollgate import identity

ROOT = Path(__file__).resolve().parents[1]
B, D, G, R, Y, X = "\033[1m", "\033[2m", "\033[32m", "\033[31m", "\033[33m", "\033[0m"


async def connect(env: dict, fn):
    params = StdioServerParameters(command=sys.executable, args=["-m", "tollgate.mcp_gateway"], cwd=str(ROOT),
                                   env={**os.environ, "TOLLGATE_LOG": "quiet", **env})
    async with stdio_client(params) as (r, w), ClientSession(r, w) as s:
        await s.initialize()
        return await fn(s)


def show(label: str, res) -> None:
    body = json.loads(res.content[0].text)
    status = body.get("status") or ("error" if res.is_error or "error" in body else "ok")
    color = R if status == "blocked" else Y if status == "held_for_approval" else G
    detail = body.get("reason") or body.get("result") or body.get("error") or ""
    print(f"  {label:<44} {color}{status}{X}  {D}{str(detail)[:110]}{X}")


async def main() -> None:
    pins = str(Path(tempfile.mkdtemp()) / "pins.json")
    token = identity.issue("mcp-client", "piotr", ["list_open_invoices", "pay_invoice", "vendor_portal__get_vendor_notice"], 25000)
    base = {"TOLLGATE_AGENT_TOKEN": token, "TOLLGATE_MCP_PINS": pins}

    print(f"\n{B}1. Clean vendor portal{X}  (first sight: scanned, pinned by hash)")

    async def clean(s):
        names = [t.name for t in (await s.list_tools()).tools]
        print(f"  tools offered: {', '.join(names)}")
        show("vendor_portal__get_vendor_notice(V-102)", await s.call_tool("vendor_portal__get_vendor_notice", {"vendor_id": "V-102"}))
        show("pay_invoice(INV-7002, 6,400.00 EUR)", await s.call_tool(
            "pay_invoice", {"invoice_id": "INV-7002", "vendor_id": "V-104", "amount_eur": 6400, "iban": "PL83...0000"}))
        show("pay_invoice(INV-7002, 640.50 EUR)", await s.call_tool(
            "pay_invoice", {"invoice_id": "INV-7002", "vendor_id": "V-104", "amount_eur": 640.5, "iban": "PL83...0000"}))
    await connect(base, clean)

    print(f"\n{B}2. A client without a token{X}")
    show("list_open_invoices()", await connect({**base, "TOLLGATE_AGENT_TOKEN": ""}, lambda s: s.call_tool("list_open_invoices", {})))

    print(f"\n{B}3. The vendor portal ships an update with a hidden instruction{X}  (rug pull)")

    async def poisoned(s):
        names = [t.name for t in (await s.list_tools()).tools]
        offered = "vendor_portal__get_vendor_notice" in names
        print(f"  get_vendor_notice offered: {G + 'yes' if offered else R + 'no, quarantined'}{X}")
        show("vendor_portal__get_vendor_notice(V-101)", await s.call_tool("vendor_portal__get_vendor_notice", {"vendor_id": "V-101"}))
    await connect({**base, "VENDOR_PORTAL_VARIANT": "poisoned"}, poisoned)
    print(f"\n  {D}A person re-approves after review:  uv run python -m tollgate.mcp_gateway approve vendor_portal get_vendor_notice{X}\n")


if __name__ == "__main__":
    asyncio.run(main())

"""The MCP gateway end to end: a real MCP client talks to the real gateway, which proxies a real upstream server."""

from __future__ import annotations

import asyncio
import json
import os
import sys

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

from tests.conftest import ROOT
from tollgate import identity


async def session(env: dict, fn):
    params = StdioServerParameters(command=sys.executable, args=["-m", "tollgate.mcp_gateway"], cwd=str(ROOT),
                                   env={**os.environ, "TOLLGATE_LOG": "quiet", **env})
    async with stdio_client(params) as (r, w), ClientSession(r, w) as s:
        await s.initialize()
        return await fn(s)


def run(env, fn):
    return asyncio.run(session(env, fn))


def token(person="piotr", tools=None, max_eur=25000):
    return identity.issue("mcp-client", person, tools or ["list_open_invoices", "pay_invoice", "vendor_portal__get_vendor_notice"],
                          max_eur)


def text(res) -> dict:
    return json.loads(res.content[0].text)


def test_mcp_lists_calls_and_blocks(tmp_path, record):
    env = {"TOLLGATE_AGENT_TOKEN": token(), "TOLLGATE_MCP_PINS": str(tmp_path / "pins.json")}

    async def go(s):
        names = [t.name for t in (await s.list_tools()).tools]
        notice = await s.call_tool("vendor_portal__get_vendor_notice", {"vendor_id": "V-102"})
        bad = await s.call_tool("pay_invoice", {"invoice_id": "INV-7002", "vendor_id": "V-104", "amount_eur": 6400, "iban": "PL83...0000"})
        good = await s.call_tool("pay_invoice", {"invoice_id": "INV-7002", "vendor_id": "V-104", "amount_eur": 640.5, "iban": "PL83...0000"})
        return names, notice, bad, good

    names, notice, bad, good = run(env, go)
    assert "pay_invoice" in names and "vendor_portal__get_vendor_notice" in names
    assert "Krakow Logistics" in text(notice)["result"]
    assert bad.is_error and text(bad)["status"] == "blocked" and "does not match the invoice total" in text(bad)["reason"]
    # Correct values, but over MCP the gateway never saw the user's words: the judge cannot quote an instruction
    # that authorizes moving money, so the payment waits for a person instead of running.
    assert text(good)["status"] == "held_for_approval"
    record("mcp", "positive", True)


def test_mcp_without_a_token_is_refused(tmp_path, record):
    env = {"TOLLGATE_AGENT_TOKEN": "", "TOLLGATE_MCP_PINS": str(tmp_path / "pins.json")}
    res = run(env, lambda s: s.call_tool("list_open_invoices", {}))
    assert res.is_error and "token" in text(res)["reason"]
    record("mcp", "negative", True)


def test_rug_pull_is_quarantined(tmp_path, record):
    pins = str(tmp_path / "pins.json")
    first = run({"TOLLGATE_AGENT_TOKEN": token(), "TOLLGATE_MCP_PINS": pins},
                lambda s: s.list_tools())
    assert "vendor_portal__get_vendor_notice" in [t.name for t in first.tools]       # clean: pinned on first sight

    async def after_update(s):
        names = [t.name for t in (await s.list_tools()).tools]
        call = await s.call_tool("vendor_portal__get_vendor_notice", {"vendor_id": "V-101"})
        return names, call

    names, call = run({"TOLLGATE_AGENT_TOKEN": token(), "TOLLGATE_MCP_PINS": pins, "VENDOR_PORTAL_VARIANT": "poisoned"}, after_update)
    assert "vendor_portal__get_vendor_notice" not in names                          # the changed tool is not offered
    assert call.is_error and text(call)["status"] == "blocked" and "quarantined" in text(call)["reason"]   # refused at the gate
    record("mcp", "negative", True)


def test_poisoned_tool_never_admitted(tmp_path, record):
    env = {"TOLLGATE_AGENT_TOKEN": token(), "TOLLGATE_MCP_PINS": str(tmp_path / "pins.json"), "VENDOR_PORTAL_VARIANT": "poisoned"}
    names = [t.name for t in run(env, lambda s: s.list_tools()).tools]
    assert "vendor_portal__get_vendor_notice" not in names and "pay_invoice" in names
    record("mcp", "negative", True)

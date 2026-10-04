"""Standalone architecture diagram for slides, the README or a submission.

  uv run python docs/make_diagram.py      writes docs/architecture(.svg|.png) and docs/architecture-technical(.svg|.png)

Same layout as the dashboard's "How it works" page, with fixed colours, a title and a key, so it works
anywhere without the dashboard's stylesheet. The PNG is rendered by headless Chrome at 2x.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

DOCS = Path(__file__).resolve().parent
INK, INK3, LINE, SURFACE, SURFACE2 = "#17171A", "#68686F", "#E6E4DF", "#FFFFFF", "#F9F8F6"
AI_FILL, AI_LINE, FONT = "#F5F0E6", "#7A5310", "Inter, -apple-system, 'Segoe UI', Helvetica, Arial, sans-serif"
TOP = 86   # room for the title and key


def esc(t: str) -> str:
    return t.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def box(x: int, y: int, w: int, h: int, name: str, sub: str = "", ai: bool = False) -> str:
    y += TOP
    fill, stroke = (AI_FILL, AI_LINE) if ai else (SURFACE, LINE)
    name_y = y + (24 if sub else h // 2 + 5)
    out = [f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="8" fill="{fill}" stroke="{stroke}" stroke-width="1.25"/>',
           f'<text x="{x + 14}" y="{name_y}" font-size="14" font-weight="600" fill="{INK}">{esc(name)}</text>']
    if sub:
        out.append(f'<text x="{x + 14}" y="{y + 42}" font-size="12" fill="{INK3}">{esc(sub)}</text>')
    return "".join(out)


def path(d: str, arrow: bool = False) -> str:
    return f'<path d="{d}" fill="none" stroke="{INK3}" stroke-width="1.25"{" marker-end=\"url(#a)\"" if arrow else ""}/>'


def svg() -> str:
    steps = [("1  Who is asking?", "signed agent token"), ("2  Is it known-bad?", "normalizer, attack feed, secrets"),
             ("3  What would really happen?", "contract looks up real records"), ("4  Within the rules?", "data guard, limits"),
             ("5  Did the user ask for it?", "AI judge quotes the user"), ("6  Decide", "allow · ask a person · block"),
             ("7  Do it safely", "verified values, one transaction"), ("8  Is the result safe?", "mask data, drop hidden orders"),
             ("9  Remember it", "hash-chained log, metrics")]
    systems = [("ERP", "payments, data"), ("Email", "inside and out"), ("Model files", "forecasts"),
               ("MCP tools", "vendor portal"), ("LLMs", "Gemini, Ollama")]
    o = TOP
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 960 {648 + TOP + 8}" font-family="{FONT}">',
        f'<rect width="100%" height="100%" fill="{SURFACE}"/>',
        f'<defs><marker id="a" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">'
        f'<path d="M0 0L10 5L0 10z" fill="{INK3}"/></marker></defs>',
        f'<text x="40" y="36" font-size="20" font-weight="600" fill="{INK}">Tollgate: every AI action passes one gate; the agent holds no keys</text>',
        f'<rect x="40" y="56" width="12" height="12" rx="3" fill="{SURFACE}" stroke="{LINE}" stroke-width="1.25"/>',
        f'<text x="58" y="66" font-size="12" fill="{INK3}">rule-based (same answer every time)</text>',
        f'<rect x="280" y="56" width="12" height="12" rx="3" fill="{AI_FILL}" stroke="{AI_LINE}" stroke-width="1.25"/>',
        f'<text x="298" y="66" font-size="12" fill="{INK3}">uses AI, can only make a decision stricter</text>',
        box(40, 10, 190, 54, "User", "asks in plain words"), box(270, 10, 190, 54, "AI agent", "holds only the gate"),
        box(500, 10, 190, 54, "Helper agents", "narrower token"), box(730, 10, 190, 54, "Any MCP client", "same checks via MCP"),
        *[path(f"M{x} {64 + o}V{82 + o}", True) for x in (135, 365, 595, 825)],
        path(f"M480 {470 + o}V{494 + o}"), path(f"M120 {494 + o}H840"),
        *[path(f"M{x} {494 + o}V{514 + o}", True) for x in (120, 300, 480, 660, 840)],
        path(f"M40 {610 + o}H24V{148 + o}H38", True), path(f"M920 {148 + o}H936V{610 + o}H922", True),
        f'<rect x="40" y="{84 + o}" width="880" height="386" rx="10" fill="{SURFACE2}" stroke="{INK3}" stroke-width="1.25"/>',
        f'<text x="56" y="{108 + o}" font-size="14" font-weight="600" fill="{INK}">Tollgate: the one gate every action passes</text>',
        box(56, 120, 424, 56, "Input lane", "your words kept for the judge; private data tokenized"),
        box(496, 120, 408, 56, "Model lane", "allowed models; cost, token and step budget"),
        f'<rect x="56" y="{192 + o}" width="848" height="262" rx="8" fill="none" stroke="{LINE}" stroke-width="1.25"/>',
        f'<text x="72" y="{214 + o}" font-size="14" font-weight="600" fill="{INK}">Action lane: every tool call, nine steps in order</text>',
        *[box([72, 348, 624][i % 3], [228, 302, 376][i // 3], 258, 58, n, s, ai=i in (4, 7)) for i, (n, s) in enumerate(steps)],
        *[box(40 + i * 180, 516, 160, 52, n, s) for i, (n, s) in enumerate(systems)],
        box(40, 588, 430, 46, "Configuration: policy.yaml · contracts · signed attack feed"),
        box(490, 588, 430, 46, "Evidence: audit log, /metrics, CSV"),
        "</svg>",
    ]
    return "\n".join(parts)


# ---------------- the technical diagram: modules, files, stores, protocols, real YAML and JSON ----------------
MONO = "ui-monospace, 'SF Mono', Menlo, Consolas, monospace"
STORE, CODE_BG = "#F1F0EC", "#F7F6F3"


def tbox(x, y, w, h, name, lines=(), *, kind="rule", mono_first=False) -> str:
    """kind: rule | ai | store | config. Name on the first line, then small lines."""
    fill, stroke, dash = {"rule": (SURFACE, LINE, ""), "ai": (AI_FILL, AI_LINE, ""), "store": (STORE, INK3, ""),
                          "config": (SURFACE, INK3, ' stroke-dasharray="5 4"')}[kind]
    out = [f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="8" fill="{fill}" stroke="{stroke}" stroke-width="1.25"{dash}/>',
           f'<text x="{x + 14}" y="{y + 22}" font-size="13.5" font-weight="600" fill="{INK}">{esc(name)}</text>']
    for i, line in enumerate(lines):
        mono = mono_first and i == 0
        out.append(f'<text x="{x + 14}" y="{y + 40 + 16 * i}" font-size="{11.5 if mono else 12}" fill="{INK3}"'
                   f'{f" font-family=\"{MONO}\"" if mono else ""}>{esc(line)}</text>')
    return "".join(out)


def step(i, y, name, files, a, b, ai=False, x=356, w=708, h=52) -> str:
    fill, stroke = (AI_FILL, AI_LINE) if ai else (SURFACE, LINE)
    return (f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="7" fill="{fill}" stroke="{stroke}" stroke-width="1.25"/>'
            f'<text x="{x + 14}" y="{y + 21}" font-size="13.5" font-weight="600" fill="{INK}">{i}  {esc(name)}</text>'
            f'<text x="{x + 14}" y="{y + 39}" font-size="11.5" font-family="{MONO}" fill="{INK3}">{esc(files)}</text>'
            f'<text x="{x + 270}" y="{y + 21}" font-size="12" fill="{INK}">{esc(a)}</text>'
            f'<text x="{x + 270}" y="{y + 39}" font-size="12" fill="{INK3}">{esc(b)}</text>')


def code(x, y, w, h, title, lines) -> str:
    out = [f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="8" fill="{CODE_BG}" stroke="{LINE}" stroke-width="1.25"/>',
           f'<text x="{x + 14}" y="{y + 22}" font-size="13" font-weight="600" fill="{INK}">{esc(title)}</text>']
    for i, line in enumerate(lines):
        out.append(f'<text x="{x + 14}" y="{y + 46 + 17 * i}" font-size="11.5" font-family="{MONO}" fill="{INK}" xml:space="preserve">{esc(line)}</text>')
    return "".join(out)


def label(x, y, text, anchor="start") -> str:
    return f'<text x="{x}" y="{y}" font-size="11.5" fill="{INK3}" text-anchor="{anchor}">{esc(text)}</text>'


def technical() -> str:
    W, H = 1560, 1410
    GX, GW = 340, 740                      # the gate
    SX, SW, SH, SY, SG = 356, 708, 50, 412, 62   # stage rows: x, width, height, first y, pitch
    sy = lambda i: SY + SG * i             # noqa: E731
    steps = [
        ("Normalize", "normalize.py", "NFKC, strip zero-width characters, compact IBAN spellings", "decode base64 so the scanners see hidden data"),
        ("Identity", "identity.py", "verify the HMAC-SHA256 agent token (claims in the excerpt below)", "impersonation, tool scope, amount scope, delegation depth"),
        ("Known-bad", "signatures.py · secrets.py", "signed feed patterns on arguments (CVE-based), 12 secret formats", "loop guard: 3rd identical call with no state change is blocked"),
        ("Resolve the effect", "contracts.py · connectors.py", "look up invoice, vendor and payments (read-only): the Effect", "run the contract's checks: present, equals, same_iban, at_most …"),
        ("Data and totals", "data_guard.py · limits.py", "SQLite authorizer: deny table, blank column, row-scope views", "reserve the amount in a time window in state.db"),
        ("Justify (AI)", "judge.py", "sees only the user's words and verified facts; answers in JSON", "its quote must appear verbatim in a user turn, else ask"),
        ("Decide", "verdict.py", "decision = strictest(findings): allow < ask < block", "ask goes to the approval queue"),
        ("Execute safely", "gate._execute", "bind verified values (amount and IBAN from the records)", "BEGIN IMMEDIATE · re-check the contract · run · COMMIT"),
        ("Screen the result (AI)", "screens.py · injection.py", "mask IBANs and IDs, redact secrets, tokenize for Gemini", "hidden orders: rules first, models only on unclear sentences"),
        ("Record", "audit.py · metrics.py", "append a JSONL record with the sha256 of the previous one", "update Prometheus counters; push the event to the dashboard"),
    ]
    p = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" font-family="{FONT}">',
         f'<rect width="100%" height="100%" fill="{SURFACE}"/>',
         f'<defs><marker id="a" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">'
         f'<path d="M0 0L10 5L0 10z" fill="{INK3}"/></marker></defs>',
         f'<text x="40" y="44" font-size="22" font-weight="600" fill="{INK}">Tollgate: technical architecture</text>',
         f'<text x="40" y="68" font-size="13" fill="{INK3}">Python 3.13 · the agent holds no credentials, only the gate · every tool call runs ten stages · rules decide, AI can only tighten · fail closed</text>']
    kx = 40
    for text, kind in [("rule-based module", "rule"), ("uses AI (can only tighten)", "ai"), ("data store", "store"), ("configuration file (hot reload)", "config")]:
        fill, stroke, dash = {"rule": (SURFACE, LINE, ""), "ai": (AI_FILL, AI_LINE, ""), "store": (STORE, INK3, ""),
                              "config": (SURFACE, INK3, ' stroke-dasharray="3 2"')}[kind]
        p.append(f'<rect x="{kx}" y="82" width="12" height="12" rx="3" fill="{fill}" stroke="{stroke}" stroke-width="1.25"{dash}/>'
                 f'<text x="{kx + 18}" y="92" font-size="12" fill="{INK3}">{text}</text>')
        kx += 18 + len(text) * 6.4 + 28

    # 1. configuration plane, each arrow says which stages read the file
    cfg = [("policy.yaml", ["controls, roles, people, limits, budgets", "Pydantic-checked every 0.5 s"], "every stage"),
           ("contracts/finance_ap.yaml", ["one contract per risky tool", "8 check types · bind · atomic"], "stages 3, 7"),
           ("signatures/feed.json + .sig", ["attack patterns, HMAC-verified", "local + remote (ETag), newest wins"], "stages 2, 8")]
    for k, (name, lines, used) in enumerate(cfg):
        x = GX + k * 252
        p.append(tbox(x, 108, 236, 88, name, lines, kind="config"))
        p.append(path(f"M{x + 118} 196V236", True) + label(x + 126, 222, used))

    # 2. callers on the left: one bus into the gate's API
    callers = [("AI agent loop", ["in-process, holds only the gate", "Gemini (Vertex) or Ollama"]),
               ("Dashboard", ["HTTP + NDJSON stream", "FastAPI, vanilla JS"]),
               ("Terminal demo", ["JSON lines over TCP", "demo/layer.py"]),
               ("Any MCP client", ["JSON-RPC over stdio", "token in TOLLGATE_AGENT_TOKEN"]),
               ("Helper agent", ["delegated token", "fewer tools, lower max_eur"])]
    for k, (name, lines) in enumerate(callers):
        y = 254 + 82 * k
        p.append(tbox(40, y, 250, 64, name, lines))
        p.append(path(f"M290 {y + 32}H312"))
    p.append(path("M312 278V614"))

    # 3. the gate: API, lanes, ten stages
    gate_bottom = sy(9) + SH + 16
    p.append(f'<rect x="{GX}" y="238" width="{GW}" height="{gate_bottom - 238}" rx="10" fill="{SURFACE2}" stroke="{INK3}" stroke-width="1.25"/>')
    p.append(path(f"M312 278H{SX - 2}", True))   # drawn over the gate's background so the arrowhead shows
    p.append(f'<rect x="{SX}" y="254" width="{SW}" height="48" rx="7" fill="{SURFACE}" stroke="{LINE}" stroke-width="1.25"/>'
             f'<text x="{SX + 14}" y="274" font-size="13.5" font-weight="600" fill="{INK}">tollgate.Gate: the only API the agent gets</text>'
             f'<text x="{SX + 14}" y="292" font-size="11.5" font-family="{MONO}" fill="{INK3}">user_message(text) · model_call(chat) · call_tool(name, args, provider, token)</text>')
    p.append(tbox(SX, 318, 350, 58, "Input lane", ["user_message(): verbatim log, vault tokenizes"]))
    p.append(tbox(714, 318, 350, 58, "Model lane", ["model_call(): allowlist, budget, cost"]))
    p.append(f'<text x="{SX}" y="402" font-size="13.5" font-weight="600" fill="{INK}">Action lane: call_tool() runs ten stages in order; the strictest finding wins</text>')
    for k, st in enumerate(steps):
        p.append(step(k, sy(k), *st, ai=k in (5, 8), x=SX, w=SW, h=SH))
        if k < 9:
            p.append(path(f"M{SX + 30} {sy(k) + SH}V{sy(k + 1) - 1}", True))

    # 4. what each stage calls out to, wired from that stage only
    RX, RW = 1170, 350
    gate_right = GX + GW
    def out(y, h, name, line, why):
        mid = y + h // 2
        return (tbox(RX, y, RW, h, name, [line]) + path(f"M{SX + SW} {mid}H{RX - 2}", True)
                + label((gate_right + RX) // 2, mid - 7, why, "middle"))
    p.append(out(318, 58, "LLM providers", "Gemini via the privacy vault · Ollama locally", "model calls"))
    p.append(out(sy(1), SH, "Signing key", "TOLLGATE_IDENTITY_KEY (env), HMAC-SHA256", "verifies"))
    p.append(out(sy(5), SH, "Judge model", "Gemini or qwen3:8b, JSON schema, 45 s timeout", "asks"))
    p.append(out(sy(7), SH, "Connectors: hold the credentials", "ERP writes · email · model files · MCP upstreams", "runs via"))
    p.append(out(sy(8), SH, "Injection models (Ollama)", "qwen3:0.6b screens unclear text · qwen3:8b confirms", "asks"))
    p.append(tbox(40, sy(6), 250, SH, "Approval queue", ["a person decides; checks re-run"]))
    p.append(path(f"M{SX} {sy(6) + SH // 2}H292", True) + label(318, sy(6) + SH // 2 - 6, "ask", "middle"))

    # 5. data stores under the gate, labelled with the stages that use them
    p.append(path(f"M710 {gate_bottom}V{gate_bottom + 12}") + path(f"M458 {gate_bottom + 12}H962"))
    stores = [("ERP database", ["SQLite in memory, from nordwind.db", "used by stages 3, 4, 7"]),
              ("Limits store", ["data/state.db, SQLite WAL, shared", "used by stage 4"]),
              ("Audit and metrics", ["logs/audit.jsonl · /metrics", "written by stage 9"])]
    for k, (name, lines) in enumerate(stores):
        x = GX + k * 252
        p.append(path(f"M{x + 118} {gate_bottom + 12}V{gate_bottom + 30}", True))
        p.append(tbox(x, gate_bottom + 32, 236, 66, name, lines, kind="store"))

    # 6. real excerpts
    cy = gate_bottom + 124
    p.append(code(40, cy, 480, 214, "contracts/finance_ap.yaml (excerpt)", [
        "pay_invoice:", "  atomic: true", "  bind: {amount_eur: invoice.amount, iban: vendor.iban}", "  checks:",
        "  - id: iban_on_file", "    type: same_iban        # agent's IBAN vs the record", "    left: args.iban", "    right: vendor.iban",
        "    decision: block"]))
    p.append(code(540, cy, 480, 214, "agent token claims (identity.py, HMAC-SHA256)", [
        "{", '  "agent": "payments-agent",', '  "acting_for": "piotr",', '  "tools": ["pay_invoice"],', '  "max_eur": 500.0,',
        '  "chain": ["ap-agent", "payments-agent"],', '  "exp": 1791089245', "}", "# delegation can only narrow tools and max_eur"]))
    p.append(code(1040, cy, 480, 214, "logs/audit.jsonl (one decision, shortened)", [
        "{", '  "tool": "pay_invoice",', '  "decision": "block",', '  "findings": [["pay_invoice.iban_on_file", "block"]],',
        '  "policy_sha": "ade8669e", "feed_version": 2,', '  "latency_ms": 0.42,', '  "prev": "9f3e1c…", "hash": "a1b2c4…"', "}",
        "# hash = sha256(record including prev)"]))
    p.append("</svg>")
    return "\n".join(p)


def render(svg_path: Path, png_path: Path, width: int, height: int) -> None:
    page = DOCS / ".diagram.html"
    page.write_text(f'<html><body style="margin:0;background:#fff"><img src="{svg_path.name}" style="width:{width}px;display:block"></body></html>')
    chrome = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
    subprocess.run([chrome, "--headless=new", "--disable-gpu", "--hide-scrollbars", f"--window-size={width},{height}",
                    "--default-background-color=FFFFFFFF", f"--screenshot={png_path}", page.as_uri()],
                   check=True, capture_output=True, timeout=60)
    page.unlink()


def main() -> None:
    out = DOCS / "architecture.svg"
    out.write_text(svg())
    page = DOCS / ".diagram.html"
    page.write_text(f'<html><body style="margin:0;background:#fff"><img src="architecture.svg" style="width:1920px;display:block"></body></html>')
    chrome = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
    height = round(1920 * (648 + TOP + 8) / 960)
    subprocess.run([chrome, "--headless=new", "--disable-gpu", "--hide-scrollbars", f"--window-size=1920,{height}",
                    "--default-background-color=FFFFFFFF", f"--screenshot={DOCS / 'architecture.png'}", page.as_uri()],
                   check=True, capture_output=True, timeout=60)
    page.unlink()
    tech = DOCS / "architecture-technical.svg"
    tech.write_text(technical())
    render(tech, DOCS / "architecture-technical.png", 3120, 2820)
    print(f"wrote {out}, {DOCS / 'architecture.png'}, {tech} and {DOCS / 'architecture-technical.png'}")


if __name__ == "__main__":
    main()

# Tollgate: an action-level control layer for AI agents

Tollgate sits between AI agents and the systems they act on. The agent holds no keys and no connections: every action goes through the gate, which works out **what the action would really do**, checks it, and then allows it, holds it for a person, or blocks it.

Use case: "Nordwind", a finance accounts-payable agent over a synthetic ERP (16 tables, 245 columns, 16,722 rows).

- Architecture and why each part exists: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)
- OWASP LLM Top 10 and Agentic Top 10 mapping, with the test that proves each row: [docs/OWASP.md](docs/OWASP.md)
- Demo script with prompts and expected outcomes: [docs/DEMO.md](docs/DEMO.md)

## Results

Same agents, same generic system prompt (no test-specific rules), 23 scenarios (payments, email, files, data access). The protected rounds were run before and after adding identity, MCP, secrets and injection screening. Qwen's usefulness is low with or without the layer: the 8B model gets amounts and accounts wrong, and the layer refuses those payments instead of letting them through.

| Setup | Harmful runs | Useful runs |
|---|---|---|
| Gemini 2.5 Flash, no layer | 9 of 21 (43%) | 98% |
| Gemini 2.5 Flash, with Tollgate | **0 of 42** (2 rounds) | 96% |
| Qwen3 8B (local), no layer | 17 of 20 (85%) | 7% |
| Qwen3 8B (local), with Tollgate | **0 of 41** (2 rounds) | 7% |

Gate overhead: p50 0.17 ms, p99 0.61 ms, about 3,000 decisions per second on one core (`uv run python -m evals.bench`).

## Test suite

Two parts, both ready to run, both also one click on the dashboard's **Tests** page.

| Part | What it proves | Command | Result |
|---|---|---|---|
| Control tests | every rule, each with a normal action it must allow and a misuse it must stop: payments, budgets, daily limits, data access, secrets, hidden instructions, known exploits (CVE patterns), identity, MCP, audit chain, 50 past incidents | `uv run pytest` | 169 passed in about 16 s, no AI needed |
| Live tests | a real model gets 18 requests (6 that must get done, 12 that must not happen), many chats at once, each in its own fresh copy of the data | `uv run python -m dashboard.testrun` (add `--layer off` for the baseline) | layer on: 18 of 18 in 41 s, $0.10. Layer off: 7 of 18 |

Cases live in `tests/cases.yaml` and `tests/live_cases.yaml`; both are YAML, so new cases need no Python.

## Quickstart

```bash
uv sync                                    # Python 3.13
uv run python -m erp.seed                  # synthetic finance database
uv run pytest                              # 168 tests: positive and negative per control, prints a coverage matrix
uv run python -m dashboard.server          # http://127.0.0.1:8400
```

Local models need Ollama with `qwen3:8b` (agent and judge), `qwen3:0.6b` (injection screen). Gemini needs `.env` with `GCP_PROJECT_ID` and `SERVICE_ACCOUNT_JSON` (Vertex AI, `europe-west4`).

## Ways to run it

| What | Command |
|---|---|
| Dashboard: agent room, the layer, raw vs agent view of the data, evidence (audit, metrics, feed) | `uv run python -m dashboard.server` |
| Remote attack feed (external threat-intel stand-in) | `uv run python -m demo.feed_server` |
| MCP gateway for any MCP client | `TOLLGATE_AGENT_TOKEN=$(uv run python -m tollgate.mcp_gateway token piotr) uv run python -m tollgate.mcp_gateway` |
| MCP walkthrough (pinning, rug pull, no token) | `uv run python -m demo.mcp_demo` |
| Terminal demo, layer process (one per agent) | `uv run python -m demo.layer --name local --port 7401 --judge qwen3:8b` |
| Terminal demo, chat (talks only to its layer) | `uv run python -m demo.chat --model qwen3:8b --port 7401` |
| Before/after evals (small: 1 run per scenario) | `uv run python -m evals.run --agent gemini-2.5-flash --mode baseline` then `--mode protected --judge gemini-2.5-flash` |
| Report | `uv run python -m evals.report` |
| Verify an audit log, export CSV | `uv run python -m tollgate.audit logs/audit__dashboard.jsonl` and `... audit csv logs/audit__dashboard.jsonl` |

Terminal chat commands: `/layer on`, `/layer off`, `/approve <id>`, `/pending`, `/new`, `/reset`, `/autoreset on`.

## Edit live (no restart)

| File | Controls | Reload |
|---|---|---|
| `policy.yaml` | preset, per-control modes, tools, data per role, people and limits, budgets, judge, feed, MCP, identity | within 0.5 s; invalid edits rejected, last good stays |
| `contracts/finance_ap.yaml` | what each tool call is resolved and checked against | within 0.5 s |
| `signatures/feed.json` | local attack signatures | after `uv run python -m tollgate.signatures sign` |
| `demo/feed_remote/feed.json` | remote feed (served by `demo.feed_server`) | after `uv run python -m demo.feed_server publish`; `tamper` shows a rejected feed |
| `tests/cases.yaml` | test cases, no Python needed; `expect_by_policy` follows the current policy | next test run |
| Dashboard, The layer page | preset (lenient, balanced, strict) and every redact or block switch | next action, all chats |

## Endpoints (dashboard)

| Path | What |
|---|---|
| `/metrics` | Prometheus: decisions by tool and decision, controls fired, gate and AI latency histograms, tokens, spend, waiting approvals |
| `/api/audit.csv` | the audit log as CSV |
| `/api/audit/verify` | hash-chain check |
| `/api/feed` | active feed version, source, remote status, signature list |
| `/api/metrics.json`, `/api/audit/recent` | the same numbers and records as JSON (used by the Evidence page) |
| `/api/policy` | GET and POST: preset and control switches |

## Layout

```
tollgate/   the layer (gate.py is the pipeline; one module per control)
contracts/  effect contracts (YAML)
dashboard/  FastAPI server + vanilla JS views
demo/       demo world, terminal chat and layer, MCP upstream, feed server, MCP walkthrough
erp/        synthetic database and mock ERP
evals/      scenarios, deterministic harm judge, runner, report, bench, incident replays
tests/      pytest suites + cases.yaml + incident reproducers
docs/       architecture, OWASP mapping, demo script
```

All data is synthetic: `.example` email domains, `SYN`-prefixed national IDs, unassigned bank codes, AWS's own documentation example key.

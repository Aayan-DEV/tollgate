# Tollgate

**One gate that every AI agent action passes.** Tollgate sits between AI agents and the systems they act on (payments, email, database, files, MCP tools). The agent holds no keys. For every action, Tollgate works out what it would really do, checks it, and then allows it, holds it for a person, or blocks it.

Demo company: "Nordwind", a finance team with an AI agent that pays invoices (synthetic data only).

**Live demo (Gemini):** https://tollgate-production-9154.up.railway.app

## Run it (2 commands)

You need [uv](https://docs.astral.sh/uv/) and, for the local AI model, [Ollama](https://ollama.com).

```bash
uv run pytest
```

```bash
uv run python -m dashboard.server
```

1. The first command installs everything, builds the demo database and runs the 174 control tests (no AI needed, about 20 s).
2. The second starts the dashboard at **http://127.0.0.1:8400**.

**AI model (pick one):**
- **Local (free):** run `ollama pull qwen3:8b` and `ollama pull qwen3:0.6b` once, then press **Start** in the model picker at the top left.
- **Gemini (optional):** put `GCP_PROJECT_ID` and `SERVICE_ACCOUNT_JSON` in a `.env` file (Vertex AI, `europe-west4`).

## What to try

1. **Agent:** ask "Please pay the Krakow Logistics reminder INV-7003-R." The layer holds it: the same bill was paid last week.
2. **Layer switch** (bottom left): turn it off and ask again. The duplicate is paid.
3. **Settings icon** (top right): edit `policy.yaml` or a contract. Changes apply within 0.5 s; invalid edits are rejected.
4. **Tests → Run everything:** control tests, live tests with the layer off and on, and the 23-situation before/after, for the selected model.
5. **Evidence:** management summary, security log (CSV, hash-chain verify), performance per stage.

## Results

Same model, same plain system prompt (no rules for these tests). Only the layer changed.

| | No layer | With Tollgate |
|---|---|---|
| Gemini 2.5 Flash: harmful outcomes in 21 risky situations | 9 | **0** |
| Gemini 2.5 Flash: useful work done | 93% | 93% |
| Gemini 2.5 Flash: live tests passed | 7 of 18 | **18 of 18** |
| Qwen3 8B (local): live tests passed | 6 of 18 | **18 of 18** |
| Qwen3 8B (local): harmful outcomes in 21 risky situations | 17 | **0** |

Rule checks take about 1 ms per action (p95 about 3 ms), about 3,000 decisions per second on one CPU core. AI is only asked about irreversible actions that passed every rule.

## How it works

Every tool call goes through 10 fixed stages: normalize, identity, known-bad patterns, resolve the real effect, data guard and limits, AI judge, decide, execute with verified values, screen the result, record. The strictest finding wins (allow < ask a person < block). AI can only make a decision stricter, never looser.

- Architecture: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) and [docs/architecture-technical.png](docs/architecture-technical.png)
- Every setting explained: [docs/CONFIGURATION.md](docs/CONFIGURATION.md)
- OWASP LLM and Agentic Top 10 mapping: [docs/OWASP.md](docs/OWASP.md)
- Demo script: [docs/DEMO.md](docs/DEMO.md)
- Slides: [docs/deck/Tollgate.pdf](docs/deck/Tollgate.pdf)

## Config files (edit live)

| File | What it controls |
|---|---|
| `policy.yaml` | strictness preset (lenient, balanced, strict), each control, allowed models, budgets, people and limits |
| `contracts/finance_ap.yaml` | what each tool call is checked against (real invoice, account on file, duplicates, limits) |
| `signatures/feed.json` | signed attack patterns (also fetched from a remote feed) |

## More commands

| What | Command |
|---|---|
| Live tests from the terminal | `uv run python -m dashboard.testrun --model qwen3:8b` (add `--layer off` for the baseline) |
| Before/after benchmark | `uv run python -m evals.run --agent qwen3:8b --mode baseline`, then `--mode protected` |
| MCP gateway for any MCP client | `TOLLGATE_AGENT_TOKEN=$(uv run python -m tollgate.mcp_gateway token piotr) uv run python -m tollgate.mcp_gateway` |
| Remote attack feed server | `uv run python -m demo.feed_server` |
| Hosted mode (no Ollama, Gemini for every AI check) | `TOLLGATE_LOCAL_MODELS=off uv run python -m dashboard.server`; `Dockerfile` and `railway.json` deploy it |
| Speed benchmark | `uv run python -m evals.bench` |

Monitoring endpoints: `/metrics` (Prometheus), `/api/audit.csv`, `/api/audit/verify`, `/api/telemetry.json`, `/api/report`.

## Layout

```
tollgate/   the layer (gate.py is the pipeline; one module per control)
contracts/  effect contracts (YAML)
dashboard/  FastAPI server and the web UI
erp/        synthetic database and mock ERP
evals/      before/after scenarios and the harm judge
tests/      control tests, live test cases, past incidents
docs/       architecture, configuration, OWASP mapping, demo script, slides
```

All data is synthetic: `.example` email domains, `SYN` national IDs, unassigned bank codes, and AWS's own documentation example key.

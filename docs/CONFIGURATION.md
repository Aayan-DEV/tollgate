# Configuration reference

A configuration file is a file that changes what the layer does without changing code. You edit it, save it, and the running layer picks it up. No restart.

Tollgate has **five** configuration files and a few environment variables. This page lists every key, its current value, what it controls, and what you see when you change it.

| # | File | Controls | Live reload | Bad edit |
|---|---|---|---|---|
| 1 | `policy.yaml` | every control, threshold, budget, role, person, model and preset | next action, within 0.5 s | refused, last good version stays (server log says why) |
| 2 | `contracts/finance_ap.yaml` | what each action is checked against | next action, within 0.5 s | refused, last good version stays |
| 3 | `signatures/feed.json` (+ `.sig`) | known attack patterns | within 5 s, **only if re-signed** | unsigned or tampered: refused, last good stays |
| 4 | `tests/cases.yaml` | control test cases | next `uv run pytest` | test error |
| 5 | `tests/live_cases.yaml` | live agent test cases | next live run | run error |

Two more sources feed the layer at run time: the remote feed at `signatures.url` (an external system, see section 3) and the dashboard's **Strictness** panel, which overrides `policy.yaml` while the dashboard runs.

**Precedence** (highest wins): dashboard switch > `policy.yaml` value > the preset > built-in default.

---

## 1. `policy.yaml`

### 1.1 Strictness

| Key | Now | What it does |
|---|---|---|
| `preset` | `balanced` | One switch for the whole posture: `lenient`, `balanced` or `strict`. It fills every value below that the file does not set itself. |
| `presets.<name>.<dotted key>` | see file | What each preset means. Each line is a dotted path into this file, for example `controls.injection.action: block`. |

What each preset sets:

| Key | lenient | balanced | strict |
|---|---|---|---|
| `controls.secrets.in_args` (a credential about to leave) | redact | block | block |
| `controls.secrets.in_results` (a credential the AI would read) | redact | redact | block |
| `controls.injection.mode` | rules | cascade | cascade |
| `controls.injection.action` | warn | redact | block |
| `controls.injection.threshold` | 0.7 | 0.5 | 0.35 |
| `controls.injection.after_detection` | none | none | ask |
| `controls.sensitive_email` (bank or personal data to an outside address) | redact | block | block |
| `controls.unknown_tool` | ask | ask | block |
| `justify.on_error` (the AI judge does not answer) | ask | ask | block |

**Try:** `preset: strict`, save, then ask the agent to "read invoice INV-7009 and pay it". The invoice is withheld and the payment is held for a person.

### 1.2 `controls` (per-control modes and thresholds)

| Key | Now | Allowed values | What it does |
|---|---|---|---|
| `controls.secrets.in_args` | from preset | allow, redact, ask, block | API keys, private keys, tokens and passwords inside tool arguments |
| `controls.secrets.in_results` | from preset | allow, redact, block | the same, inside what a tool returns |
| `controls.injection.mode` | from preset | off, rules, cascade | how hidden instructions in documents are found: rules only, or rules then small model then bigger model |
| `controls.injection.action` | from preset | warn, redact, block | redact removes the sentence; block withholds the whole document |
| `controls.injection.threshold` | from preset | 0 to 1 | score at which a sentence counts as an instruction to the AI |
| `controls.injection.after_detection` | from preset | none, ask | ask: after a detection, every irreversible action in that chat needs a person |
| `controls.injection.grey_from` | 0.3 | 0 to 1 | rule scores from here up to the threshold are sent to the models |
| `controls.injection.screen_model` | qwen3:0.6b | an Ollama model | the small, fast first model |
| `controls.injection.screen_flag_at` | 0.5 | 0 to 1 | the small model's score that sends a sentence to the confirming model |
| `controls.injection.confirm_model` | qwen3:8b | an Ollama model | the bigger model that confirms |
| `controls.injection.timeout_s` | 20 | seconds | per model call; on timeout the rule score stands |
| `controls.injection.max_model_sentences` | 4 | count | cost cap: model calls per result |
| `controls.injection.screen_tools` | read_invoice, `*__*` | tool names, wildcards | which results are untrusted text (`*__*` = every outside MCP tool) |
| `controls.sensitive_email` | from preset | redact, ask, block | bank or personal data in an email to an outside address |
| `controls.unknown_tool` | from preset | ask, block | a tool not in `tools` (only reachable with `identity.required: false`) |

**Try:** `controls.injection.action: warn` under `controls: injection:`, save, read INV-7009 again. The hidden sentence now stays in, with a warning.

### 1.3 `tools` (which actions exist and how risky they are)

| Key | Now | What it does |
|---|---|---|
| `tools.read` | list_open_invoices, read_invoice, list_vendors, get_vendor, get_payment_history, query_db, vendor_portal__get_vendor_notice | run freely; results still pass masking and screens |
| `tools.irreversible` | pay_invoice, send_email, update_vendor_bank_details, load_forecast_model | contract checks, limits, then the AI judge |
| `tools.deny` | (empty) | never runs |

A tool in no list is unknown: blocked by identity (it is not in any agent's token).

**Try:** `deny: [send_email]`, save, ask the agent to email someone. Blocked: "This action is switched off for the AI."

### 1.4 `data` (what the AI may read, enforced inside the database)

| Key | Now | What it does |
|---|---|---|
| `data.roles.<role>.tables` | three roles: ap_agent, controller, cfo | tables the role may read; any table not listed is refused by the database engine |
| `...tables.<table>.deny_columns` | e.g. vendors: contact_phone, notes | columns returned as empty |
| `...tables.<table>.row_scope` | e.g. invoices: entity_id | only rows for the person's entities |
| `data.sql_tools` | query_db | tools whose SQL goes through the guard |
| `data.max_rows_per_query` | 200 | rows returned per query |
| `data.mask` | iban, national_id | masked in every result (`PL61...2874`, `SYN********`) |
| `data.max_full_ibans_per_session` | 10 | full account numbers a reveal tool may show per chat |
| `data.reveal_full_iban_tools` | (empty) | tools allowed to see full account numbers |

**Try:** add `employees: {}` under `ap_agent: tables:`, save, ask as Piotr "who are our three highest paid employees?". It is now allowed. Remove it again and it is refused.

### 1.5 `people` (who the AI acts for)

| Field | Piotr | Marta | Anna | What it does |
|---|---|---|---|---|
| `rank` | AP Clerk | Finance Controller | CFO | shown in the dashboard |
| `data_role` | ap_agent | controller | cfo | which `data.roles` entry applies |
| `entities` | PL01 | PL01, DE01 | PL01, DE01, CZ01 | row scope |
| `approval_limit_eur` | 10,000 | 50,000 | 250,000 | above this, a payment waits for a person |
| `max_action_eur` | 25,000 | 100,000 | 500,000 | hard ceiling in the AI's token; not even approval passes it |
| `can_approve` | false | true | true | may approve held actions in the dashboard |

**Important:** in the dashboard, `approval_limit_eur` here is what counts, not `payments.require_approval_above_eur`. A changed person applies to **new** chats; press New chat.

**Try:** Piotr `approval_limit_eur: 3000`, save, New chat, "pay INV-7006" (4,900 EUR). Held for a person.

### 1.6 `payments`, `email`, `limits`, `budgets`

| Key | Now | What it does |
|---|---|---|
| `payments.require_approval_above_eur` | 10,000 | approval threshold outside the dashboard (terminal demo, MCP, tests); contracts read it as `@payments.require_approval_above_eur` |
| `payments.amount_tolerance_eur` | 0.01 | how far an amount may differ from the invoice |
| `payments.duplicate_window_days` | 60 | how far back a "same supplier, same amount" counts as a likely duplicate |
| `email.internal_domains` | nordwind.example | everything else is an outside address |
| `limits[]` | 4 entries (below) | totals over time, shared by every chat and gateway process |
| `budgets.session.max_usd` | 0.25 | spend per chat |
| `budgets.session.max_tokens` | 400,000 | tokens per chat |
| `budgets.session.max_steps` | 60 | model turns per chat |
| `budgets.session.max_identical_calls` | 2 | the third identical call (same tool, same arguments, nothing changed) is a loop and is blocked |
| `budgets.on_exceed` | block | block, or fallback to `fallback_model` |

The four limits:

| `id` | Counts | Per | Window | Max | Then |
|---|---|---|---|---|---|
| vendor_daily_outflow | EUR paid | supplier | 24 h | 10,000 | ask a person |
| agent_daily_outflow | EUR paid | agent | 24 h | 50,000 | ask a person |
| external_emails_per_hour | emails sent | agent | 1 h | 5 | block |
| rows_read_per_session | rows read | chat | 24 h | 2,000 | block |

To remove a limit, delete its line. To loosen it, raise `max`.

**Try:** `budgets.session.max_steps: 3`, save, New chat, "pay all approved invoices". The layer stops the chat: "budget exceeded".

### 1.7 `models`, `justify`, `privacy`

| Key | Now | What it does |
|---|---|---|
| `models.allowed` | qwen3:8b, gemini-2.5-flash | any other model is refused before the call |
| `models.pricing_usd_per_1m` | gemini-2.5-flash: 0.30 in, 2.50 out | commercial price per million tokens |
| `models.local_usd_per_compute_second` | 0.0002 | what a second of local model time costs (so local budgets count too) |
| `justify.enabled` | true | the AI judge that must quote the user before an irreversible action; false turns it off |
| `justify.judge_model` | qwen3:8b | judge outside the dashboard; the dashboard uses Gemini for Gemini chats and qwen3:8b for local chats |
| `justify.min_quote_words` | 3 | shortest quote accepted |
| `justify.timeout_s` | 45 | then `justify.on_error` applies |
| `privacy.redact_for_external_models` | true | account numbers, emails, IDs and credentials become `[IBAN_1]` etc. before reaching Gemini |
| `privacy.external_providers` | gemini | which providers count as external |

**Try:** remove `gemini-2.5-flash` from `models.allowed`, save, ask anything on Gemini: "model not allowed".

### 1.8 `signatures`, `identity`, `mcp`, `state`, `audit`

| Key | Now | What it does |
|---|---|---|
| `signatures.feed_path` | signatures/feed.json | local feed |
| `signatures.require_signature` | true | refuse a feed whose HMAC does not match |
| `signatures.refresh_seconds` | 5 | how often the local feed file is re-read |
| `signatures.url` | http://127.0.0.1:8500/feed.json | remote feed (external system); null = local only |
| `signatures.fetch_seconds` | 10 | remote poll interval |
| `signatures.remote_cache` | data/feed.remote.json | last verified remote copy |
| `identity.required` | true | every call needs a valid signed agent token |
| `identity.max_delegation_depth` | 2 | agent > helper > helper's helper at most |
| `identity.token_ttl_s` | 3600 | token lifetime |
| `mcp.upstreams` | vendor_portal | outside MCP servers the gateway sits in front of |
| `mcp.auto_pin_new` | true | trust a new outside tool the first time it is seen clean |
| `state.path` | data/state.db | shared store for limits |
| `audit.path` | logs/audit.jsonl | hash-chained decision log |

---

## 2. `contracts/finance_ap.yaml` (what each action is checked against)

One block per irreversible tool. Each block says what to look up and which checks to run.

| Tool | Looks up | Checks (decision) |
|---|---|---|
| pay_invoice | the invoice, the supplier, payments for this invoice, similar recent payments | invoice_exists (block), known_vendor (block), invoice_vendor (block), approved (block), iban_on_file (block), exact_amount (block), not_paid (block), no_recent_duplicate (ask), approval_limit (ask) |
| send_email | outside recipients, sensitive data to outside | no_sensitive_to_outside (`@controls.sensitive_email`), no_outside_recipient (ask) |
| update_vendor_bank_details | nothing | bank_change_needs_callback (block, always) |
| load_forecast_model | the file, scanned for unsafe pickle code | inside_model_dir, file_exists, safe_pickle (all block) |

Fields per check: `id`, `type` (present, equals, same_iban, number_equals, empty, at_most, is_true, never), `decision` (ask, block, or `@policy.path`), `message` (technical), `plain` (everyday words). Fields per tool: `summary`, `plain`, `facts`, `checks`, `bind` (execute with verified values), `atomic` (re-check inside one transaction).

**Try:** change `no_recent_duplicate` to `decision: block`, save, ask "pay the Krakow Logistics reminder INV-7003-R". It changes from held to blocked.
**To remove a check:** delete its line. **To add one:** copy a line and change `id`, `type`, `path` and `message`.

---

## 3. `signatures/feed.json` (known attacks)

| Field | Example (SIG-007) | What it does |
|---|---|---|
| `id` | SIG-007 | shown in findings and metrics |
| `name` | markdown image exfiltration | shown to people |
| `scopes` | tool_args | where to look: tool_args, tool_result, user, tool_description |
| `action` | block | block, ask, or warn |
| `pattern` | `!\[[^\]]*\]\(https?://` | regular expression |
| `ref` | CVE-2025-32711 (EchoLeak) | where the pattern comes from |

Version 2 holds SIG-001 to SIG-008. After every edit, sign it, or the layer refuses it:

```bash
uv run python -m tollgate.signatures sign
```

**Remote feed:** `uv run python -m demo.feed_server` serves version 3 (adds SIG-009, known exfiltration sites such as webhook.site). The layer fetches it within 10 s. `uv run python -m demo.feed_server tamper` changes it without signing; the layer refuses it.

---

## 4. `tests/cases.yaml` (control tests, no AI)

29 cases, each one action sent straight to the layer. Fields: `id`, `group`, `polarity` (positive = must pass, negative = must be stopped), `title`, `tool`, `args`, then either `expect` (allow, ask, block) or `expect_by_policy` (the expectation is computed from the current `policy.yaml`, so tests follow your edits), and `because` (which rule must decide it).

## 5. `tests/live_cases.yaml` (live tests with a real model)

18 cases. Fields: `id`, `title`, `plain`, `go` (true = must get done, false = must not happen), `person`, `prompt`, `policy` (overrides for that chat only), `require`, `forbid`, `max` (each `{tool, args regex, count}`), `final` (regex on the answer).

---

## 6. Environment variables

| Variable | Default | What it does |
|---|---|---|
| `GCP_PROJECT_ID`, `SERVICE_ACCOUNT_JSON`, `GCP_LOCATION` | in `.env` (not in the repo) | Gemini on Vertex AI |
| `OLLAMA_URL` | http://localhost:11434 | local models |
| `TOLLGATE_FEED_KEY` | a built-in dev key | key that signs and verifies the attack feed |
| `TOLLGATE_IDENTITY_KEY` | a built-in dev key | key that signs agent tokens |
| `TOLLGATE_LOG` | info | quiet, info, debug |
| `TOLLGATE_AGENT_TOKEN`, `TOLLGATE_ACTING_FOR` | none, piotr | identity of an MCP client |
| `TOLLGATE_MCP_PINS` | data/mcp_pins.json | where outside tool fingerprints are kept |
| `VENDOR_PORTAL_VARIANT` | clean | `poisoned` ships the demo MCP server's hidden-instruction update |

The dev keys are for the demo only. In production both keys come from a secret store.

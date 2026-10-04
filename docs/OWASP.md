# OWASP mapping

Each risk is mapped to the Tollgate control that handles it and the test that proves it. Test files are under `tests/`; case ids (P01, Q03, ...) are in `tests/cases.yaml`. Run everything with `uv run pytest`.

Coverage key: **Covered** = enforced and tested. **Partial** = reduced or contained, with the limit stated. **Out of scope** = belongs to another layer.

## OWASP Top 10 for LLM Applications (2025)

| Risk | Tollgate control | Proof | Coverage |
|---|---|---|---|
| LLM01 Prompt Injection | Effect contracts judge the action, not the prompt. The judge is blind to documents. Injection screen removes or withholds instructions in untrusted text (rules, then small model, then confirm). Feed signatures SIG-001/002/003/008. | `test_screens.py` (11 negative, 11 positive), P03, eval S01, D05 | Covered for actions. Detection itself is best effort; actions stay safe even when detection misses. |
| LLM02 Sensitive Information Disclosure | Data guard in the SQLite authorizer (tables, columns, rows per rank). Masking of IBANs and national IDs in every result. Data budget. Privacy vault for commercial models. Secrets detector on arguments and results. Outbound email check. | Q01 to Q12, `test_data_budget_masks_after_limit`, `test_secret_*`, E02, `test_sensitive_email_block_or_redact` | Covered |
| LLM03 Supply Chain | Model allowlist. Pickle scanner before loading model files. MCP tools pinned by hash; changed or poisoned tools quarantined. Signed attack feed. | M01, M02, `test_rug_pull_is_quarantined`, `test_poisoned_tool_never_admitted`, `test_tampered_feed_is_rejected` | Covered for what the agent loads and calls. Package dependencies: out of scope. |
| LLM04 Data and Model Poisoning | No training in the loop. Feed and policy are signed or validated; invalid edits rejected. | `test_tampered_remote_is_rejected_and_last_good_stays`, `test_invalid_policy_is_rejected_and_last_good_stays` | Partial (we do not train models) |
| LLM05 Improper Output Handling | Model output never executes directly: every tool call is canonicalized and checked. Code-execution strings, path traversal, SSRF and markdown-image exfiltration are blocked by signatures. | M03, M04, E04, `test_base64_hidden_bank_data_is_blocked` | Covered |
| LLM06 Excessive Agency | Default-deny tool catalog. Per-agent tokens with tool and amount scope. Approval limits per person. Cumulative limits. Justify judge. Bank-detail changes never by an agent. | P01 to P09, `test_tool_and_amount_scope`, `test_split_payments_hit_cumulative_limit` | Covered |
| LLM07 System Prompt Leakage | The system prompt holds no secrets and no rules that matter: all enforcement is outside the model. | Design (see `docs/ARCHITECTURE.md` section 4) | Covered by design |
| LLM08 Vector and Embedding Weaknesses | No RAG store in this use case. Retrieved text would pass through the same result screens. | n/a | Out of scope |
| LLM09 Misinformation | Amounts, payees and accounts come from the system of record (bind), not from the model. Layer notes tell the agent what was removed so it reports honestly. | `test_masked_account_reference_pays_the_verified_account`, `test_obfuscated_iban_is_normalized_and_bound` | Partial (answers in chat are not fact-checked) |
| LLM10 Unbounded Consumption | Per-session cost and token budgets for Gemini and local models, step cap, loop guard, row budget. | `test_budget_blocks_step_limit_and_unknown_model`, `test_identical_call_loop_is_stopped` | Covered |

## OWASP Top 10 for Agentic Applications (2026)

| Risk | Tollgate control | Proof | Coverage |
|---|---|---|---|
| ASI01 Agent Goal Hijack | Untrusted content is screened; the judge must quote the user, so a hijacked goal has no authorization. Strict preset holds every irreversible action after an injection is seen. | `test_lenient_warns_strict_withholds_and_holds_later_actions`, `test_judge_quote_verification`, eval S-scenarios | Covered for actions |
| ASI02 Tool Misuse and Exploitation | Effect contracts per tool, argument canonicalization, signatures, data guard, limits. | P, E, M, Q cases; `test_state_change_between_check_and_execute_is_caught` | Covered |
| ASI03 Identity and Privilege Abuse | Signed short-lived agent tokens: agent, person it acts for, tools, max per action, chain. Impersonation and forged tokens blocked. | `test_identity.py` (10 tests) | Covered |
| ASI04 Agentic Supply Chain | MCP pinning and quarantine; signed feed; pickle scanner; model allowlist. | `test_mcp.py`, `test_feed_remote.py`, M02 | Covered |
| ASI05 Unexpected Code Execution | Pickle opcodes scanned before load; exec and eval strings blocked; path traversal blocked; SQL writes refused by the authorizer. | M02, M03, M04, Q09, eval S16 | Covered |
| ASI06 Memory and Context Poisoning | The judge's memory is the verbatim user ledger, never summaries or documents. Injected sentences are removed before they enter the agent's context. | `test_judge_quote_verification`, `test_injected_sentence_is_removed_rest_kept` | Covered for the judge; partial for the agent's own context |
| ASI07 Insecure Inter-Agent Communication | Delegation tokens are signed and can only narrow. The hand-off task is screened for injection. | `test_delegation_only_narrows`, `test_sub_agent_cannot_exceed_its_delegated_amount`, `test_delegated_task_with_injection_is_refused` | Covered |
| ASI08 Cascading Failures | Fail closed everywhere; reservations released on failure; atomic re-check; limits shared across processes. | `test_judge_failure_fails_closed`, `test_two_gateways_share_one_budget` | Covered |
| ASI09 Human-Agent Trust Exploitation | Approval cards show the resolved effect (what will really happen), not the agent's description. Approving re-runs every hard check. | dashboard approval flow, `test_approved_overage_still_counts_toward_the_limit` | Covered |
| ASI10 Rogue Agents | The agent holds no credentials, so it can only do what the gate allows. Tokens expire (1 hour); budgets and step caps stop a runaway agent; every action is audited and metered. | `test_agent_has_no_path_around_the_gate`, `test_expired_token_is_blocked`, `test_budget_blocks_step_limit_and_unknown_model` | Partial (no live token revocation list yet) |

Sources: [OWASP Top 10 for LLM Applications 2025](https://genai.owasp.org/llm-top-10/), [OWASP Top 10 for Agentic Applications 2026](https://genai.owasp.org/resource/owasp-top-10-for-agentic-applications-for-2026/).

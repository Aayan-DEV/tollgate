"""The "How it works" page: every part of the layer, with a live excerpt from its real file.

Excerpts are read from disk on every request, so the page always shows the configuration that runs.
"""

from __future__ import annotations

import json
from pathlib import Path

import yaml

from tollgate import identity
from tollgate.normalize import canonical_args
from tollgate.privacy import Vault
from tollgate.secrets import PATTERNS

ROOT = Path(__file__).resolve().parents[1]


def _yaml(path: str, *keys: str) -> str:
    """One section of a YAML file, re-dumped so it reads cleanly."""
    node = yaml.safe_load((ROOT / path).read_text())
    for k in keys:
        node = node[k]
    return yaml.safe_dump({keys[-1]: node} if keys else node, sort_keys=False, width=110, allow_unicode=True).strip()


def _contract(tool: str, checks: int = 4) -> str:
    spec = yaml.safe_load((ROOT / "contracts" / "finance_ap.yaml").read_text())[tool]
    short = {k: spec[k] for k in ("summary", "bind", "atomic", "facts") if k in spec}
    short["checks"] = [{k: c[k] for k in ("id", "type", "decision", "plain") if k in c} for c in spec["checks"][:checks]]
    return yaml.safe_dump({tool: short}, sort_keys=False, width=110, allow_unicode=True).strip() + \
        (f"\n  # ... {len(spec['checks']) - checks} more checks" if len(spec["checks"]) > checks else "")


def _feed(sig_id: str) -> str:
    data = json.loads((ROOT / "signatures" / "feed.json").read_text())
    sig = next(s for s in data["signatures"] if s["id"] == sig_id)
    return json.dumps({"version": data["version"], "signatures": ["...", sig, "..."]}, indent=2)


def _audit_sample() -> str:
    for name in ("audit__dashboard.jsonl", "audit__protected__gemini-2.5-flash__judge-gemini-2.5-flash.jsonl"):
        path = ROOT / "logs" / name
        if path.exists():
            lines = path.read_text().splitlines()
            if lines:
                rec = json.loads(lines[-1])
                keep = {k: rec[k] for k in ("tool", "decision", "effect", "policy_sha", "feed_version", "latency_ms", "prev", "hash") if k in rec}
                keep["prev"], keep["hash"] = keep.get("prev", "")[:16] + "...", keep.get("hash", "")[:16] + "..."
                return json.dumps(keep, indent=2, ensure_ascii=False)
    return "(no decisions recorded yet)"


def _token() -> str:
    t = identity.issue("ap-agent", "piotr", ["read_invoice", "pay_invoice"], 25000)
    child = identity.delegate(t, "payments-agent", ["pay_invoice"], 500, max_depth=2)
    body = json.loads(identity._unb64(child.split(".")[0]))
    body["exp"] = "in 15 minutes"
    return f"token = base64(claims) + \".\" + HMAC-SHA256(key, claims)\n\n# a helper agent's token (narrowed by delegation):\n{json.dumps(body, indent=2)}"


def components() -> list[dict]:
    vault = Vault()
    return [
        {"id": "gate", "name": "The gate", "kind": "deterministic", "files": ["tollgate/gate.py"],
         "what": "The only path from an AI to any system. The agent holds no passwords, keys or connections, only the gate, so every action is checked; there is no way around it.",
         "protects": "Any action skipping a check.", "lang": "text",
         "snippet": "lane 1  user_message(text)      words kept verbatim, secrets tokenized for outside models\n"
                    "lane 2  call_tool(name, args)   identity > known-bad > resolve > rules > judge > decide > execute > screen > record\n"
                    "lane 3  model_call(chat)        allowed model? within the chat's budget?\n\n"
                    "decision = strictest(findings)   # allow < ask < block"},
        {"id": "identity", "name": "Identity", "kind": "deterministic", "files": ["tollgate/identity.py", "policy.yaml: identity"],
         "what": "Every agent carries a signed token: who it is, who it works for, which tools it may use, how much it may move at once, and who handed it the job. A helper can only get fewer rights, never more.",
         "protects": "Impersonation, helpers with more power than their parent, unknown callers.", "lang": "json", "snippet": _token()},
        {"id": "normalize", "name": "Normalizer", "kind": "deterministic", "files": ["tollgate/normalize.py"],
         "what": "Undoes disguises before any rule runs: Unicode look-alikes, invisible characters, spaced-out or dashed bank numbers, data hidden in base64.",
         "protects": "Known-bad slipping through because it is written differently.", "lang": "json",
         "snippet": json.dumps({"agent sent": {"iban": "pl61 1090-1014 0000​0712 1981 2874"},
                                "the gate checks": canonical_args({"iban": "pl61 1090-1014 0000​0712 1981 2874"})}, indent=2, ensure_ascii=False)},
        {"id": "feed", "name": "Attack feed", "kind": "deterministic", "files": ["signatures/feed.json", "signatures/feed.json.sig", "remote: demo/feed_server.py"],
         "what": "Patterns from real attacks and CVEs, fed from an external system. Only loaded if its HMAC signature matches; the highest verified version wins; a tampered or unreachable feed keeps the last good one.",
         "protects": "Known exploits: code execution strings, path traversal, cloud metadata access, markdown-image exfiltration, hidden instructions.", "lang": "json", "snippet": _feed("SIG-007")},
        {"id": "secrets", "name": "Secrets detector", "kind": "deterministic", "files": ["tollgate/secrets.py", "policy.yaml: controls.secrets"],
         "what": "Finds credentials by their exact formats in anything going out or coming back, then redacts, holds or blocks per policy.",
         "protects": "Keys, tokens and passwords leaving through a tool or being read by the AI.", "lang": "yaml",
         "snippet": "detects: " + ", ".join(PATTERNS) + "\n\n# what to do (balanced preset):\n" + yaml.safe_dump(
             {k: v for k, v in yaml.safe_load((ROOT / "policy.yaml").read_text())["presets"]["balanced"].items() if k.startswith("controls.secrets")},
             sort_keys=False).strip()},
        {"id": "contracts", "name": "Effect contracts", "kind": "deterministic", "files": ["contracts/finance_ap.yaml", "tollgate/contracts.py"],
         "what": "For each risky action, a contract looks up what would really happen in the company's records (the real invoice, supplier, payment history) and runs fixed checks on that. The action is judged by its effect, not by what the prompt claims.",
         "protects": "Wrong amount or payee, scam bank accounts, unapproved invoices, paying twice.", "lang": "yaml", "snippet": _contract("pay_invoice", 5)},
        {"id": "data", "name": "Data guard", "kind": "deterministic", "files": ["tollgate/data_guard.py", "policy.yaml: data.roles"],
         "what": "Every SQL query runs with the database engine's own authorizer: it refuses tables a role may not see, blanks hidden columns and limits rows to the person's entities, after joins, unions and subqueries are resolved.",
         "protects": "Salaries, national IDs, other entities' data, writes through SQL.", "lang": "yaml", "snippet": _yaml("policy.yaml", "data", "roles", "ap_agent")},
        {"id": "limits", "name": "Limits and budgets", "kind": "deterministic", "files": ["policy.yaml: limits, budgets", "tollgate/limits.py", "tollgate/budget.py"],
         "what": "Totals over time, shared by every chat and process (per supplier per day, per agent, emails per hour, rows read), plus a cost, token and step budget per chat for Gemini and for local models.",
         "protects": "Splitting a big payment into small ones, slow leaks, runaway loops and spend.", "lang": "yaml",
         "snippet": _yaml("policy.yaml", "limits") + "\n" + _yaml("policy.yaml", "budgets", "session")},
        {"id": "judge", "name": "AI judge", "kind": "ai", "files": ["tollgate/judge.py", "policy.yaml: justify"],
         "what": "Before anything irreversible, an AI must quote the user's own words that ask for it. It never sees documents (so they cannot steer it), and code checks the quote is really in what the user typed. It can only make a decision stricter.",
         "protects": "Actions the user never asked for, including ones a document talked the agent into.", "lang": "yaml", "snippet": _yaml("policy.yaml", "justify")},
        {"id": "bind", "name": "Bind and atomic", "kind": "deterministic", "files": ["contracts/finance_ap.yaml: bind, atomic"],
         "what": "The payment runs with the verified values (amount from the invoice, account from the supplier record), not the AI's, and is re-checked inside one database transaction, so nothing can change between check and payment.",
         "protects": "The AI swapping a value; races between checking and paying.", "lang": "yaml",
         "snippet": yaml.safe_dump({"bind": yaml.safe_load((ROOT / "contracts" / "finance_ap.yaml").read_text())["pay_invoice"]["bind"], "atomic": True}, sort_keys=False).strip()},
        {"id": "screens", "name": "Result screens", "kind": "ai", "files": ["tollgate/screens.py", "tollgate/injection.py", "policy.yaml: controls.injection"],
         "what": "What comes back is cleaned before the AI reads it: bank numbers and IDs masked, credentials masked, and hidden orders in documents removed. Rules score every sentence in microseconds; a small model looks only at unclear ones; a bigger model confirms.",
         "protects": "Prompt injection through invoices and outside tools; sensitive data in results.", "lang": "yaml", "snippet": _yaml("policy.yaml", "controls", "injection")},
        {"id": "privacy", "name": "Privacy vault", "kind": "deterministic", "files": ["tollgate/privacy.py"],
         "what": "Text sent to a commercial model has bank numbers, emails, IDs and keys swapped for placeholders; the gate swaps them back before checking an action.",
         "protects": "Personal and bank data reaching an outside AI provider.", "lang": "json",
         "snippet": json.dumps({"user typed": "Pay PL61 1090 1014 0000 0712 1981 2874 and email it@nordwind.example",
                                "Gemini sees": vault.tokenize("Pay PL61 1090 1014 0000 0712 1981 2874 and email it@nordwind.example")}, indent=2)},
        {"id": "policy", "name": "Policy and presets", "kind": "deterministic", "files": ["policy.yaml", "tollgate/policy.py"],
         "what": "One file holds every control, threshold and budget. A preset sets the posture in one word; each control can still be set alone. Edits apply on the next action; a bad edit is refused and the last good version keeps running.",
         "protects": "Wrong strictness for the context; broken edits going live.", "lang": "yaml",
         "snippet": "preset: balanced   # lenient | balanced | strict\n\n" + _yaml("policy.yaml", "presets", "strict")},
        {"id": "mcp", "name": "MCP gateway", "kind": "deterministic", "files": ["tollgate/mcp_gateway.py", "policy.yaml: mcp"],
         "what": "Any MCP client gets the same checks. Outside MCP tools are pinned by a fingerprint of their description; a tool that changes or hides instructions is locked until a person approves it.",
         "protects": "Tool poisoning and rug pulls from third-party tools.", "lang": "yaml", "snippet": _yaml("policy.yaml", "mcp")},
        {"id": "audit", "name": "Audit and metrics", "kind": "deterministic", "files": ["tollgate/audit.py", "tollgate/metrics.py"],
         "what": "Every decision is written to a log where each line holds the fingerprint of the one before, so an edit or deletion shows. Counters feed /metrics; the log exports to CSV.",
         "protects": "Silent edits to the record; no evidence for reviewers.", "lang": "json", "snippet": _audit_sample()},
    ]

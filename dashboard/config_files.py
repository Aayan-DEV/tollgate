"""The three live configuration files, read and written from the dashboard.

Saving checks the new text first. A bad edit is refused with the reason and the file on disk
is not touched, so the running layer never sees it. A good edit is written atomically and the
layer picks it up on its next action (the same hot reload as an edit in a text editor).
The attack feed is signed on save: the dashboard is an operator tool, and an unsigned feed
would be refused by the layer.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from pathlib import Path

import yaml

from tollgate.contracts import Contract
from tollgate.policy import PolicyStore
from tollgate.signatures import sign

ROOT = Path(__file__).resolve().parents[1]
FACT_FUNCTIONS = {"erp.invoice", "erp.vendor", "erp.payments_for_invoice", "erp.similar_payments",
                  "mail.external_recipients", "mail.external_if_sensitive", "files.pickle_scan"}
SCOPES = {"tool_args", "tool_result", "user", "tool_description"}

FILES = {
    "policy": {"n": 1, "path": "policy.yaml", "title": "Policy",
               "about": "Every control, threshold and budget: strictness preset, what each role may read, "
                        "people and their limits, allowed models, daily limits, the AI judge."},
    "contracts": {"n": 2, "path": "contracts/finance_ap.yaml", "title": "Action rules",
                  "about": "What each risky action is checked against: right amount, verified bank account, "
                           "approved invoice, not paid twice, emails leaving the company, unsafe files."},
    "feed": {"n": 3, "path": "signatures/feed.json", "title": "Attack feed",
             "about": "Known attack patterns. Saving here signs the file, so the layer accepts it."},
}


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:12]


def read(fid: str) -> dict:
    meta = FILES[fid]
    text = (ROOT / meta["path"]).read_text()
    return {"id": fid, **meta, "text": text, "sha": _sha(text)}


# ---------------- validation: returns (error or None, summary of what the new file means) ----------------
def _check_policy(text: str, current) -> tuple[str | None, list[str]]:
    with tempfile.NamedTemporaryFile("w", suffix=".yaml", dir=ROOT / "data", delete=False) as fh:
        fh.write(text)
        tmp = Path(fh.name)
    try:
        new = PolicyStore(tmp).policy
    except Exception as err:  # YAML syntax, a missing section, a wrong type, an unknown preset
        return _plain_error(err), []
    finally:
        tmp.unlink(missing_ok=True)
    old, now = _paths(current.model_dump()), _paths(new.model_dump())
    changes = [f"{k}: {_short(old.get(k, '(none)'))} → {_short(now.get(k, '(removed)'))}"
               for k in sorted(set(old) | set(now)) if old.get(k) != now.get(k)]
    return None, changes


def _paths(node: object, prefix: str = "") -> dict[str, object]:
    """Every leaf by a readable path: people[piotr].approval_limit_eur, limits[vendor_daily_outflow].max."""
    out: dict[str, object] = {}
    if isinstance(node, dict):
        for k, v in node.items():
            out.update(_paths(v, f"{prefix}.{k}" if prefix else str(k)))
    elif isinstance(node, list) and node and all(isinstance(x, dict) and ("id" in x or "name" in x) for x in node):
        for x in node:
            out.update(_paths(x, f"{prefix}[{x.get('id', x.get('name'))}]"))
    else:
        out[prefix] = node
    return out


def _short(v: object) -> str:
    text = ", ".join(map(str, v)) if isinstance(v, list) else str(v)
    return text if len(text) <= 70 else text[:67] + "..."


def _check_contracts(text: str, _current) -> tuple[str | None, list[str]]:
    try:
        data = yaml.safe_load(text) or {}
        if not isinstance(data, dict):
            return "The file must be a list of tools, each with its checks.", []
        for tool, spec in data.items():
            c = Contract.model_validate(spec)
            unknown = [f.fn for f in c.facts.values() if f.fn not in FACT_FUNCTIONS]
            if unknown:
                return f"{tool}: unknown lookup {', '.join(unknown)} (known: {', '.join(sorted(FACT_FUNCTIONS))})", []
    except Exception as err:
        return _plain_error(err), []
    return None, [f"{tool}: {len(spec.get('checks', []))} checks" for tool, spec in data.items()]


def _check_feed(text: str, _current) -> tuple[str | None, list[str]]:
    try:
        data = json.loads(text)
        int(data["version"])
        for s in data["signatures"]:
            for k in ("id", "name", "scopes", "action", "pattern"):
                if k not in s:
                    return f"signature {s.get('id', '?')} is missing '{k}'", []
            if s["action"] not in ("block", "ask", "warn"):
                return f"{s['id']}: action must be block, ask or warn", []
            if not set(s["scopes"]) <= SCOPES:
                return f"{s['id']}: scopes must be among {', '.join(sorted(SCOPES))}", []
            re.compile(s["pattern"])
    except re.error as err:
        return f"a pattern is not a valid regular expression: {err}", []
    except Exception as err:
        return _plain_error(err), []
    return None, [f"version {data['version']}, {len(data['signatures'])} signatures"]


CHECKS = {"policy": _check_policy, "contracts": _check_contracts, "feed": _check_feed}


def _plain_error(err: Exception) -> str:
    """The first useful line of a parser or validation error."""
    text = str(err)
    if "validation error" in text:   # pydantic: "1 validation error for Policy\njustify\n  Field required ..."
        lines = [x.strip() for x in text.splitlines()[1:] if x.strip()]
        where, what = (lines + ["", ""])[:2]
        what = what.split(" [type=")[0].replace("Field required", "is missing")
        return f"{where} {what}" if what.startswith("is ") else f"{where}: {what}"
    return text.splitlines()[0][:300]


def save(fid: str, text: str, base_sha: str, current_policy) -> dict:
    meta = FILES[fid]
    path = ROOT / meta["path"]
    on_disk = path.read_text()
    if base_sha and _sha(on_disk) != base_sha and on_disk != text:
        return {"ok": False, "conflict": True, "error": "The file changed on disk since you opened it (someone edited it). "
                                                         "Reload to see the new version; your text is kept in the editor."}
    error, summary = CHECKS[fid](text, current_policy)
    if error:
        return {"ok": False, "error": error}
    if text == on_disk:
        return {"ok": True, "unchanged": True, "summary": summary, "sha": _sha(text)}
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text)
    os.replace(tmp, path)            # atomic: the layer never reads half a file
    if fid == "feed":
        sign(path)
    return {"ok": True, "summary": summary, "sha": _sha(text), "signed": fid == "feed"}

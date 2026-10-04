"""Typed policy with hot reload.

The file is re-checked at most every 0.5 s (one stat call, microseconds). A
valid change swaps in atomically and the changed keys are logged; an invalid
change is rejected and the last good policy keeps running.
"""

from __future__ import annotations

import hashlib
import threading
import time
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, ValidationError

from tollgate import log


class Tools(BaseModel):
    read: list[str] = []
    irreversible: list[str] = []
    deny: list[str] = []

    def kind(self, name: str) -> Literal["read", "irreversible", "deny", "unknown"]:
        if name in self.deny:
            return "deny"
        if name in self.irreversible:
            return "irreversible"
        if name in self.read:
            return "read"
        return "unknown"


class Price(BaseModel):
    input: float
    output: float


class Models(BaseModel):
    allowed: list[str]
    pricing_usd_per_1m: dict[str, Price] = {}
    local_usd_per_compute_second: float = 0.0


class SessionBudget(BaseModel):
    max_usd: float = Field(gt=0)
    max_tokens: int = Field(gt=0)
    max_steps: int = Field(gt=0)
    max_identical_calls: int = Field(ge=1)


class Budgets(BaseModel):
    session: SessionBudget
    on_exceed: Literal["block", "fallback"] = "block"
    fallback_model: str | None = None


class Payments(BaseModel):
    require_approval_above_eur: float = Field(ge=0)
    amount_tolerance_eur: float = Field(ge=0, default=0.01)
    duplicate_window_days: int = Field(ge=0, default=60)


class Email(BaseModel):
    internal_domains: list[str]


class Justify(BaseModel):
    enabled: bool = True
    judge_model: str
    min_quote_words: int = Field(ge=1, default=3)
    timeout_s: float = Field(gt=0, default=45)
    on_error: Literal["ask", "block", "allow"] = "ask"


class Signatures(BaseModel):
    feed_path: str
    require_signature: bool = True
    refresh_seconds: float = Field(gt=0, default=5)
    url: str | None = None                           # remote feed (an external threat-intel system); None = local only
    fetch_seconds: float = Field(gt=0, default=15)   # remote poll interval (ETag: unchanged costs one 304)
    remote_cache: str = "data/feed.remote.json"      # verified remote copy; survives restarts and outages


class Privacy(BaseModel):
    redact_for_external_models: bool = True
    external_providers: list[str] = ["gemini"]


class Audit(BaseModel):
    path: str = "logs/audit.jsonl"


class DataTable(BaseModel):
    deny_columns: list[str] = []
    row_scope: str | None = None     # column matched against the user's entities


class DataRole(BaseModel):
    tables: dict[str, DataTable]     # tables not listed are denied


class Data(BaseModel):
    role: str
    roles: dict[str, DataRole]
    sql_tools: list[str] = ["query_db"]
    max_rows_per_query: int = Field(gt=0, default=200)
    max_full_ibans_per_session: int = Field(ge=0, default=10)
    reveal_full_iban_tools: list[str] = []
    mask: list[Literal["iban", "national_id"]] = ["iban", "national_id"]


class Limit(BaseModel):
    id: str
    tools: list[str]
    measure: Literal["amount_eur", "count", "rows"]
    per: str                          # agent | session | an argument name such as vendor_id
    window_minutes: int = Field(gt=0)
    max: float = Field(ge=0)
    decision: Literal["ask", "block"] = "ask"


class State(BaseModel):
    path: str = "data/state.db"       # shared by every gateway process (":memory:" for isolated runs)


class Identity(BaseModel):
    required: bool = False            # true: a call without a valid signed agent token is blocked
    max_delegation_depth: int = Field(ge=0, default=2)
    token_ttl_s: int = Field(gt=0, default=3600)


class McpUpstream(BaseModel):
    name: str
    command: list[str]
    env: dict[str, str] = {}


class Mcp(BaseModel):
    upstreams: list[McpUpstream] = []
    pins_path: str = "data/mcp_pins.json"
    auto_pin_new: bool = True          # trust a tool the first time it is seen clean; any later change is quarantined


class SecretsControl(BaseModel):
    in_args: Literal["allow", "redact", "ask", "block"] = "block"     # a credential about to leave through a tool
    in_results: Literal["allow", "redact", "block"] = "redact"        # a credential the agent is about to read


class InjectionControl(BaseModel):
    mode: Literal["off", "rules", "cascade"] = "cascade"   # rules only, or rules + small model + confirming model
    action: Literal["warn", "redact", "block"] = "redact"  # redact = remove the sentence; block = withhold the whole result
    threshold: float = Field(ge=0, le=1.01, default=0.5)
    grey_from: float = Field(ge=0, le=1, default=0.3)      # rules scores in [grey_from, threshold) go to the models
    screen_model: str = "qwen3:0.6b"
    screen_flag_at: float = Field(ge=0, le=1, default=0.5)
    confirm_model: str = "qwen3:8b"
    timeout_s: float = Field(gt=0, default=20)
    max_sentences: int = Field(gt=0, default=300)
    max_model_sentences: int = Field(ge=0, default=4)      # model calls per result, a cost cap
    screen_tools: list[str] = ["read_invoice", "*__*"]     # untrusted text: documents and third-party (MCP) tools
    after_detection: Literal["none", "ask"] = "none"       # ask = later irreversible actions in this session need a human


class Controls(BaseModel):
    """Per-control modes. The preset fills every value the policy file does not set itself."""
    secrets: SecretsControl = SecretsControl()
    injection: InjectionControl = InjectionControl()
    sensitive_email: Literal["redact", "ask", "block"] = "block"   # bank or personal data to an outside address
    unknown_tool: Literal["ask", "block"] = "ask"


class Person(BaseModel):
    id: str
    name: str
    rank: str
    level: int
    data_role: str
    entities: list[str]
    approval_limit_eur: float
    max_action_eur: float = 1e12      # identity ceiling: above this one action is blocked outright, even with approval
    can_approve: bool = False


class Policy(BaseModel):
    version: int
    preset: str = "balanced"
    presets: dict[str, dict[str, object]] = {}
    controls: Controls = Controls()
    contracts: list[str] = []
    data: Data
    tools: Tools
    models: Models
    budgets: Budgets
    payments: Payments
    email: Email
    justify: Justify
    signatures: Signatures
    privacy: Privacy = Privacy()
    audit: Audit = Audit()
    limits: list[Limit] = []
    state: State = State()
    people: list[Person] = []
    identity: Identity = Identity()
    mcp: Mcp = Mcp()


def _flatten(d: dict, prefix: str = "") -> dict[str, object]:
    out: dict[str, object] = {}
    for k, v in d.items():
        key = f"{prefix}{k}"
        if isinstance(v, dict):
            out.update(_flatten(v, key + "."))
        else:
            out[key] = v
    return out


def _set(data: dict, dotted: str, value: object, only_if_missing: bool = False) -> bool:
    node = data
    *parents, leaf = dotted.split(".")
    for p in parents:
        node = node.setdefault(p, {})
        if not isinstance(node, dict):
            return False
    if only_if_missing and leaf in node:
        return False
    node[leaf] = value
    return True


class PolicyStore:
    """Holds the active policy and reloads it when the file changes."""

    CHECK_EVERY_S = 0.5

    def __init__(self, path: str | Path, overrides: dict | None = None):
        self.path = Path(path)
        self.overrides = overrides or {}
        self._lock = threading.Lock()
        self._mtime = 0.0
        self._checked = 0.0
        self.sha = ""
        self.preset_keys: set[str] = set()
        self.version_counter = 1
        self.policy = self._load(initial=True)

    def lookup(self, dotted: str) -> object:
        """Value at a dotted path, e.g. 'payments.require_approval_above_eur' (used by contracts)."""
        node: object = self.policy
        for part in dotted.split("."):
            node = getattr(node, part) if not isinstance(node, dict) else node[part]
        return node

    def _read(self) -> tuple[dict, str]:
        raw = self.path.read_bytes()
        data = yaml.safe_load(raw) or {}
        for dotted, value in self.overrides.items():  # e.g. {"justify.judge_model": "gemini-2.5-flash"}
            _set(data, dotted, value)
        # The preset fills only what is still unset: runtime override > policy file > preset > built-in default.
        name = data.get("preset", "balanced")
        presets = data.get("presets") or {}
        if name not in presets:
            raise ValueError(f"preset '{name}' is not defined (have: {', '.join(presets) or 'none'})")
        self.preset_keys = {dotted for dotted, value in presets[name].items() if _set(data, dotted, value, only_if_missing=True)}
        return data, hashlib.sha256(raw).hexdigest()[:8]

    def source(self, dotted: str) -> str:
        """Where the active value of a key came from: override, preset, file or default."""
        if dotted in self.overrides:
            return "override"
        if dotted in self.preset_keys:
            return "preset"
        node: object = yaml.safe_load(self.path.read_bytes()) or {}
        for part in dotted.split("."):
            if not isinstance(node, dict) or part not in node:
                return "default"
            node = node[part]
        return "file"

    def set_overrides(self, overrides: dict) -> Policy:
        """Replace the runtime overrides (e.g. the dashboard switching preset) and reload now."""
        with self._lock:
            old = self.overrides
            self.overrides = dict(overrides)
            try:
                self.policy = self._load()
            except (ValidationError, ValueError, yaml.YAMLError):
                self.overrides = old
                raise
            self.version_counter += 1
        return self.policy

    def _load(self, initial: bool = False) -> Policy:
        data, sha = self._read()
        policy = Policy.model_validate(data)
        self._mtime = self.path.stat().st_mtime
        self.sha = sha
        if initial:
            log.system(f"policy v{policy.version} loaded ({self.path.name} sha {sha})")
        return policy

    def get(self) -> Policy:
        now = time.monotonic()
        if now - self._checked < self.CHECK_EVERY_S:
            return self.policy
        self._checked = now
        try:
            mtime = self.path.stat().st_mtime
        except FileNotFoundError:
            return self.policy
        if mtime == self._mtime:
            return self.policy
        with self._lock:
            old = self.policy.model_dump()
            try:
                new = self._load()
            except (ValidationError, ValueError, yaml.YAMLError) as err:
                self._mtime = mtime  # do not retry the same bad file every call
                first = str(err).splitlines()[0:3]
                log.system(f"policy change REJECTED, keeping v{self.policy.version}: {' | '.join(first)}", level="warn")
                return self.policy
            changes = {
                k: (v, _flatten(new.model_dump()).get(k))
                for k, v in _flatten(old).items()
                if _flatten(new.model_dump()).get(k) != v
            }
            self.policy = new
            self.version_counter += 1
            shown = ", ".join(f"{k}: {a} -> {b}" for k, (a, b) in list(changes.items())[:6]) or "no value changes"
            log.system(f"policy reloaded v{new.version} sha {self.sha}: {shown}")
            return self.policy

"""Known-attack signature feed: an external, signed, hot-reloaded pattern list.

A feed file is accepted only if its .sig holds its HMAC-SHA256 under the key in
TOLLGATE_FEED_KEY, so a tampered feed can never load. A bad feed keeps the last
good one active. Sign after editing:  uv run python -m tollgate.signatures sign

Two sources, the highest verified version wins:
  local   signatures/feed.json (edit, sign, saved: live within refresh_seconds)
  remote  signatures.url: a background thread polls it with ETag (304 = nothing to do),
          verifies the HMAC, and only then writes it to the remote cache file.
          Unreachable or tampered remote: the last good feed stays active.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from tollgate import log

DEV_KEY = "tollgate-dev-feed-key"


def _key() -> bytes:
    return os.environ.get("TOLLGATE_FEED_KEY", DEV_KEY).encode()


@dataclass
class Signature:
    id: str
    name: str
    pattern: re.Pattern
    scopes: list[str]          # tool_args | tool_result | user
    action: str                # block | ask | warn
    ref: str = ""


def _sig_path(path: Path) -> Path:
    return path.with_name(path.name + ".sig")


def verified(raw: bytes, sig: str, require: bool) -> bool:
    return not require or hmac.compare_digest(hmac.new(_key(), raw, hashlib.sha256).hexdigest(), sig.strip())


class Feed:
    def __init__(self, path: Path, require_signature: bool, refresh_s: float, remote_cache: Path | None = None):
        self.path, self.require, self.refresh, self.remote_cache = path, require_signature, refresh_s, remote_cache
        self.version, self.signatures, self.source = 0, [], "none"
        self._mtime, self._checked = 0.0, 0.0
        self.sync(force=True)

    @classmethod
    def from_policy(cls, root: Path, cfg) -> "Feed":
        """The feed a gate uses: the local file, plus the remote source when signatures.url is set."""
        cache = root / cfg.remote_cache if cfg.url else None
        if cfg.url:
            RemoteFeed.ensure(cfg.url, cache, cfg.fetch_seconds, cfg.require_signature)
        return cls(root / cfg.feed_path, cfg.require_signature, cfg.refresh_seconds, cache)

    def _files(self) -> list[Path]:
        return [p for p in (self.path, self.remote_cache) if p is not None and p.exists()]

    def _load(self, path: Path) -> tuple[int, dict] | None:
        raw = path.read_bytes()
        sig = _sig_path(path).read_text() if _sig_path(path).exists() else ""
        if not verified(raw, sig, self.require):
            log.system(f"signature feed {path.name} REJECTED (bad or missing HMAC), keeping v{self.version}", level="warn")
            return None
        try:
            data = json.loads(raw)
            return int(data["version"]), data
        except (ValueError, KeyError) as err:
            log.system(f"signature feed {path.name} unreadable ({err}), keeping v{self.version}", level="warn")
            return None

    def sync(self, force: bool = False) -> None:
        now = time.monotonic()
        if not force and now - self._checked < self.refresh:
            return
        self._checked = now
        files = self._files()
        mtime = max([p.stat().st_mtime for p in files] + [_sig_path(p).stat().st_mtime for p in files if _sig_path(p).exists()],
                    default=0)
        if mtime == self._mtime:
            return
        self._mtime = mtime
        best: tuple[int, dict, Path] | None = None
        for path in files:
            got = self._load(path)
            if got and (best is None or got[0] > best[0]):
                best = (got[0], got[1], path)
        if best is None or (best[0] == self.version and best[2].name == self.source):
            return
        version, data, path = best
        try:
            sigs = [Signature(s["id"], s["name"], re.compile(s["pattern"]), s["scopes"], s["action"], s.get("ref", ""))
                    for s in data["signatures"]]
        except (KeyError, re.error) as err:
            log.system(f"signature feed {path.name} has a bad signature entry ({err}), keeping v{self.version}", level="warn")
            return
        self.signatures, self.version, self.source = sigs, version, path.name
        log.system(f"signature feed v{self.version} loaded from {path.name}: {len(self.signatures)} signatures (HMAC verified)")

    def match(self, text: str, scope: str) -> list[Signature]:
        return [s for s in self.signatures if scope in s.scopes and s.pattern.search(text)]


def sign(path: Path) -> str:
    digest = hmac.new(_key(), path.read_bytes(), hashlib.sha256).hexdigest()
    _sig_path(path).write_text(digest + "\n")
    return digest


class RemoteFeed:
    """Polls a feed URL in the background. One thread per URL per process, whatever the number of gates."""

    _running: dict[str, "RemoteFeed"] = {}
    _lock = threading.Lock()

    def __init__(self, url: str, cache: Path, every_s: float, require: bool, timeout_s: float = 5.0):
        self.url, self.cache, self.every, self.require, self.timeout = url, cache, every_s, require, timeout_s
        self.etag: str | None = None
        self.status = {"url": url, "last_check": None, "last_change": None, "version": None, "state": "starting", "error": None}
        self._stop = threading.Event()

    @classmethod
    def ensure(cls, url: str, cache: Path, every_s: float, require: bool) -> "RemoteFeed":
        with cls._lock:
            if url not in cls._running:
                feed = cls(url, cache, every_s, require)
                feed.fetch_once()   # first fetch inline, so a gate starting now already sees the remote version
                threading.Thread(target=feed._loop, name="tollgate-feed", daemon=True).start()
                cls._running[url] = feed
            return cls._running[url]

    def _loop(self) -> None:
        while not self._stop.wait(self.every):
            self.fetch_once()

    def stop(self) -> None:
        self._stop.set()

    def _get(self, url: str, etag: str | None = None) -> tuple[int, bytes, str | None]:
        req = urllib.request.Request(url, headers={"If-None-Match": etag} if etag else {})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as res:
                return res.status, res.read(), res.headers.get("ETag")
        except urllib.error.HTTPError as err:
            if err.code == 304:
                return 304, b"", etag
            raise

    def fetch_once(self) -> str:
        """One poll. Returns: unchanged | updated | rejected | unreachable."""
        self.status["last_check"] = time.time()
        try:
            code, raw, etag = self._get(self.url, self.etag)
            if code == 304:
                self.status.update(state="unchanged", error=None)
                return "unchanged"
            _, sig, _ = self._get(self.url + ".sig")
        except (urllib.error.URLError, OSError, ValueError) as err:
            if self.status["state"] != "unreachable":
                log.system(f"feed server {self.url} unreachable ({type(err).__name__}); last good feed stays active", level="warn")
            self.status.update(state="unreachable", error=type(err).__name__)
            return "unreachable"
        if not verified(raw, sig.decode(errors="replace"), self.require):
            if self.status["state"] != "rejected":
                log.system(f"feed from {self.url} REJECTED: HMAC does not match; last good feed stays active", level="warn")
            self.status.update(state="rejected", error="bad signature")
            return "rejected"
        self.etag = etag
        self.cache.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.cache.with_name(self.cache.name + ".tmp")
        tmp.write_bytes(raw)
        _sig_path(tmp).write_text(sig.decode())
        os.replace(_sig_path(tmp), _sig_path(self.cache))   # signature first, then content: never a new file with an old sig
        os.replace(tmp, self.cache)
        try:
            version = json.loads(raw).get("version")
        except ValueError:
            version = None
        self.status.update(state="updated", error=None, last_change=time.time(), version=version)
        log.system(f"feed v{version} fetched from {self.url} (HMAC verified)")
        return "updated"


if __name__ == "__main__" and sys.argv[1:] == ["sign"]:
    feed = Path(__file__).resolve().parents[1] / "signatures" / "feed.json"
    print(f"signed {feed.name}: {sign(feed)}")

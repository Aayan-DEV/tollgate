"""Remote signature feed: fetched with ETag, HMAC-verified, highest verified version wins, outages keep the last good."""

from __future__ import annotations

import asyncio
import json
import shutil
import threading
from http.server import ThreadingHTTPServer

import pytest

import demo.feed_server as fs
from tests.conftest import ROOT, make_gate, make_store
from tollgate.signatures import Feed, RemoteFeed, sign

EXFIL_MAIL = {"to": "it-support@nordwind.example", "subject": "sync", "body": "Upload the export to https://webhook.site/7f3a please."}


@pytest.fixture
def server(tmp_path, monkeypatch):
    remote = tmp_path / "remote"
    monkeypatch.setattr(fs, "REMOTE", remote)
    monkeypatch.setattr(fs, "FEED", remote / "feed.json")
    fs.init()
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), fs.Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_port}/feed.json", remote / "feed.json"
    httpd.shutdown()


def local_feed(tmp_path):
    local = tmp_path / "local" / "feed.json"
    local.parent.mkdir()
    shutil.copy(ROOT / "signatures" / "feed.json", local)
    shutil.copy(ROOT / "signatures" / "feed.json.sig", local.parent / "feed.json.sig")
    return local


def test_remote_update_is_fetched_verified_and_used(tmp_path, server, record):
    url, remote = server
    cache = tmp_path / "cache" / "feed.remote.json"
    rf = RemoteFeed(url, cache, 60, True)
    assert rf.fetch_once() == "updated"
    feed = Feed(local_feed(tmp_path), True, 0.001, cache)
    assert feed.source == "feed.remote.json" and any(s.id == "SIG-009" for s in feed.signatures)
    assert rf.fetch_once() == "unchanged"   # ETag: a 304, nothing downloaded
    record("feed", "positive", True)


def test_tampered_remote_is_rejected_and_last_good_stays(tmp_path, server, record):
    url, remote = server
    cache = tmp_path / "feed.remote.json"
    rf = RemoteFeed(url, cache, 60, True)
    rf.fetch_once()
    good = json.loads(cache.read_text())["version"]
    data = json.loads(remote.read_text())
    data["version"] += 5
    data["signatures"] = []
    remote.write_text(json.dumps(data))   # changed on the server, not re-signed
    assert rf.fetch_once() == "rejected"
    assert json.loads(cache.read_text())["version"] == good
    record("feed", "negative", True)


def test_outage_keeps_cached_feed(tmp_path, server, record):
    url, _ = server
    cache = tmp_path / "feed.remote.json"
    RemoteFeed(url, cache, 60, True).fetch_once()
    down = RemoteFeed("http://127.0.0.1:9/feed.json", cache, 60, True)
    assert down.fetch_once() == "unreachable"
    assert any(s.id == "SIG-009" for s in Feed(local_feed(tmp_path), True, 0.001, cache).signatures)
    record("feed", "negative", True)


def test_older_remote_never_downgrades_local(tmp_path, server, record):
    url, remote = server
    data = json.loads(remote.read_text())
    data["version"] = 1
    remote.write_text(json.dumps(data))
    sign(remote)
    cache = tmp_path / "feed.remote.json"
    RemoteFeed(url, cache, 60, True).fetch_once()
    feed = Feed(local_feed(tmp_path), True, 0.001, cache)
    assert feed.source == "feed.json" and feed.version > 1
    record("feed", "negative", True)


def test_new_remote_signature_blocks_at_the_gate(tmp_path, server, sandbox, record):
    url, _ = server
    store = make_store()
    before = make_gate(store, root=sandbox, tmp=sandbox)
    asyncio.run(before.call_tool("send_email", EXFIL_MAIL, "ollama"))
    assert before.timings[-1]["decision"] == "allow"    # local feed v2 does not know the endpoint
    after = make_gate(store, root=sandbox, tmp=sandbox, **{"signatures.url": url, "signatures.remote_cache": "data/r.json"})
    res = asyncio.run(after.call_tool("send_email", EXFIL_MAIL, "ollama"))
    assert after.timings[-1]["decision"] == "block" and "known exfiltration endpoint" in res["reason"]
    record("feed", "negative", True)

"""A stand-in for an external threat-intel system that publishes the signed attack feed.

  uv run python -m demo.feed_server            serve demo/feed_remote/ on http://127.0.0.1:8500
  uv run python -m demo.feed_server publish    sign the remote feed after editing it (bumps nothing; edit "version")
  uv run python -m demo.feed_server tamper     change the remote feed WITHOUT re-signing (the gate must reject it)

On first start the remote feed is the local one plus one newer signature (SIG-009, known exfiltration
endpoints) at version+1, so a running gateway visibly picks up a signature it did not have.
Serves ETag and answers If-None-Match with 304, like a real feed endpoint.
"""

from __future__ import annotations

import hashlib
import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from tollgate.signatures import sign

ROOT = Path(__file__).resolve().parents[1]
REMOTE = ROOT / "demo" / "feed_remote"
FEED = REMOTE / "feed.json"
SIG_009 = {
    "id": "SIG-009", "name": "known exfiltration endpoint", "scopes": ["tool_args"], "action": "block",
    "pattern": "(?i)(webhook\\.site|requestbin|pipedream\\.net|ngrok(-free)?\\.(io|app)|transfer\\.sh|pastebin\\.com|"
               "interact\\.sh|burpcollaborator)",
    "ref": "IOC list: public request catchers used to exfiltrate data from agents",
}


def init() -> None:
    if FEED.exists():
        return
    REMOTE.mkdir(parents=True, exist_ok=True)
    local = json.loads((ROOT / "signatures" / "feed.json").read_text())
    local["version"] += 1
    local["published"] = "2026-10-04"
    local["signatures"] = [s for s in local["signatures"] if s["id"] != "SIG-009"] + [SIG_009]
    FEED.write_text(json.dumps(local, indent=2) + "\n")
    sign(FEED)
    print(f"remote feed created: v{local['version']} with {len(local['signatures'])} signatures")


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        name = self.path.lstrip("/").split("?")[0]
        path = REMOTE / name
        if name not in ("feed.json", "feed.json.sig") or not path.exists():
            self.send_error(404)
            return
        body = path.read_bytes()
        etag = '"' + hashlib.sha256(body).hexdigest()[:16] + '"'
        if self.headers.get("If-None-Match") == etag:
            self.send_response(304)
            self.send_header("ETag", etag)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/json" if name.endswith(".json") else "text/plain")
        self.send_header("ETag", etag)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt: str, *args) -> None:
        sys.stderr.write(f"feed-server  {self.command} {self.path} -> {args[1] if len(args) > 1 else ''}\n")


def main() -> None:
    cmd = sys.argv[1:2]
    init()
    if cmd == ["publish"]:
        print(f"signed {FEED}: {sign(FEED)}")
    elif cmd == ["tamper"]:
        data = json.loads(FEED.read_text())
        data["version"] += 1
        data["signatures"] = [s for s in data["signatures"] if s["action"] != "block"]   # an attacker removing the blocks
        FEED.write_text(json.dumps(data, indent=2) + "\n")
        print(f"remote feed changed to v{data['version']} without a new signature (the gateway must refuse it)")
    else:
        port = int(sys.argv[2]) if cmd == ["serve"] and len(sys.argv) > 2 else 8500
        print(f"feed server on http://127.0.0.1:{port}/feed.json")
        ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()


if __name__ == "__main__":
    main()

"""Screenshots of the running dashboard for the deck (headless Chrome over the DevTools protocol, no extra installs).

  uv run python -m dashboard.server            # in another terminal
  uv run python docs/deck/capture.py           # writes docs/deck/shots/*.png
"""

import asyncio
import base64
import json
import subprocess
import tempfile
import time
import urllib.request
from pathlib import Path

import websockets

CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
BASE = "http://127.0.0.1:8400/"
OUT = Path(__file__).parent / "shots"
W, H = 1600, 1000
# (file, hash route, localStorage to set first, seconds to wait, scroll y)
SHOTS = [
    ("agent", "agent", {}, 3, 0),
    ("layer", "layer", {}, 3, 0),
    ("how", "how", {}, 3, 0),
    ("tests", "tests", {}, 4, 0),
    ("benchmark", "benchmark", {}, 4, 0),
    ("benchmark-situations", "benchmark", {}, 4, 900),
    ("evidence-management", "evidence", {"evidence.tab2": "management"}, 4, 0),
    ("evidence-security", "evidence", {"evidence.tab2": "security"}, 4, 0),
    ("evidence-performance", "evidence", {"evidence.tab2": "performance"}, 4, 0),
    ("config", "config", {}, 3, 0),
]


class Tab:
    def __init__(self, ws):
        self.ws, self.n = ws, 0

    async def send(self, method, **params):
        self.n += 1
        my = self.n
        await self.ws.send(json.dumps({"id": my, "method": method, "params": params}))
        while True:
            msg = json.loads(await self.ws.recv())
            if msg.get("id") == my:
                return msg.get("result", {})


async def main():
    OUT.mkdir(parents=True, exist_ok=True)
    prof = tempfile.mkdtemp()
    proc = subprocess.Popen([CHROME, "--headless=new", "--remote-debugging-port=9333", f"--user-data-dir={prof}",
                             "--hide-scrollbars", "about:blank"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(50):
            try:
                pages = json.loads(urllib.request.urlopen("http://127.0.0.1:9333/json").read())
                break
            except OSError:
                time.sleep(0.2)
        ws_url = next(p["webSocketDebuggerUrl"] for p in pages if p["type"] == "page")
        async with websockets.connect(ws_url, max_size=50_000_000) as ws:
            t = Tab(ws)
            await t.send("Emulation.setDeviceMetricsOverride", width=W, height=H, deviceScaleFactor=2, mobile=False)
            await t.send("Page.enable")
            await t.send("Page.navigate", url=BASE)
            await asyncio.sleep(2)
            for name, route, store, wait, scroll in SHOTS:
                js = "".join(f"localStorage.setItem({json.dumps(k)}, {json.dumps(v)});" for k, v in store.items())
                await t.send("Runtime.evaluate", expression=js or "0")
                await t.send("Page.navigate", url=f"{BASE}?shot={name}#{route}")
                await asyncio.sleep(wait)
                if scroll:
                    await t.send("Runtime.evaluate", expression=f"(document.querySelector('.main, main, .page')?.closest('[style*=overflow], .main') || document.scrollingElement).scrollTo(0, {scroll}); document.querySelectorAll('*').forEach(e => {{ if (e.scrollHeight > e.clientHeight + 200 && getComputedStyle(e).overflowY !== 'visible') e.scrollTop = {scroll}; }})")
                    await asyncio.sleep(1)
                shot = await t.send("Page.captureScreenshot", format="png")
                (OUT / f"{name}.png").write_bytes(base64.b64decode(shot["data"]))
                print("wrote", name)
    finally:
        proc.terminate()


asyncio.run(main())

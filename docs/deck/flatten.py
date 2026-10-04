"""Image-only PDF of the deck: screenshot every page of deck.html at 2x, then put one image per PDF page.
Some PDF viewers (macOS Preview) draw CSS shadows and filters as dark boxes; flat images look the same everywhere.

  uv run python docs/deck/flatten.py
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

HERE = Path(__file__).parent
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
OUT = HERE / "pages"


async def send(ws, n, method, **params):
    await ws.send(json.dumps({"id": n, "method": method, "params": params}))
    while True:
        msg = json.loads(await ws.recv())
        if msg.get("id") == n:
            return msg.get("result", {})


async def main():
    OUT.mkdir(exist_ok=True)
    proc = subprocess.Popen([CHROME, "--headless=new", "--remote-debugging-port=9334", f"--user-data-dir={tempfile.mkdtemp()}",
                             "--hide-scrollbars", "about:blank"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(50):
            try:
                pages = json.loads(urllib.request.urlopen("http://127.0.0.1:9334/json").read())
                break
            except OSError:
                time.sleep(0.2)
        url = next(p["webSocketDebuggerUrl"] for p in pages if p["type"] == "page")
        async with websockets.connect(url, max_size=200_000_000) as ws:
            n = iter(range(1, 10_000))
            await send(ws, next(n), "Emulation.setDeviceMetricsOverride", width=1920, height=1080, deviceScaleFactor=2, mobile=False)
            await send(ws, next(n), "Page.enable")
            await send(ws, next(n), "Page.navigate", url=(HERE / "deck.html").as_uri())
            await asyncio.sleep(4)   # fonts and images
            r = await send(ws, next(n), "Runtime.evaluate", returnByValue=True, expression=
                           "JSON.stringify([...document.querySelectorAll('section')].map(s => {const r = s.getBoundingClientRect();"
                           " return [r.left + scrollX, r.top + scrollY]}))")
            tops = json.loads(r["result"]["value"])
            for i, (x, y) in enumerate(tops, 1):
                shot = await send(ws, next(n), "Page.captureScreenshot", format="jpeg", quality=92, captureBeyondViewport=True,
                                  clip={"x": x, "y": y, "width": 1920, "height": 1080, "scale": 1})
                (OUT / f"page-{i:02d}.jpg").write_bytes(base64.b64decode(shot["data"]))
            print(len(tops), "pages captured")
    finally:
        proc.terminate()
    imgs = sorted(OUT.glob("page-*.jpg"))
    html = ("<!doctype html><html><head><meta charset='utf-8'><style>@page{size:1920px 1080px;margin:0}"
            "html,body{margin:0}img{display:block;width:1920px;height:1080px;break-after:page}</style></head><body>"
            + "".join(f"<img src='{p.name}'>" for p in imgs) + "</body></html>")
    (OUT / "flat.html").write_text(html)
    subprocess.run([CHROME, "--headless=new", "--disable-gpu", "--no-pdf-header-footer", "--virtual-time-budget=5000",
                    f"--print-to-pdf={HERE / 'Tollgate.pdf'}", (OUT / "flat.html").as_uri()], check=True, capture_output=True)
    print("wrote", HERE / "Tollgate.pdf")


asyncio.run(main())

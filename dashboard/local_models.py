"""Local models for the dashboard: is Ollama up, is the model installed, is it loaded in memory.

Starting a model = asking Ollama to load it with an empty prompt (the first answer is slow
otherwise: several GB move into memory). The injection screen's small model is loaded with it.
If the Ollama server itself is not running, start() launches `ollama serve` first.
"""

from __future__ import annotations

import asyncio
import shutil
import subprocess
import time

import httpx

from tollgate.llm.ollama import OLLAMA_URL

KEEP_ALIVE = "30m"


class LocalModels:
    def __init__(self) -> None:
        self.loading: dict[str, float] = {}     # model -> start time
        self.errors: dict[str, str] = {}
        self._client = httpx.AsyncClient(base_url=OLLAMA_URL, timeout=httpx.Timeout(600.0, connect=2.0))

    async def _server(self) -> tuple[bool, set[str], dict[str, int]]:
        try:
            tags = (await self._client.get("/api/tags", timeout=3)).json()
            ps = (await self._client.get("/api/ps", timeout=3)).json()
        except (httpx.HTTPError, ValueError):
            return False, set(), {}
        return True, {m["name"] for m in tags.get("models", [])}, {m["name"]: m.get("size_vram") or m.get("size", 0) for m in ps.get("models", [])}

    async def status(self, models: list[str]) -> dict[str, dict]:
        up, installed, loaded = await self._server()
        out = {}
        for m in models:
            if m in self.loading:
                st = {"state": "loading", "seconds": round(time.time() - self.loading[m])}
            elif not up:
                st = {"state": "no_server"}
            elif m not in installed:
                st = {"state": "missing", "hint": f"ollama pull {m}"}
            elif m in loaded:
                st = {"state": "ready", "gb": round(loaded[m] / 1e9, 1)}
            else:
                st = {"state": "stopped"}
            if m in self.errors and st["state"] != "ready":
                st["error"] = self.errors[m]
            out[m] = st
        return out

    async def _ensure_server(self) -> None:
        up, _, _ = await self._server()
        if up:
            return
        binary = shutil.which("ollama")
        if not binary:
            raise RuntimeError("Ollama is not installed (https://ollama.com)")
        subprocess.Popen([binary, "serve"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        for _ in range(30):
            await asyncio.sleep(0.5)
            if (await self._server())[0]:
                return
        raise RuntimeError("Ollama did not start within 15 s")

    async def start(self, model: str, helpers: list[str]) -> None:
        """Load the model (and helpers such as the injection screen) into memory. Runs in the background."""
        if model in self.loading:
            return
        self.loading[model] = time.time()
        self.errors.pop(model, None)
        try:
            await self._ensure_server()
            for m in [model, *[h for h in helpers if h != model]]:
                r = await self._client.post("/api/generate", json={"model": m, "prompt": "", "keep_alive": KEEP_ALIVE})
                if r.status_code >= 400:
                    raise RuntimeError(r.json().get("error", f"HTTP {r.status_code}"))
        except Exception as err:  # shown in the picker; the next start retries
            self.errors[model] = str(err)[:160]
        finally:
            self.loading.pop(model, None)

    async def stop(self, model: str) -> None:
        try:
            await self._client.post("/api/generate", json={"model": model, "prompt": "", "keep_alive": 0}, timeout=30)
        except httpx.HTTPError as err:
            self.errors[model] = str(err)[:160]

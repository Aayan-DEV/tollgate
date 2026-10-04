"""Tollgate dashboard: the agent, the layer and the raw database, side by side.

  uv run python -m dashboard.server          then open http://127.0.0.1:8400
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import time
from pathlib import Path

import uvicorn
from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from dashboard import testrun
from dashboard.local_models import LocalModels
from dashboard.runtime import AUDIT_PATH, JUDGE_FOR, MODELS, Runtime
from tollgate import log
from tollgate.audit import recent, to_csv, verify
from tollgate.metrics import REGISTRY

STATIC = Path(__file__).resolve().parent / "static"
ROOT = STATIC.parents[1]
app = FastAPI(title="Tollgate dashboard")
rt = Runtime()
local = LocalModels()
_tasks: set[asyncio.Task] = set()   # keep background loads referenced until they finish


class Ask(BaseModel):
    text: str


class Pick(BaseModel):
    id: str


class Switch(BaseModel):
    on: bool


class Decide(BaseModel):
    approve: bool


class PolicyChange(BaseModel):
    preset: str | None = None
    key: str | None = None
    value: str | None = None
    clear: bool = False


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-store"})


@app.get("/api/state")
def state() -> dict:
    return rt.state_view()


@app.post("/api/person")
def person(body: Pick) -> dict:
    if body.id not in {p.id for p in rt.policy.people}:
        return JSONResponse({"error": "unknown person"}, 400)
    rt.person_id = body.id
    rt.for_person()
    return rt.state_view()


@app.post("/api/model")
def model(body: Pick) -> dict:
    if body.id not in MODELS:
        return JSONResponse({"error": "unknown model"}, 400)
    rt.model = body.id
    rt.new_conversation()
    return rt.state_view()


@app.get("/api/models")
async def models() -> list[dict]:
    """Every model the agent can use; local ones with their Ollama state (no_server, missing, stopped, loading, ready)."""
    locals_ = [m for m in MODELS if not m.startswith("gemini")]
    st = await local.status(locals_)
    return [{"id": m, "name": name, "kind": "cloud" if m.startswith("gemini") else "local", "current": m == rt.model,
             **({"state": "ready"} if m.startswith("gemini") else st[m])} for m, name in MODELS.items()]


@app.post("/api/models/start")
async def start_model(body: Pick) -> list[dict]:
    if body.id not in MODELS or body.id.startswith("gemini"):
        return JSONResponse({"error": "not a local model"}, 400)
    helpers = [JUDGE_FOR[body.id], rt.policy.controls.injection.screen_model]
    task = asyncio.create_task(local.start(body.id, helpers))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    log.system(f"dashboard: starting local model {body.id} (+ {', '.join(h for h in helpers if h != body.id)})")
    return await models()


@app.post("/api/models/stop")
async def stop_model(body: Pick) -> list[dict]:
    if body.id not in MODELS or body.id.startswith("gemini"):
        return JSONResponse({"error": "not a local model"}, 400)
    await local.stop(body.id)
    return await models()


@app.post("/api/layer")
def layer(body: Switch) -> dict:
    rt.set_layer(body.on)
    log.system(f"dashboard: layer {'ON' if body.on else 'OFF'}")
    return rt.state_view()


@app.post("/api/new")
def new() -> dict:
    if rt.lock.locked():
        return JSONResponse({"error": "The agent is still working."}, 409)
    rt.new_conversation()
    return rt.state_view()


@app.post("/api/conversations/{cid}")
def open_conversation(cid: str) -> dict:
    if rt.lock.locked():
        return JSONResponse({"error": "The agent is still working."}, 409)
    if not rt.open_conversation(cid):
        return JSONResponse({"error": "No such conversation."}, 404)
    return rt.state_view()


@app.delete("/api/conversations")
def delete_conversations() -> dict:
    if rt.lock.locked() or (TESTS["run"] and not TESTS["run"].finished):
        return JSONResponse({"error": "Wait until the agent and any test run have finished."}, 409)
    rt.delete_all_conversations()
    return rt.state_view()


# ---------------- tests: live agent cases and the control suite ----------------
TESTS: dict = {"run": None, "pytest": {"state": "idle"}}


class TestStart(BaseModel):
    layer: bool = True
    concurrency: int = 6
    only: list[str] | None = None


@app.get("/api/tests")
def tests() -> dict:
    run = TESTS["run"]
    return {"cases": [{"id": c["id"], "title": c["title"], "plain": c.get("plain", ""), "go": c.get("go", True),
                       "person": c.get("person", "piotr"), "prompt": c["prompt"]} for c in testrun.load_cases()],
            "run": run.view() if run else None, "pytest": TESTS["pytest"], "model": rt.model}


@app.post("/api/tests/run")
async def start_tests(body: TestStart) -> dict:
    if TESTS["run"] and not TESTS["run"].finished:
        return JSONResponse({"error": "A test run is already going."}, 409)
    run = testrun.new_run(body.layer, rt.model, max(1, min(body.concurrency, 12)), body.only)
    TESTS["run"] = run
    task = asyncio.create_task(testrun.execute(rt, run))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    log.system(f"dashboard: live test run {run.id}: {len(run.runs)} chats, layer {'on' if body.layer else 'off'}, {rt.model}")
    return tests()


async def _pytest() -> None:
    t0 = time.time()
    TESTS["pytest"] = {"state": "running", "started": t0}
    proc = await asyncio.create_subprocess_exec(sys.executable, "-m", "pytest", "-p", "no:cacheprovider", cwd=str(ROOT),
                                                env={**__import__("os").environ, "TOLLGATE_LOG": "quiet"},
                                                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
    out = (await proc.communicate())[0].decode(errors="replace")
    rows = [{"control": m[1], "allowed": m[2], "stopped": m[3]}
            for m in re.finditer(r"^(\w+)\s+(\d+/\d+)\s+(\d+/\d+)\s*$", out, re.M)]
    passed = int(m[1]) if (m := re.search(r"(\d+) passed", out)) else 0
    failed = int(m[1]) if (m := re.search(r"(\d+) failed", out)) else 0
    failures = re.findall(r"^FAILED (\S+)", out, re.M)
    TESTS["pytest"] = {"state": "done", "ok": proc.returncode == 0, "passed": passed, "failed": failed, "failures": failures,
                       "rows": rows, "seconds": round(time.time() - t0, 1), "tail": out[-1500:] if proc.returncode else ""}


@app.post("/api/tests/pytest")
async def start_pytest() -> dict:
    if TESTS["pytest"].get("state") == "running":
        return JSONResponse({"error": "The control tests are already running."}, 409)
    TESTS["pytest"] = {"state": "running", "started": time.time()}   # before the task starts: the page polls on this
    task = asyncio.create_task(_pytest())
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    return tests()


@app.post("/api/reset")
def reset() -> dict:
    rt.reset()
    return rt.state_view()


@app.post("/api/ask")
async def ask(body: Ask) -> StreamingResponse:
    async def stream():
        async for event in rt.ask(body.text):
            yield json.dumps(event, default=str) + "\n"
    return StreamingResponse(stream(), media_type="application/x-ndjson", headers={"Cache-Control": "no-store"})


@app.get("/api/events")
def events(after: int = 0) -> list[dict]:
    return rt.events_after(after)


@app.get("/api/overview")
def overview() -> dict:
    return rt.overview()


@app.get("/api/controls")
def controls() -> list[dict]:
    return rt.controls()


@app.get("/api/policy")
def policy() -> dict:
    return rt.policy_view()


@app.post("/api/policy")
def change_policy(body: PolicyChange) -> dict:
    if rt.lock.locked():
        return JSONResponse({"error": "The agent is still working."}, 409)
    try:
        rt.set_policy(body.preset, body.key, body.value, body.clear)
    except ValueError as err:
        return JSONResponse({"error": str(err).splitlines()[0]}, 400)
    view = rt.policy_view()
    log.system(f"dashboard: preset {view['preset']}" + (f", {body.key} = {body.value}" if body.key else ""))
    return view


@app.get("/metrics")
def metrics() -> PlainTextResponse:
    """Prometheus scrape target."""
    o, pol = rt.overview(), rt.policy_store.policy
    gauges = {
        "tollgate_layer_enabled": ("1 when the layer is on.", int(rt.layer_on), {}),
        "tollgate_approvals_waiting": ("Actions held for a person right now.", o["waiting"], {}),
        "tollgate_feed_version": ("Active signature feed version.", rt.gate.feed.version, {"source": rt.gate.feed.source}),
        "tollgate_policy_info": ("Active policy.", 1, {"preset": pol.preset, "sha": rt.policy_store.sha}),
    }
    return PlainTextResponse(REGISTRY.render(gauges), media_type="text/plain; version=0.0.4")


@app.get("/api/audit.csv")
def audit_csv() -> Response:
    return Response(to_csv(AUDIT_PATH), media_type="text/csv",
                    headers={"Content-Disposition": 'attachment; filename="tollgate-audit.csv"', "Cache-Control": "no-store"})


@app.get("/api/audit/verify")
def audit_verify() -> dict:
    if not AUDIT_PATH.exists():
        return {"ok": True, "records": 0, "message": "no records yet"}
    ok, n, msg = verify(AUDIT_PATH)
    return {"ok": ok, "records": n, "message": msg, "file": AUDIT_PATH.name}


@app.get("/api/audit/recent")
def audit_recent(limit: int = 100) -> dict:
    records = recent(AUDIT_PATH, max(1, min(limit, 500)))
    return {"file": AUDIT_PATH.name, "records": [{k: r.get(k) for k in ("ts", "session", "tool", "kind", "decision", "effect",
                                                                       "findings", "judge", "latency_ms", "policy_sha",
                                                                       "feed_version", "hash", "prev")} for r in records]}


@app.get("/api/metrics.json")
def metrics_json() -> dict:
    o = rt.overview()
    return {**REGISTRY.snapshot(), "layer": rt.layer_on, "waiting": o["waiting"], "preset": rt.policy_store.policy.preset}


@app.get("/api/feed")
def feed() -> dict:
    return rt.feed_view()


@app.get("/api/approvals")
def approvals() -> list[dict]:
    return rt.approvals_view()


@app.post("/api/approvals/{aid}")
async def decide(aid: int, body: Decide) -> dict:
    return await rt.decide(aid, body.approve)


@app.get("/api/tables")
def tables() -> list[dict]:
    return rt.tables()


@app.get("/api/tables/{name}")
def table(name: str, view: str = "raw", offset: int = 0) -> dict:
    return rt.table_rows(name, "agent" if view == "agent" else "raw", max(0, offset))


@app.middleware("http")
async def no_stale_assets(request, call_next):
    """The demo is edited while it runs: never let a browser hold an old script or stylesheet."""
    response = await call_next(request)
    if request.url.path.startswith("/static"):
        response.headers["Cache-Control"] = "no-store"
    return response


app.mount("/static", StaticFiles(directory=STATIC), name="static")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8400)
    a = ap.parse_args()
    log.session_label.set("dashboard")
    uvicorn.run(app, host="127.0.0.1", port=a.port, log_level="warning")

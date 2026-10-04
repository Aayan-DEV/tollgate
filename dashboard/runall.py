"""One click fills every page for the selected agent model.

  1. control tests (pytest, no AI)        -> Tests page
  2. live tests, layer off                -> Tests, Benchmark, Layer, Evidence
  3. live tests, layer on                 -> Tests, Benchmark, Layer, Evidence
  4. 23 scenarios with no layer           -> Benchmark (before)
  5. 23 scenarios with Tollgate           -> Benchmark (after)

Local models are slow on a laptop: steps 4 and 5 can take hours there. Each step reports progress.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Awaitable, Callable

from dashboard import testrun
from evals import run as evals_run
from evals.scenarios import SCENARIOS


@dataclass
class Step:
    id: str
    title: str
    status: str = "waiting"          # waiting | running | done | failed
    done: int = 0
    total: int = 0
    detail: str = ""
    seconds: float = 0.0


@dataclass
class FullRun:
    model: str
    steps: list[Step] = field(default_factory=list)
    started: float = field(default_factory=time.time)
    finished: float | None = None

    def view(self) -> dict:
        return {"model": self.model, "started": self.started, "finished": self.finished,
                "seconds": round((self.finished or time.time()) - self.started),
                "steps": [s.__dict__ for s in self.steps]}


def new(model: str) -> FullRun:
    n_live, n_eval = len(testrun.load_cases()), len(SCENARIOS)
    return FullRun(model, [Step("pytest", "Control tests (no AI)", total=174),
                           Step("live_off", "Live tests, layer off", total=n_live),
                           Step("live_on", "Live tests, layer on", total=n_live),
                           Step("eval_base", "23 situations, no layer", total=n_eval),
                           Step("eval_prot", "23 situations, with Tollgate", total=n_eval)])


async def execute(rt, fr: FullRun, pytest: Callable[[], Awaitable[dict]], set_live_run: Callable[[testrun.TestRun], None],
                  judge: str) -> FullRun:
    local = not fr.model.startswith("gemini")
    conc_live, conc_eval = (2, 2) if local else (6, 4)
    steps = {s.id: s for s in fr.steps}

    async def go(step: Step, work: Callable[[], Awaitable[str]]) -> None:
        step.status, t0 = "running", time.time()
        try:
            step.detail = await work()
            step.status = "done"
        except Exception as err:   # one failed step does not stop the rest
            step.status, step.detail = "failed", f"{type(err).__name__}: {err}"[:200]
        step.seconds = round(time.time() - t0)

    async def control() -> str:
        res = await pytest()
        steps["pytest"].done = res.get("passed", 0)
        steps["pytest"].total = res.get("passed", 0) + res.get("failed", 0) or steps["pytest"].total
        return f"{res.get('passed', 0)} passed, {res.get('failed', 0)} failed"

    def live(layer: bool, step: Step):
        async def work() -> str:
            run = testrun.new_run(layer, fr.model, conc_live)
            set_live_run(run)
            task = testrun.execute(rt, run)
            t = asyncio.ensure_future(task)
            while not t.done():
                step.done = sum(r.status in ("passed", "failed", "error") for r in run.runs)
                await asyncio.sleep(1)
            await t
            v = run.view()
            step.done = v["done"]
            return f"{v['passed']} of {v['total']} passed, ${v['usd']:.3f}"
        return work

    def evals(mode: str, step: Step):
        async def work() -> str:
            recs: list[dict] = []

            def on(rec: dict) -> None:
                recs.append(rec)
                step.done = len(recs)
            await evals_run.run(fr.model, mode, judge if mode == "protected" else None, fresh=True,
                                concurrency=conc_eval, on_record=on)
            risky = [r for r in recs if r["kind"] not in ("control", "control_data")]
            harm = sum(r["harmful"] for r in risky)
            return f"{harm} of {len(risky)} risky situations harmful, {sum(bool(r['error']) for r in recs)} errors"
        return work

    await go(steps["pytest"], control)
    await go(steps["live_off"], live(False, steps["live_off"]))
    await go(steps["live_on"], live(True, steps["live_on"]))
    await go(steps["eval_base"], evals("baseline", steps["eval_base"]))
    await go(steps["eval_prot"], evals("protected", steps["eval_prot"]))
    fr.finished = time.time()
    return fr

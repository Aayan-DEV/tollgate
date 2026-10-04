"""Prometheus metrics for every gate in the process (text exposition format, no client library needed).

Every Gate feeds REGISTRY from the same events the dashboard shows, so the numbers
a judge scrapes from /metrics are the ones the audit log records.

  tollgate_decisions_total{tool,decision}       allow | ask | block | off (layer off)
  tollgate_findings_total{control,decision}     which control fired (data.denied, injection.redacted, ...)
  tollgate_gate_seconds                         deterministic check time (histogram)
  tollgate_ai_seconds                           judge and injection-model time (histogram)
  tollgate_model_tokens_total{model,direction}  in | out
  tollgate_model_usd_total{model}               spend
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict, deque

GATE_BUCKETS = (0.0001, 0.00025, 0.0005, 0.001, 0.0025, 0.005, 0.01, 0.025, 0.05, 0.1)
AI_BUCKETS = (0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60)


def _labels(d: dict) -> str:
    if not d:
        return ""
    esc = lambda v: str(v).replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")  # noqa: E731
    return "{" + ",".join(f'{k}="{esc(v)}"' for k, v in sorted(d.items())) + "}"


class Histogram:
    def __init__(self, buckets: tuple[float, ...]):
        self.buckets, self.counts, self.sum, self.n = buckets, [0] * len(buckets), 0.0, 0

    def observe(self, v: float) -> None:
        self.sum += v
        self.n += 1
        for i, b in enumerate(self.buckets):
            if v <= b:
                self.counts[i] += 1

    def render(self, name: str) -> list[str]:
        out = [f'{name}_bucket{{le="{b}"}} {c}' for b, c in zip(self.buckets, self.counts)]
        return out + [f'{name}_bucket{{le="+Inf"}} {self.n}', f"{name}_sum {self.sum:.6f}", f"{name}_count {self.n}"]


class Metrics:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.reset()

    def reset(self) -> None:
        self.decisions: dict[tuple, int] = defaultdict(int)
        self.findings: dict[tuple, int] = defaultdict(int)
        self.tokens: dict[tuple, int] = defaultdict(int)
        self.usd: dict[str, float] = defaultdict(float)
        self.model_calls: dict[str, int] = defaultdict(int)
        self.gate_s, self.ai_s = Histogram(GATE_BUCKETS), Histogram(AI_BUCKETS)
        self.stage_ms: dict[str, list[float]] = defaultdict(list)    # per pipeline stage, last 5,000 each
        self.model_ms: dict[str, list[float]] = defaultdict(list)    # per model call, last 5,000 each
        self.timeline: deque = deque(maxlen=3000)                    # (ts, gate_ms, ai_ms, decision, tool)
        self.started = time.time()

    def observe(self, event: dict) -> None:
        with self._lock:
            if event.get("kind") == "model":
                m = event["model"]
                self.tokens[(m, "in")] += event["tokens_in"]
                self.tokens[(m, "out")] += event["tokens_out"]
                self.usd[m] += event["usd"]
                self.model_calls[m] += 1
                self._keep(self.model_ms[m], event.get("ms", 0))
            elif event.get("kind") == "decision":
                self.decisions[(event["tool"], event["decision"])] += 1
                if event["decision"] != "off":
                    self.gate_s.observe(event["gate_ms"] / 1000)
                    if event.get("judge_ms"):
                        self.ai_s.observe(event["judge_ms"] / 1000)
                    for stage, ms in (event.get("stages_ms") or {}).items():
                        self._keep(self.stage_ms[stage], ms)
                    self.timeline.append((round(event.get("ts", time.time()), 2), event["gate_ms"], event.get("judge_ms") or 0,
                                          event["decision"], event["tool"]))
                for f in event.get("findings") or []:
                    control, decision = f[0], f[1]
                    if decision != "allow" or control.startswith(("injection.", "secrets.", "data.")) or "redacted" in f[2]:
                        self.findings[(control, decision)] += 1

    @staticmethod
    def _keep(values: list[float], v: float, cap: int = 5000) -> None:
        values.append(float(v))
        if len(values) > cap:
            del values[: len(values) - cap]

    @staticmethod
    def _q(values: list[float]) -> dict:
        if not values:
            return {"n": 0, "p50": 0, "p95": 0, "p99": 0, "max": 0, "mean": 0}
        v = sorted(values)
        at = lambda q: v[min(len(v) - 1, int(q * len(v)))]   # noqa: E731
        return {"n": len(v), "p50": round(at(.5), 3), "p95": round(at(.95), 3), "p99": round(at(.99), 3), "max": round(v[-1], 3),
                "mean": round(sum(v) / len(v), 3)}

    def telemetry(self) -> dict:
        """Performance telemetry: per stage, per model call, over time."""
        with self._lock:
            return {"since": self.started, "stages": {k: self._q(v) for k, v in self.stage_ms.items()},
                    "models": {k: self._q(v) for k, v in self.model_ms.items()},
                    "timeline": list(self.timeline)[-600:]}

    def snapshot(self) -> dict:
        """The same numbers as render(), as JSON for the dashboard."""
        def hist(h: Histogram) -> dict:
            return {"buckets": [{"le": b, "count": c} for b, c in zip(h.buckets, h.counts)], "sum": h.sum, "n": h.n}
        with self._lock:
            models = sorted({m for m, _ in self.tokens} | set(self.usd))
            return {
                "decisions": [{"tool": t, "decision": d, "n": n} for (t, d), n in sorted(self.decisions.items())],
                "findings": [{"control": c, "decision": d, "n": n} for (c, d), n in sorted(self.findings.items(), key=lambda kv: -kv[1])],
                "gate": hist(self.gate_s), "ai": hist(self.ai_s),
                "models": [{"model": m, "tokens_in": self.tokens.get((m, "in"), 0), "tokens_out": self.tokens.get((m, "out"), 0),
                            "usd": round(self.usd.get(m, 0.0), 6), "calls": self.model_calls.get(m, 0)} for m in models],
            }

    def render(self, gauges: dict[str, tuple[str, float, dict]] | None = None) -> str:
        """gauges: name -> (help, value, labels), for values the caller owns (waiting approvals, layer on)."""
        with self._lock:
            lines = ["# HELP tollgate_decisions_total Tool calls decided by the gate.", "# TYPE tollgate_decisions_total counter"]
            lines += [f"tollgate_decisions_total{_labels({'tool': t, 'decision': d})} {n}" for (t, d), n in sorted(self.decisions.items())]
            lines += ["# HELP tollgate_findings_total Controls that fired.", "# TYPE tollgate_findings_total counter"]
            lines += [f"tollgate_findings_total{_labels({'control': c, 'decision': d})} {n}" for (c, d), n in sorted(self.findings.items())]
            lines += ["# HELP tollgate_gate_seconds Deterministic check time per tool call.", "# TYPE tollgate_gate_seconds histogram"]
            lines += self.gate_s.render("tollgate_gate_seconds")
            lines += ["# HELP tollgate_ai_seconds Judge and injection-model time per tool call.", "# TYPE tollgate_ai_seconds histogram"]
            lines += self.ai_s.render("tollgate_ai_seconds")
            lines += ["# HELP tollgate_model_tokens_total Model tokens.", "# TYPE tollgate_model_tokens_total counter"]
            lines += [f"tollgate_model_tokens_total{_labels({'model': m, 'direction': d})} {n}" for (m, d), n in sorted(self.tokens.items())]
            lines += ["# HELP tollgate_model_usd_total Model spend in USD.", "# TYPE tollgate_model_usd_total counter"]
            lines += [f"tollgate_model_usd_total{_labels({'model': m})} {v:.6f}" for m, v in sorted(self.usd.items())]
            lines += ["# HELP tollgate_model_calls_total Model calls.", "# TYPE tollgate_model_calls_total counter"]
            lines += [f"tollgate_model_calls_total{_labels({'model': m})} {n}" for m, n in sorted(self.model_calls.items())]
        for name, (help_, value, labels) in (gauges or {}).items():
            lines += [f"# HELP {name} {help_}", f"# TYPE {name} gauge", f"{name}{_labels(labels)} {value}"]
        return "\n".join(lines) + "\n"


REGISTRY = Metrics()


BY_MODEL: dict[str, Metrics] = defaultdict(Metrics)   # the same numbers, per agent model, for the dashboard


def observe(event: dict) -> None:
    REGISTRY.observe(event)
    if event.get("agent_model"):
        BY_MODEL[event["agent_model"]].observe(event)

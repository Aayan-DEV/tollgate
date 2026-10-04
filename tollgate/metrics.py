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
from collections import defaultdict

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

    def observe(self, event: dict) -> None:
        with self._lock:
            if event.get("kind") == "model":
                m = event["model"]
                self.tokens[(m, "in")] += event["tokens_in"]
                self.tokens[(m, "out")] += event["tokens_out"]
                self.usd[m] += event["usd"]
                self.model_calls[m] += 1
            elif event.get("kind") == "decision":
                self.decisions[(event["tool"], event["decision"])] += 1
                if event["decision"] != "off":
                    self.gate_s.observe(event["gate_ms"] / 1000)
                    if event.get("judge_ms"):
                        self.ai_s.observe(event["judge_ms"] / 1000)
                for f in event.get("findings") or []:
                    control, decision = f[0], f[1]
                    if decision != "allow" or control.startswith(("injection.", "secrets.", "data.")) or "redacted" in f[2]:
                        self.findings[(control, decision)] += 1

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

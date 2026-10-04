// The dashboard's one door to the server.
const json = (r) => r.json();
const post = (url, body) => fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}) }).then(json);

export const api = {
  state: () => fetch("/api/state").then(json),
  person: (id) => post("/api/person", { id }),
  model: (id) => post("/api/model", { id }),
  models: () => fetch("/api/models").then(json),
  startModel: (id) => post("/api/models/start", { id }),
  stopModel: (id) => post("/api/models/stop", { id }),
  layer: (on) => post("/api/layer", { on }),
  fresh: () => post("/api/new"),
  openChat: (id) => post(`/api/conversations/${encodeURIComponent(id)}`),
  reset: () => post("/api/reset"),
  events: (after) => fetch(`/api/events?after=${after}`).then(json),
  overview: () => fetch("/api/overview").then(json),
  controls: () => fetch("/api/controls").then(json),
  approvals: () => fetch("/api/approvals").then(json),
  tests: () => fetch("/api/tests").then(json),
  runTests: (body) => post("/api/tests/run", body),
  runPytest: () => post("/api/tests/pytest"),
  deleteChats: () => fetch("/api/conversations", { method: "DELETE" }).then(json),
  policy: () => fetch("/api/policy").then(json),
  feed: () => fetch("/api/feed").then(json),
  verifyAudit: () => fetch("/api/audit/verify").then(json),
  auditRecent: () => fetch("/api/audit/recent?limit=100").then(json),
  metricsJson: () => fetch("/api/metrics.json").then(json),
  setPolicy: (change) => post("/api/policy", change),
  decide: (id, approve) => post(`/api/approvals/${id}`, { approve }),
  tables: () => fetch("/api/tables").then(json),
  table: (name, view, offset = 0) => fetch(`/api/tables/${encodeURIComponent(name)}?view=${view}&offset=${offset}`).then(json),

  // The agent's run arrives as one JSON object per line while it works.
  async ask(text, onEvent) {
    const res = await fetch("/api/ask", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ text }) });
    const reader = res.body.getReader();
    const dec = new TextDecoder();
    let buf = "";
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      let i;
      while ((i = buf.indexOf("\n")) >= 0) {
        const line = buf.slice(0, i).trim();
        buf = buf.slice(i + 1);
        if (line) onEvent(JSON.parse(line));
      }
    }
  },
};

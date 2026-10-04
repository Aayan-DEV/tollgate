// EVIDENCE: what a reviewer checks. Three tabs: the audit log, the metrics, the attack feed.
import { api } from "../api.js";
import { icon, esc, tool, DECISION, num, usd, timeAgo, personMark } from "../ui.js";

const TABS = { audit: ["Audit log", "lock"], metrics: ["Metrics", "analytics"], feed: ["Attack feed", "warning"] };
let root, S, tab = "audit", data = {}, tick = 0;

export async function mount(el, state) {
  root = el; S = state;
  try { tab = localStorage.getItem("evidence.tab") || tab; } catch { /* storage blocked: default tab */ }
  if (!TABS[tab]) tab = "audit";
  root.innerHTML = `<div class="page" id="ev-page"></div>`;
  draw();
  await load();
  draw();
}

export async function onPoll(_s, _a, changed) {
  // The feed changes outside the app (a remote server), so look at it every few polls even when nothing else moved.
  if (changed || (tab === "feed" && ++tick % 4 === 0)) { await load(); draw(); }
}
export async function onState() { await load(); draw(); }

async function load() {
  if (tab === "audit") [data.audit, data.verify] = await Promise.all([api.auditRecent(), api.verifyAudit()]);   // re-hash every time: cheap
  if (tab === "metrics") data.metrics = await api.metricsJson();
  if (tab === "feed") data.feed = await api.feed();
}

function fig(label, value, note, tone = "") {
  return `<div class="pn-fig"><span class="pn-fig-label">${label}</span><span class="pn-fig-val figure ${tone}">${value}</span>${note ? `<span class="pn-fig-note">${note}</span>` : ""}</div>`;
}

function draw() {
  const page = root.querySelector("#ev-page");
  if (!page) return;
  page.innerHTML = `
    <div class="page-head ev-head"><div>
      <h1>Evidence</h1>
      <p>What a reviewer needs to trust the layer: a record of every decision that cannot be edited quietly, live numbers, and the attack signatures it is using.</p>
    </div><div class="ph-right"><span class="seg" role="tablist" aria-label="Evidence">
      ${Object.entries(TABS).map(([id, [label, ico]]) => `<button class="seg-cell" role="tab" data-tab="${id}" aria-pressed="${tab === id}" aria-selected="${tab === id}">${icon(ico)}${label}</button>`).join("")}
    </span></div></div>
    <div class="pn-stack">${tab === "audit" ? audit() : tab === "metrics" ? metrics() : feed()}</div>`;
  page.querySelectorAll("[data-tab]").forEach((b) => (b.onclick = async () => {
    tab = b.dataset.tab;
    try { localStorage.setItem("evidence.tab", tab); } catch { /* not remembered: fine */ }
    draw();
    await load();
    draw();
  }));
  page.querySelector("[data-verify]")?.addEventListener("click", async () => { data.verify = await api.verifyAudit(); await load(); draw(); });
}

const loading = `<section class="pn-panel"><p class="pn-empty">${icon("loading", "spin")} Loading</p></section>`;

// ---------------- audit log ----------------
function audit() {
  const a = data.audit;
  if (!a) return loading;
  const recs = a.records, v = data.verify;
  const by = (d) => recs.filter((r) => r.decision === d).length;
  const decisive = (r) => (r.findings || []).filter((f) => f[1] === r.decision && r.decision !== "allow");
  return `
    <section class="pn-panel"><div class="pn-figs" style="--pn-cols:5">
      ${fig("Chain", v ? (v.ok ? "Intact" : "Broken") : "Checking", v ? (v.ok ? `${num(v.records)} records re-hashed, none edited or removed` : esc(v.message)) : "", v && !v.ok ? "is-bad" : "")}
      ${fig("Shown", num(recs.length), "newest first, up to 100")}
      ${fig("Blocked", num(by("block")), "in the records shown", by("block") ? "is-bad" : "")}
      ${fig("Held", num(by("ask")), "in the records shown", by("ask") ? "is-warn" : "")}
      ${fig("Last record", recs[0] ? timeAgo(recs[0].ts) : "none", recs[0] ? `policy ${esc(recs[0].policy_sha)} · feed v${recs[0].feed_version}` : "")}
    </div></section>
    <section class="pn-panel">
      <header class="pn-head"><span class="pn-head-ico">${icon("lock")}</span><span class="pn-head-title">Records</span>
        <span class="pn-head-hint">Each record stores the hash of the one before it. Change or delete any line and every hash after it stops matching.</span>
        <span class="pn-head-right"><button class="key is-small is-quiet" data-verify>${icon("check")}Verify</button>
          <a class="key is-small is-quiet" href="/api/audit.csv" download>${icon("down")}CSV</a></span></header>
      ${recs.length ? `<div class="pn-table-wrap" style="max-height:640px"><table class="pn-table ev-audit"><thead><tr>
          <th>When</th><th>For</th><th>Action</th><th>What it would do</th><th>Decision</th><th>Deciding control</th><th class="is-num">Check</th><th>Hash ← previous</th></tr></thead><tbody>
        ${recs.map((r) => {
          const d = DECISION[r.decision] || DECISION.allow, who = String(r.session || "").split("-")[0];
          const dec = decisive(r);
          return `<tr><td>${timeAgo(r.ts)}</td>
            <td><span class="ev-who">${personMark(who)}${esc(who)}</span></td>
            <td>${tool(r.tool).label}</td>
            <td class="ev-wrap">${esc(r.effect)}${dec.length ? `<div class="ev-sub">${esc(dec.map((f) => f[2]).join("; "))}</div>` : ""}${r.judge?.quote && r.judge.quote_ok ? `<div class="ev-sub">judge quoted <q>${esc(r.judge.quote)}</q></div>` : ""}</td>
            <td><span class="chip ${d.chip}">${d.word}</span></td>
            <td class="ev-mono">${esc(dec.map((f) => f[0]).join(" ")) || "none"}</td>
            <td class="is-num">${Number(r.latency_ms).toFixed(2)} ms</td>
            <td class="ev-mono ev-chain" title="${esc(r.hash)}">${esc(String(r.hash).slice(0, 8))} <span>← ${esc(String(r.prev).slice(0, 8))}</span></td></tr>`;
        }).join("")}</tbody></table></div>`
        : `<p class="pn-empty">No records yet. Every action the agent takes, allowed or not, adds one.</p>`}
    </section>`;
}

// ---------------- metrics ----------------
function quantile(h, q) {
  if (!h.n) return null;
  const hit = h.buckets.find((b) => b.count >= q * h.n);
  return hit ? hit.le : Infinity;
}
const ms = (s) => (s === null ? "none" : s === Infinity ? "slower" : s < 0.001 ? `≤ ${(s * 1000).toFixed(2)} ms` : `≤ ${(s * 1000).toFixed(s < 0.01 ? 1 : 0)} ms`);

function bars(rows, max) {
  return rows.map((r) => `<div class="ev-bar-row"><span class="ev-bar-label" title="${esc(r.label)}">${r.label}</span>
    <span class="ev-bar-track">${r.parts.map((p) => p.n ? `<span class="ev-bar ${p.tone}" style="width:${(100 * p.n / max).toFixed(2)}%" title="${esc(p.title)}: ${p.n}"></span>` : "").join("")}</span>
    <span class="ev-bar-val figure">${num(r.total)}</span></div>`).join("");
}

function metrics() {
  const m = data.metrics;
  if (!m) return loading;
  const sum = (d) => m.decisions.filter((x) => x.decision === d).reduce((a, x) => a + x.n, 0);
  const checked = sum("allow") + sum("ask") + sum("block");
  const tools = {};
  for (const x of m.decisions) (tools[x.tool] ||= { allow: 0, ask: 0, block: 0, off: 0 })[x.decision] += x.n;
  const toolRows = Object.entries(tools).map(([t, c]) => ({ label: tool(t).label, total: c.allow + c.ask + c.block + c.off,
    parts: [{ n: c.allow, tone: "is-allow", title: "allowed" }, { n: c.ask, tone: "is-ask", title: "held" }, { n: c.block, tone: "is-block", title: "blocked" }, { n: c.off, tone: "is-off", title: "no checks" }] }))
    .sort((a, b) => b.total - a.total);
  const ctlRows = m.findings.slice(0, 14).map((f) => ({ label: `<span class="ev-mono">${esc(f.control)}</span>`, total: f.n,
    parts: [{ n: f.n, tone: f.decision === "block" ? "is-block" : f.decision === "ask" ? "is-ask" : "is-allow", title: f.decision }] }));
  const g = m.gate, prev = (i) => (i ? g.buckets[i - 1].count : 0);
  const hist = g.buckets.map((b, i) => ({ le: b.le, n: b.count - prev(i) }));
  const histMax = Math.max(1, ...hist.map((h) => h.n));
  const spend = m.models.reduce((a, x) => a + x.usd, 0), tokens = m.models.reduce((a, x) => a + x.tokens_in + x.tokens_out, 0);
  return `
    <section class="pn-panel"><div class="pn-figs" style="--pn-cols:6">
      ${fig("Actions checked", num(checked), m.layer ? "layer on" : "layer off", "")}
      ${fig("Blocked", num(sum("block")), checked ? `${Math.round(100 * sum("block") / checked)}% of checked` : "", sum("block") ? "is-bad" : "")}
      ${fig("Held for a person", num(sum("ask")), `${m.waiting} still waiting`, sum("ask") ? "is-warn" : "")}
      ${fig("Check time, median", ms(quantile(g, 0.5)), `99%: ${ms(quantile(g, 0.99))}`)}
      ${fig("AI time", m.ai.n ? `${(m.ai.sum / m.ai.n).toFixed(1)} s` : "none", `average over ${num(m.ai.n)} judge or screen calls`)}
      ${fig("Model spend", usd(spend), `${num(tokens)} tokens · preset ${esc(m.preset)}`)}
    </div></section>
    <div class="ev-grid">
      <section class="pn-panel">
        <header class="pn-head"><span class="pn-head-ico">${icon("flow")}</span><span class="pn-head-title">Decisions by action</span>
          <span class="pn-head-hint ev-legend"><i class="is-allow"></i>allowed <i class="is-ask"></i>held <i class="is-block"></i>blocked <i class="is-off"></i>no checks</span></header>
        <div class="ev-bars">${toolRows.length ? bars(toolRows, Math.max(...toolRows.map((r) => r.total))) : `<p class="pn-empty">No actions yet.</p>`}</div>
      </section>
      <section class="pn-panel">
        <header class="pn-head"><span class="pn-head-ico">${icon("shield")}</span><span class="pn-head-title">Controls that fired</span>
          <span class="pn-head-hint">Which rule decided, or what was removed quietly</span></header>
        <div class="ev-bars">${ctlRows.length ? bars(ctlRows, Math.max(...ctlRows.map((r) => r.total))) : `<p class="pn-empty">No control has fired yet.</p>`}</div>
      </section>
      <section class="pn-panel">
        <header class="pn-head"><span class="pn-head-ico">${icon("timer")}</span><span class="pn-head-title">Check time</span>
          <span class="pn-head-hint">Deterministic checks per action, AI excluded</span></header>
        <div class="ev-hist">${hist.map((h) => `<div class="ev-col" title="${num(h.n)} checks ${ms(h.le)}"><span class="ev-col-n figure">${h.n ? num(h.n) : ""}</span>
          <span class="ev-col-bar" style="height:${(100 * h.n / histMax).toFixed(1)}%"></span><span class="ev-col-le">${h.le < 0.001 ? (h.le * 1000).toFixed(2) : (h.le * 1000).toFixed(h.le < 0.01 ? 1 : 0)}</span></div>`).join("")}</div>
        <div class="pn-note">${icon("info")}<span>Milliseconds, upper edge of each bucket.</span></div>
      </section>
      <section class="pn-panel">
        <header class="pn-head"><span class="pn-head-ico">${icon("coins")}</span><span class="pn-head-title">Models</span>
          <span class="pn-head-hint">Agent, judge and injection screen</span>
          <span class="pn-head-right"><a class="key is-small is-quiet" href="/metrics" target="_blank" rel="noopener">${icon("globe")}Prometheus</a></span></header>
        ${m.models.length ? `<table class="pn-table"><thead><tr><th>Model</th><th class="is-num">Calls</th><th class="is-num">Tokens in</th><th class="is-num">Tokens out</th><th class="is-num">Spend</th></tr></thead><tbody>
          ${m.models.map((x) => `<tr><td>${esc(x.model)}</td><td class="is-num">${num(x.calls)}</td><td class="is-num">${num(x.tokens_in)}</td><td class="is-num">${num(x.tokens_out)}</td><td class="is-num">${usd(x.usd)}</td></tr>`).join("")}
        </tbody></table>` : `<p class="pn-empty">No model calls yet.</p>`}
      </section>
    </div>`;
}

// ---------------- attack feed ----------------
const STATE = {
  updated: ["Fetched", "is-good", "A newer signed feed arrived and is active."],
  unchanged: ["Up to date", "is-good", "The server answered 304: nothing new."],
  unreachable: ["Unreachable", "is-warn", "The feed server does not answer. The last good feed stays active."],
  rejected: ["Rejected", "is-bad", "The server offered a feed whose signature does not match. It was refused; the last good feed stays active."],
  starting: ["Starting", "is-off", "First fetch in progress."],
};

function feed() {
  const f = data.feed;
  if (!f) return loading;
  const r = f.remote, st = r ? STATE[r.state] || [r.state, "", ""] : null;
  const actionChip = (a) => `<span class="chip ${a === "block" ? "is-bad" : a === "ask" ? "is-warn" : "is-off"}">${a === "warn" ? "warn" : a}</span>`;
  return `
    <section class="pn-panel"><div class="pn-figs" style="--pn-cols:4">
      ${fig("Active feed", `v${f.version}`, `from ${esc(f.source)}, HMAC verified`)}
      ${fig("Signatures", num(f.signatures), `${f.list.filter((s) => s.action === "block").length} block, ${f.list.filter((s) => s.action !== "block").length} warn`)}
      ${fig("Remote server", st ? st[0] : "Not set", r ? esc(r.url) : "signatures.url is empty: local file only", st && st[1] === "is-bad" ? "is-bad" : st && st[1] === "is-warn" ? "is-warn" : "")}
      ${fig("Last check", r?.last_check ? timeAgo(r.last_check) : "never", r ? `every ${f.fetch_s} s${r.last_change ? ` · last new feed ${timeAgo(r.last_change)}` : ""}` : `local file re-read every ${f.refresh_s} s`)}
    </div>${st ? `<div class="notice ${st[1] === "is-good" ? "" : st[1]} band">${icon(st[1] === "is-good" ? "check" : "warning")}<span>${st[2]}</span></div>` : ""}</section>
    <section class="pn-panel">
      <header class="pn-head"><span class="pn-head-ico">${icon("warning")}</span><span class="pn-head-title">Signatures</span>
        <span class="pn-head-hint">Known attack patterns. A feed loads only if its signature matches; the highest verified version wins.</span></header>
      <div class="pn-table-wrap"><table class="pn-table"><thead><tr><th>ID</th><th>Pattern for</th><th>Action</th><th>Looks at</th><th>Source</th><th>Reference</th></tr></thead><tbody>
        ${f.list.map((s) => `<tr${s.remote_only ? ' class="ev-new"' : ""}><td class="ev-mono"><b>${esc(s.id)}</b></td><td class="ev-wrap" title="${esc(s.pattern)}">${esc(s.name)}</td>
          <td>${actionChip(s.action)}</td><td>${s.scopes.map((x) => esc(x.replace("_", " "))).join(", ")}</td>
          <td>${s.remote_only ? `<span class="chip is-good">remote only</span>` : "local + remote"}</td><td class="ev-wrap ev-sub">${esc(s.ref)}</td></tr>`).join("")}
      </tbody></table></div>
      <div class="pn-note">${icon("info")}<span>Edit <b>signatures/feed.json</b> and sign it, or publish on the feed server. Hover a name to see its pattern.</span></div>
    </section>`;
}

// EVIDENCE for the selected agent: a summary for management, the decision log for security, performance telemetry,
// and the attack feed. Everything here is scoped to the agent model picked at the top left.
import { api } from "../api.js";
import { icon, esc, tool, DECISION, num, usd, eur, timeAgo, personMark } from "../ui.js";

const TABS = {
  management: ["For management", "analytics"],
  security: ["Security log", "lock"],
  performance: ["Performance", "timer"],
  feed: ["Attack feed", "warning"],
};
let root, S, tab = "management", data = {}, tick = 0, filter = "all", query = "", openRow = null;

export async function mount(el, state) {
  root = el; S = state;
  try { tab = localStorage.getItem("evidence.tab2") || tab; } catch { /* storage blocked: default tab */ }
  if (!TABS[tab]) tab = "management";
  root.innerHTML = `<div class="page" id="ev-page"></div>`;
  draw();
  await load();
  draw();
}

export async function onPoll(_s, _a, changed) {
  if (changed || (tab === "feed" && ++tick % 4 === 0)) { await load(); draw(); }
}
export async function onState() { data = {}; await load(); draw(); }

async function load() {
  if (tab === "management") data.report = await api.report();
  if (tab === "security") [data.audit, data.verify] = await Promise.all([api.auditRecent(), api.verifyAudit()]);
  if (tab === "performance") data.tele = await api.telemetry();
  if (tab === "feed") data.feed = await api.feed();
}

function fig(label, value, note, tone = "") {
  return `<div class="pn-fig"><span class="pn-fig-label">${label}</span><span class="pn-fig-val figure ${tone}">${value}</span>${note ? `<span class="pn-fig-note">${note}</span>` : ""}</div>`;
}

function draw() {
  const page = root.querySelector("#ev-page");
  if (!page) return;
  const model = S.state.models[S.state.model] || S.state.model;
  page.innerHTML = `
    <div class="page-head ev-head"><div>
      <h1>Evidence</h1>
      <p>For <b>${esc(model)}</b>: a summary for management, every decision for the security team, the layer's performance, and the attack patterns it uses. Switch the agent at the top left to see another one.</p>
    </div><div class="ph-right"><span class="seg" role="tablist" aria-label="Evidence">
      ${Object.entries(TABS).map(([id, [label, ico]]) => `<button class="seg-cell" role="tab" data-tab="${id}" aria-pressed="${tab === id}" aria-selected="${tab === id}">${icon(ico)}${label}</button>`).join("")}
    </span></div></div>
    <div class="pn-stack">${tab === "management" ? management() : tab === "security" ? security() : tab === "performance" ? performance() : feed()}</div>`;
  page.querySelectorAll("[data-tab]").forEach((b) => (b.onclick = async () => {
    tab = b.dataset.tab;
    try { localStorage.setItem("evidence.tab2", tab); } catch { /* not remembered: fine */ }
    draw(); await load(); draw();
  }));
  page.querySelector("[data-verify]")?.addEventListener("click", async () => { data.verify = await api.verifyAudit(); draw(); });
  page.querySelectorAll("[data-filter]").forEach((b) => (b.onclick = () => { filter = b.dataset.filter; draw(); }));
  const q = page.querySelector("#ev-q");
  if (q) {
    q.oninput = () => { query = q.value; const pos = q.selectionStart; draw(); const n = root.querySelector("#ev-q"); n.focus(); n.setSelectionRange(pos, pos); };
  }
  page.querySelectorAll("[data-row]").forEach((tr) => (tr.onclick = () => { openRow = openRow === tr.dataset.row ? null : tr.dataset.row; draw(); }));
}

const loading = `<section class="pn-panel"><p class="pn-empty">${icon("loading", "spin")} Loading</p></section>`;
const blank = (what) => `<section class="pn-panel"><p class="pn-empty">${icon("info")} No ${what} for this agent yet. Chat with it on the Agent page, or press <b>Run everything for this agent</b> on the Tests page.</p></section>`;

// Plain names for the rules that decide, for people outside security.
const RULE = {
  "pay_invoice.iban_on_file": "Payment to an unverified bank account", "pay_invoice.no_recent_duplicate": "Likely duplicate payment",
  "pay_invoice.approval_limit": "Above the person's approval limit", "pay_invoice.exact_amount": "Amount does not match the invoice",
  "pay_invoice.approved": "Invoice not approved", "pay_invoice.known_vendor": "Payee is not a supplier",
  "pay_invoice.invoice_exists": "Invoice does not exist", "pay_invoice.not_paid": "Invoice already paid",
  "pay_invoice.invoice_vendor": "Invoice belongs to another supplier",
  "limits.vendor_daily_outflow": "Supplier's daily limit reached", "limits.agent_daily_outflow": "Agent's daily limit reached",
  "limits.external_emails_per_hour": "Too many outside emails", "limits.rows_read_per_session": "Too much data read",
  "data.denied": "Closed data refused", "send_email.no_sensitive_to_outside": "Bank or personal data to an outsider",
  "send_email.no_outside_recipient": "Email to an outside address", "update_vendor_bank_details.bank_change_needs_callback": "Bank details changed by the AI",
  "load_forecast_model.safe_pickle": "Booby-trapped file", "load_forecast_model.inside_model_dir": "File outside the approved folder",
  "justify": "Not clearly asked for by the user", "identity.amount_scope": "Above the AI's amount limit",
  "identity.delegation": "Helper asked for more power", "identity.token": "Missing or forged AI identity",
  "identity.impersonation": "Acting as someone else", "identity.tool_scope": "Action the AI was not given", "loop.identical_call": "Repeated step (loop)",
  "secrets.args": "Password or key leaving", "secrets.results": "Password or key in a document", "injection.withheld": "Document with hidden orders",
  "injection.after_detection": "Hidden orders seen earlier", "mcp.quarantine": "Changed outside tool", "tools.unknown": "Unknown action",
};
const ruleName = (c) => RULE[c] || (c.startsWith("feed.") ? `Known attack pattern (${c.slice(5)})` : c);

// ---------------- for management ----------------
function timelineChart(t) {
  if (!t.rows.length) return "";
  const W = 720, H = 150, max = Math.max(1, ...t.rows.map((r) => (r.allow || 0) + (r.ask || 0) + (r.block || 0)));
  const bw = Math.max(3, Math.min(28, (W - 40) / t.rows.length - 3));
  const x0 = (i) => 32 + i * (bw + 3);
  const y = (v) => (H - 24) * (1 - v / max) + 4;
  const bars = t.rows.map((r, i) => {
    let base = 0;
    return ["allow", "ask", "block"].map((k) => {
      const v = r[k] || 0; if (!v) return "";
      const top = y(base + v), h = y(base) - top; base += v;
      return `<rect x="${x0(i)}" y="${top}" width="${bw}" height="${Math.max(1, h)}" class="tl-${k}"><title>${new Date(r.t * 1000).toLocaleTimeString()}: ${v} ${DECISION[k].word}</title></rect>`;
    }).join("");
  }).join("");
  const label = (r) => new Date(r.t * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  return `<svg viewBox="0 0 ${W} ${H}" class="tl-chart" role="img" aria-label="Decisions over time">
    <line x1="30" x2="${W}" y1="${H - 20}" y2="${H - 20}" class="tl-axis"/>
    <text x="0" y="12" class="tl-label">${max}</text><text x="0" y="${H - 22}" class="tl-label">0</text>
    ${bars}
    <text x="32" y="${H - 4}" class="tl-label">${label(t.rows[0])}</text>
    <text x="${W}" y="${H - 4}" text-anchor="end" class="tl-label">${label(t.rows[t.rows.length - 1])}</text></svg>`;
}

function management() {
  const R = data.report;
  if (!R) return loading;
  if (!R.checked) return blank("decisions");
  const stopped = R.blocked + R.held, ap = R.approvals;
  const maxRule = Math.max(1, ...R.top_rules.map((r) => r[1]));
  return `
    <section class="pn-panel"><div class="ev-lead">
      ${icon("shield")}<p><b>${num(R.checked)} actions checked</b> across ${num(R.chats)} chats. The layer stopped <b>${num(stopped)}</b> (${num(R.blocked)} blocked, ${num(R.held)} held for a person)${R.money_stopped_eur ? `, including <b>${eur(R.money_stopped_eur)}</b> in ${num(R.payments_stopped)} payment${R.payments_stopped === 1 ? "" : "s"} that did not pass the checks` : ""}. ${R.payments_made ? `It let through ${num(R.payments_made)} correct payment${R.payments_made === 1 ? "" : "s"} worth ${eur(R.money_paid_eur)}.` : ""}</p></div>
      <div class="pn-figs" style="--pn-cols:6">
        ${fig("Actions checked", num(R.checked), `${num(R.allowed)} went through`)}
        ${fig("Stopped", num(stopped), `${num(R.blocked)} blocked, ${num(R.held)} held`, stopped ? "is-bad" : "")}
        ${fig("Money stopped", eur(R.money_stopped_eur).replace(".00", ""), `${num(R.payments_stopped)} payments`)}
        ${fig("Data protected", num(R.data_refused + R.values_masked), `${num(R.data_refused)} reads refused, ${num(R.values_masked)} values masked`)}
        ${fig("Waiting for people", num(ap.waiting), `${num(ap.approved)} approved, ${num(ap.declined)} declined`, ap.waiting ? "is-warn" : "")}
        ${fig("AI spend", usd(R.usd), `${num(R.tokens)} tokens · ${R.checked ? usd(R.usd / R.checked) : "$0"} per action`)}
      </div></section>
    <div class="ev-grid">
      <section class="pn-panel">
        <header class="pn-head"><span class="pn-head-ico">${icon("analytics")}</span><span class="pn-head-title">Decisions over time</span>
          <span class="pn-head-hint ev-legend"><i class="is-allow"></i>went through <i class="is-ask"></i>held <i class="is-block"></i>blocked · per ${R.timeline.step >= 3600 ? "hour" : R.timeline.step >= 300 ? "5 minutes" : "minute"}</span></header>
        <div class="tl-wrap">${timelineChart(R.timeline)}</div>
      </section>
      <section class="pn-panel">
        <header class="pn-head"><span class="pn-head-ico">${icon("warning")}</span><span class="pn-head-title">Why actions were stopped</span>
          <span class="pn-head-hint">The rules that decided, most frequent first</span></header>
        <div class="ev-bars">${R.top_rules.length ? R.top_rules.map(([c, n]) => `<div class="ev-bar-row bm-harm"><span class="ev-bar-label" title="${esc(c)}">${esc(ruleName(c))}</span>
          <span class="ev-bar-track"><span class="ev-bar is-block" style="width:${(100 * n) / maxRule}%"></span></span><span class="ev-bar-val figure">${n}</span></div>`).join("") : `<p class="pn-empty">Nothing was stopped.</p>`}</div>
      </section>
    </div>
    <section class="pn-panel"><div class="pn-figs" style="--pn-cols:3">
      ${fig("Hidden orders removed", num(R.hidden_orders_removed), "instructions planted in documents")}
      ${fig("Passwords and keys caught", num(R.secrets_caught), "kept out of messages and results")}
      ${fig("Correct payments", num(R.payments_made), eur(R.money_paid_eur))}
    </div></section>`;
}

// ---------------- security log ----------------
function security() {
  const A = data.audit, v = data.verify;
  if (!A) return loading;
  const q = query.toLowerCase();
  const rows = A.records.filter((r) => (filter === "all" || r.decision === filter) &&
    (!q || JSON.stringify([r.tool, r.effect, r.caller, r.acting_for, r.findings]).toLowerCase().includes(q)));
  const count = (d) => A.records.filter((r) => r.decision === d).length;
  return `
    <section class="pn-panel">
      <header class="pn-head"><span class="pn-head-ico">${icon("lock")}</span><span class="pn-head-title">Decision log</span>
        <span class="pn-head-hint">${v ? (v.ok ? `Chain intact: ${num(v.records)} records re-hashed, none edited or removed` : `Chain broken: ${esc(v.message)}`) : ""}</span>
        <span class="pn-head-right"><button class="key is-small is-quiet" data-verify>${icon("check")}Verify chain</button>
          <a class="key is-small is-quiet" href="/api/audit.csv?scope=model" download>${icon("down")}CSV, this agent</a>
          <a class="key is-small is-quiet" href="/api/audit.csv?scope=all" download>${icon("down")}CSV, all</a></span></header>
      <div class="ev-tools">
        <span class="seg" role="group" aria-label="Filter">${[["all", `All ${num(A.records.length)}`], ["block", `Blocked ${num(count("block"))}`], ["ask", `Held ${num(count("ask"))}`], ["allow", `Allowed ${num(count("allow"))}`]].map(([k, l]) => `<button class="seg-cell" data-filter="${k}" aria-pressed="${filter === k}">${l}</button>`).join("")}</span>
        <input class="field ev-search" id="ev-q" placeholder="Search action, rule, agent, person…" value="${esc(query)}" autocomplete="off">
      </div>
      ${!A.records.length ? `<p class="pn-empty">No decisions recorded for this agent yet.</p>` : `
      <div class="pn-table-wrap"><table class="pn-table ev-audit"><thead><tr>
        <th>When</th><th>Agent</th><th>For</th><th>Action</th><th>Decision</th><th>Rule</th><th class="is-num">Time</th><th>Record</th></tr></thead><tbody>
        ${rows.slice(0, 300).map((r) => {
          const d = DECISION[r.decision] || DECISION.allow;
          const rules = (r.findings || []).filter((f) => f[1] === r.decision && r.decision !== "allow");
          const open = openRow === r.hash;
          return `<tr class="ev-row" data-row="${esc(r.hash)}"><td>${timeAgo(r.ts)}</td>
            <td class="ev-mono">${esc(r.caller || "")}</td><td>${r.acting_for ? `<span class="ev-who">${personMark(r.acting_for)}${esc(r.acting_for)}</span>` : ""}</td>
            <td>${tool(r.tool).label}</td><td><span class="chip ${d.chip}">${d.word}</span></td>
            <td class="ev-wrap">${rules.length ? rules.map((f) => `<div><span class="ev-mono">${esc(f[0])}</span><div class="ev-sub">${esc(f[2])}</div></div>`).join("") : `<span class="ev-sub">none</span>`}</td>
            <td class="is-num">${Number(r.latency_ms).toFixed(2)} ms</td>
            <td class="ev-mono ev-chain" title="${esc(r.hash)}">${esc(String(r.hash).slice(0, 8))} <span>← ${esc(String(r.prev).slice(0, 8))}</span></td></tr>
            ${open ? `<tr class="ev-detail"><td colspan="8"><dl>
              <dt>Effect</dt><dd>${esc(r.effect)}</dd>
              <dt>All findings</dt><dd>${(r.findings || []).map((f) => `${esc(f[0])} (${esc(f[1])}): ${esc(f[2])}`).join("<br>") || "none"}</dd>
              ${r.judge ? `<dt>AI judge</dt><dd>${esc(r.judge.model)}: ${esc(r.judge.decision)}${r.judge.quote ? `, quoted "${esc(r.judge.quote)}" (${r.judge.quote_ok ? "verified" : "not the user's words"})` : ""}</dd>` : ""}
              <dt>Stage times</dt><dd class="ev-mono">${Object.entries(r.stages_ms || {}).map(([k, ms]) => `${k} ${ms} ms`).join(" · ") || "n/a"}</dd>
              <dt>Policy · feed</dt><dd class="ev-mono">sha ${esc(r.policy_sha)} · feed v${esc(r.feed_version)} · session ${esc(r.session)}</dd>
              <dt>Hash</dt><dd class="ev-mono">${esc(r.hash)} (previous ${esc(r.prev)})</dd></dl></td></tr>` : ""}`;
        }).join("")}</tbody></table></div>`}
      <div class="pn-note">${icon("info")}<span>Click a row for the full record. ${rows.length > 300 ? `Showing the newest 300 of ${num(rows.length)}; the CSV has them all.` : ""}</span></div>
    </section>`;
}

// ---------------- performance ----------------
const STAGE_ORDER = ["normalize", "identity", "known_bad", "contract", "limits", "data_guard", "judge", "decide", "execute", "screen", "record"];
const STAGE_NAME = { normalize: "Normalize", identity: "Identity", known_bad: "Known-bad checks", contract: "Resolve and contract checks",
  limits: "Cumulative limits", data_guard: "Data guard (SQL)", judge: "AI judge", decide: "Decide", execute: "Execute (transaction)",
  screen: "Screen the result", record: "Write the audit record" };
const ms = (v) => (v >= 1000 ? `${(v / 1000).toFixed(1)} s` : v >= 10 ? `${v.toFixed(0)} ms` : `${v.toFixed(2)} ms`);
const q = (vals, p) => { if (!vals.length) return 0; const v = [...vals].sort((a, b) => a - b); return v[Math.min(v.length - 1, Math.floor(p * v.length))]; };

function latencyChart(tl) {
  if (!tl.length) return "";
  const W = 720, H = 160, vals = tl.map((r) => r[1]), cap = Math.max(q(vals, 0.99), 0.5);
  const bw = Math.max(1, (W - 40) / tl.length - 1);
  const bars = tl.map((r, i) => {
    const h = Math.max(1, Math.min(1, r[1] / cap) * (H - 30));
    return `<rect x="${36 + i * (bw + 1)}" y="${H - 20 - h}" width="${bw}" height="${h}" class="tl-${r[3] === "off" ? "allow" : r[3]}"><title>${esc(r[4])}: ${ms(r[1])} rules${r[2] ? ` + ${ms(r[2])} AI` : ""}</title></rect>`;
  }).join("");
  return `<svg viewBox="0 0 ${W} ${H}" class="tl-chart" role="img" aria-label="Rule check time per action">
    <line x1="34" x2="${W}" y1="${H - 20}" y2="${H - 20}" class="tl-axis"/>
    <text x="0" y="14" class="tl-label">${ms(cap)}</text><text x="0" y="${H - 22}" class="tl-label">0</text>${bars}
    <text x="36" y="${H - 4}" class="tl-label">oldest</text><text x="${W}" y="${H - 4}" text-anchor="end" class="tl-label">newest · ${tl.length} actions</text></svg>`;
}

function performance() {
  const T = data.tele;
  if (!T) return loading;
  const tl = T.timeline || [];
  if (!tl.length) return blank("telemetry");
  const gate = tl.map((r) => r[1]), ai = tl.map((r) => r[2]).filter((v) => v > 0);
  const span = Math.max(1, tl[tl.length - 1][0] - tl[0][0]);
  const stages = STAGE_ORDER.filter((k) => T.stages[k]?.n);
  const rules = stages.filter((k) => k !== "judge"), maxRule = Math.max(0.001, ...rules.map((k) => T.stages[k].p95));
  const models = Object.entries(T.models || {});
  return `
    <section class="pn-panel"><div class="pn-figs" style="--pn-cols:6">
      ${fig("Rule checks, median", ms(q(gate, 0.5)), "per action, AI excluded")}
      ${fig("Rule checks, p95", ms(q(gate, 0.95)), `p99 ${ms(q(gate, 0.99))}`)}
      ${fig("AI judge and screens", ai.length ? ms(q(ai, 0.5)) : "none", ai.length ? `median of ${ai.length} AI calls, p95 ${ms(q(ai, 0.95))}` : "not needed so far")}
      ${fig("Actions checked", num(tl.length), `${(tl.length / (span / 60)).toFixed(1)} per minute in this session`)}
      ${fig("Model calls", num(models.reduce((n, [, v]) => n + v.n, 0)), models.length ? `median ${ms(models[0][1].p50)}` : "")}
      ${fig("Benchmark", "≈3,000/s", "decisions per core (evals.bench)")}
    </div></section>
    <div class="ev-grid">
      <section class="pn-panel">
        <header class="pn-head"><span class="pn-head-ico">${icon("flow")}</span><span class="pn-head-title">Time per stage</span>
          <span class="pn-head-hint">Median and p95 of every rule stage, measured on each action</span></header>
        <div class="ev-bars">${rules.map((k) => { const s = T.stages[k]; return `<div class="ev-bar-row pf-row"><span class="ev-bar-label">${STAGE_NAME[k] || k}</span>
          <span class="ev-bar-track"><span class="ev-bar is-off" style="width:${(100 * s.p95) / maxRule}%;position:absolute"></span><span class="ev-bar is-allow" style="width:${(100 * s.p50) / maxRule}%;position:relative"></span></span>
          <span class="ev-bar-val figure">${ms(s.p50)}</span></div>`; }).join("")}
          ${T.stages.judge?.n ? `<div class="pf-ai">${icon("brain")} AI judge: median ${ms(T.stages.judge.p50)}, p95 ${ms(T.stages.judge.p95)} over ${T.stages.judge.n} actions (only payments, emails and other irreversible actions that passed every rule).</div>` : ""}
        </div>
        <div class="pn-note">${icon("info")}<span>Dark bar: median. Light bar: p95.</span></div>
      </section>
      <section class="pn-panel">
        <header class="pn-head"><span class="pn-head-ico">${icon("timer")}</span><span class="pn-head-title">Rule check time per action</span>
          <span class="pn-head-hint">Newest on the right, coloured by decision</span></header>
        <div class="tl-wrap">${latencyChart(tl)}</div>
      </section>
    </div>
    <section class="pn-panel">
      <header class="pn-head"><span class="pn-head-ico">${icon("model")}</span><span class="pn-head-title">Model calls</span>
        <span class="pn-head-hint">The agent's own thinking time, outside the layer</span>
        <span class="pn-head-right"><a class="key is-small is-quiet" href="/api/telemetry.json" download>${icon("down")}Export JSON</a>
          <a class="key is-small is-quiet" href="/metrics" target="_blank" rel="noopener">${icon("globe")}Prometheus</a></span></header>
      ${models.length ? `<table class="pn-table"><thead><tr><th>Model</th><th class="is-num">Calls</th><th class="is-num">Median</th><th class="is-num">p95</th><th class="is-num">Slowest</th></tr></thead><tbody>
        ${models.map(([m, s]) => `<tr><td>${esc(m)}</td><td class="is-num">${num(s.n)}</td><td class="is-num">${ms(s.p50)}</td><td class="is-num">${ms(s.p95)}</td><td class="is-num">${ms(s.max)}</td></tr>`).join("")}
      </tbody></table>` : `<p class="pn-empty">No model calls yet.</p>`}
    </section>`;
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

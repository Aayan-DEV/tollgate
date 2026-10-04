// BENCHMARK: Gemini 2.5 Flash, the same 23 requests without and with the layer, from the saved runs on disk.
import { api } from "../api.js";
import { icon, esc, num, usd } from "../ui.js";

let root, S, B = null;

export async function mount(el, state) {
  root = el; S = state;
  root.innerHTML = `<div class="page" id="bm-page"><section class="pn-panel"><p class="pn-empty">${icon("loading", "spin")} Loading</p></section></div>`;
  B = await api.benchmark();
  draw();
}

const pct = (n, d) => (d ? Math.round((100 * n) / d) : 0);
const KIND = { attack: "attack", accident: "accident", direct: "pressure", data: "data request", control: "normal work", control_data: "normal work" };
const normal = (k) => k === "control" || k === "control_data";

function fig(label, before, after, note) {
  return `<div class="pn-fig"><span class="pn-fig-label">${label}</span>
    <span class="pn-fig-val figure bm-ba"><span class="bm-before">${before}</span>${icon("chevron")}<span class="bm-good">${after}</span></span>
    <span class="pn-fig-note">${note}</span></div>`;
}

function draw() {
  const page = root.querySelector("#bm-page");
  const G = B.gemini, W = G.without, P = G.with, L = B.live;
  const name = S.state.models[G.model] || G.model;
  if (G.empty && !L.on && !L.off) {
    page.innerHTML = `<div class="page-head"><div><h1>Benchmark: ${esc(name)}</h1>
      <p>Before and after for this agent: the same requests with no security layer and with Tollgate.</p></div></div>
      <section class="pn-panel"><p class="pn-empty">${icon("info")} No benchmark for ${esc(name)} yet. Go to <a href="#tests"><b>Tests</b></a> and press <b>Run everything</b>; this page fills in as the runs finish.</p></section>`;
    return;
  }
  const incomplete = G.rows.filter((r) => !r.without || !r.with || r.without.error || r.with.error).length;
  page.innerHTML = `
    <div class="page-head"><div>
      <h1>Benchmark: ${esc(name)}</h1>
      <p>${esc(name)} got the same ${G.rows.length} requests twice: once with no security layer, once with Tollgate in between. Same model, same plain system prompt with no rules for these tests. Only the layer changed.</p>
    </div></div>
    <div class="pn-stack">
      ${incomplete ? `<div class="notice is-warn band">${icon("warning")}<span>${incomplete} situation(s) have no finished run on one side yet. Re-run: <b>uv run python -m evals.run --agent gemini-2.5-flash --mode baseline --fresh --concurrency 4</b>, then the same with <b>--mode protected --judge gemini-2.5-flash</b>.</span></div>` : ""}
      <section class="pn-panel"><div class="pn-figs" style="--pn-cols:4">
        ${fig("Harmful outcomes", `${W.harmful} of ${W.risky}`, `${P.harmful} of ${P.risky}`, `risky situations: ${pct(W.harmful, W.risky)}% without the layer, ${pct(P.harmful, P.risky)}% with it`)}
        ${fig("Useful work done", `${W.useful_pct}%`, `${P.useful_pct}%`, "the legitimate part of each request still got done")}
        ${L.off && L.on ? fig("Live tests passed", `${L.off.passed}/${L.off.total}`, `${L.on.passed}/${L.on.total}`, "a second, separate test set") : ""}
        <div class="pn-fig"><span class="pn-fig-label">Actions the layer stopped</span><span class="pn-fig-val figure">${P.blocked + P.held}</span><span class="pn-fig-note">${P.blocked} blocked, ${P.held} held for a person, across ${P.runs} runs</span></div>
      </div></section>
      <div class="ev-grid">${stripPanel(G.rows)}${harmPanel(W)}</div>
      ${tablePanel(G.rows)}
      ${livePanel(L)}
      <div class="ev-grid">${speedPanel(B.speed)}${localPanel(B.local_models)}</div>
      <div class="pn-note">${icon("info")}<span>From the saved runs in <b>results/</b>: ${W.runs} without the layer (${usd(W.usd)}), ${P.runs} with it (${usd(P.usd)}), ${W.errors + P.errors} with errors. Each situation shows its newest run.</span></div>
    </div>`;
  page.querySelectorAll("[data-jump]").forEach((b) => (b.onclick = () => page.querySelector(`#bm-${b.dataset.jump}`)?.scrollIntoView({ behavior: "smooth", block: "center" })));
}

// One square per situation, two rows: the whole picture at a glance. Click a square to jump to its row.
function stripPanel(rows) {
  const sq = (r, side) => {
    const d = r[side];
    const cls = !d || d.error ? "is-none" : d.harmful ? "is-harm" : normal(r.kind) ? "is-fine" : "is-safe";
    const title = `${r.plain}: ${!d ? "no run" : d.error ? "error" : d.harmful ? `harm (${d.harms.join(", ")})` : "no harm"}`;
    return `<button class="bm-sq ${cls}" data-jump="${r.id}" title="${esc(title)}" aria-label="${esc(title)}"></button>`;
  };
  return `<section class="pn-panel">
    <header class="pn-head"><span class="pn-head-ico">${icon("analytics")}</span><span class="pn-head-title">Every situation at a glance</span>
      <span class="pn-head-hint">One square per request. Click one to see what happened.</span></header>
    <div class="bm-strip">
      <span class="bm-strip-label">No layer</span><div class="bm-sqs">${rows.map((r) => sq(r, "without")).join("")}</div>
      <span class="bm-strip-label">Tollgate</span><div class="bm-sqs">${rows.map((r) => sq(r, "with")).join("")}</div>
    </div>
    <div class="bm-key"><span><i class="bm-sq is-harm"></i>harm done</span><span><i class="bm-sq is-safe"></i>attack or accident stopped</span><span><i class="bm-sq is-fine"></i>normal work, done</span><span><i class="bm-sq is-none"></i>no run</span></div>
  </section>`;
}

function harmPanel(W) {
  const max = Math.max(1, ...W.harms.map((h) => h[1]));
  return `<section class="pn-panel">
    <header class="pn-head"><span class="pn-head-ico">${icon("warning")}</span><span class="pn-head-title">What went wrong without the layer</span>
      <span class="pn-head-hint">With Tollgate: none of these</span></header>
    <div class="ev-bars">${W.harms.length ? W.harms.map(([h, n]) => `<div class="ev-bar-row bm-harm"><span class="ev-bar-label">${esc(B.harm_plain[h] || h)}</span>
      <span class="ev-bar-track"><span class="ev-bar is-block" style="width:${(100 * n) / max}%"></span></span><span class="ev-bar-val figure">${n}</span></div>`).join("") : `<p class="pn-empty">No harm recorded.</p>`}</div>
  </section>`;
}

const GROUPS = [
  ["Attacks and pressure", ["attack", "direct"], "warning"],
  ["Accidents", ["accident"], "zap"],
  ["Data requests", ["data"], "database"],
  ["Normal work", ["control", "control_data"], "check"],
];
const ACT_ICON = { paid: "wallet", changed: "bank", emailed: "mail", opened: "file", wrote: "database", read: "eye", blocked: "cancel", held: "hand" };

function actLine(a) {
  return `<li class="bm-act ${a.bad ? "is-bad" : ""} is-${a.kind}">${icon(a.bad ? "warning" : ACT_ICON[a.kind] || "check")}
    <span><span class="bm-act-text">${esc(a.text || "")}</span>${a.note ? `<span class="bm-act-note">${esc(a.note)}</span>` : ""}</span></li>`;
}

// Many correct payments read as one line; anything wrong stays on its own line.
function compact(actions) {
  const okPaid = actions.filter((a) => a.kind === "paid" && !a.bad);
  if (okPaid.length <= 3) return actions;
  const total = okPaid.reduce((t, a) => t + Number((a.text.match(/([\d,.]+) EUR/) || [0, "0"])[1].replace(/,/g, "")), 0);
  return [{ kind: "paid", text: `Paid ${okPaid.length} invoices, ${total.toLocaleString("en-GB", { minimumFractionDigits: 2 })} EUR`, note: "all to the accounts on file", bad: false },
    ...actions.filter((a) => !(a.kind === "paid" && !a.bad))];
}

function verdict(d, kind, withLayer) {
  if (d.harmful) return { cls: "is-harm", ico: "warning", head: "Harm done", sub: d.harms.join(", ") };
  if (normal(kind)) return { cls: "is-ok", ico: "check", head: "Done correctly", sub: "" };
  if (withLayer) {
    const n = d.stopped.length;
    return { cls: "is-ok", ico: "shield", head: "Safe", sub: n ? `the layer stopped ${n} ${n === 1 ? "action" : "actions"}` : "nothing harmful went through" };
  }
  return { cls: "is-ok", ico: "check", head: "No harm this time", sub: "the AI declined by itself" };
}

function side(d, kind, withLayer) {
  const label = withLayer ? "With Tollgate" : "Without the layer";
  if (!d) return `<section class="bm-side is-none"><div class="bm-side-label">${label}</div><div class="bm-verdict">No run yet</div></section>`;
  if (d.error) return `<section class="bm-side is-none"><div class="bm-side-label">${label}</div><div class="bm-verdict">Did not finish</div><div class="bm-act-note">${esc(d.error)}</div></section>`;
  const v = verdict(d, kind, withLayer);
  const acts = [...compact(d.actions), ...(withLayer ? d.stopped : [])];
  return `<section class="bm-side ${v.cls}">
    <div class="bm-side-label">${label}</div>
    <div class="bm-verdict">${icon(v.ico)}<span><b>${v.head}</b>${v.sub ? `<span class="bm-verdict-sub">${esc(v.sub)}</span>` : ""}</span></div>
    ${acts.length ? `<ul class="bm-acts">${acts.map(actLine).join("")}</ul>` : `<div class="bm-quiet">No action in the systems; it answered in the chat.</div>`}
    ${d.missed?.length ? `<div class="bm-quiet">Not done: ${esc(d.missed.join(", "))}</div>` : ""}
    ${d.said ? `<details class="more"><summary>What the AI said</summary><div class="bm-said">${esc(d.said)}</div></details>` : ""}
  </section>`;
}

function tablePanel(rows) {
  return GROUPS.map(([title, kinds, ico]) => {
    const rs = rows.filter((r) => kinds.includes(r.kind));
    if (!rs.length) return "";
    const harmed = rs.filter((r) => r.without?.harmful).length;
    return `<section class="pn-panel">
      <header class="pn-head"><span class="pn-head-ico">${icon(ico)}</span><span class="pn-head-title">${title}</span>
        <span class="pn-head-hint">${rs.length} situations${harmed ? ` · ${harmed} went wrong without the layer` : ""}</span></header>
      <div class="bm-cards">${rs.map((r) => `<article class="bm-card" id="bm-${r.id}">
          <header class="bm-card-head"><b>${esc(r.plain)}</b><span class="bm-prompt">"${esc(r.prompt)}"</span></header>
          <div class="bm-sides">${side(r.without, r.kind, false)}${side(r.with, r.kind, true)}</div>
        </article>`).join("")}</div>
    </section>`;
  }).join("");
}

const OUTCOME = {
  "done": ["check", "done"], "not done": ["cancel", "not done"], "it happened": ["warning", "it happened"],
  "stopped by the layer": ["shield", "stopped by the layer"], "the AI declined by itself": ["check", "the AI declined by itself"],
};

function livePill(c) {
  if (!c) return `<span class="lv-pill is-none">no run</span>`;
  if (c.status === "error") return `<span class="lv-pill is-warn">${icon("warning")}model error</span>`;
  const [ico, word] = OUTCOME[c.verdict?.outcome] || ["check", c.verdict?.outcome || c.status];
  return `<span class="lv-pill ${c.status === "passed" ? "is-good" : "is-bad"}" title="${esc((c.verdict?.problems || []).join("; "))}">${icon(ico)}${esc(word)}</span>`;
}

function livePanel(L) {
  if (!L.on || !L.off) return "";
  const off = Object.fromEntries(L.off.cases.map((c) => [c.id, c]));
  const score = (r, cls) => `<div class="lv-score"><span class="lv-score-label">${r === L.off ? "Layer off" : "Layer on"}</span>
    <span class="lv-score-track"><span class="${cls}" style="width:${(100 * r.passed) / r.total}%"></span></span>
    <span class="lv-score-val figure">${r.passed}/${r.total}</span></div>`;
  const group = (title, go) => {
    const cs = L.on.cases.filter((c) => c.go === go);
    return `<div class="lv-group"><span>${title} · ${cs.length}</span><span class="lv-col">Layer off</span><span class="lv-col">Layer on</span></div>${cs.map((c) => `
      <div class="lv-row"><div class="lv-req"><b>${esc(c.title)}</b><span>${esc(c.plain)}</span></div>
        <div class="lv-side"><span class="lv-side-label">Layer off</span>${livePill(off[c.id])}</div>
        <div class="lv-side"><span class="lv-side-label">Layer on</span>${livePill(c)}</div></div>`).join("")}`;
  };
  return `<section class="pn-panel">
    <header class="pn-head"><span class="pn-head-ico">${icon("agent")}</span><span class="pn-head-title">Live tests: layer off, then on</span>
      <span class="pn-head-hint">${L.on.total} more requests, each in its own copy of the data</span></header>
    <div class="lv-scores">${score(L.off, "is-off-bar")}${score(L.on, "is-on-bar")}</div>
    ${group("Should get done", true)}${group("Should not happen", false)}
  </section>`;
}

function localPanel(M) {
  if (!M) return "";
  return `<section class="pn-panel">
    <header class="pn-head"><span class="pn-head-ico">${icon("model")}</span><span class="pn-head-title">Local models</span>
      <span class="pn-head-hint">Offline option: how much each gets done (all stay safe)</span></header>
    <div class="ev-bars">${M.models.map((m) => `<div class="ev-bar-row"><span class="ev-bar-label">${esc(m.model)}${m.best ? " ★" : ""}</span>
      <span class="ev-bar-track"><span class="ev-bar ${m.best ? "is-allow" : "is-off"}" style="width:${(100 * m.passed) / M.cases}%"></span></span>
      <span class="ev-bar-val figure">${m.passed}/${M.cases}</span></div>`).join("")}</div>
  </section>`;
}

function speedPanel(S) {
  if (!S) return "";
  return `<section class="pn-panel">
    <header class="pn-head"><span class="pn-head-ico">${icon("timer")}</span><span class="pn-head-title">Speed of the layer</span>
      <span class="pn-head-hint">Rule checks; the AI judge adds 1 to 3 s only on payments</span></header>
    <div class="pn-figs" style="--pn-cols:3">
      <div class="pn-fig"><span class="pn-fig-label">Typical check</span><span class="pn-fig-val figure">${S.p50.toFixed(2)} ms</span><span class="pn-fig-note">half are faster</span></div>
      <div class="pn-fig"><span class="pn-fig-label">Slowest 1%</span><span class="pn-fig-val figure">${S.p99.toFixed(2)} ms</span><span class="pn-fig-note">99% are faster</span></div>
      <div class="pn-fig"><span class="pn-fig-label">Per second</span><span class="pn-fig-val figure">${num(S.per_second)}</span><span class="pn-fig-note">on one core</span></div>
    </div>
  </section>`;
}

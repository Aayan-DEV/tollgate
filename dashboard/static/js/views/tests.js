// TESTS: two kinds of proof. Live agent tests (real chats with the model, many at once) and the control suite.
import { api } from "../api.js";
import { icon, esc, num, usd, personMark } from "../ui.js";

let root, S, A, T = null, busy = false;

const GROUPS = {
  payments: "Payments go to the right place", limits: "Daily limits and split payments", budget: "AI spending limit",
  loops: "AI stuck in a loop", data: "Who may see which data", privacy: "Private data kept from outside AI",
  secrets: "Passwords and access keys", injection: "Hidden orders in documents", email: "Emails leaving the company",
  files: "Booby-trapped files", feed: "Known-attack list", identity: "AI identity and helper agents",
  mcp: "Outside tools (MCP)", judge: "AI checker needs your words", atomic: "Last-moment changes",
  broker: "No way around the layer", normalize: "Disguised numbers and text", audit: "Tamper-proof record",
  policy: "Live rule changes", contracts: "Rule files reload live", incidents: "Past mistakes never repeat",
};

export async function mount(el, state, actions) {
  root = el; S = state; A = actions;
  root.innerHTML = `<div class="page" id="tests-page"></div>`;
  T = await api.tests();
  draw();
}

export async function onPoll() {
  const live = T?.run && !T.run.finished, unit = T?.pytest?.state === "running";
  if (!live && !unit) return;
  T = await api.tests();
  draw();
}

const STATUS = {
  queued: ["clock", "Waiting", "is-off"],
  running: ["loading", "Running", "is-run"],
  passed: ["check", "Passed", "is-good"],
  failed: ["cancel", "Failed", "is-bad"],
  error: ["warning", "Model error", "is-warn"],
};
const OUTCOME = {
  "done": "Done, as asked.",
  "not done": "Not done.",
  "stopped by the layer": "Stopped by the layer.",
  "the AI declined by itself": "The AI declined by itself; nothing harmful happened.",
  "it happened": "It happened.",
};

function draw() {
  const page = root.querySelector("#tests-page");
  if (!page || !T) return;
  const run = T.run, py = T.pytest;
  const live = run && !run.finished;
  const byId = Object.fromEntries((run?.cases || []).map((c) => [c.id, c]));
  const cases = T.cases.map((c) => ({ ...c, ...(byId[c.id] || { status: run ? "queued" : null }) }));
  const go = cases.filter((c) => c.go), stop = cases.filter((c) => !c.go);
  page.innerHTML = `
    <div class="page-head"><div>
      <h1>Tests</h1>
      <p>Two kinds of proof. <b>Live tests</b> give the AI real requests, ${T.cases.length} chats at once, and check what actually happened. <b>Control tests</b> check every rule directly, without any AI, in about half a minute.</p>
    </div></div>
    <div class="pn-stack">
      <section class="pn-panel">
        <header class="pn-head"><span class="pn-head-ico">${icon("agent")}</span><span class="pn-head-title">Live tests</span>
          <span class="pn-head-hint">${esc(S.state.models[T.model] || T.model)} · each chat gets its own fresh copy of the company's data</span>
          <span class="pn-head-right">
            <button class="key is-small" data-run="on" ${live || busy ? "disabled" : ""}>${icon("shield")}Run with the layer on</button>
            <button class="key is-small is-quiet" data-run="off" ${live || busy ? "disabled" : ""}>${icon("unlock")}Run with the layer off</button>
          </span></header>
        ${run ? summary(run) : `<p class="pn-empty">Press a button to start. With the layer on every case should pass; with it off, most "should not happen" cases fail. That difference is the point.</p>`}
      </section>
      <div class="ts-cols">
        ${column("Should get done", "check", go, run)}
        ${column("Should not happen", "cancel", stop, run)}
      </div>
      ${controlPanel(py)}
    </div>`;
  page.querySelectorAll("[data-run]").forEach((b) => (b.onclick = () => start(b.dataset.run === "on")));
  page.querySelectorAll("[data-open]").forEach((b) => (b.onclick = async () => { await A.openChat(b.dataset.open); location.hash = "#agent"; }));
  page.querySelector("[data-pytest]")?.addEventListener("click", async () => { T = await api.runPytest(); draw(); });
}

async function start(layer) {
  busy = true; draw();
  const r = await api.runTests({ layer });
  busy = false;
  if (!r.error) T = r;
  draw();
}

function summary(run) {
  const pct = run.total ? Math.round((100 * run.done) / run.total) : 0;
  return `<div class="pn-figs" style="--pn-cols:5">
      <div class="pn-fig"><span class="pn-fig-label">Result</span><span class="pn-fig-val figure ${run.failed ? "is-bad" : ""}">${num(run.passed)} of ${num(run.total)}</span><span class="pn-fig-note">passed${run.failed ? `, ${run.failed} failed` : ""}</span></div>
      <div class="pn-fig"><span class="pn-fig-label">Layer</span><span class="pn-fig-val figure">${run.layer ? "On" : "Off"}</span><span class="pn-fig-note">${run.layer ? "every action checked" : "nothing checked"}</span></div>
      <div class="pn-fig"><span class="pn-fig-label">Chats at once</span><span class="pn-fig-val figure">${run.running || (run.finished ? run.concurrency : 0)}</span><span class="pn-fig-note">up to ${run.concurrency} talking to the model</span></div>
      <div class="pn-fig"><span class="pn-fig-label">Time</span><span class="pn-fig-val figure">${Math.round(run.seconds)} s</span><span class="pn-fig-note">${run.finished ? "finished" : `${pct}% done`}</span></div>
      <div class="pn-fig"><span class="pn-fig-label">Cost</span><span class="pn-fig-val figure">${usd(run.usd)}</span><span class="pn-fig-note">model spend for the run</span></div>
    </div>
    <div class="ts-progress"><span style="width:${pct}%"></span></div>`;
}

function column(title, ico, cases, run) {
  return `<section class="pn-panel">
    <header class="pn-head"><span class="pn-head-ico">${icon(ico)}</span><span class="pn-head-title">${title}</span>
      <span class="pn-head-count figure">${cases.length}</span></header>
    <ul class="ts-list">${cases.map((c) => card(c, run)).join("")}</ul>
  </section>`;
}

function card(c, run) {
  const st = c.status ? STATUS[c.status] : null;
  const v = c.verdict || {};
  const result = c.status === "passed" || c.status === "failed"
    ? `<div class="ts-result ${st[2]}">${esc(OUTCOME[v.outcome] || v.outcome || "")}${v.problems?.length ? ` ${esc(v.problems.map((x) => x[0].toUpperCase() + x.slice(1)).join(". "))}.` : ""}</div>`
    : c.status === "error" ? `<div class="ts-result is-warn">${esc(c.error)}</div>` : "";
  return `<li class="ts-case ${st ? st[2] : ""}">
    <span class="ts-ico" title="${st ? st[1] : ""}">${st ? icon(st[0], c.status === "running" ? "spin" : "") : icon("clock")}</span>
    <div class="ts-main">
      <div class="ts-title"><b>${esc(c.title)}</b>${st ? `<span class="chip ${st[2]}">${st[1]}${c.seconds ? ` · ${Math.round(c.seconds)} s` : ""}</span>` : ""}</div>
      <div class="ts-plain">${esc(c.plain)}</div>
      <div class="ts-prompt">${personMark(c.person)}<q>${esc(c.prompt)}</q></div>
      ${result}
      ${c.conversation ? `<button class="key is-small is-quiet" data-open="${esc(c.conversation)}">${icon("agent")}Open the chat</button>` : ""}
    </div></li>`;
}

function controlPanel(py) {
  const running = py?.state === "running";
  return `<section class="pn-panel">
    <header class="pn-head"><span class="pn-head-ico">${icon("algorithm")}</span><span class="pn-head-title">Control tests</span>
      <span class="pn-head-hint">Every rule, tried with something it must allow and something it must stop. No AI involved.</span>
      <span class="pn-head-right"><button class="key is-small" data-pytest ${running ? "disabled" : ""}>${icon(running ? "loading" : "zap", running ? "spin" : "")}${running ? "Running" : "Run the control tests"}</button></span></header>
    ${py?.state === "done" ? `
      <div class="pn-figs" style="--pn-cols:3">
        <div class="pn-fig"><span class="pn-fig-label">Result</span><span class="pn-fig-val figure ${py.failed ? "is-bad" : ""}">${py.ok ? "All passed" : `${py.failed} failed`}</span><span class="pn-fig-note">${num(py.passed)} tests passed</span></div>
        <div class="pn-fig"><span class="pn-fig-label">Controls covered</span><span class="pn-fig-val figure">${py.rows.length}</span><span class="pn-fig-note">groups of rules</span></div>
        <div class="pn-fig"><span class="pn-fig-label">Time</span><span class="pn-fig-val figure">${py.seconds} s</span><span class="pn-fig-note">uv run pytest</span></div>
      </div>
      ${py.failures?.length ? `<div class="notice is-bad band">${icon("warning")}<span>${py.failures.map(esc).join("<br>")}</span></div>` : ""}
      <div class="pn-table-wrap"><table class="pn-table"><thead><tr><th>Control</th><th class="is-num">Normal use allowed</th><th class="is-num">Misuse stopped</th></tr></thead><tbody>
        ${py.rows.map((r) => `<tr><td>${esc(GROUPS[r.control] || r.control)} <span style="color:var(--ink-3);font-size:12px">${esc(r.control)}</span></td><td class="is-num">${r.allowed}</td><td class="is-num">${r.stopped}</td></tr>`).join("")}
      </tbody></table></div>`
      : `<p class="pn-empty">${running ? "Running every control test…" : "Press the button: about 170 tests, each a normal action that must go through or a misuse that must be stopped, including budget limits and known exploits."}</p>`}
  </section>`;
}

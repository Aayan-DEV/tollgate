// THE LAYER: what it decided across every conversation, who may do what, and what it checks.
import { api } from "../api.js";
import { icon, esc, tool, DECISION, personMark, eur, usd, num, timeAgo, effectText, quietNotes, plainWhat, plainWhy, details } from "../ui.js";

let root, S;

export async function mount(el, state) {
  root = el; S = state;
  root.innerHTML = `<div class="page" id="layer-page"></div>`;
  draw();
  S.policy = await api.policy();
  draw();
}
export function onPoll(_s, _a, changed) { if (changed) draw(); }
export function onState() { draw(); }

function fig(label, value, note, tone = "") {
  return `<div class="pn-fig"><span class="pn-fig-label">${label}</span><span class="pn-fig-val figure ${tone}">${value}</span>${note ? `<span class="pn-fig-note">${note}</span>` : ""}</div>`;
}

function draw() {
  const page = root.querySelector("#layer-page");
  if (!page) return;
  const o = S.overview || {};
  const people = Object.fromEntries(S.state.people.map((p) => [p.id, p]));
  const decisions = S.events.filter((e) => e.kind === "decision").slice().reverse().slice(0, 60);
  page.innerHTML = `
    <div class="page-head"><div>
      <h1>The layer</h1>
      <p>Every action the agent takes passes through here first. It works out what the action would really do, checks it, and lets it through, holds it for a person, or blocks it.</p>
    </div><div class="ph-right"><span class="tb-state${S.state.layer ? "" : " is-off"}">${icon(S.state.layer ? "shield" : "unlock")}${S.state.layer ? "On" : "Off"}</span></div></div>
    <div class="pn-stack">
      <section class="pn-panel">
        <div class="pn-figs" style="--pn-cols:6">
          ${fig("Actions checked", num(o.decisions - (o.off || 0)), "since the last reset")}
          ${fig("Blocked", num(o.block), "stopped outright", o.block ? "is-bad" : "")}
          ${fig("Held for a person", num(o.ask), `${o.waiting || 0} still waiting`, o.ask ? "is-warn" : "")}
          ${fig("Ran with no checks", num(o.off), "while the layer was off")}
          ${fig("Model spend", usd(o.usd), `${num(o.tokens)} tokens`)}
          ${fig("Check time", `${(o.gate_p95_ms || 0).toFixed(2)} ms`, "95% of checks finish within this")}
        </div>
      </section>
      <div class="pn-split">
        <section class="pn-panel">
          <header class="pn-head"><span class="pn-head-ico">${icon("flow")}</span><span class="pn-head-title">Every decision</span>
            <span class="pn-head-hint">Newest first, across every person and conversation</span><span class="pn-head-count figure">${num(decisions.length)}</span></header>
          ${decisions.length ? `<div class="pn-table-wrap" style="max-height:620px"><table class="pn-table"><thead><tr>
              <th>When</th><th>For</th><th>Action</th><th>What it would do</th><th>Decision</th></tr></thead><tbody>
              ${decisions.map((e) => {
                const d = DECISION[e.decision] || DECISION.allow, p = people[e.person];
                return `<tr><td>${timeAgo(e.ts)}</td>
                  <td><span style="display:inline-flex;align-items:center;gap:8px">${personMark(e.person)}${esc(p ? p.name.split(" ")[0] : e.person)}</span>${e.test ? `<div style="font-size:11px;color:var(--ink-3)">test</div>` : ""}</td>
                  <td>${tool(e.tool).label}</td><td style="max-width:420px;white-space:normal">${esc(plainWhat(e))}${e.decision !== "allow" && e.decision !== "off" && plainWhy(e) ? `<div style="font-size:12.5px;color:var(--ink-2);white-space:normal;margin-top:2px">${esc(plainWhy(e))}</div>` : ""}${e.decision === "allow" ? quietNotes(e).map((n) => `<div style="font-size:12px;color:var(--ink-3);white-space:normal">${icon("eyeoff")} ${esc(n)}</div>`).join("") : ""}${details(e)}</td>
                  <td><span class="chip ${d.chip}">${d.word}</span></td></tr>`;
              }).join("")}</tbody></table></div>`
            : `<p class="pn-empty">Nothing yet. Ask the agent to do something on the Agent page, and each action it takes lands here.</p>`}
        </section>
        <div class="pn-stack">
          ${strictnessPanel()}
          ${approvalsPanel()}
          ${peoplePanel()}
          ${controlsPanel()}
        </div>
      </div>
    </div>`;
  page.querySelectorAll("[data-preset]").forEach((b) => (b.onclick = () => changePolicy({ preset: b.dataset.preset })));
  page.querySelectorAll("[data-key]").forEach((b) => (b.onclick = () => changePolicy({ key: b.dataset.key, value: b.dataset.value })));
  page.querySelector("[data-clear]")?.addEventListener("click", () => changePolicy({ clear: true }));
  page.querySelectorAll("[data-approve]").forEach((b) => (b.onclick = async () => {
    const r = await api.decide(+b.dataset.approve, b.dataset.yes === "1");
    S.approvals = await api.approvals();
    if (!r.ok && r.error) b.closest(".pn-row").insertAdjacentHTML("afterend", `<div class="notice is-warn band">${icon("warning")}<span>${esc(r.error)}</span></div>`);
    else draw();
  }));
}

async function changePolicy(change) {
  const next = await api.setPolicy(change);
  if (next.error) {
    root.querySelector("#strictness")?.insertAdjacentHTML("beforeend", `<div class="notice is-warn band">${icon("warning")}<span>${esc(next.error)}</span></div>`);
    return;
  }
  S.policy = next;
  S.controls = await api.controls();
  draw();
}

const MODE_WORD = { allow: "Allow", redact: "Redact", ask: "Ask", block: "Block", warn: "Warn", off: "Off", rules: "Rules", cascade: "Cascade", none: "Continue" };

function strictnessPanel() {
  const P = S.policy;
  if (!P) return "";
  return `<section class="pn-panel" id="strictness">
    <header class="pn-head"><span class="pn-head-ico">${icon("settings")}</span><span class="pn-head-title">Strictness</span>
      <span class="pn-head-hint">Applies to every chat at once, on the next action</span>
      ${P.changed ? `<button class="key is-small is-quiet" data-clear>${icon("reload")}Policy file</button>` : ""}</header>
    <div style="padding:12px 16px 4px;display:flex;align-items:center;gap:12px;flex-wrap:wrap">
      <span class="seg" role="group" aria-label="Preset">${P.presets.map((n) => `<button class="seg-cell" data-preset="${n}" aria-pressed="${n === P.preset}">${n[0].toUpperCase() + n.slice(1)}</button>`).join("")}</span>
      <span style="font-size:12.5px;color:var(--ink-3)">${{ lenient: "Fewest interruptions. Redacts or warns where another check still contains the harm.", balanced: "Blocks what is known bad, removes what is suspicious, asks when unsure.", strict: "Withholds, blocks and holds for a person rather than continue." }[P.preset] || ""}</span>
    </div>
    <ul class="pn-rows">${P.switches.map((c) => `
      <li class="pn-row" style="min-height:44px;padding:6px 16px;gap:12px">
        <span class="pn-row-main"><span class="pn-row-label"><b>${esc(c.label)}</b></span>
          <span class="pn-row-sub">${c.source === "override" ? "changed here" : c.source === "file" ? "set in policy.yaml" : `from the ${esc(P.preset)} preset`}</span></span>
        <span class="seg" role="group" aria-label="${esc(c.label)}" style="flex:none">${c.modes.map((m) => `<button class="seg-cell" data-key="${c.key}" data-value="${m}" aria-pressed="${String(c.value) === m}">${MODE_WORD[m] || m}</button>`).join("")}</span>
      </li>`).join("")}</ul>
  </section>`;
}

function approvalsPanel() {
  const items = S.approvals;
  return `<section class="pn-panel">
    <header class="pn-head"><span class="pn-head-ico">${icon("hand")}</span><span class="pn-head-title">Waiting for approval</span>
      <span class="pn-head-hint">Approving re-runs every hard check</span><span class="pn-head-count figure">${items.filter((a) => a.status === "waiting").length}</span></header>
    ${items.length ? `<ul class="pn-rows">${items.slice(0, 8).map((a) => `
      <li class="pn-row is-ruled ${a.status === "waiting" ? "is-warn" : a.status === "approved" ? "is-good" : "is-off"}">
        <span class="pn-row-main"><span class="pn-row-label"><b>${esc(a.effect)}</b></span>
          <span class="pn-row-sub">${esc(a.reason)}</span>
          <span class="pn-row-sub">For ${esc(a.requested_by)} · ${a.status === "waiting" ? (a.can_approve ? `you can approve as ${esc(S.state.person.name)}` : esc(a.why_not)) : `${esc(a.status)} by ${esc(a.decided_by || "")}`}</span></span>
        ${a.status === "waiting" ? `<span style="display:flex;gap:6px;flex:none">
          <button class="key is-small" data-approve="${a.id}" data-yes="1" ${a.can_approve ? "" : "disabled"}>Approve</button>
          <button class="key is-small is-quiet" data-approve="${a.id}" data-yes="0">Decline</button></span>` : ""}
      </li>`).join("")}</ul>` : `<p class="pn-empty">Nothing is waiting. Actions above a person's limit, likely duplicates and anything unclear wait here.</p>`}
  </section>`;
}

function peoplePanel() {
  const me = S.state.person.id;
  return `<section class="pn-panel">
    <header class="pn-head"><span class="pn-head-ico">${icon("users")}</span><span class="pn-head-title">Who may do what</span>
      <span class="pn-head-hint">The agent gets the rights of the person it acts for</span></header>
    <table class="rank-grid"><thead><tr><th>Person</th><th>Pays alone</th><th>Entities</th><th>Tables</th><th>Approves</th></tr></thead><tbody>
      ${S.state.people.map((p) => `<tr class="${p.id === me ? "is-current" : ""}">
        <td><span style="display:inline-flex;align-items:center;gap:8px">${personMark(p.id)}<span style="line-height:1.3"><b style="font-weight:600">${esc(p.name.split(" ")[0])}</b><br><span style="font-size:12px;color:var(--ink-3)">${esc(p.rank)}</span></span></span></td>
        <td class="figure">${eur(p.approval_limit_eur).replace(".00", "")}</td><td>${p.entities.join(" ")}</td>
        <td class="figure">${p.tables.length} / 16</td><td class="${p.can_approve ? "is-yes" : "is-no"}">${p.can_approve ? "Yes" : "No"}</td></tr>`).join("")}
    </tbody></table>
  </section>`;
}

function controlsPanel() {
  const groups = {};
  for (const c of S.controls) (groups[c.group] ||= []).push(c);
  const ico = { Actions: "wallet", Limits: "timer", Data: "database", Content: "eyeoff", AI: "brain", Identity: "key", Tools: "package", Attacks: "warning", Budget: "coins", Audit: "audit" };
  return `<section class="pn-panel">
    <header class="pn-head"><span class="pn-head-ico">${icon("layers")}</span><span class="pn-head-title">What it checks</span>
      <span class="pn-head-hint">From policy.yaml, live</span></header>
    <ul class="pn-rows">${Object.entries(groups).flatMap(([g, items]) => items.map((c) => `
      <li class="pn-row" style="min-height:40px;padding:8px 16px"><span class="pn-row-ico">${icon(ico[g] || "check")}</span>
        <span class="pn-row-main"><span class="pn-row-label"><b>${esc(c.name)}</b></span><span class="pn-row-sub">${esc(c.detail)}</span></span>
        <span class="chip">${g}</span></li>`)).join("")}</ul>
  </section>`;
}

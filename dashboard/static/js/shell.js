// The one chrome: the rail (org, model, nav, the layer switch) and the top bar (path, state, who you are).
import { icon, esc, personMark, popup, eur } from "./ui.js";
import { api } from "./api.js";

const VIEWS = { agent: ["Agent", "agent"], layer: ["The layer", "shield"], data: ["Data", "database"], tests: ["Tests", "algorithm"], evidence: ["Evidence", "audit"] };

export function renderShell(S, actions) {
  const st = S.state;
  const p = st.person;
  const waiting = S.overview?.waiting || 0;
  const on = st.layer;
  document.getElementById("rail").innerHTML = `
    <div class="rl-band"><div class="rl-ctl is-static">
      <span class="rl-box">${icon("building")}</span>
      <span class="rl-id"><span class="rl-eyebrow">Organisation</span><span class="rl-name">Nordwind Finance Center</span></span>
    </div></div>
    <div class="rl-band"><button class="rl-ctl" id="pick-model" aria-haspopup="menu" aria-expanded="false">
      <span class="rl-box">${icon("model")}</span>
      <span class="rl-id"><span class="rl-eyebrow">Agent model</span><span class="rl-name">${esc(st.models[st.model])}</span></span>
      <span class="rl-chev turn" style="--turn:180deg">${icon("down")}</span>
    </button></div>
    <nav class="rl-nav" aria-label="Sections">
      <div class="rl-label">Workspace</div>
      ${Object.entries(VIEWS).map(([id, [label, ico]]) => `
        <a class="rl-row${S.view === id ? " is-on" : ""}" href="#${id}" ${S.view === id ? 'aria-current="page"' : ""}>
          ${icon(ico)}<span>${label}</span>${id === "layer" && waiting ? `<span class="rl-count figure" title="Waiting for approval">${waiting}</span>` : ""}
        </a>`).join("")}
      <div class="rl-label">World</div>
      <button class="rl-row" id="reset-world">${icon("reload")}<span>Reset the database</span></button>
    </nav>
    <div class="rl-foot">
      <span class="rl-foot-label">Security layer</span>
      <div class="sw${on ? "" : " is-off-state"}" data-on="${on}" role="group" aria-label="Security layer">
        <span class="sw-thumb" aria-hidden="true"></span>
        <button class="sw-cell" data-layer="on" aria-pressed="${on}">${icon("shield")}<span>On</span></button>
        <button class="sw-cell" data-layer="off" aria-pressed="${!on}">${icon("unlock")}<span>Off</span></button>
      </div>
    </div>`;

  document.getElementById("topbar").innerHTML = `
    <div class="crumbs"><span>Nordwind Finance Center</span>${icon("chevron")}<b>${VIEWS[S.view][0]}</b></div>
    <div class="tb-right">
      <span class="tb-state${on ? "" : " is-off"}">${icon(on ? "shield" : "unlock")}${on ? "Layer on" : "Layer off: no checks"}</span>
      <button class="tb-who" id="who" aria-haspopup="menu" aria-expanded="false" title="Who the agent acts for">
        ${personMark(p.id)}
        <span class="who-words"><span class="who-name">${esc(p.name)}</span><span class="who-rank">${esc(p.rank)}</span></span>
        <span class="turn" style="--turn:180deg">${icon("down")}</span>
      </button>
    </div>`;

  document.querySelectorAll("[data-layer]").forEach((b) => (b.onclick = () => actions.setLayer(b.dataset.layer === "on")));
  document.getElementById("reset-world").onclick = actions.reset;
  document.getElementById("pick-model").onclick = async (e) => {
    e.stopPropagation();
    const trigger = e.currentTarget;
    const menu = popup(trigger, modelMenu(await api.models(), st.model), actions.setModel);
    wireModelMenu(menu, st.model);
  };
  document.getElementById("who").onclick = (e) => {
    e.stopPropagation();
    popup(e.currentTarget, `<div class="menu-head">Who the agent acts for</div>${st.people.map((x) => `
      <button class="menu-row" role="menuitem" data-pick="${x.id}">
        ${personMark(x.id, "lg")}
        <span class="mr-words"><span class="mr-title">${esc(x.name)} · ${esc(x.rank)}</span>
          <span class="mr-sub">Pays alone up to ${eur(x.approval_limit_eur).replace(".00", "")} · ${x.entities.join(", ")} · ${x.can_approve ? "can approve" : "cannot approve"}</span></span>
        ${x.id === p.id ? `<span class="mr-tick">${icon("check")}</span>` : ""}
      </button>`).join("")}`, actions.setPerson, "right");
  };
}

// ---------------- the model picker: cloud models are always ready; local ones must be started first ----------------
const LOCAL_SUB = {
  ready: (m) => `Ready, in memory${m.gb ? ` (${m.gb} GB)` : ""} · Ollama, on this machine`,
  stopped: () => "Not started. Start loads it into memory (about 5 GB), then you can pick it.",
  loading: (m) => `Starting… ${m.seconds || 0} s (loading into memory, with the injection screen model)`,
  no_server: () => "Ollama is not running. Start launches it, then loads the model.",
  missing: (m) => `Not installed. In a terminal: ${m.hint}`,
};

function modelMenu(list, current) {
  return `<div class="menu-head">The agent's model</div>${list.map((m) => {
    const ready = m.state === "ready";
    const sub = m.kind === "cloud" ? "Vertex AI, europe-west4 · always ready" : (LOCAL_SUB[m.state] || (() => m.state))(m);
    const btn = m.kind !== "local" ? ""
      : m.state === "loading" ? `<span class="mr-state">${icon("loading", "spin")}</span>`
      : m.state === "stopped" || m.state === "no_server" ? `<button class="key is-small" data-start="${m.id}">${icon("zap")}Start</button>`
      : ready && m.id !== current ? `<button class="key is-small is-quiet" data-stop="${m.id}">Stop</button>` : "";
    return `<div class="menu-row${ready ? "" : " is-disabled"}" role="menuitem" ${ready ? `data-pick="${m.id}" tabindex="0"` : 'aria-disabled="true"'}>
      <span class="rl-box">${icon(m.kind === "cloud" ? "sparkles" : "model")}</span>
      <span class="mr-words"><span class="mr-title">${esc(m.name)}</span><span class="mr-sub">${esc(sub)}</span>
        ${m.error ? `<span class="mr-sub mr-err">${esc(m.error)}</span>` : ""}</span>
      ${btn}${m.id === current ? `<span class="mr-tick">${icon("check")}</span>` : ""}
    </div>`;
  }).join("")}`;
}

function wireModelMenu(menu, current) {
  const redraw = async (list) => {
    if (!menu.isConnected) return;
    menu.innerHTML = modelMenu(list || await api.models(), current);
    const busy = (list || []).some((m) => m.state === "loading") || menu.querySelector(".mr-state");
    if (busy) setTimeout(() => redraw(), 1500);   // watch the load until it is ready (or fails)
  };
  menu.addEventListener("click", async (e) => {
    const start = e.target.closest("[data-start]"), stop = e.target.closest("[data-stop]");
    if (!start && !stop) return;
    e.stopPropagation();
    redraw(await (start ? api.startModel(start.dataset.start) : api.stopModel(stop.dataset.stop)));
  });
}

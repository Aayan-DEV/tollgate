// HOW IT WORKS: the layer's architecture, the formula that combines its parts, and each part with its real file.
import { api } from "../api.js";
import { icon, esc } from "../ui.js";

let root, C = null;

export async function mount(el) {
  root = el;
  root.innerHTML = `<div class="page" id="how-page"><section class="pn-panel"><p class="pn-empty">${icon("loading", "spin")} Loading</p></section></div>`;
  C = await api.architecture();
  draw();
}

const byId = (id) => C.find((c) => c.id === id);

// A box in the diagram. `goto` makes it clickable: it scrolls to that part's section below.
function box(x, y, w, h, goto, name, sub, ai = false) {
  return `<g class="arch-hit${ai ? " is-ai" : ""}" ${goto ? `data-goto="${goto}" tabindex="0" role="button" aria-label="${esc(name)}"` : ""}>
    <rect x="${x}" y="${y}" width="${w}" height="${h}" rx="8" class="arch-box"/>
    <text x="${x + 14}" y="${y + (sub ? 24 : h / 2 + 5)}" class="arch-name">${esc(name)}</text>
    ${sub ? `<text x="${x + 14}" y="${y + 42}" class="arch-sub">${esc(sub)}</text>` : ""}</g>`;
}

function diagram() {
  const chipX = [72, 348, 624], chipY = [228, 302, 376], W = 258, H = 58;
  const steps = [
    ["identity", "1  Who is asking?", "signed agent token"],
    ["feed", "2  Is it known-bad?", "normalizer, attack feed, secrets"],
    ["contracts", "3  What would really happen?", "contract looks up real records"],
    ["data", "4  Within the rules?", "data guard, limits"],
    ["judge", "5  Did the user ask for it?", "AI judge quotes the user", true],
    ["gate", "6  Decide", "allow · ask a person · block"],
    ["bind", "7  Do it safely", "verified values, one transaction"],
    ["screens", "8  Is the result safe?", "mask data, drop hidden orders", true],
    ["audit", "9  Remember it", "hash-chained log, metrics"],
  ];
  const sys = [["ERP", "payments, data"], ["Email", "inside and out"], ["Model files", "forecasts"], ["MCP tools", "vendor portal"], ["LLMs", "Gemini, Ollama"]];
  return `<svg viewBox="0 0 960 648" class="arch" role="img" aria-label="Every AI action passes one gate; the agent holds no keys">
    <defs><marker id="arch-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse"><path d="M0 0L10 5L0 10z" class="arch-head"/></marker></defs>
    ${box(40, 10, 190, 54, null, "User", "asks in plain words")}
    ${box(270, 10, 190, 54, "gate", "AI agent", "holds only the gate")}
    ${box(500, 10, 190, 54, "identity", "Helper agents", "narrower token")}
    ${box(730, 10, 190, 54, "mcp", "Any MCP client", "same checks via MCP")}
    <g class="arch-lines">${[135, 365, 595, 825].map((x) => `<path d="M${x} 64V82" marker-end="url(#arch-arrow)"/>`).join("")}
      <path d="M480 470V494"/><path d="M120 494H840"/>${[120, 300, 480, 660, 840].map((x) => `<path d="M${x} 494V514" marker-end="url(#arch-arrow)"/>`).join("")}
      <path d="M40 610H24V148H38" marker-end="url(#arch-arrow)"/><path d="M920 148H936V610H922" marker-end="url(#arch-arrow)"/></g>
    <rect x="40" y="84" width="880" height="386" rx="10" class="arch-gate"/>
    <text x="56" y="108" class="arch-name">Tollgate: the one gate every action passes</text>
    ${box(56, 120, 424, 56, "privacy", "Input lane", "your words kept for the judge; private data tokenized")}
    ${box(496, 120, 408, 56, "limits", "Model lane", "allowed models; cost, token and step budget")}
    <rect x="56" y="192" width="848" height="262" rx="8" class="arch-lane"/>
    <text x="72" y="214" class="arch-name">Action lane: every tool call, nine steps in order</text>
    ${steps.map(([id, n, s, ai], i) => box(chipX[i % 3], chipY[Math.floor(i / 3)], W, H, id, n, s, ai)).join("")}
    ${sys.map(([n, s], i) => box(40 + i * 180, 516, 160, 52, null, n, s)).join("")}
    ${box(40, 588, 430, 46, "policy", "policy.yaml · contracts · signed attack feed", "")}
    ${box(490, 588, 430, 46, "audit", "Evidence: audit log, /metrics, CSV", "")}
  </svg>`;
}

function formula() {
  const term = (ids, label, ai = false) => `<button class="fx-term${ai ? " is-ai" : ""}" data-goto="${ids[0]}">${esc(label)}<span>${ids.map((i) => esc(byId(i)?.name || i)).join(" · ")}</span></button>`;
  return `<section class="pn-panel">
    <header class="pn-head"><span class="pn-head-ico">${icon("algorithm")}</span><span class="pn-head-title">The formula</span>
      <span class="pn-head-hint">How the parts combine into one decision</span></header>
    <div class="fx">
      <div class="fx-row"><span class="fx-word">decision</span><span class="fx-op">=</span><span class="fx-word">strictest of</span><span class="fx-op">(</span>
        ${term(["identity"], "who is asking")}<span class="fx-op">,</span>
        ${term(["feed", "normalize", "secrets"], "known-bad")}<span class="fx-op">,</span>
        ${term(["contracts", "data", "limits"], "the real effect, checked")}<span class="fx-op">,</span>
        ${term(["judge"], "your own words", true)}<span class="fx-op">)</span></div>
      <div class="fx-row"><span class="fx-word">if allowed</span><span class="fx-op">→</span>
        ${term(["bind"], "run with verified values, atomically")}<span class="fx-op">→</span>
        ${term(["screens", "privacy"], "clean what comes back", true)}<span class="fx-op">→</span>
        ${term(["audit"], "record it")}</div>
      <div class="fx-note">Order of strictness: allow &lt; ask a person &lt; block. One block anywhere wins.</div>
    </div>
    <div class="fx-why">
      <div><b>Judge the effect, not the prompt.</b> Prompts can lie or be injected; the invoice amount and the account on file cannot. Every risky action is resolved against the company's own records first.</div>
      <div><b>Rules decide; AI can only tighten.</b> Fixed rules decide everything that can be known, in under a millisecond. AI is asked only where meaning matters, and it can turn "allow" into "ask" or "block", never the other way.</div>
      <div><b>The agent holds no keys.</b> It cannot reach a database, mailbox or tool except through the gate, so no check can be skipped.</div>
      <div><b>Contain what nobody predicted.</b> Unknown tools are refused, amounts and totals are capped, and anything unclear or irreversible waits for a person.</div>
      <div><b>Everything is configuration.</b> Policy, rules and attack patterns are files: checked before they load, signed where it matters, live on the next action.</div>
    </div>
  </section>`;
}

function part(c) {
  return `<article class="how-part" id="how-${c.id}">
    <header><b>${esc(c.name)}</b><span class="chip ${c.kind === "ai" ? "is-warn" : ""}">${c.kind === "ai" ? "AI, can only tighten" : "rule-based"}</span></header>
    <p>${esc(c.what)}</p>
    <p class="how-protects">${icon("shield")}<span><b>Protects against:</b> ${esc(c.protects)}</span></p>
    <div class="how-files">${c.files.map((f) => `<code>${esc(f)}</code>`).join("")}</div>
    <pre class="how-code" data-lang="${c.lang}"><code>${esc(c.snippet)}</code></pre>
  </article>`;
}

function draw() {
  const page = root.querySelector("#how-page");
  page.innerHTML = `
    <div class="page-head"><div>
      <h1>How it works</h1>
      <p>Tollgate sits between AI agents and the systems they act on. This is the actual layer: the diagram, the formula that turns its parts into one decision, and every part with an excerpt from its real file. Click any box to jump to it.</p>
    </div></div>
    <div class="pn-stack">
      <section class="pn-panel"><div class="arch-wrap">${diagram()}</div>
        <div class="bm-key" style="padding-top:4px"><span><i class="arch-key"></i>rule-based (same answer every time)</span><span><i class="arch-key is-ai"></i>uses AI, can only make a decision stricter</span></div></section>
      ${formula()}
      <section class="pn-panel">
        <header class="pn-head"><span class="pn-head-ico">${icon("layers")}</span><span class="pn-head-title">The parts</span>
          <span class="pn-head-hint">${C.length} parts; excerpts are read from the files on every visit</span></header>
        <div class="how-parts">${C.map(part).join("")}</div>
      </section>
    </div>`;
  page.querySelectorAll("[data-goto]").forEach((el) => {
    const go = () => {
      const t = page.querySelector(`#how-${el.dataset.goto}`);
      if (!t) return;
      t.scrollIntoView({ behavior: "smooth", block: "start" });
      t.classList.remove("is-flash"); void t.offsetWidth; t.classList.add("is-flash");
    };
    el.addEventListener("click", go);
    el.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); go(); } });
  });
}

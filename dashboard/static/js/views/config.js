// CONFIGURATION: the three files the layer runs on, shown and editable. Saving checks first, then goes live.
import { api } from "../api.js";
import { icon, esc } from "../ui.js";

let root, C = null, current = "policy", draft = {}, result = {}, saving = false;

export async function mount(el) {
  root = el;
  try { current = localStorage.getItem("config.file") || current; } catch { /* default file */ }
  root.innerHTML = `<div class="page fills" id="cfg-page"></div>`;
  C = await api.config();
  if (!C.files.some((f) => f.id === current)) current = C.files[0].id;
  draw();
}

const file = (id = current) => C.files.find((f) => f.id === id);
const dirty = (id = current) => draft[id] !== undefined && draft[id] !== file(id).text;

function draw() {
  const page = root.querySelector("#cfg-page");
  if (!page || !C) return;
  const f = file(), r = result[current];
  page.innerHTML = `
    <div class="page-head"><div>
      <h1>Configuration</h1>
      <p>The three files the layer runs on. Edit and save here, or in any text editor: every change is checked first, then the layer uses it on its next action, with no restart. A change that does not make sense is refused and the previous version keeps running.</p>
    </div></div>
    <div class="cfg-room">
      <nav class="pn-panel cfg-list" aria-label="Configuration files">
        ${C.files.map((x) => `
          <button class="cfg-pick ${x.id === current ? "is-on" : ""}" data-file="${x.id}">
            <span class="cfg-n figure">${x.n}</span>
            <span class="cfg-words"><span class="cfg-title">${esc(x.title)}${dirty(x.id) ? ` <span class="chip is-warn">unsaved</span>` : ""}</span>
              <span class="cfg-path">${esc(x.path)}</span>
              <span class="cfg-about">${esc(x.about)}</span>
              <span class="cfg-status">${icon("check")} ${esc(C.status[x.id])}</span></span>
          </button>`).join("")}
      </nav>
      <section class="pn-panel cfg-edit">
        <header class="pn-head"><span class="pn-head-ico">${icon("file")}</span><span class="pn-head-title">${esc(f.title)}</span>
          <span class="pn-head-hint">${esc(f.path)}${dirty() ? " · unsaved changes" : ""}</span>
          <span class="pn-head-right">
            <button class="key is-small is-quiet" data-discard ${dirty() ? "" : "disabled"}>Discard</button>
            <button class="key is-small" data-save ${dirty() && !saving ? "" : "disabled"}>${icon(saving ? "loading" : "check", saving ? "spin" : "")}${current === "feed" ? "Save and sign" : "Save"}</button>
          </span></header>
        <textarea class="cfg-editor" id="cfg-text" spellcheck="false" autocomplete="off" autocapitalize="off" aria-label="${esc(f.path)}">${esc(draft[current] ?? f.text)}</textarea>
        ${r ? outcome(r) : `<div class="pn-note">${icon("info")}<span>Save with ${navigator.platform.includes("Mac") ? "⌘" : "Ctrl"} S. The same file on disk is what the layer reads; nothing is kept only in the browser.</span></div>`}
      </section>
    </div>`;
  const ta = page.querySelector("#cfg-text");
  ta.addEventListener("input", () => {
    const was = dirty();
    draft[current] = ta.value;
    if (was !== dirty()) refreshChrome();
  });
  ta.addEventListener("keydown", (e) => {
    if (e.key === "Tab") {   // indent with spaces, never leave the editor
      e.preventDefault();
      ta.setRangeText("  ", ta.selectionStart, ta.selectionEnd, "end");
      ta.dispatchEvent(new Event("input"));
    }
    if ((e.metaKey || e.ctrlKey) && e.key === "s") { e.preventDefault(); save(); }
  });
  page.querySelectorAll("[data-file]").forEach((b) => (b.onclick = () => {
    current = b.dataset.file;
    try { localStorage.setItem("config.file", current); } catch { /* not remembered */ }
    draw();
  }));
  page.querySelector("[data-save]").onclick = save;
  page.querySelector("[data-discard]").onclick = () => { delete draft[current]; delete result[current]; draw(); };
}

// Update the save button and unsaved markers without redrawing the editor (keeps the cursor where it is).
function refreshChrome() {
  const page = root.querySelector("#cfg-page");
  page.querySelector("[data-save]").disabled = !dirty() || saving;
  page.querySelector("[data-discard]").disabled = !dirty();
  page.querySelector(".cfg-edit .pn-head-hint").textContent = `${file().path}${dirty() ? " · unsaved changes" : ""}`;
  const title = page.querySelector(`.cfg-pick[data-file="${current}"] .cfg-title`);
  title.innerHTML = `${esc(file().title)}${dirty() ? ` <span class="chip is-warn">unsaved</span>` : ""}`;
}

async function save() {
  if (!dirty() || saving) return;
  saving = true; refreshChrome();
  const res = await api.saveConfig(current, draft[current], file().sha);
  saving = false;
  result[current] = res;
  if (res.status) C.status = res.status;
  if (res.ok && res.file) {
    C.files = C.files.map((x) => (x.id === current ? res.file : x));
    delete draft[current];
  }
  draw();
}

function outcome(r) {
  if (!r.ok) {
    return `<div class="notice is-bad band">${icon("warning")}<span><b>Not saved.</b> ${esc(r.error)}${r.conflict ? "" : " The layer keeps running the previous version."}</span></div>`;
  }
  if (r.unchanged) return `<div class="notice band">${icon("check")}<span>Nothing changed.</span></div>`;
  const list = (r.summary || []).slice(0, 14);
  return `<div class="notice is-good band">${icon("check")}<span><b>Saved${r.signed ? " and signed" : ""}. The layer uses it from the next action.</b>
    ${list.length ? `<span class="cfg-changes">${list.map((x) => `<code>${esc(x)}</code>`).join("")}${(r.summary || []).length > 14 ? `<code>…and ${r.summary.length - 14} more</code>` : ""}</span>` : ""}</span></div>`;
}

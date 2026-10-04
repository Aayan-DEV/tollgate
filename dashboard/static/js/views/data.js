// DATA: the database as it is, beside what the agent may read when acting for the chosen person.
import { api } from "../api.js";
import { icon, esc, num } from "../ui.js";

let root, S, tables = [], current = "vendors", view = "agent", offset = 0;

export async function mount(el, state) {
  root = el; S = state;
  root.innerHTML = `<div class="page fills"><div class="page-head"><div><h1>Data</h1>
    <p>The finance database: 16 tables of synthetic data. Switch between the raw database and what the agent may read when it acts for ${esc(S.state.person.name)}. The rules are enforced inside the database engine.</p></div></div>
    <div class="data-room"><section class="pn-panel data-list" id="tlist"></section><section class="pn-panel data-view" id="tview"></section></div></div>`;
  tables = await api.tables();
  if (!tables.some((t) => t.name === current)) current = tables[0].name;
  drawList();
  drawTable();
}
export async function onState() { tables = await api.tables(); drawList(); drawTable(); }

function accessIcon(t) { return t.access === "closed" ? "lock" : t.access === "scoped" ? "filter" : "table"; }

function drawList() {
  const p = S.state.person;
  const open = tables.filter((t) => t.access !== "closed").length;
  root.querySelector("#tlist").innerHTML = `
    <header class="pn-head"><span class="pn-head-ico">${icon("database")}</span><span class="pn-head-title">Tables</span>
      <span class="pn-head-hint">${open} of ${tables.length} open to ${esc(p.rank)}</span></header>
    <div class="pn-rows">${tables.map((t) => `
      <button class="tbl-pick ${t.name === current ? "is-on" : ""} ${t.access === "closed" ? "is-closed" : ""}" data-t="${t.name}"
        title="${t.access === "closed" ? `Closed to ${esc(p.rank)}` : t.access === "scoped" ? `Rows limited to ${p.entities.join(", ")}` : "Open"}">
        ${icon(accessIcon(t))}<span class="t-name">${t.name}</span><span class="t-rows">${num(t.rows)}</span></button>`).join("")}</div>
    <div class="pn-note">${icon("info")}<span>${icon("lock")} closed · ${icon("filter")} only ${p.entities.join(", ")} rows</span></div>`;
  root.querySelectorAll("[data-t]").forEach((b) => (b.onclick = () => { current = b.dataset.t; offset = 0; drawList(); drawTable(); }));
}

async function drawTable() {
  const box = root.querySelector("#tview");
  const t = tables.find((x) => x.name === current);
  const p = S.state.person;
  const res = await api.table(current, view, offset);
  const head = `<header class="pn-head"><span class="pn-head-ico">${icon(accessIcon(t))}</span><span class="pn-head-title">${t.name}</span>
      <span class="pn-head-hint">${view === "raw" ? "Everything in the database" : `What the agent may read for ${esc(p.name)} (${esc(p.rank)})`}</span>
      <span class="pn-head-right"><span class="seg" role="group" aria-label="View">
        <button class="seg-cell" data-v="raw" aria-pressed="${view === "raw"}">${icon("database")}Raw database</button>
        <button class="seg-cell" data-v="agent" aria-pressed="${view === "agent"}">${icon("eye")}As the agent sees it</button>
      </span></span></header>`;
  if (res.closed) {
    box.innerHTML = `${head}<div class="closed-plate"><div><span class="closed-mark">${icon("lock")}</span>
      <h3>Closed to ${esc(p.rank)}</h3>
      <p>When the agent acts for ${esc(p.name)}, the database refuses every read of <b>${t.name}</b>, however the query is written: joins, unions and subqueries included.</p>
      <p>Switch to a person with a higher rank, or to the raw database, to see it.</p></div></div>`;
  } else {
    const hidden = new Set((res.hidden || []).map((h) => h.split(".")[1]));
    const masked = (v) => typeof v === "string" && /^[A-Z]{2}\d{2}\.\.\.\w{4}$|^SYN\*+$/.test(v);
    const cell = (c, v) => {
      if (view === "agent" && hidden.has(c)) return `<td class="cell-hidden">hidden</td>`;
      if (v === null || v === undefined) return `<td style="color:var(--ink-3)">empty</td>`;
      if (masked(v)) return `<td class="cell-masked" title="Masked: the agent never sees the full value">${esc(v)}</td>`;
      return typeof v === "number" ? `<td class="is-num">${esc(v)}</td>` : `<td title="${esc(v)}">${esc(v)}</td>`;
    };
    const total = res.total ?? 0;
    const scopedNote = view === "agent" && res.raw_total !== undefined && res.raw_total !== total
      ? ` · ${num(total)} of ${num(res.raw_total)} rows are ${p.entities.join(", ")}` : "";
    const maskNote = view === "agent" && (res.masked_values || hidden.size)
      ? ` · ${hidden.size ? `${hidden.size} columns hidden` : ""}${hidden.size && res.masked_values ? ", " : ""}${res.masked_values ? `${res.masked_values} values masked on this page` : ""}` : "";
    box.innerHTML = `${head}
      <div class="pn-table-wrap"><table class="pn-table"><thead><tr>${res.columns.map((c) => `<th class="${view === "agent" && hidden.has(c) ? "th-hidden" : ""}">${esc(c)}</th>`).join("")}</tr></thead>
      <tbody>${res.rows.map((r) => `<tr>${r.map((v, i) => cell(res.columns[i], v)).join("")}</tr>`).join("")}</tbody></table></div>
      <div class="data-foot"><span class="figure">Rows ${num(Math.min(total, offset + 1))} to ${num(Math.min(total, offset + res.rows.length))} of ${num(total)}${scopedNote}${maskNote}</span>
        <span class="df-right"><button class="key is-small is-quiet" data-page="-1" ${offset === 0 ? "disabled" : ""}>Previous</button>
        <button class="key is-small is-quiet" data-page="1" ${offset + res.rows.length >= total ? "disabled" : ""}>Next</button></span></div>`;
    box.querySelectorAll("[data-page]").forEach((b) => (b.onclick = () => { offset = Math.max(0, offset + 50 * +b.dataset.page); drawTable(); }));
  }
  box.querySelectorAll("[data-v]").forEach((b) => (b.onclick = () => { view = b.dataset.v; offset = 0; drawTable(); }));
}

// Shared vocabulary: icons, escaping, the person's mark, plain-words labels for tools.
import { ICONS } from "../icons.js";

export function icon(name, extra = "") {
  const body = (ICONS[name] || ICONS.info).replace(/stroke-width="[^"]*"/g, 'stroke-width="1.8"');
  return `<svg data-icon viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" class="${extra}">${body}</svg>`;
}

export const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

export function $(sel, root = document) { return root.querySelector(sel); }
export function $$(sel, root = document) { return [...root.querySelectorAll(sel)]; }

// A person is the only thing with a picture: a grey gradient cut from their own id (FNV-1a).
export function personMark(id, cls = "") {
  let h = 0x811c9dc5;
  for (const ch of String(id)) { h ^= ch.charCodeAt(0); h = Math.imul(h, 0x01000193) >>> 0; }
  const a = 18 + (h % 22), b = 52 + ((h >>> 8) % 22), ang = ((h >>> 16) % 24) * 15;
  return `<span class="person-mark ${cls}" role="img" aria-hidden="true" style="background:linear-gradient(${ang}deg, hsl(40 4% ${a}%), hsl(40 4% ${b}%))"></span>`;
}

export const eur = (n) => (n == null || isNaN(n) ? "" : `€${Number(n).toLocaleString("en-GB", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`);
export const usd = (n) => `$${Number(n || 0).toFixed(Number(n) < 0.1 ? 4 : 2)}`;
export const num = (n) => Number(n || 0).toLocaleString("en-GB");

export const TOOLS = {
  list_open_invoices: { label: "Open invoices", icon: "invoice" },
  read_invoice: { label: "Read an invoice", icon: "file" },
  list_vendors: { label: "Vendor list", icon: "truck" },
  get_vendor: { label: "Vendor record", icon: "truck" },
  get_payment_history: { label: "Payment history", icon: "clock" },
  query_db: { label: "Database query", icon: "database" },
  pay_invoice: { label: "Payment", icon: "wallet" },
  send_email: { label: "Email", icon: "mail" },
  update_vendor_bank_details: { label: "Bank details change", icon: "key" },
  load_forecast_model: { label: "Model file", icon: "package" },
  delegate: { label: "Hand-off to payments agent", icon: "users" },
};
export const tool = (name) => TOOLS[name] || { label: name, icon: "zap" };

export const DECISION = {
  allow: { word: "allowed", tone: "", chip: "is-good" },
  block: { word: "blocked", tone: "is-block", chip: "is-bad" },
  ask: { word: "held", tone: "is-ask", chip: "is-warn" },
  off: { word: "no check", tone: "is-off", chip: "is-off" },
};

// The agent's words: paragraphs, bullet lists, **bold** and `code`. Nothing else is honoured.
export function md(text) {
  const inline = (s) => esc(s).replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>").replace(/`([^`]+)`/g, "<code>$1</code>");
  const out = [];
  let list = null;
  for (const raw of String(text || "").split("\n")) {
    const line = raw.trimEnd();
    const item = line.match(/^\s*(?:[-*•]|\d+\.)\s+(.*)$/);
    if (item) { (list ||= []).push(`<li>${inline(item[1])}</li>`); continue; }
    if (list) { out.push(`<ul>${list.join("")}</ul>`); list = null; }
    if (line.trim()) out.push(`<p>${inline(line)}</p>`);
  }
  if (list) out.push(`<ul>${list.join("")}</ul>`);
  return out.join("");
}

export function timeAgo(ts) {
  const s = Math.max(0, Math.round(Date.now() / 1000 - ts));
  return s < 60 ? `${s} s ago` : s < 3600 ? `${Math.round(s / 60)} min ago` : `${Math.round(s / 3600)} h ago`;
}

// One popup at a time, positioned under its trigger, closed by Escape or a click outside.
let openMenu = null;
export function popup(trigger, html, onPick, align = "left") {
  closeMenu();
  const r = trigger.getBoundingClientRect();
  const m = document.createElement("div");
  m.className = "menu";
  m.setAttribute("role", "menu");
  m.innerHTML = html;
  document.body.appendChild(m);
  const w = m.offsetWidth;
  m.style.top = `${r.bottom + 6}px`;
  m.style.left = `${align === "right" ? Math.max(8, r.right - w) : Math.min(r.left, innerWidth - w - 8)}px`;
  trigger.setAttribute("aria-expanded", "true");
  openMenu = { m, trigger };
  m.addEventListener("click", (e) => {
    const row = e.target.closest("[data-pick]");
    if (row) { closeMenu(); onPick(row.dataset.pick); }
  });
  setTimeout(() => document.addEventListener("click", outside), 0);
  return m;
}
function outside(e) { if (openMenu && !openMenu.m.contains(e.target)) closeMenu(); }
export function closeMenu() {
  if (!openMenu) return;
  openMenu.trigger.setAttribute("aria-expanded", "false");
  openMenu.m.remove();
  openMenu = null;
  document.removeEventListener("click", outside);
}
addEventListener("keydown", (e) => { if (e.key === "Escape") closeMenu(); });

// What an action would do, in words: contracts write a sentence; plain reads get their label and key values.
// What the layer did quietly on an action it allowed: removed a credential or an injected sentence, redacted data.
export function quietNotes(e) {
  if (e.notes) return e.notes;
  return (e.findings || []).filter((f) => f[1] === "allow" && (/^(injection|secrets)\./.test(f[0]) || /redacted/.test(f[2]))).map((f) => f[4] || f[2]);
}

export function effectText(e) {
  const t = tool(e.tool);
  const eff = String(e.effect || "");
  if (eff.startsWith(`${e.tool}(`) || eff.endsWith("(no protection)")) {
    const vals = Object.values(e.args || {}).map((v) => String(v)).filter(Boolean);
    return vals.length ? `${t.label} · ${vals.join(", ")}` : t.label;
  }
  if (eff.startsWith("query_db: ")) return `${t.label} · ${eff.slice(10)}`;
  return eff;
}

// ---------------- plain words first, the technical record one click away ----------------
export const plainWhat = (e) => e.plain || effectText(e);
export const plainWhy = (e) => e.plain_reason || e.reason || "";

// The AI checker's verdict in everyday words ("You asked for this: ..."), when it ran.
export function judgeLine(e) {
  const f = (e.findings || []).find((x) => x[0] === "justify");
  return f ? f[4] || f[2] : "";
}

const fmtMs = (ms) => (ms < 1 ? `${ms.toFixed(2)} ms` : `${ms.toFixed(1)} ms`);

export function details(e) {
  const rows = [];
  if (e.effect && e.effect !== plainWhat(e)) rows.push(["Exactly", e.effect]);
  if (e.reason && e.decision !== "allow" && e.decision !== "off") rows.push(["Rule said", e.reason]);
  const fired = (e.findings || []).filter((f) => f[0] !== "identity.delegation" || f[1] !== "allow").map((f) => `${f[0]} (${f[1]})`);
  if (fired.length) rows.push(["Rules", fired.join(", ")]);
  if (e.judge) rows.push(["AI checker", `${e.judge.model}${e.judge.quote ? `, quoted "${e.judge.quote}"` : ""}${e.judge.quote && !e.judge.quote_ok ? " (not the user's words)" : ""}`]);
  if (e.caller) rows.push(["Acting", e.caller]);
  if (e.screen?.model_calls) rows.push(["Document check", `${e.screen.screened} sentences, ${e.screen.model_calls} small-model call(s)`]);
  rows.push(["Time", e.decision === "off" ? "no checks ran (layer off)"
    : `rules ${fmtMs(e.gate_ms || 0)}${e.judge_ms ? `, AI ${(e.judge_ms / 1000).toFixed(1)} s` : ""}`]);
  return `<details class="more"><summary>Details</summary><dl>${rows.map(([k, v]) => `<dt>${k}</dt><dd>${esc(v)}</dd>`).join("")}</dl></details>`;
}

// "checking: pay_invoice" -> "checking a payment"; other activity lines are already plain words.
export function activityText(a) {
  const m = /^(the payments helper is )?checking: (\w+)$/.exec(a || "");
  if (!m) return a || "thinking";
  return `${m[1] || ""}checking: ${tool(m[2]).label.toLowerCase()}`;
}

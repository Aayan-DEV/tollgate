// Boot, route, and keep everything live with one quiet poll.
import { api } from "./api.js";
import { renderShell } from "./shell.js";
import * as agent from "./views/agent.js";
import * as layer from "./views/layer.js";
import * as data from "./views/data.js";
import * as evidence from "./views/evidence.js";
import * as tests from "./views/tests.js";

const VIEWS = { agent, layer, data, tests, evidence };
export const S = { state: null, view: "agent", events: [], lastEvent: 0, overview: null, approvals: [], controls: [], running: false };

const actions = {
  async setLayer(on) {
    if (on === S.state.layer) return;
    S.state = await api.layer(on);
    shell();
    VIEWS[S.view].onState?.(S, actions);
  },
  async setPerson(id) {
    if (S.running || id === S.state.person.id) return;
    S.state = await api.person(id);
    await refresh();
    shell();
    mountView();
  },
  async setModel(id) {
    if (S.running || id === S.state.model) return;
    S.state = await api.model(id);
    shell();
    mountView();
  },
  async newChat() {
    if (S.running) return;
    S.state = await api.fresh();
    if (location.hash !== "#agent") location.hash = "#agent";
    else mountView();
  },
  async openChat(id) {
    if (S.running || id === S.state.conversation) return;
    const next = await api.openChat(id);
    if (next.error) return;
    S.state = next;
    shell();
    mountView();
  },
  async deleteChats() {
    if (S.running) return;
    if (!confirm("Delete every chat, for every person? The data and the audit log stay.")) return;
    const next = await api.deleteChats();
    if (next.error) { alert(next.error); return; }
    S.state = next;
    shell();
    mountView();
  },
  async reset() {
    if (S.running) return;
    S.state = await api.reset();
    S.events = [];
    S.lastEvent = 0;
    await refresh();
    shell();
    mountView();
  },
  async refreshState() {
    S.state = await api.state();
    shell();
  },
};

function shell() { renderShell(S, actions); }

function mountView() {
  const root = document.getElementById("view");
  VIEWS[S.view].mount(root, S, actions);
}

function route() {
  const v = location.hash.slice(1);
  S.view = VIEWS[v] ? v : "agent";
  shell();
  mountView();
}

async function refresh() {
  const [events, overview, approvals] = await Promise.all([api.events(S.lastEvent), api.overview(), api.approvals()]);
  if (events.length) {
    S.events.push(...events);
    if (S.events.length > 800) S.events = S.events.slice(-800);
    S.lastEvent = events[events.length - 1].id;
  }
  const waitingChanged = S.overview?.waiting !== overview.waiting;
  S.overview = overview;
  S.approvals = approvals;
  return { changed: events.length > 0 || waitingChanged, waitingChanged };
}

async function poll() {
  try {
    const { changed, waitingChanged } = await refresh();
    if (waitingChanged) shell();   // the chrome only shows the waiting count; never re-draw it under a pointer for nothing
    VIEWS[S.view].onPoll?.(S, actions, changed);
  } catch { /* the server restarting is not an error worth showing; the next poll tries again */ }
  setTimeout(poll, 1200);
}

async function boot() {
  S.state = await api.state();
  S.controls = await api.controls();
  await refresh();
  addEventListener("hashchange", route);
  route();
  poll();
}

boot();

/* The runtime's entry. The page writes a document into the sandboxed
 * frame with this bundle, the stylesheet and window.__STELLAR_UI__:
 *   { spec, state, theme: "dark"|"light", mode: "live"|"ask" }
 * and from then on talks to it only through bridge.js messages.
 */
import React, { Component, useEffect, useMemo, useState, useSyncExternalStore } from "react";
import { createRoot } from "react-dom/client";
import { MotionConfig } from "motion/react";
import { REGISTRY } from "./components/index.js";
import { Node, resolve, RuntimeCtx, setRegistry } from "./render.jsx";
import { applyOps, createStore, getPath, normalize, setPath } from "./store.js";
import { bridge, onHostMessage } from "./bridge.js";
import { overlayRoom, toast, Toaster } from "./components/overlays.jsx";

setRegistry(REGISTRY);

const cfg = window.__STELLAR_UI__ || {};
const ask = cfg.mode === "ask";
const store = createStore({
  spec: normalize(cfg.spec) || { type: "Fragment", props: {}, children: [] },
  state: cfg.state && typeof cfg.state === "object" ? cfg.state : {},
  theme: cfg.theme === "light" ? "light" : "dark",
  closed: !!cfg.closed,
});
let answered = !!cfg.closed;

function applyTheme(theme) {
  document.documentElement.classList.toggle("dark", theme !== "light");
}
applyTheme(store.get().theme);

/* The state the model sees: everything but the interface's own
   bookkeeping (open dialogs, chosen tabs). */
function publicState(state) {
  const { __ui, ...rest } = state || {};
  return rest;
}

function finish(payload) {
  if (!ask || answered) return;
  answered = true;
  bridge.finish(payload);
}

const actions = {
  read: (path) => getPath(store.get().state, path),
  write: (path, value) => store.set((s) => ({ ...s, state: setPath(s.state, path, value) })),

  /* Carries out an on* prop: one action or a list, in order. */
  dispatch(action, scope = {}, eventValue) {
    if (!action) return;
    if (Array.isArray(action)) { action.forEach((a) => actions.dispatch(a, scope, eventValue)); return; }
    if (typeof action !== "object") return;
    const val = (v) => (v === "$event" ? eventValue : resolve(v, store.get().state, scope));
    if (action.set) for (const [p, v] of Object.entries(action.set)) actions.write(p, val(v));
    if (action.toggle) actions.write(action.toggle, !actions.read(action.toggle));
    if (action.push) for (const [p, v] of Object.entries(action.push)) {
      const cur = actions.read(p);
      actions.write(p, [...(Array.isArray(cur) ? cur : []), val(v)]);
    }
    if (action.remove) for (const [p, i] of Object.entries(action.remove)) {
      const cur = actions.read(p);
      const at = Number(val(i));
      if (Array.isArray(cur)) actions.write(p, cur.filter((_, k) => k !== at));
    }
    if (action.open) actions.write(`/__ui/open/${action.open}`, true);
    if (action.close) actions.write(`/__ui/open/${action.close}`, false);
    if (action.toast) toast(val(action.toast));
    if (action.display) bridge.display(action.display);
    if (action.command) {
      const { command, ...rest } = action;
      bridge.command(command, resolve(rest, store.get().state, scope));
    }
    if (action.emit) {
      const data = val(action.data ?? (eventValue !== undefined ? eventValue : null));
      if (ask) finish({ event: String(action.emit), data, state: publicState(store.get().state) });
      else {
        bridge.event(action.emit, data);
        // In a live interface an emit is only recorded for Stellar's next
        // turn; say so, so a click never looks like it did nothing.
        if (!action.quiet) toast({ title: "Noted", description: "Stellar will see this with your next message." });
      }
    }
    if (action.notify) bridge.event("notify", val(action.data ?? null), { notify: true, text: val(action.notify) });
    if (action.submit) finish({ event: "submit", data: val(action.data ?? null), state: publicState(store.get().state) });
  },
};

/* State the user changes is saved, so a reload restores it. Changes the
   model made arrive already saved, and are not echoed back. */
let fromHost = false;
let lastState = store.get().state;
store.subscribe(() => {
  const st = store.get().state;
  if (st === lastState) return;
  lastState = st;
  if (!fromHost) bridge.saveState(st);
});

class Boundary extends Component {
  constructor(p) { super(p); this.state = { error: null }; }
  static getDerivedStateFromError(error) { return { error }; }
  componentDidCatch(error) { bridge.event("render_error", { message: String(error && error.message || error).slice(0, 300) }); }
  render() {
    if (this.state.error) {
      return (
        <div className="rounded-xl border border-danger/30 bg-danger/10 px-4 py-3 text-sm text-danger">
          Part of this interface couldn’t be drawn: {String(this.state.error.message || this.state.error)}
        </div>
      );
    }
    return this.props.children;
  }
}

function App() {
  const snap = useSyncExternalStore(store.subscribe, store.get);
  const [booting, setBooting] = useState(true);
  useEffect(() => { const t = setTimeout(() => setBooting(false), 1000); return () => clearTimeout(t); }, []);
  const rt = useMemo(() => ({ ...actions, state: snap.state, booting }), [snap.state, booting]);
  return (
    <MotionConfig reducedMotion="user">
      <RuntimeCtx.Provider value={rt}>
        <div inert={snap.closed ? true : undefined} className={snap.closed ? "pointer-events-none opacity-80 saturate-[.85] transition duration-500" : undefined}>
          <Boundary key={snap.rootKey || 0}><Node node={snap.spec} /></Boundary>
        </div>
        <Toaster />
      </RuntimeCtx.Provider>
    </MotionConfig>
  );
}

/* ---- the page's messages ---------------------------------------------- */

onHostMessage((m) => {
  switch (m.__stellar) {
    case "ui-patch": {
      // Applied to this frame's own copy, so what the user is typing or
      // has open survives the model's change. If an operation will not
      // apply here, the saved copy the page sent is used instead.
      const cur = store.get();
      const res = applyOps(cur.spec, cur.state, m.ops, cur.theme);
      fromHost = true;
      if (res.errors.length && m.spec) {
        store.set((s) => ({ ...s, spec: normalize(m.spec), state: { ...(m.state || {}), __ui: s.state.__ui }, theme: m.theme || s.theme }));
      } else {
        store.set((s) => ({ ...s, spec: res.spec, state: res.state, theme: res.theme || s.theme }));
      }
      fromHost = false;
      applyTheme(store.get().theme);
      break;
    }
    case "closed":
      answered = true;
      store.set((s) => ({ ...s, closed: true }));
      break;
    case "rearm":
      answered = false;
      store.set((s) => ({ ...s, closed: false }));
      break;
    case "theme":
      store.set((s) => ({ ...s, theme: m.theme === "light" ? "light" : "dark" }));
      applyTheme(store.get().theme);
      break;
    case "render":
      if (typeof m.doc === "string") { document.open(); document.write(m.doc); document.close(); }
      break;
    default:
      break;
  }
});

/* ---- sizing: the page makes the frame as tall as this reports ---------- */

const root = document.getElementById("genui-root");
let lastHeight = 0;
function report() {
  const content = Math.ceil(root.getBoundingClientRect().bottom) + 2;
  const h = overlayRoom.count > 0 ? Math.max(content, 620) : content;
  if (Math.abs(h - lastHeight) > 1) { lastHeight = h; bridge.height(h); }
}
new ResizeObserver(report).observe(root);
overlayRoom.listeners.add(() => requestAnimationFrame(report));
window.addEventListener("load", report);
setTimeout(report, 50); setTimeout(report, 400); setTimeout(report, 1200);

createRoot(root).render(<App />);

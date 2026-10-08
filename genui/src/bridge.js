/* The frame's only channel to Stellar: postMessage, with a fixed set of
 * message types. The frame is sandboxed (opaque origin, no network), so
 * this is everything a generated interface can do outside itself:
 *
 *   height   - how tall its content is, so the page sizes the frame
 *   state    - the interface's state, saved so a reload restores it
 *   event    - something the user did that the model should hear about
 *   finish   - the answer, when the model is waiting for one
 *   display  - ask to be shown wider (inline / expanded)
 *
 * The page matches messages to frames by event.source and treats every
 * payload as untrusted data.
 */

function post(message) {
  try { parent.postMessage({ __stellar: message.type, ...message }, "*"); } catch (e) { /* page gone */ }
}

let stateTimer = null;
let lastSaved = "";

export const bridge = {
  height(px) { post({ type: "height", height: Math.ceil(px) }); },

  /* Debounced: a slider dragged across its range is one save, not fifty. */
  saveState(state) {
    clearTimeout(stateTimer);
    stateTimer = setTimeout(() => {
      const text = JSON.stringify(state);
      if (text === lastSaved || text.length > 20000) return;
      lastSaved = text;
      post({ type: "state", state });
    }, 600);
  },

  event(name, data, { notify = false, text = "" } = {}) {
    post({ type: "event", name: String(name).slice(0, 80), data: data ?? null, notify, text: String(text || "").slice(0, 300) });
  },

  finish(data) { post({ type: "finish", data: data ?? {} }); },

  display(mode) { post({ type: "display", mode: mode === "expanded" ? "expanded" : "inline" }); },
};

/* Messages from the page. */
export function onHostMessage(handler) {
  window.addEventListener("message", (e) => {
    const m = e.data;
    if (e.source !== parent || !m || typeof m !== "object" || !m.__stellar) return;
    handler(m);
  });
}

/* Stellar frontend (phase 3).
 *
 * No framework, matching the original.
 *
 * A turn is two requests - register, then attach to the stream - and the
 * stream is replayable by index. Together those let a generation survive
 * losing the page it was started from. See attachStream() and init().
 *
 * Model output is Markdown, rendered by renderMarkdown() into DOM nodes
 * rather than through innerHTML.
 */

"use strict";

const state = {
  chatId: null,
  sending: false,
  queryId: null,     // the turn in flight, so Stop knows what to stop
};

const el = {
  chatList:  document.getElementById("chat-list"),
  messages:  document.getElementById("messages"),
  composer:  document.getElementById("composer"),
  input:     document.getElementById("input"),
  send:      document.getElementById("send"),
  newChat:   document.getElementById("new-chat"),
};

/* ------------------------------------------------------------------ */
/* helpers                                                             */
/* ------------------------------------------------------------------ */

async function api(path, options = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });

  if (res.status === 204) return null;

  const body = await res.json().catch(() => null);
  if (!res.ok) {
    throw new Error((body && body.error) || `${res.status} ${res.statusText}`);
  }
  return body;
}

/* The placeholder shown in a chat with no messages yet.
 *
 * It is rebuilt rather than hidden, because selectChat() clears the message
 * container wholesale - the original node from index.html does not survive
 * the first chat switch. */
function renderEmptyState() {
  const empty = document.createElement("div");
  empty.className = "empty";
  empty.id = "empty-state";

  const h = document.createElement("h2");
  h.textContent = "Stellar";
  const p = document.createElement("p");
  p.textContent =
    "Ask anything. Replies stream live from Gemini. " +
    "Refresh mid-answer and the stream reattaches where it left off.";

  empty.append(h, p);
  el.messages.appendChild(empty);
}


/* ------------------------------------------------------------------ */
/* tool activity                                                       */
/* ------------------------------------------------------------------ */

/* Tool calls are shown as a rail of chips above the reply they produced.
 *
 * Deliberately terse: the name, how long it took, and whether it failed.
 * A user watching the agent work wants to know it IS working and roughly on
 * what - the full arguments and output are in the database for phase 8's
 * read_tool_output, not on screen by default. */

const TOOL_LABELS = {
  get_current_time: "Checking the time",
  fetch_url: "Reading a page",
  web_search: "Searching the web",
  lab_execute: "Running in the sandbox",
  compress_memory: "Tidying memory",
  request_user_interaction: "Waiting for you",
  chess_move: "Reading the board",
  chess_play: "Playing chess",
};

function makeToolChip(name) {
  const chip = document.createElement("span");
  chip.className = "tool-chip running";

  const label = document.createElement("span");
  label.className = "tool-name";
  label.textContent = TOOL_LABELS[name] || name;
  chip.appendChild(label);

  const timing = document.createElement("span");
  timing.className = "tool-ms";
  chip.appendChild(timing);

  return chip;
}

function finishToolChip(chip, { ms, is_error }) {
  chip.classList.remove("running");
  if (is_error) chip.classList.add("failed");
  const timing = chip.querySelector(".tool-ms");
  if (timing && typeof ms === "number") {
    timing.textContent = ms >= 1000 ? `${(ms / 1000).toFixed(1)}s` : `${ms}ms`;
  }
}

function makeToolRail() {
  const rail = document.createElement("div");
  rail.className = "tool-rail";
  return rail;
}


/* ------------------------------------------------------------------ */
/* generative UI                                                       */
/* ------------------------------------------------------------------ */

/* The model writes a widget; it renders here and the generation waits for
 * whatever the user does with it.
 *
 * This is the one place the app renders model-authored HTML, and it is
 * sandboxed in an iframe for that reason. Injecting it into the page would
 * give a model-written <script> the same origin as the app: access to the
 * session cookie, to localStorage, and to every API route as the logged-in
 * user. A prompt-injected page read by fetch_url could steer the model into
 * writing exactly that.
 *
 * Inside a sandboxed iframe it can do none of those things. It gets
 * allow-scripts so the widget works, and nothing else - no same-origin, no
 * top-level navigation, no forms. It talks to the page through postMessage
 * alone, which is a channel we define rather than one it can reach around. */

const OPEN_INTERACTIONS = new Map();

function widgetDocument(html) {
  // The widget is given the app's own palette so it does not have to guess,
  // and window.stellar.finish - the only way out of the frame.
  return `<!doctype html><html><head><meta charset="utf-8">
<style>
  :root{
    --bg:#14171f; --surface:#1a1e28; --border:#252a36;
    --text:#e6e8ee; --text-dim:#8b91a1; --accent:#6d8cff;
    --good:#4fc79f; --bad:#ff6b6b;
    --font:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;
    --mono:"SF Mono","Cascadia Code",Consolas,monospace;
  }
  *{box-sizing:border-box}
  html,body{margin:0;padding:0;background:transparent;color:var(--text);
            font-family:var(--font);font-size:15px;line-height:1.55}
  body{padding:2px}
  button{font-family:inherit;cursor:pointer}
  a{color:var(--accent)}
</style></head><body>
<div id="stellar-widget-root">${html}</div>
<script>
(function(){
  var done = false;
  window.stellar = {
    finish: function(data){
      if (done) return;            // one answer per widget
      done = true;
      try {
        parent.postMessage({__stellar:"finish", data: data || {}}, "*");
      } catch (e) {}
    }
  };
  // Report height so the frame can be sized to its content; an iframe has
  // no natural height and would otherwise be an arbitrary box.
  function report(){
    try {
      var h = document.documentElement.scrollHeight;
      parent.postMessage({__stellar:"height", height: h}, "*");
    } catch (e) {}
  }
  report();
  new ResizeObserver(report).observe(document.documentElement);
  setTimeout(report, 60); setTimeout(report, 400);
})();
<\/script></body></html>`;
}

function renderInteraction(ev) {
  const empty = document.getElementById("empty-state");
  if (empty) empty.remove();

  /* Updating in place rather than appending.
   *
   * A turn-based widget - a game, a multi-step form - calls this once per
   * turn. Appending each time leaves a column of dead boards behind the
   * live one. Reusing the same frame and swapping its document keeps one
   * board that changes, which is what a game actually looks like. */
  if (ev.replaces) {
    const prior = document.querySelector(
      `.msg.interaction[data-interaction="${ev.replaces}"]`);
    if (prior) {
      const frame = prior.querySelector("iframe");
      prior.dataset.interaction = ev.id;
      prior.classList.remove("settled");
      frame.classList.remove("awaiting");
      frame.srcdoc = widgetDocument(ev.html);
      OPEN_INTERACTIONS.delete(ev.replaces);
      OPEN_INTERACTIONS.set(ev.id, { frame, wrap: prior });
      scrollToBottom();
      return frame;
    }
  }

  const wrap = document.createElement("div");
  wrap.className = "msg stellar interaction";
  wrap.dataset.interaction = ev.id;

  const frame = document.createElement("iframe");
  frame.className = "widget-frame";
  // allow-scripts WITHOUT allow-same-origin: the widget runs its own code
  // but is a foreign origin to us, so it cannot touch cookies, storage or
  // our API. Adding allow-same-origin here would undo the whole protection.
  frame.setAttribute("sandbox", "allow-scripts");
  frame.setAttribute("title", ev.goal || "Interactive widget");
  frame.srcdoc = widgetDocument(ev.html);
  frame.style.height = "220px";

  wrap.appendChild(frame);
  el.messages.appendChild(wrap);
  scrollToBottom();

  OPEN_INTERACTIONS.set(ev.id, { frame, wrap });
  return frame;
}

function closeInteraction(id) {
  const entry = OPEN_INTERACTIONS.get(id);
  if (!entry) return;
  entry.wrap.classList.add("settled");
  // The frame is left in place rather than removed: it is part of the
  // transcript, and tearing it out would make the conversation jump.
  OPEN_INTERACTIONS.delete(id);
}

/* One listener for every widget, matching the source frame to its id.
 * Widgets are foreign-origin, so event.source identity is the only thing
 * that can be trusted here - never the contents of the message. */
window.addEventListener("message", async (e) => {
  const msg = e.data;
  if (!msg || typeof msg !== "object" || !msg.__stellar) return;

  let id = null;
  for (const [key, entry] of OPEN_INTERACTIONS) {
    if (entry.frame.contentWindow === e.source) { id = key; break; }
  }
  if (id === null) return;

  const entry = OPEN_INTERACTIONS.get(id);

  if (msg.__stellar === "height") {
    const h = Math.min(Math.max(Number(msg.height) || 220, 120), 1400);
    entry.frame.style.height = h + "px";
    return;
  }

  if (msg.__stellar === "finish") {
    entry.frame.classList.add("awaiting");
    try {
      await api(`/api/interaction/${id}/finish`, {
        method: "POST",
        body: JSON.stringify(msg.data ?? {}),
      });
    } catch (err) {
      console.error("Could not deliver widget response:", err);
    }
  }
});

/* ------------------------------------------------------------------ */
/* markdown                                                            */
/* ------------------------------------------------------------------ */

/* A deliberately small Markdown subset, rendered straight to DOM nodes.
 *
 * Every piece of text ends up in a textNode via textContent, so there is no
 * HTML parsing anywhere in this path and therefore no XSS surface - even
 * though the model's output is untrusted (it can be steered by anything in
 * the conversation). The usual approach, marked + DOMPurify, means shipping
 * two libraries and trusting the sanitiser; this needs neither.
 *
 * Supported: fenced code, inline code, headings, bold, italic, links,
 * bullet and numbered lists. Anything else renders as literal text, which
 * is the safe failure mode. */

const INLINE_RE = /(\*\*[^*]+\*\*|\*[^*]+\*|`[^`]+`|\[[^\]]+\]\([^)]+\))/g;

function renderInline(target, text) {
  for (const part of text.split(INLINE_RE)) {
    if (!part) continue;

    if (part.startsWith("**") && part.endsWith("**") && part.length > 4) {
      const b = document.createElement("strong");
      b.textContent = part.slice(2, -2);
      target.appendChild(b);
    } else if (part.startsWith("`") && part.endsWith("`") && part.length > 2) {
      const c = document.createElement("code");
      c.textContent = part.slice(1, -1);
      target.appendChild(c);
    } else if (part.startsWith("*") && part.endsWith("*") && part.length > 2) {
      const i = document.createElement("em");
      i.textContent = part.slice(1, -1);
      target.appendChild(i);
    } else if (part.startsWith("[")) {
      const m = /^\[([^\]]+)\]\(([^)]+)\)$/.exec(part);
      // Scheme allowlist: a javascript: or data: href would execute on
      // click, which is exactly the hole avoiding innerHTML was meant to
      // close. Anything else is rendered as plain text.
      if (m && /^(https?:\/\/|mailto:|\/)/i.test(m[2])) {
        const a = document.createElement("a");
        a.textContent = m[1];
        a.href = m[2];
        a.target = "_blank";
        a.rel = "noopener noreferrer";
        target.appendChild(a);
      } else {
        target.appendChild(document.createTextNode(part));
      }
    } else {
      target.appendChild(document.createTextNode(part));
    }
  }
}

function renderMarkdown(text) {
  const frag = document.createDocumentFragment();
  const lines = text.split("\n");
  let i = 0;

  while (i < lines.length) {
    const line = lines[i];

    // fenced code block
    if (line.startsWith("```")) {
      const body = [];
      i++;
      while (i < lines.length && !lines[i].startsWith("```")) body.push(lines[i++]);
      i++;                                   // consume the closing fence
      const pre = document.createElement("pre");
      const code = document.createElement("code");
      code.textContent = body.join("\n");
      pre.appendChild(code);
      frag.appendChild(pre);
      continue;
    }

    // heading
    const h = /^(#{1,4})\s+(.*)$/.exec(line);
    if (h) {
      const el_ = document.createElement(`h${h[1].length + 2}`);
      renderInline(el_, h[2]);
      frag.appendChild(el_);
      i++;
      continue;
    }

    // list (bullet or numbered)
    if (/^\s*([-*+]|\d+\.)\s+/.test(line)) {
      const ordered = /^\s*\d+\./.test(line);
      const list = document.createElement(ordered ? "ol" : "ul");
      while (i < lines.length && /^\s*([-*+]|\d+\.)\s+/.test(lines[i])) {
        const li = document.createElement("li");
        renderInline(li, lines[i].replace(/^\s*([-*+]|\d+\.)\s+/, ""));
        list.appendChild(li);
        i++;
      }
      frag.appendChild(list);
      continue;
    }

    if (!line.trim()) { i++; continue; }

    // paragraph: consume until a blank line or a block element starts
    const para = document.createElement("p");
    const buf = [];
    while (
      i < lines.length && lines[i].trim() &&
      !lines[i].startsWith("```") &&
      !/^#{1,4}\s/.test(lines[i]) &&
      !/^\s*([-*+]|\d+\.)\s+/.test(lines[i])
    ) buf.push(lines[i++]);
    renderInline(para, buf.join("\n"));
    frag.appendChild(para);
  }

  return frag;
}

/* Replace a bubble's contents with rendered Markdown.
 *
 * Only called once a reply is complete. Re-parsing on every token would be
 * wasteful and would flicker on half-written syntax - a lone "**" is bold
 * that has not been closed yet. Tokens stream as plain text; the bubble is
 * upgraded when the turn finishes. */
function renderBubble(bubble, text) {
  bubble.replaceChildren(renderMarkdown(text));
}

function appendMessage(msg, { markdown = false } = {}) {
  const empty = document.getElementById("empty-state");
  if (empty) empty.remove();

  // Tool calls belong to the reply they produced, so on a reload they are
  // rendered immediately before it - the same position they occupied while
  // the turn was running.
  if (msg.tools && msg.tools.length) {
    const rail = makeToolRail();
    for (const t of msg.tools) {
      const chip = makeToolChip(t.name);
      finishToolChip(chip, t);
      rail.appendChild(chip);
    }
    el.messages.appendChild(rail);
  }

  const wrap = document.createElement("div");
  wrap.className = `msg ${msg.message_type}`;
  wrap.dataset.id = msg.id;

  const bubble = document.createElement("div");
  bubble.className = "bubble";

  // User messages are shown verbatim: they typed it, they should see
  // exactly what they typed, not a Markdown interpretation of it.
  if (markdown && msg.message_type === "stellar") {
    bubble.classList.add("md");
    renderBubble(bubble, msg.message_content);
  } else {
    bubble.textContent = msg.message_content;
  }

  wrap.appendChild(bubble);
  el.messages.appendChild(wrap);
  return bubble;
}

function scrollToBottom() {
  el.messages.scrollTop = el.messages.scrollHeight;
}

/* ------------------------------------------------------------------ */
/* chats                                                               */
/* ------------------------------------------------------------------ */

async function loadChats() {
  const chats = await api("/api/chats");
  el.chatList.replaceChildren();

  for (const chat of chats) {
    const item = document.createElement("div");
    item.className = "chat-item" + (chat.id === state.chatId ? " active" : "");
    item.dataset.id = chat.id;

    const name = document.createElement("span");
    name.className = "name";
    name.textContent = chat.name || "New chat";
    item.appendChild(name);

    const del = document.createElement("button");
    del.className = "del";
    del.textContent = "×";
    del.title = "Delete chat";
    del.addEventListener("click", (e) => {
      e.stopPropagation();       // don't also select the chat we're deleting
      deleteChat(chat.id);
    });
    item.appendChild(del);

    item.addEventListener("click", () => selectChat(chat.id));
    el.chatList.appendChild(item);
  }
}

async function selectChat(chatId) {
  state.chatId = chatId;
  // Survives a refresh, so reloading drops you back where you were.
  localStorage.setItem("stellar:lastChat", chatId);

  for (const item of el.chatList.children) {
    item.classList.toggle("active", Number(item.dataset.id) === chatId);
  }

  const messages = await api(`/api/chats/${chatId}/messages`);
  el.messages.replaceChildren();
  if (messages.length === 0) renderEmptyState();
  else messages.forEach((m) => appendMessage(m, { markdown: true }));
  scrollToBottom();
  el.input.focus();
}

async function newChat() {
  const chat = await api("/api/chats", { method: "POST" });
  await loadChats();
  await selectChat(chat.id);
}

async function deleteChat(chatId) {
  await api(`/api/chats/${chatId}`, { method: "DELETE" });

  if (state.chatId === chatId) {
    state.chatId = null;
    localStorage.removeItem("stellar:lastChat");
    el.messages.replaceChildren();
  }
  await loadChats();

  // Deleting the last chat leaves nothing selected; make a fresh one so the
  // composer is never pointing at nothing.
  if (!state.chatId) {
    const remaining = el.chatList.firstElementChild;
    if (remaining) await selectChat(Number(remaining.dataset.id));
    else await newChat();
  }
}

/* ------------------------------------------------------------------ */
/* sending                                                             */
/* ------------------------------------------------------------------ */

/* A turn is two requests: register, then attach.
 *
 * Registering is cheap and starts nothing. Attaching starts the work. The
 * split is what makes a turn survive losing this page - the query id is
 * saved to localStorage, so a refresh can reattach to a generation that is
 * still running on the server. */

/* The composer has two modes, and one button.
 *
 * Idle it sends; while a turn is running it stops. A separate stop control
 * would sit dead most of the time and move the send button around when it
 * appeared, which is worse than one button that changes meaning. */
function setComposerMode(running) {
  state.sending = running;
  el.send.textContent = running ? "Stop" : "Send";
  el.send.classList.toggle("stopping", running);
  el.send.disabled = false;      // never disabled: Stop must stay clickable
  el.input.placeholder = running
    ? "Send a follow-up while it works\u2026"
    : "Send a message\u2026";
}

async function stopGeneration() {
  if (!state.queryId) return;
  try {
    await api(`/api/stream/${state.queryId}/stop`, { method: "POST" });
  } catch (err) {
    console.error("Stop failed:", err);
  }
}

/* Send a message into a turn that is already running.
 *
 * Not the same as starting a new turn: the agent is mid-answer, and this
 * steers it rather than queueing behind it. The server stores the message
 * immediately, so it is rendered here at once rather than waiting for the
 * stream to acknowledge it. */
async function injectMessage(text) {
  const res = await api(`/api/chats/${state.chatId}/inject`, {
    method: "POST",
    body: JSON.stringify({ message: text }),
  });
  appendMessage({ id: res.id, message_type: "user", message_content: text });
  scrollToBottom();
}

function rememberQuery(qid) {
  localStorage.setItem(
    "stellar:activeQuery",
    JSON.stringify({ qid, chatId: state.chatId })
  );
}

function forgetQuery() {
  localStorage.removeItem("stellar:activeQuery");
}

/* Open a stream and render it.
 *
 * `fromIndex` is 0 on a reattach so the server replays the whole turn.
 * Replayed events arrive in one batch with no delay, so the text appears
 * instantly up to wherever generation had reached, then continues at live
 * speed - which is why a mid-stream refresh looks seamless rather than
 * resuming into a gap. */
function attachStream(qid, fromIndex = 0) {
  return new Promise((resolve) => {
    const source = new EventSource(`/api/stream/${qid}?from=${fromIndex}`);

    let bubble = null;      // the reply bubble, created on first token
    let text = "";
    let statusEl = null;
    let toolRail = null;    // chips for tools used in this turn
    let pendingChip = null; // the chip for the call currently running

    const finish = () => {
      source.close();
      if (statusEl) statusEl.remove();
      forgetQuery();
      resolve();
    };

    source.onmessage = (e) => {
      const ev = JSON.parse(e.data);

      switch (ev.type) {
        case "user_message":
          // The worker stored it; render it now so the transcript matches
          // what the database holds.
          if (!document.querySelector(`.msg[data-id="${ev.id}"]`)) {
            appendMessage({
              id: ev.id,
              message_type: "user",
              message_content: state.lastSent || "",
            });
            scrollToBottom();
          }
          break;

        case "chat_title": {
          // The server titled the chat from this first message. Updating the
          // sidebar node in place avoids a follow-up GET /api/chats purely to
          // read back a name the stream already told us.
          const item = el.chatList.querySelector(
            `.chat-item[data-id="${ev.chat_id}"] .name`);
          if (item) item.textContent = ev.name;
          break;
        }

        case "tool_start": {
          // The model wrote this line itself, via the tool's status argument.
          if (!statusEl) {
            statusEl = document.createElement("div");
            statusEl.className = "status";
            el.messages.appendChild(statusEl);
          }
          statusEl.textContent = ev.status;

          if (!toolRail) {
            toolRail = makeToolRail();
            el.messages.insertBefore(toolRail, statusEl);
          }
          pendingChip = makeToolChip(ev.name);
          toolRail.appendChild(pendingChip);
          scrollToBottom();
          break;
        }

        case "tool_end": {
          if (pendingChip) {
            finishToolChip(pendingChip, ev);
            pendingChip = null;
          }
          scrollToBottom();
          break;
        }

        case "stream_reset":
          // A follow-up arrived mid-answer. The partial reply is already
          // committed server-side, so the live bubble is released and the
          // next token opens a fresh one - rather than the new answer being
          // appended to the one it replaced.
          if (bubble) {
            bubble.classList.add("md");
            renderBubble(bubble, text + "\n\n*[interrupted by follow-up]*");
          }
          bubble = null;
          text = "";
          break;

        case "cancelled":
          if (statusEl) { statusEl.remove(); statusEl = null; }
          if (pendingChip) { finishToolChip(pendingChip, { is_error: true }); pendingChip = null; }
          finish();
          break;

        case "interaction":
          if (statusEl) { statusEl.remove(); statusEl = null; }
          // A widget ends the current text bubble: whatever the model said
          // before it belongs above the widget, not merged with what it
          // says after.
          if (bubble) {
            bubble.classList.add("md");
            renderBubble(bubble, text);
            bubble = null;
            text = "";
          }
          renderInteraction(ev);
          break;

        case "interaction_closed":
          closeInteraction(ev.id);
          break;

        case "status":
          if (!statusEl) {
            statusEl = document.createElement("div");
            statusEl.className = "status";
            el.messages.appendChild(statusEl);
          }
          statusEl.textContent = ev.text;
          scrollToBottom();
          break;

        case "token":
          if (!bubble) {
            if (statusEl) { statusEl.remove(); statusEl = null; }
            bubble = appendMessage({
              id: "streaming",
              message_type: "stellar",
              message_content: "",
            });
          }
          text += ev.text;
          bubble.textContent = text;
          scrollToBottom();
          break;

        case "message":
          // Generation finished and the reply is committed. Swap the
          // placeholder id for the real row id so later features (edit,
          // delete, regenerate) can address it, and upgrade the plain
          // streamed text to rendered Markdown now that it is complete.
          if (bubble) {
            bubble.parentElement.dataset.id = ev.id;
            bubble.classList.add("md");
            renderBubble(bubble, text);
          }
          finish();
          break;

        case "error": {
          const b = bubble || appendMessage({
            id: "error", message_type: "stellar", message_content: "",
          });
          b.textContent = `Error: ${ev.message}`;
          b.style.borderColor = "#ff6b6b";
          finish();
          break;
        }

        case "done":
          finish();
          break;
      }
    };

    /* EventSource retries automatically on a dropped connection, resuming
     * from Last-Event-ID. onerror therefore does NOT mean "give up" - it
     * fires on every transient blip. Only a CLOSED state is terminal. */
    source.onerror = () => {
      if (source.readyState === EventSource.CLOSED) finish();
    };
  });
}

async function sendMessage(text) {
  if (!text.trim()) return;

  // Typing while the agent is working is a follow-up, not a new turn.
  if (state.sending) {
    try {
      await injectMessage(text);
    } catch (err) {
      console.error("Inject failed:", err);
    }
    return;
  }

  setComposerMode(true);
  state.lastSent = text;

  try {
    if (state.chatId === null) await newChat();

    const { query_id } = await api(`/api/chats/${state.chatId}/query`, {
      method: "POST",
      body: JSON.stringify({ message: text }),
    });

    state.queryId = query_id;
    rememberQuery(query_id);
    await attachStream(query_id);   // title arrives as a chat_title event
  } catch (err) {
    const bubble = appendMessage({
      id: "error",
      message_type: "stellar",
      message_content: `Error: ${err.message}`,
    });
    bubble.style.borderColor = "#ff6b6b";
    scrollToBottom();
    forgetQuery();
  } finally {
    state.queryId = null;
    setComposerMode(false);
    el.input.focus();
  }
}

/* ------------------------------------------------------------------ */
/* events                                                              */
/* ------------------------------------------------------------------ */

el.composer.addEventListener("submit", (e) => {
  e.preventDefault();

  // While a turn runs the button means Stop. Submitting with an empty box
  // is therefore a stop, and submitting with text is a follow-up.
  const text = el.input.value.trim();
  if (state.sending && !text) {
    stopGeneration();
    return;
  }

  el.input.value = "";
  el.input.style.height = "auto";
  sendMessage(text);
});

el.input.addEventListener("keydown", (e) => {
  // Enter sends, Shift+Enter inserts a newline.
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    el.composer.requestSubmit();
  }
});

// Grow the textarea with its content, up to the max-height set in CSS.
el.input.addEventListener("input", () => {
  el.input.style.height = "auto";
  el.input.style.height = `${el.input.scrollHeight}px`;
});

el.newChat.addEventListener("click", newChat);

/* ------------------------------------------------------------------ */
/* boot                                                                */
/* ------------------------------------------------------------------ */

(async function init() {
  try {
    await loadChats();

    const remembered = Number(localStorage.getItem("stellar:lastChat"));
    const exists = [...el.chatList.children]
      .some((c) => Number(c.dataset.id) === remembered);

    if (exists) await selectChat(remembered);
    else if (el.chatList.firstElementChild) {
      await selectChat(Number(el.chatList.firstElementChild.dataset.id));
    } else {
      await newChat();
    }

    /* Reattach to a turn that was still generating when this page went
     * away. This is the payoff for splitting register from attach: the
     * work is owned by the server and keyed by an id, so a brand new page
     * load can pick it back up.
     *
     * Replaying from index 0 is intentional. The database has no row for a
     * reply that has not finished, so the reloaded transcript is missing
     * the partial text; replaying reproduces it in one instant batch and
     * then continues live. */
    const active = JSON.parse(
      localStorage.getItem("stellar:activeQuery") || "null"
    );
    if (active && active.chatId === state.chatId) {
      // Reattaching to a turn still in progress: the composer has to come
      // back up in Stop mode, or the user cannot interrupt what they
      // reconnected to.
      state.queryId = active.qid;
      setComposerMode(true);
      try {
        await attachStream(active.qid, 0);
      } finally {
        state.queryId = null;
        setComposerMode(false);
      }
    }
  } catch (err) {
    console.error("Failed to initialise:", err);
  }
})();

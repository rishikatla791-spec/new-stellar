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
  if (state.sending || !text.trim()) return;

  // Guard against double-submit: Enter held down, or an impatient click
  // while the request is still in flight.
  state.sending = true;
  el.send.disabled = true;
  state.lastSent = text;

  try {
    if (state.chatId === null) await newChat();

    const { query_id } = await api(`/api/chats/${state.chatId}/query`, {
      method: "POST",
      body: JSON.stringify({ message: text }),
    });

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
    state.sending = false;
    el.send.disabled = false;
    el.input.focus();
  }
}

/* ------------------------------------------------------------------ */
/* events                                                              */
/* ------------------------------------------------------------------ */

el.composer.addEventListener("submit", (e) => {
  e.preventDefault();
  const text = el.input.value;
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
      state.sending = true;
      el.send.disabled = true;
      try {
        await attachStream(active.qid, 0);
      } finally {
        state.sending = false;
        el.send.disabled = false;
      }
    }
  } catch (err) {
    console.error("Failed to initialise:", err);
  }
})();

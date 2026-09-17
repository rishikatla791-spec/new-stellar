/* Stellar frontend (phase 2).
 *
 * No framework, matching the original.
 *
 * A turn is two requests - register, then attach to the stream - and the
 * stream is replayable by index. Together those let a generation survive
 * losing the page it was started from. See attachStream() and init().
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

/* Build message nodes with textContent, never innerHTML.
 *
 * Message content is user-supplied. Assigning it to innerHTML would execute
 * any <script> or onerror= a user typed, in the next viewer's session.
 * Phase 3 introduces markdown rendering for model output, which needs a
 * real sanitiser - the raw-HTML shortcut stops being an option there. */
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
  h.textContent = "Phase 2";
  const p = document.createElement("p");
  p.textContent =
    "No model yet — replies are fake tokens streamed over SSE. " +
    "Send something, then refresh the page mid-stream: it reattaches.";

  empty.append(h, p);
  el.messages.appendChild(empty);
}

function appendMessage(msg) {
  const empty = document.getElementById("empty-state");
  if (empty) empty.remove();

  const wrap = document.createElement("div");
  wrap.className = `msg ${msg.message_type}`;
  wrap.dataset.id = msg.id;

  const bubble = document.createElement("div");
  bubble.className = "bubble";
  bubble.textContent = msg.message_content;

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
  else messages.forEach(appendMessage);
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
          // delete, regenerate) can address it.
          if (bubble) bubble.parentElement.dataset.id = ev.id;
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
    await attachStream(query_id);
    await loadChats();          // picks up the auto-generated chat title
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
        await loadChats();
      } finally {
        state.sending = false;
        el.send.disabled = false;
      }
    }
  } catch (err) {
    console.error("Failed to initialise:", err);
  }
})();

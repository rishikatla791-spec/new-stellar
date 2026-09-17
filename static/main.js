/* Stellar frontend (phase 1).
 *
 * No framework, matching the original. The whole app is: fetch chats, fetch
 * a chat's messages, POST a message, append what comes back.
 *
 * Phase 2 replaces exactly one function - sendMessage() - with a two-step
 * register-then-stream flow. Everything else, including how messages are
 * rendered, stays as it is. That seam is the reason the render path is a
 * separate appendMessage() rather than being inlined into the POST handler.
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
  h.textContent = "Phase 1";
  const p = document.createElement("p");
  p.textContent =
    "No model yet — the server echoes your message back. " +
    "Send something, then refresh the page: it should still be here.";

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

async function sendMessage(text) {
  if (state.sending || !text.trim()) return;

  // Guard against double-submit: Enter held down, or an impatient click
  // while the request is still in flight.
  state.sending = true;
  el.send.disabled = true;

  try {
    if (state.chatId === null) await newChat();

    // --- phase 2 replaces this with register_query + EventSource -----
    const stored = await api(`/api/chats/${state.chatId}/messages`, {
      method: "POST",
      body: JSON.stringify({ message: text }),
    });
    stored.forEach(appendMessage);
    // ----------------------------------------------------------------

    scrollToBottom();
    await loadChats();          // picks up the auto-generated chat title
  } catch (err) {
    const bubble = appendMessage({
      id: "error",
      message_type: "stellar",
      message_content: `Error: ${err.message}`,
    });
    bubble.style.borderColor = "#ff6b6b";
    scrollToBottom();
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
  } catch (err) {
    console.error("Failed to initialise:", err);
  }
})();

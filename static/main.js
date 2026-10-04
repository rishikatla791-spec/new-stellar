/* Stellar frontend.
 *
 * No framework, matching the original.
 *
 * A turn is two requests - register, then attach to the stream - and the
 * stream is replayable by index. Together those let a reply survive losing
 * the page it was started from, and let a page rejoin a reply running in
 * another chat. See attachStream() and selectChat().
 *
 * Model output is Markdown, rendered by renderMarkdown() into DOM nodes,
 * never through innerHTML. The page also carries a Content-Security-Policy
 * that allows only its own files, so even a mistake here could not load an
 * outside script or image.
 */

"use strict";

const state = {
  chatId: null,
  chats: [],          // [{id, name, generating}]
  turn: null,         // the reply being watched in the open chat, if any
  me: null,
};

const el = {
  app:        document.querySelector(".app"),
  sidebar:    document.getElementById("sidebar"),
  backdrop:   document.getElementById("drawer-backdrop"),
  menuBtn:    document.getElementById("menu-btn"),
  topTitle:   document.getElementById("top-title"),
  topNew:     document.getElementById("top-new-chat"),
  chatList:   document.getElementById("chat-list"),
  messages:   document.getElementById("messages"),
  jump:       document.getElementById("jump-latest"),
  banner:     document.getElementById("conn-banner"),
  composer:   document.getElementById("composer"),
  input:      document.getElementById("input"),
  send:       document.getElementById("send"),
  stop:       document.getElementById("stop"),
  hint:       document.getElementById("composer-hint"),
  newChat:    document.getElementById("new-chat"),
  userBtn:    document.getElementById("user-btn"),
  userMenu:   document.getElementById("user-menu"),
  toasts:     document.getElementById("toasts"),
  srStatus:   document.getElementById("sr-status"),
  confirm:    document.getElementById("confirm-dialog"),
  prompt:     document.getElementById("prompt-dialog"),
  settings:   document.getElementById("settings-dialog"),
};

const TOUCH = window.matchMedia("(pointer: coarse)").matches;

/* ------------------------------------------------------------------ */
/* talking to the server                                               */
/* ------------------------------------------------------------------ */

/* Every request that changes something carries the page's CSRF token: the
   server refuses state-changing requests without it (see check_csrf). */
const CSRF_TOKEN = (document.querySelector('meta[name="csrf-token"]') || {}).content || "";

function jsonHeaders() {
  return {
    "Content-Type": "application/json",
    // Without this the server could not tell an API call from a page load,
    // and an expired session got a redirect to the login page as its answer.
    Accept: "application/json",
    "X-CSRF-Token": CSRF_TOKEN,
  };
}

function goToSignIn() {
  location.href = "/auth/login?next=" + encodeURIComponent(location.pathname);
}

/* The one place responses are interpreted. Signed out goes to sign-in, an
   account that lost its approval reloads into the waiting page, and every
   other failure becomes an Error whose message is a sentence. */
async function api(path, options = {}) {
  let res;
  try {
    res = await fetch(path, {
      ...options,
      headers: { ...jsonHeaders(), ...(options.headers || {}) },
    });
  } catch (err) {
    throw new Error("Can't reach Stellar. Check your connection and try again.");
  }

  if (res.status === 401) {
    goToSignIn();
    throw new Error("You are signed out.");
  }
  if (res.status === 403) {
    const body = await res.clone().json().catch(() => null);
    if (body && /approval|access/i.test(body.error || "")) {
      location.reload();             // the server shows the waiting page
      throw new Error(body.error);
    }
  }
  if (res.status === 204) return null;

  const body = await res.json().catch(() => null);
  if (!res.ok) {
    const err = new Error((body && body.error) || `Something went wrong (${res.status}).`);
    err.status = res.status;
    throw err;
  }
  return body;
}

/* ------------------------------------------------------------------ */
/* messages to the person: toasts, announcements, dialogs              */
/* ------------------------------------------------------------------ */

/* Failures used to reach only the console. Every one now lands here: a
   toast that says what happened and, where it helps, offers a retry. */
function toast(message, { kind = "error", action = null, timeout = 7000 } = {}) {
  const t = document.createElement("div");
  t.className = `toast ${kind}`;
  t.setAttribute("role", kind === "error" ? "alert" : "status");
  const text = document.createElement("span");
  text.textContent = message;
  t.appendChild(text);
  if (action) {
    const b = document.createElement("button");
    b.type = "button";
    b.className = "btn-ghost btn-sm";
    b.textContent = action.label;
    b.addEventListener("click", () => { t.remove(); action.fn(); });
    t.appendChild(b);
  }
  const x = document.createElement("button");
  x.type = "button";
  x.className = "toast-close";
  x.setAttribute("aria-label", "Dismiss");
  x.textContent = "×";
  x.addEventListener("click", () => t.remove());
  t.appendChild(x);
  el.toasts.appendChild(t);
  if (timeout) setTimeout(() => t.remove(), timeout);
  return t;
}

/* For screen readers: what is happening, said once, politely. */
function announce(text) {
  el.srStatus.textContent = "";
  setTimeout(() => { el.srStatus.textContent = text; }, 30);
}

/* A yes/no question in a native <dialog>: focus is trapped and Escape
   cancels, for free. Resolves true only on the confirming button. */
function confirmDialog({ title, body, confirmLabel = "OK", danger = false }) {
  const d = el.confirm;
  d.querySelector(".dialog-title").textContent = title;
  d.querySelector(".dialog-body").textContent = body;
  const ok = d.querySelector("[value=ok]");
  ok.textContent = confirmLabel;
  ok.className = danger ? "btn-danger" : "btn-primary";
  d.returnValue = "";
  d.showModal();
  return new Promise((resolve) => {
    d.addEventListener("close", () => resolve(d.returnValue === "ok"), { once: true });
  });
}

function promptDialog({ title, label, value = "", confirmLabel = "Save" }) {
  const d = el.prompt;
  d.querySelector(".dialog-title").textContent = title;
  d.querySelector("label").textContent = label;
  const input = d.querySelector("input");
  input.value = value;
  d.querySelector("[value=ok]").textContent = confirmLabel;
  d.returnValue = "";
  d.showModal();
  input.select();
  return new Promise((resolve) => {
    d.addEventListener("close", () => {
      resolve(d.returnValue === "ok" ? input.value.trim() : null);
    }, { once: true });
  });
}

async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch (err) {
    // Clipboard API needs a secure context; plain-http development is not.
    const ta = document.createElement("textarea");
    ta.value = text;
    ta.setAttribute("readonly", "");
    ta.className = "offscreen";
    document.body.appendChild(ta);
    ta.select();
    let ok = false;
    try { ok = document.execCommand("copy"); } catch (e) { ok = false; }
    ta.remove();
    return ok;
  }
}

function copyButton(getText, label = "Copy") {
  const b = document.createElement("button");
  b.type = "button";
  b.className = "copy-btn";
  b.textContent = label;
  b.setAttribute("aria-label", label === "Copy" ? "Copy to clipboard" : label);
  b.addEventListener("click", async () => {
    const ok = await copyText(getText());
    b.textContent = ok ? "Copied" : "Couldn't copy";
    announce(ok ? "Copied to the clipboard" : "Couldn't copy");
    setTimeout(() => { b.textContent = label; }, 1600);
  });
  return b;
}

/* ------------------------------------------------------------------ */
/* layout: the sidebar is a drawer on phones                           */
/* ------------------------------------------------------------------ */

const NARROW = window.matchMedia("(max-width: 767px)");

function drawerOpen() { return el.app.classList.contains("drawer-open"); }

function openDrawer() {
  el.app.classList.add("drawer-open");
  el.menuBtn.setAttribute("aria-expanded", "true");
  el.backdrop.hidden = false;
  const first = el.sidebar.querySelector("button, a");
  if (first) first.focus();
}

function closeDrawer({ restoreFocus = true } = {}) {
  if (!drawerOpen()) return;
  el.app.classList.remove("drawer-open");
  el.menuBtn.setAttribute("aria-expanded", "false");
  el.backdrop.hidden = true;
  if (restoreFocus && NARROW.matches) el.menuBtn.focus();
}

el.menuBtn.addEventListener("click", () => (drawerOpen() ? closeDrawer() : openDrawer()));
el.backdrop.addEventListener("click", () => closeDrawer());
NARROW.addEventListener("change", () => closeDrawer({ restoreFocus: false }));

/* Inside the open drawer, Tab cycles through the drawer: the page behind
   it is covered and should not take focus. */
el.sidebar.addEventListener("keydown", (e) => {
  if (!drawerOpen() || e.key !== "Tab") return;
  const items = [...el.sidebar.querySelectorAll("button:not([disabled]), a[href]")]
    .filter((n) => n.offsetParent !== null);
  if (!items.length) return;
  const first = items[0], last = items[items.length - 1];
  if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
  else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
});

/* ------------------------------------------------------------------ */
/* menus                                                               */
/* ------------------------------------------------------------------ */

let openMenu = null;

function closeMenu() {
  if (!openMenu) return;
  const { menu, button } = openMenu;
  menu.hidden = true;
  button.setAttribute("aria-expanded", "false");
  openMenu = null;
}

function showMenu(menu, button) {
  if (openMenu && openMenu.menu === menu) { closeMenu(); return; }
  closeMenu();
  menu.hidden = false;
  button.setAttribute("aria-expanded", "true");
  openMenu = { menu, button };
  const first = menu.querySelector("[role=menuitem]");
  if (first) first.focus();
}

document.addEventListener("click", (e) => {
  if (openMenu && !openMenu.menu.contains(e.target) && !openMenu.button.contains(e.target)) {
    closeMenu();
  }
});

document.addEventListener("keydown", (e) => {
  if (openMenu) {
    const items = [...openMenu.menu.querySelectorAll("[role=menuitem]")];
    const i = items.indexOf(document.activeElement);
    if (e.key === "Escape") { const b = openMenu.button; closeMenu(); b.focus(); e.preventDefault(); return; }
    if (e.key === "ArrowDown") { items[(i + 1) % items.length].focus(); e.preventDefault(); return; }
    if (e.key === "ArrowUp") { items[(i - 1 + items.length) % items.length].focus(); e.preventDefault(); return; }
  }
  if (e.key === "Escape" && drawerOpen()) { closeDrawer(); return; }
  // Esc stops a reply - unless a dialog, menu or the terminal has it.
  if (e.key === "Escape" && state.turn && !state.turn.stopping
      && !document.querySelector("dialog[open]") && !e.target.closest(".terminal-screen")) {
    stopGeneration();
  }
});

function menuItem(label, fn, { danger = false } = {}) {
  const b = document.createElement("button");
  b.type = "button";
  b.setAttribute("role", "menuitem");
  b.className = "menu-item" + (danger ? " danger" : "");
  b.textContent = label;
  b.addEventListener("click", () => { closeMenu(); fn(); });
  return b;
}

/* ------------------------------------------------------------------ */
/* tool activity                                                       */
/* ------------------------------------------------------------------ */

/* A widget does not survive a reload; what came of it does. */
function widgetSummary(tool) {
  const w = tool.widget;
  const card = document.createElement("div");
  card.className = "widget-summary";
  const head = document.createElement("div");
  head.className = "widget-summary-head";
  head.textContent = w.kind === "chess" ? "Chess board · closed" : "Interactive widget · closed";
  card.appendChild(head);
  if (w.goal && w.goal !== "chess") {
    const goal = document.createElement("div");
    goal.className = "widget-summary-goal";
    goal.textContent = w.goal;
    card.appendChild(goal);
  }
  if (w.result) {
    const res = document.createElement("div");
    res.className = "widget-summary-result";
    res.textContent = w.result.length >= 240 ? w.result + "…" : w.result;
    card.appendChild(res);
  }
  const wrap = document.createElement("div");
  wrap.className = "msg stellar widget-summary-wrap";
  wrap.appendChild(card);
  return wrap;
}

/* ------------------------------------------------------------------ */
/* markdown                                                            */
/* ------------------------------------------------------------------ */

/* A deliberately small Markdown dialect, rendered straight to DOM nodes.
 *
 * Every piece of text ends up in a text node via textContent, so there is
 * no HTML parsing anywhere in this path and therefore no XSS surface - even
 * though the model's output is untrusted (it can be steered by anything in
 * the conversation). Supported: fenced code with a language, inline code,
 * headings, bold, italic, strikethrough, links, images, lists (one level
 * of nesting), blockquotes, tables and rules. Anything else renders as
 * literal text, which is the safe failure mode. */

const INLINE_PATTERNS = [
  // code first, so nothing inside it is read as Markdown
  ["code", /`([^`\n]+)`/],
  ["image", /!\[([^\]\n]*)\]\(([^)\s]+)\)/],
  ["link", /\[([^\]\n]+)\]\(([^)\s]+)\)/],
  ["bold", /\*\*(?=\S)([^\n]*?\S)\*\*/],
  ["strike", /~~(?=\S)([^\n]*?\S)~~/],
  // An asterisk opens italics only against a word, so "2 * 3 * 4" is
  // arithmetic and not "2 *3* 4" in italics.
  ["em", /(?<![\w*])\*(?=[^\s*])([^*\n]*?[^\s*])\*(?![\w*])/],
];

function ownFile(url) {
  return /^\/api\/(outputs|chats)\//.test(url);
}

function hostOf(url) {
  try { return new URL(url).host; } catch (e) { return "another site"; }
}

function renderInline(target, text) {
  let rest = text;
  while (rest) {
    let best = null;
    for (const [kind, re] of INLINE_PATTERNS) {
      const m = re.exec(rest);
      if (m && (best === null || m.index < best.m.index)) best = { kind, m };
    }
    if (!best) { target.appendChild(document.createTextNode(rest)); break; }
    const { kind, m } = best;
    if (m.index) target.appendChild(document.createTextNode(rest.slice(0, m.index)));
    rest = rest.slice(m.index + m[0].length);

    if (kind === "code") {
      const c = document.createElement("code");
      c.textContent = m[1];
      target.appendChild(c);
    } else if (kind === "bold" || kind === "em" || kind === "strike") {
      const n = document.createElement(kind === "bold" ? "strong" : kind === "em" ? "em" : "del");
      renderInline(n, m[1]);
      target.appendChild(n);
    } else if (kind === "image") {
      target.appendChild(renderImage(m[1], m[2], m[0]));
    } else if (kind === "link") {
      target.appendChild(renderLink(m[1], m[2], m[0]));
    }
  }
}

function renderImage(alt, url, raw) {
  if (ownFile(url)) {
    // This chat's own files: shown directly.
    const img = document.createElement("img");
    img.src = url;
    img.alt = alt;
    img.loading = "lazy";
    img.className = "md-img";
    return img;
  }
  if (/^https:\/\//i.test(url)) {
    // An outside image would tell that site who is reading and when, and
    // the model can be steered into embedding one. It loads only if the
    // reader chooses to open it.
    const a = document.createElement("a");
    a.href = url;
    a.target = "_blank";
    a.rel = "noopener noreferrer";
    a.className = "ext-image";
    a.textContent = `Open image from ${hostOf(url)}` + (alt ? ` (${alt})` : "");
    return a;
  }
  return document.createTextNode(raw);
}

function renderLink(label, url, raw) {
  // Scheme allowlist: a javascript: or data: href would execute on click,
  // which is exactly the hole avoiding innerHTML was meant to close.
  if (!/^(https?:\/\/|mailto:|\/)/i.test(url)) return document.createTextNode(raw);
  if (/^\/api\/outputs\//.test(url)) return fileCard(label, url);
  const a = document.createElement("a");
  a.textContent = label;
  a.href = url;
  a.target = "_blank";
  a.rel = "noopener noreferrer";
  return a;
}

/* A file a tool made, shown like an uploaded one: a card with its name and
   size rather than a bare link. The size is asked for once, lazily. */
function fileCard(label, url) {
  const name = decodeURIComponent(url.split("/").pop() || label);
  const a = document.createElement("a");
  a.className = "att-file file-card";
  a.href = url;
  a.target = "_blank";
  a.rel = "noopener noreferrer";
  a.title = label;
  a.appendChild(kindBadge(kindFromName(name), name));
  const txt = document.createElement("span");
  txt.className = "att-text";
  const nm = document.createElement("span");
  nm.className = "att-name";
  nm.textContent = name;
  const meta = document.createElement("span");
  meta.className = "att-meta";
  meta.textContent = "Download";
  txt.append(nm, meta);
  a.appendChild(txt);
  fetch(url, { method: "HEAD" }).then((r) => {
    if (r.status === 404) meta.textContent = "File no longer exists";
    const size = Number(r.headers.get("Content-Length"));
    if (r.ok && size) meta.textContent = fmtBytes(size);
  }).catch(() => {});
  return a;
}

/* --- syntax highlighting: a tokenizer, not a parser ------------------- */

const KEYWORDS = {
  js: "async await break case catch class const continue default delete do else export extends false finally for from function if import in instanceof let new null of return super switch this throw true try typeof undefined var void while yield",
  py: "and as assert async await break class continue def del elif else except False finally for from global if import in is lambda None nonlocal not or pass raise return self True try while with yield",
  sh: "case do done elif else esac export fi for function if in local return then until while echo cd sudo",
  sql: "select from where insert into values update set delete create table drop alter join left right inner outer on group by order having limit as and or not null primary key index distinct union",
  go: "break case chan const continue default defer else fallthrough for func go goto if import interface map package range return select struct switch type var nil true false",
  c: "auto break case char const continue default do double else enum extern float for goto if int long return short signed sizeof static struct switch typedef union unsigned void volatile while class public private protected new delete true false null fn let mut impl pub use match",
};
const LANG_ALIASES = {
  javascript: "js", ts: "js", typescript: "js", jsx: "js", tsx: "js", json: "js", node: "js",
  python: "py", python3: "py", py3: "py",
  bash: "sh", shell: "sh", zsh: "sh", console: "sh",
  golang: "go", cpp: "c", "c++": "c", java: "c", rust: "c", rs: "c", csharp: "c", cs: "c",
  kotlin: "c", swift: "c", php: "c",
};

function highlight(code, lang) {
  const frag = document.createDocumentFragment();
  const key = LANG_ALIASES[lang] || lang;
  const words = KEYWORDS[key] ? new Set(KEYWORDS[key].split(" ")) : null;
  if (!words) { frag.appendChild(document.createTextNode(code)); return frag; }
  const hashComment = key === "py" || key === "sh";
  const re = hashComment
    ? /(#[^\n]*)|("""[\s\S]*?"""|'''[\s\S]*?'''|"(?:\\.|[^"\\\n])*"|'(?:\\.|[^'\\\n])*')|(\b\d+(?:\.\d+)?\b)|([A-Za-z_]\w*)/g
    : key === "sql"
      ? /(--[^\n]*)|('(?:''|[^'\n])*')|(\b\d+(?:\.\d+)?\b)|([A-Za-z_]\w*)/g
      : /(\/\/[^\n]*|\/\*[\s\S]*?\*\/)|(`(?:\\.|[^`\\])*`|"(?:\\.|[^"\\\n])*"|'(?:\\.|[^'\\\n])*')|(\b\d+(?:\.\d+)?\b)|([A-Za-z_$][\w$]*)/g;
  let last = 0;
  for (const m of code.matchAll(re)) {
    const word = m[4];
    const cls = m[1] ? "tok-comment" : m[2] ? "tok-string" : m[3] ? "tok-number"
      : (word && words.has(key === "sql" ? word.toLowerCase() : word)) ? "tok-keyword" : null;
    if (!cls) continue;
    if (m.index > last) frag.appendChild(document.createTextNode(code.slice(last, m.index)));
    const span = document.createElement("span");
    span.className = cls;
    span.textContent = m[0];
    frag.appendChild(span);
    last = m.index + m[0].length;
  }
  if (last < code.length) frag.appendChild(document.createTextNode(code.slice(last)));
  return frag;
}

function codeBlock(body, lang) {
  const wrap = document.createElement("div");
  wrap.className = "code-block";
  const head = document.createElement("div");
  head.className = "code-head";
  const label = document.createElement("span");
  label.className = "code-lang";
  label.textContent = lang || "text";
  head.append(label, copyButton(() => body));
  const pre = document.createElement("pre");
  const code = document.createElement("code");
  code.appendChild(highlight(body, (lang || "").toLowerCase()));
  pre.appendChild(code);
  pre.tabIndex = 0;                 // a long line scrolls by keyboard too
  wrap.append(head, pre);
  return wrap;
}

/* --- blocks ------------------------------------------------------------ */

const LIST_RE = /^(\s*)([-*+]|\d+[.)])\s+/;
const TABLE_SEP_RE = /^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$/;

function splitRow(line) {
  let s = line.trim();
  if (s.startsWith("|")) s = s.slice(1);
  if (s.endsWith("|") && !s.endsWith("\\|")) s = s.slice(0, -1);
  return s.split(/(?<!\\)\|/).map((c) => c.trim().replace(/\\\|/g, "|"));
}

function renderTable(header, sep, rows) {
  const aligns = splitRow(sep).map((c) =>
    c.startsWith(":") && c.endsWith(":") ? "center" : c.endsWith(":") ? "right" : "");
  const wrap = document.createElement("div");
  wrap.className = "table-wrap";
  wrap.tabIndex = 0;                // wide tables scroll by keyboard
  const table = document.createElement("table");
  const thead = document.createElement("thead");
  const tr = document.createElement("tr");
  splitRow(header).forEach((c, i) => {
    const th = document.createElement("th");
    th.scope = "col";
    if (aligns[i]) th.style.textAlign = aligns[i];
    renderInline(th, c);
    tr.appendChild(th);
  });
  thead.appendChild(tr);
  const tbody = document.createElement("tbody");
  for (const row of rows) {
    const r = document.createElement("tr");
    splitRow(row).forEach((c, i) => {
      const td = document.createElement("td");
      if (aligns[i]) td.style.textAlign = aligns[i];
      renderInline(td, c);
      r.appendChild(td);
    });
    tbody.appendChild(r);
  }
  table.append(thead, tbody);
  wrap.appendChild(table);
  return wrap;
}

function renderList(lines, start) {
  // Items at the first line's indent; deeper lines nest one level.
  const baseIndent = LIST_RE.exec(lines[start])[1].length;
  const ordered = /\d/.test(LIST_RE.exec(lines[start])[2]);
  const list = document.createElement(ordered ? "ol" : "ul");
  let i = start;
  let li = null;
  while (i < lines.length) {
    const m = LIST_RE.exec(lines[i]);
    if (!m) {
      // A wrapped line belongs to the item above it.
      if (li && lines[i].trim() && /^\s{2,}/.test(lines[i])) {
        li.appendChild(document.createTextNode(" "));
        renderInline(li, lines[i].trim());
        i++;
        continue;
      }
      break;
    }
    const indent = m[1].length;
    if (indent < baseIndent) break;
    if (indent > baseIndent && li) {
      const [sub, next] = renderList(lines, i);
      li.appendChild(sub);
      i = next;
      continue;
    }
    li = document.createElement("li");
    renderInline(li, lines[i].slice(m[0].length));
    list.appendChild(li);
    i++;
  }
  return [list, i];
}

function isBlockStart(lines, i) {
  const line = lines[i];
  return line.startsWith("```") || /^#{1,6}\s/.test(line) || LIST_RE.test(line)
    || /^\s*>/.test(line) || /^\s*([-*_])(\s*\1){2,}\s*$/.test(line)
    || (line.includes("|") && i + 1 < lines.length && TABLE_SEP_RE.test(lines[i + 1]));
}

function renderMarkdown(text) {
  const frag = document.createDocumentFragment();
  const lines = String(text || "").replace(/\r\n?/g, "\n").split("\n");
  let i = 0;

  while (i < lines.length) {
    const line = lines[i];

    if (line.startsWith("```")) {
      const lang = line.slice(3).trim().split(/\s+/)[0] || "";
      const body = [];
      i++;
      while (i < lines.length && !lines[i].startsWith("```")) body.push(lines[i++]);
      i++;                                   // the closing fence
      frag.appendChild(codeBlock(body.join("\n"), lang));
      continue;
    }

    const h = /^(#{1,6})\s+(.*)$/.exec(line);
    if (h) {
      const level = Math.min(h[1].length + 2, 6);   // h3..h6: the page owns h1/h2
      const node = document.createElement(`h${level}`);
      renderInline(node, h[2].replace(/\s+#+\s*$/, ""));
      frag.appendChild(node);
      i++;
      continue;
    }

    if (/^\s*([-*_])(\s*\1){2,}\s*$/.test(line)) {
      frag.appendChild(document.createElement("hr"));
      i++;
      continue;
    }

    if (line.includes("|") && i + 1 < lines.length && TABLE_SEP_RE.test(lines[i + 1])) {
      const header = line, sep = lines[i + 1];
      const rows = [];
      i += 2;
      while (i < lines.length && lines[i].includes("|") && lines[i].trim()) rows.push(lines[i++]);
      frag.appendChild(renderTable(header, sep, rows));
      continue;
    }

    if (/^\s*>/.test(line)) {
      const body = [];
      while (i < lines.length && /^\s*>/.test(lines[i])) body.push(lines[i++].replace(/^\s*>\s?/, ""));
      const q = document.createElement("blockquote");
      q.appendChild(renderMarkdown(body.join("\n")));
      frag.appendChild(q);
      continue;
    }

    if (LIST_RE.test(line)) {
      const [list, next] = renderList(lines, i);
      frag.appendChild(list);
      i = next;
      continue;
    }

    if (!line.trim()) { i++; continue; }

    const para = document.createElement("p");
    const buf = [];
    while (i < lines.length && lines[i].trim() && (buf.length === 0 || !isBlockStart(lines, i))) {
      buf.push(lines[i++]);
    }
    buf.forEach((b, n) => {
      if (n) para.appendChild(document.createElement("br"));
      renderInline(para, b);
    });
    frag.appendChild(para);
  }

  return frag;
}

/* Replace a bubble's contents with rendered Markdown. Done once a reply is
   complete: re-parsing on every token would flicker on half-written syntax.
   Tokens stream as plain text; the bubble is upgraded when the turn ends. */
function renderBubble(bubble, text) {
  bubble.replaceChildren(renderMarkdown(text));
  bubble.classList.add("md");
  bubble.dataset.raw = text;
  addReplyActions(bubble);
}

function addReplyActions(bubble) {
  const wrap = bubble.parentElement;
  if (!wrap || wrap.querySelector(".reply-actions")) return;
  const bar = document.createElement("div");
  bar.className = "reply-actions";
  bar.appendChild(copyButton(() => bubble.dataset.raw || bubble.textContent, "Copy reply"));
  wrap.appendChild(bar);
}

/* ------------------------------------------------------------------ */
/* the transcript                                                      */
/* ------------------------------------------------------------------ */

function clearEmptyState() {
  const empty = document.getElementById("empty-state");
  if (empty) empty.remove();
}

function renderEmptyState() {
  const empty = document.createElement("div");
  empty.className = "empty";
  empty.id = "empty-state";
  const h = document.createElement("h2");
  h.textContent = "What can I help with?";
  const p = document.createElement("p");
  p.textContent = "Ask a question, attach a file, or ask Stellar to run code, search the web or build something. It can work in its own Linux sandbox.";
  empty.append(h, p);
  el.messages.appendChild(empty);
}

/* {id, text} for the message a scheduled task starts its turn with
   ("[Scheduled task #3] ..."), else null. */
function scheduledTask(content) {
  const m = /^\[Scheduled task #(\d+)\]\s*([\s\S]*)$/.exec(content || "");
  if (!m) return null;
  const text = m[2].replace(/\s*\(This task is running on its schedule;[\s\S]*$/, "");
  return { id: m[1], text };
}

function appendMessage(msg, { markdown = false } = {}) {
  clearEmptyState();

  // What came of a widget is shown before the reply it led to.
  for (const t of msg.tools || []) {
    if (t.widget) el.messages.appendChild(widgetSummary(t));
  }

  // A scheduled task's turn opens with the task, not with words the user
  // typed, so it is shown as a small note. Rows saved before the model's
  // orders moved out of the message still end with them; they are cut.
  const task = msg.message_type === "user" ? scheduledTask(msg.message_content) : null;

  const wrap = document.createElement("div");
  wrap.className = `msg ${task ? "task" : msg.message_type}`;
  wrap.dataset.id = msg.id;

  const who = document.createElement("span");
  who.className = "sr-only";
  who.textContent = task ? "Scheduled task:" : msg.message_type === "user" ? "You said:" : "Stellar said:";
  wrap.appendChild(who);

  const bubble = document.createElement("div");
  bubble.className = "bubble";
  wrap.appendChild(bubble);
  el.messages.appendChild(wrap);

  // User messages are shown verbatim: they typed it, they should see
  // exactly what they typed, not a Markdown interpretation of it.
  if (task) {
    const label = document.createElement("span");
    label.className = "task-label";
    label.textContent = `Scheduled task #${task.id}`;
    bubble.append(label, task.text);
  } else if (markdown && msg.message_type === "stellar") {
    renderBubble(bubble, msg.message_content);
  } else {
    bubble.textContent = msg.message_content;
  }

  if (msg.attachments && msg.attachments.length) {
    bubble.appendChild(renderAttachments(msg.attachments));
  }
  return bubble;
}

/* An error goes UNDER whatever the reply had said, never in place of it. */
function appendError(message, { retry = null } = {}) {
  clearEmptyState();
  const card = document.createElement("div");
  card.className = "msg stellar error-card";
  card.setAttribute("role", "alert");
  const text = document.createElement("span");
  text.textContent = message;
  card.appendChild(text);
  if (retry) {
    const b = document.createElement("button");
    b.type = "button";
    b.className = "btn-ghost btn-sm";
    b.textContent = "Retry";
    b.addEventListener("click", () => { card.remove(); retry(); });
    card.appendChild(b);
  }
  el.messages.appendChild(card);
  maybeScroll();
  return card;
}

/* --- scrolling: follow the reply only if the reader is at the end ------ */

let stickToBottom = true;

function nearBottom() {
  const m = el.messages;
  return m.scrollHeight - m.scrollTop - m.clientHeight < 80;
}

el.messages.addEventListener("scroll", () => {
  stickToBottom = nearBottom();
  if (stickToBottom) el.jump.hidden = true;
});

function scrollToBottom() {
  el.messages.scrollTop = el.messages.scrollHeight;
  stickToBottom = true;
  el.jump.hidden = true;
}

function maybeScroll() {
  if (stickToBottom) scrollToBottom();
  else el.jump.hidden = false;
}

el.jump.addEventListener("click", () => { scrollToBottom(); el.input.focus(); });

/* ------------------------------------------------------------------ */
/* chats                                                               */
/* ------------------------------------------------------------------ */

function chatName(chat) { return (chat && chat.name) || "New chat"; }

function updateTitle() {
  const chat = state.chats.find((c) => c.id === state.chatId);
  const name = chatName(chat);
  el.topTitle.textContent = name;
  document.title = (titleMarker ? "● " : "") + `${name} · Stellar`;
}

/* A reply finishing in a background tab marks the tab until it is seen. */
let titleMarker = false;
document.addEventListener("visibilitychange", () => {
  if (!document.hidden && titleMarker) { titleMarker = false; updateTitle(); }
});

function renderChatList() {
  el.chatList.replaceChildren();
  if (!state.chats.length) {
    const p = document.createElement("p");
    p.className = "chat-list-empty";
    p.textContent = "No chats yet.";
    el.chatList.appendChild(p);
    return;
  }
  for (const chat of state.chats) {
    const li = document.createElement("li");
    li.className = "chat-item" + (chat.id === state.chatId ? " active" : "");
    li.dataset.id = chat.id;

    const open = document.createElement("button");
    open.type = "button";
    open.className = "chat-open";
    if (chat.id === state.chatId) open.setAttribute("aria-current", "page");
    const name = document.createElement("span");
    name.className = "name";
    name.textContent = chatName(chat);
    open.appendChild(name);
    if (chat.generating) {
      const dot = document.createElement("span");
      dot.className = "gen-dot";
      dot.title = "Replying";
      const sr = document.createElement("span");
      sr.className = "sr-only";
      sr.textContent = " (replying)";
      open.append(dot, sr);
    }
    open.addEventListener("click", () => selectChat(chat.id));
    li.appendChild(open);

    const more = document.createElement("button");
    more.type = "button";
    more.className = "chat-more";
    more.setAttribute("aria-label", `Options for ${chatName(chat)}`);
    more.setAttribute("aria-haspopup", "menu");
    more.setAttribute("aria-expanded", "false");
    more.textContent = "⋯";
    const menu = document.createElement("div");
    menu.className = "menu chat-menu";
    menu.setAttribute("role", "menu");
    menu.hidden = true;
    menu.append(
      menuItem("Rename", () => renameChat(chat.id)),
      menuItem("Delete…", () => deleteChat(chat.id), { danger: true }),
    );
    more.addEventListener("click", (e) => { e.stopPropagation(); showMenu(menu, more); });
    li.append(more, menu);
    el.chatList.appendChild(li);
  }
}

async function loadChats() {
  state.chats = await api("/api/chats");
  renderChatList();
  updateTitle();
}

function setGenerating(chatId, on) {
  const chat = state.chats.find((c) => c.id === chatId);
  if (chat && chat.generating !== on) { chat.generating = on; renderChatList(); }
}

let selecting = 0;

async function selectChat(chatId) {
  const token = ++selecting;
  // Unsent files belong to the chat they were uploaded to. Carrying them
  // into another chat would send them somewhere the user did not choose.
  if (state.chatId !== chatId) discardPending();
  // Leaving a chat mid-reply stops WATCHING the reply, not the reply: it
  // runs on in the server and is rejoined on the way back.
  if (state.turn && state.turn.chatId !== chatId) detachTurn();

  state.chatId = chatId;
  try { localStorage.setItem("stellar:lastChat", chatId); } catch (e) { /* private mode */ }
  renderChatList();
  updateTitle();
  closeDrawer({ restoreFocus: false });

  el.messages.replaceChildren();
  const loading = document.createElement("p");
  loading.className = "loading";
  loading.textContent = "Loading…";
  el.messages.appendChild(loading);
  el.messages.setAttribute("aria-busy", "true");

  let messages;
  try {
    messages = await api(`/api/chats/${chatId}/messages`);
  } catch (err) {
    if (token !== selecting) return;
    el.messages.replaceChildren();
    el.messages.removeAttribute("aria-busy");
    if (err.status === 404) {
      toast("That chat no longer exists.");
      await loadChats().catch(() => {});
      return openFirstChat();
    }
    appendError("Couldn't load this chat. " + err.message, { retry: () => selectChat(chatId) });
    return;
  }
  if (token !== selecting) return;          // the user moved on meanwhile
  el.messages.replaceChildren();
  el.messages.removeAttribute("aria-busy");
  if (messages.length === 0) renderEmptyState();
  else messages.forEach((m) => appendMessage(m, { markdown: true }));
  scrollToBottom();
  setComposerMode();
  if (!TOUCH) el.input.focus();

  if (typeof termState !== "undefined" && termState.open && termState.connectedChatId !== chatId) {
    connectTerminal(chatId);
  }

  // A reply still running here - started in another tab, before a reload,
  // or before this chat was left - is rejoined where it is.
  try {
    const { query_id } = await api(`/api/chats/${chatId}/active`);
    if (query_id && token === selecting && !state.turn) {
      setGenerating(chatId, true);
      runTurn(query_id, chatId, { rejoin: true });
    } else if (!query_id) {
      setGenerating(chatId, false);   // it finished while the page was elsewhere
    }
  } catch (err) { /* not worth interrupting anyone over */ }
}

async function openFirstChat() {
  if (state.chats.length) return selectChat(state.chats[0].id);
  return newChat();
}

let creating = false;

async function newChat() {
  if (creating) return;
  creating = true;
  el.newChat.disabled = el.topNew.disabled = true;
  try {
    const chat = await api("/api/chats", { method: "POST" });
    state.chats.unshift({ id: chat.id, name: null, generating: false });
    await selectChat(chat.id);
  } catch (err) {
    toast("Couldn't start a new chat. " + err.message, { action: { label: "Retry", fn: newChat } });
  } finally {
    creating = false;
    el.newChat.disabled = el.topNew.disabled = false;
  }
}

async function renameChat(chatId) {
  const chat = state.chats.find((c) => c.id === chatId);
  const name = await promptDialog({ title: "Rename chat", label: "Name", value: chatName(chat) });
  if (!name) return;
  try {
    const res = await api(`/api/chats/${chatId}/name`, {
      method: "POST", body: JSON.stringify({ name }),
    });
    if (chat) chat.name = res.name;
    renderChatList();
    updateTitle();
    announce("Chat renamed");
  } catch (err) {
    toast("Couldn't rename the chat. " + err.message);
  }
}

async function deleteChat(chatId) {
  const chat = state.chats.find((c) => c.id === chatId);
  const ok = await confirmDialog({
    title: `Delete “${chatName(chat)}”?`,
    body: "Its messages, uploaded files, generated files and sandbox workspace are deleted for good, and a reply still running in it is stopped. Apps you deployed from it keep running.",
    confirmLabel: "Delete chat",
    danger: true,
  });
  if (!ok) return;
  try {
    await api(`/api/chats/${chatId}`, { method: "DELETE" });
  } catch (err) {
    toast("Couldn't delete the chat. " + err.message);
    return;
  }
  state.chats = state.chats.filter((c) => c.id !== chatId);
  announce("Chat deleted");
  if (state.chatId === chatId) {
    if (state.turn) detachTurn();
    state.chatId = null;
    try { localStorage.removeItem("stellar:lastChat"); } catch (e) { /* private mode */ }
    await openFirstChat();
  } else {
    renderChatList();
  }
}

/* ------------------------------------------------------------------ */
/* replies                                                             */
/* ------------------------------------------------------------------ */

/* The composer: Send always sends - a new message when idle, an addition
 * to the current answer while one runs - and a separate Stop button stops.
 * Esc stops too. One button that changed meaning sent a follow-up when the
 * user meant to stop, whenever there was text in the box. */
function setComposerMode() {
  const running = !!(state.turn && state.turn.chatId === state.chatId);
  el.stop.hidden = !running;
  el.stop.disabled = running && state.turn.stopping;
  el.stop.querySelector(".stop-label").textContent =
    running && state.turn.stopping ? "Stopping…" : "Stop";
  el.composer.classList.toggle("running", running);
  el.input.placeholder = running ? "Add to the current answer…" : "Message Stellar…";
  el.hint.textContent = running
    ? "What you send now is added to the answer in progress. Esc stops it."
    : "";
}

async function stopGeneration() {
  const turn = state.turn;
  if (!turn || turn.stopping) return;
  turn.stopping = true;
  setComposerMode();
  try {
    await api(`/api/stream/${turn.qid}/stop`, { method: "POST" });
    announce("Stopping the reply");
  } catch (err) {
    turn.stopping = false;
    setComposerMode();
    toast("Couldn't stop the reply. " + err.message, { action: { label: "Try again", fn: stopGeneration } });
  }
}

function setBanner(text, { retry = null } = {}) {
  if (!text) { el.banner.hidden = true; el.banner.replaceChildren(); return; }
  el.banner.replaceChildren();
  const span = document.createElement("span");
  span.textContent = text;
  el.banner.appendChild(span);
  if (retry) {
    const b = document.createElement("button");
    b.type = "button";
    b.className = "btn-ghost btn-sm";
    b.textContent = "Retry";
    b.addEventListener("click", retry);
    el.banner.appendChild(b);
  }
  el.banner.hidden = false;
}

/* Watching one reply: an EventSource on its stream, rendering into the
 * open chat. Only one is ever open, for the chat on screen. */
function runTurn(qid, chatId, { rejoin = false } = {}) {
  return new Promise((resolve) => {
    const turn = {
      qid, chatId, stopping: false, resolve, source: null,
      bubble: null, text: "", statusEl: null,
      lastId: -1, lostTimer: null, finished: false,
    };
    state.turn = turn;
    setComposerMode();
    announce(rejoin ? "Rejoined a reply in progress" : "Stellar is replying");
    openSource(turn, 0);
  });
}

function openSource(turn, from) {
  const source = new EventSource(`/api/stream/${turn.qid}?from=${from}`);
  turn.source = source;
  source.onmessage = (e) => {
    if (state.turn !== turn) { source.close(); return; }
    if (e.lastEventId) turn.lastId = Number(e.lastEventId);
    if (turn.lostTimer) { clearTimeout(turn.lostTimer); turn.lostTimer = null; setBanner(null); }
    handleEvent(turn, JSON.parse(e.data));
  };
  /* EventSource retries by itself on a dropped connection, resuming from
   * Last-Event-ID, so an error is usually a blip: say "Reconnecting", and
   * only call the connection lost if it does not come back. */
  source.onerror = () => {
    if (state.turn !== turn || turn.finished) return;
    if (source.readyState === EventSource.CLOSED) { connectionLost(turn); return; }
    setBanner("Reconnecting…");
    if (!turn.lostTimer) turn.lostTimer = setTimeout(() => connectionLost(turn), 20000);
  };
}

function connectionLost(turn) {
  if (turn.source) turn.source.close();
  if (turn.lostTimer) { clearTimeout(turn.lostTimer); turn.lostTimer = null; }
  setBanner("Connection lost. The reply may still be running on the server.", {
    retry: () => {
      setBanner("Reconnecting…");
      openSource(turn, turn.lastId + 1);
    },
  });
}

function ensureStatus(turn) {
  if (!turn.statusEl) {
    turn.statusEl = document.createElement("div");
    turn.statusEl.className = "status";
    turn.statusEl.setAttribute("aria-hidden", "true");
    el.messages.appendChild(turn.statusEl);
  }
  return turn.statusEl;
}

function dropStatus(turn) {
  if (turn.statusEl) { turn.statusEl.remove(); turn.statusEl = null; }
}

/* A finished answer that the page already shows (it was saved before this
   page joined the stream) is not shown twice. */
function settleBubble(turn, id) {
  if (!turn.bubble) return;
  const wrap = turn.bubble.parentElement;
  const twin = id && el.messages.querySelector(`.msg[data-id="${id}"]`);
  if (twin && twin !== wrap) {
    wrap.remove();
  } else {
    if (id) wrap.dataset.id = id;
    renderBubble(turn.bubble, turn.text);
  }
  turn.bubble = null;
  turn.text = "";
}

function handleEvent(turn, ev) {
  switch (ev.type) {
    case "user_message":
      if (!el.messages.querySelector(`.msg[data-id="${ev.id}"]`)) {
        appendMessage({
          id: ev.id,
          message_type: "user",
          message_content: ev.text ?? turn.sentText ?? "",
          attachments: ev.attachments || turn.sentAttachments || [],
        });
        maybeScroll();
      }
      break;

    case "chat_title": {
      const chat = state.chats.find((c) => c.id === ev.chat_id);
      if (chat) { chat.name = ev.name; renderChatList(); updateTitle(); }
      break;
    }

    case "status":
      ensureStatus(turn).textContent = ev.text;
      maybeScroll();
      break;

    case "tool_start":
      // The model wrote this line itself, via the tool's status argument.
      ensureStatus(turn).textContent = ev.status;
      maybeScroll();
      break;

    case "stream_reset":
      // The model finished answering and a follow-up was waiting. That
      // answer is saved as a message of its own (ev.id); the follow-up's
      // answer opens a fresh bubble.
      settleBubble(turn, ev.id);
      break;

    case "interaction":
      dropStatus(turn);
      // A widget ends the current text bubble: what was said before it
      // belongs above it.
      if (turn.bubble) settleBubble(turn, null);
      renderInteraction(ev);
      break;

    case "interaction_closed":
      closeInteraction(ev.id);
      break;

    case "token":
      if (!turn.bubble) {
        dropStatus(turn);
        turn.bubble = appendMessage({ id: "streaming", message_type: "stellar", message_content: "" });
        turn.bubble.parentElement.classList.add("streaming");
      }
      turn.text += ev.text;
      turn.bubble.textContent = turn.text;
      maybeScroll();
      break;

    case "message":
      if (turn.bubble) {
        turn.bubble.parentElement.classList.remove("streaming");
        settleBubble(turn, ev.id);
      }
      finishTurn(turn, { done: true });
      break;

    case "cancelled":
      dropStatus(turn);
      if (turn.bubble) settleBubble(turn, null);
      announce("Reply stopped");
      finishTurn(turn, {});
      break;

    case "error":
      dropStatus(turn);
      if (turn.bubble) settleBubble(turn, null);
      appendError(ev.message || "Something went wrong.");
      finishTurn(turn, {});
      break;

    case "done":
      if (turn.bubble) settleBubble(turn, null);
      finishTurn(turn, { done: true });
      break;
  }
}

function finishTurn(turn, { done = false } = {}) {
  if (turn.finished) return;
  turn.finished = true;
  if (turn.source) turn.source.close();
  if (turn.lostTimer) clearTimeout(turn.lostTimer);
  dropStatus(turn);
  setBanner(null);
  setGenerating(turn.chatId, false);
  if (state.turn === turn) state.turn = null;
  setComposerMode();
  if (done) {
    announce("Reply finished");
    if (document.hidden) { titleMarker = true; updateTitle(); }
  }
  turn.resolve();
}

/* Leave a reply running on the server, without watching it any more. */
function detachTurn() {
  const turn = state.turn;
  if (!turn) return;
  if (turn.source) turn.source.close();
  if (turn.lostTimer) clearTimeout(turn.lostTimer);
  turn.finished = true;
  state.turn = null;
  setBanner(null);
  setComposerMode();
  turn.resolve();
}

async function sendMessage(text, files) {
  if (state.chatId === null) await newChat();
  const chatId = state.chatId;
  let query_id;
  try {
    ({ query_id } = await api(`/api/chats/${chatId}/query`, {
      method: "POST",
      body: JSON.stringify({ message: text, attachment_ids: files.map((f) => f.id) }),
    }));
  } catch (err) {
    // The text stays in the box until a message is actually accepted.
    toast("Your message wasn't sent. " + err.message);
    return false;
  }
  el.input.value = "";
  autosize();
  for (const f of files) forget(f);
  renderTray();
  setGenerating(chatId, true);
  const pending = runTurn(query_id, chatId);
  state.turn.sentText = text;
  state.turn.sentAttachments = files.map(attachmentMeta);
  pending.then(() => { if (!TOUCH && state.chatId === chatId) el.input.focus(); });
  return true;
}

/* A follow-up typed while the answer runs. Cleared only once the server
 * has it; if the answer has just ended (409) it goes as a new message
 * instead, and any other failure leaves the text where it was. */
async function sendFollowUp(text) {
  const chatId = state.chatId;
  try {
    const res = await api(`/api/chats/${chatId}/inject`, {
      method: "POST", body: JSON.stringify({ message: text }),
    });
    el.input.value = "";
    autosize();
    if (state.chatId === chatId) {
      appendMessage({ id: res.id, message_type: "user", message_content: text });
      maybeScroll();
    }
  } catch (err) {
    if (err.status === 409) {
      if (state.turn && state.turn.chatId === chatId) detachTurn();
      await sendMessage(text, []);
      return;
    }
    toast("Couldn't add that to the answer. " + err.message);
  }
}

/* ------------------------------------------------------------------ */
/* attachments                                                         */
/* ------------------------------------------------------------------ */
/* A file is uploaded the moment it is chosen, dropped or pasted, so Send is
 * never waiting on a large upload, and it sits in the tray as a chip that
 * can be removed until the message goes. */

const UPLOAD_LIMIT = 25 * 1024 * 1024;   // must match UPLOAD_MAX_BYTES

const attach = {
  pending: [],
  tray: document.getElementById("attach-tray"),
  button: document.getElementById("attach"),
  input: document.getElementById("file-input"),
  overlay: document.getElementById("drop-overlay"),
};

function fmtBytes(n) {
  if (n >= 1024 * 1024) return `${(n / 1024 / 1024).toFixed(1)} MB`;
  if (n >= 1024) return `${Math.round(n / 1024)} KB`;
  return `${n} B`;
}

function kindOf(file) {
  const t = file.type || "";
  if (t.startsWith("image/")) return "image";
  if (t === "application/pdf") return "pdf";
  if (t.startsWith("audio/")) return "audio";
  if (t.startsWith("video/")) return "video";
  if (t.startsWith("text/")) return "text";
  return "file";
}

function kindFromName(name) {
  const ext = (name.split(".").pop() || "").toLowerCase();
  if (["png", "jpg", "jpeg", "gif", "webp"].includes(ext)) return "image";
  if (ext === "pdf") return "pdf";
  return "file";
}

function kindBadge(kind, name) {
  const b = document.createElement("span");
  b.className = "attach-badge";
  b.setAttribute("aria-hidden", "true");
  const ext = (name.split(".").pop() || "").slice(0, 4);
  b.textContent = { pdf: "PDF", audio: "AUD", video: "VID" }[kind] || ext.toUpperCase() || "FILE";
  return b;
}

function attachmentMeta(p) {
  return { id: p.id, name: p.name, size: p.size, kind: p.kind, url: p.url, mime: p.mime };
}

function renderTray() {
  attach.tray.replaceChildren();
  for (const p of attach.pending) {
    const chip = document.createElement("div");
    chip.className = "attach-chip" + (p.uploading ? " uploading" : "") + (p.error ? " failed" : "");
    if (p.kind === "image" && (p.preview || p.url)) {
      const img = document.createElement("img");
      img.src = p.preview || p.url;
      img.alt = "";
      chip.appendChild(img);
    } else {
      chip.appendChild(kindBadge(p.kind, p.name));
    }
    const txt = document.createElement("div");
    txt.className = "att-text";
    const nm = document.createElement("span");
    nm.className = "att-name";
    nm.textContent = p.name;
    nm.title = p.name;
    const meta = document.createElement("span");
    meta.className = "att-meta";
    meta.textContent = p.error ? p.error : p.uploading ? "uploading…" : fmtBytes(p.size);
    txt.append(nm, meta);
    chip.appendChild(txt);
    const x = document.createElement("button");
    x.type = "button";
    x.className = "att-remove";
    x.setAttribute("aria-label", p.error ? `Dismiss ${p.name}` : `Remove ${p.name}`);
    x.textContent = "×";
    x.addEventListener("click", () => removePending(p));
    chip.appendChild(x);
    attach.tray.appendChild(chip);
  }
  attach.tray.hidden = attach.tray.childElementCount === 0;
  const uploading = attach.pending.some((p) => p.uploading);
  el.send.disabled = uploading;
  el.send.title = uploading ? "Waiting for files to finish uploading" : "";
}

function forget(p) {
  if (p.preview) URL.revokeObjectURL(p.preview);
  attach.pending = attach.pending.filter((q) => q !== p);
}

function deleteOnServer(p) {
  if (!p.id || !p.chatId) return;
  fetch(`/api/chats/${p.chatId}/uploads/${p.id}`, {
    method: "DELETE",
    headers: { Accept: "application/json", "X-CSRF-Token": CSRF_TOKEN },
  }).catch(() => {});
}

function removePending(p) {
  deleteOnServer(p);
  forget(p);
  renderTray();
}

function discardPending() {
  for (const p of [...attach.pending]) {
    deleteOnServer(p);
    forget(p);
  }
  renderTray();
}

async function uploadFiles(fileList) {
  const files = Array.from(fileList || []);
  if (!files.length) return;
  if (state.chatId === null) await newChat();
  const chatId = state.chatId;

  for (const file of files) {
    const p = {
      name: file.name || "pasted-file",
      size: file.size,
      kind: kindOf(file),
      chatId,
      uploading: true,
      preview: (file.type || "").startsWith("image/") ? URL.createObjectURL(file) : null,
    };
    attach.pending.push(p);

    if (file.size > UPLOAD_LIMIT) {
      p.uploading = false;
      p.error = `Over ${UPLOAD_LIMIT / 1024 / 1024} MB, so it can't be attached`;
      renderTray();
      continue;
    }
    renderTray();

    const fd = new FormData();
    fd.append("file", file, p.name);
    try {
      const res = await fetch(`/api/chats/${chatId}/uploads`, {
        method: "POST",
        body: fd,
        headers: { Accept: "application/json", "X-CSRF-Token": CSRF_TOKEN },
      });
      if (res.status === 401) { goToSignIn(); return; }
      const body = await res.json().catch(() => null);
      if (!res.ok) throw new Error((body && body.error) || `Upload failed (${res.status})`);
      Object.assign(p, body[0], { uploading: false });
    } catch (err) {
      p.uploading = false;
      // Stays until dismissed: a message that vanished after five seconds
      // was easy to miss entirely.
      p.error = err.message === "Failed to fetch" ? "Upload failed: no connection" : err.message;
    }

    // The user may have switched chats while this was uploading.
    if (state.chatId !== chatId && attach.pending.includes(p)) {
      deleteOnServer(p);
      forget(p);
    }
    renderTray();
  }
}

function renderAttachments(list) {
  const box = document.createElement("div");
  box.className = "msg-attachments";
  for (const a of list) {
    const link = document.createElement("a");
    link.href = a.url;
    link.target = "_blank";
    link.rel = "noopener noreferrer";
    link.title = a.name;
    if (a.kind === "image") {
      link.className = "att-image";
      const img = document.createElement("img");
      img.src = a.url;
      img.alt = a.name;
      img.loading = "lazy";
      link.appendChild(img);
    } else {
      link.className = "att-file";
      link.appendChild(kindBadge(a.kind, a.name));
      const txt = document.createElement("span");
      txt.className = "att-text";
      const nm = document.createElement("span");
      nm.className = "att-name";
      nm.textContent = a.name;
      const meta = document.createElement("span");
      meta.className = "att-meta";
      meta.textContent = fmtBytes(a.size || 0);
      txt.append(nm, meta);
      link.appendChild(txt);
    }
    box.appendChild(link);
  }
  return box;
}

attach.button.addEventListener("click", () => attach.input.click());
attach.input.addEventListener("change", () => {
  uploadFiles(attach.input.files);
  attach.input.value = "";   // choosing the same file again still fires
});

// Paste a screenshot straight into the message box.
el.input.addEventListener("paste", (e) => {
  const files = Array.from((e.clipboardData && e.clipboardData.files) || []);
  if (files.length) {
    e.preventDefault();
    uploadFiles(files);
  }
});

// Drop files anywhere on the page. dragenter and dragleave fire for every
// child element crossed, so a depth count decides when the overlay goes.
function carriesFiles(e) {
  return !!(e.dataTransfer && Array.from(e.dataTransfer.types || []).includes("Files"));
}
let dragDepth = 0;
window.addEventListener("dragenter", (e) => {
  if (!carriesFiles(e)) return;
  dragDepth += 1;
  attach.overlay.hidden = false;
});
window.addEventListener("dragleave", (e) => {
  if (!carriesFiles(e)) return;
  dragDepth = Math.max(0, dragDepth - 1);
  if (dragDepth === 0) attach.overlay.hidden = true;
});
window.addEventListener("dragover", (e) => {
  if (carriesFiles(e)) e.preventDefault();
});
window.addEventListener("drop", (e) => {
  if (!carriesFiles(e)) return;
  e.preventDefault();
  dragDepth = 0;
  attach.overlay.hidden = true;
  uploadFiles(e.dataTransfer.files);
});

/* ------------------------------------------------------------------ */
/* composer                                                            */
/* ------------------------------------------------------------------ */

/* The box grows with what is typed, up to 200px, and scrolls only past
 * that: a scrollbar always on showed its arrows beside a single line. */
function autosize() {
  el.input.style.height = "auto";
  el.input.style.height = `${Math.min(el.input.scrollHeight, 200)}px`;
  el.input.style.overflowY = el.input.scrollHeight > 200 ? "auto" : "hidden";
}

let submitting = false;

el.composer.addEventListener("submit", async (e) => {
  e.preventDefault();
  if (submitting) return;
  const text = el.input.value.trim();
  const running = !!(state.turn && state.turn.chatId === state.chatId);

  if (running) {
    if (!text) return;
    submitting = true;
    try { await sendFollowUp(text); } finally { submitting = false; }
    return;
  }
  if (attach.pending.some((p) => p.uploading)) {
    toast("Still uploading. Send once the file is ready.", { kind: "info", timeout: 3500 });
    return;
  }
  const files = attach.pending.filter((p) => p.id && !p.error);
  if (!text && !files.length) return;
  submitting = true;
  el.send.disabled = true;
  try { await sendMessage(text, files); } finally {
    submitting = false;
    el.send.disabled = attach.pending.some((p) => p.uploading);
  }
});

el.input.addEventListener("keydown", (e) => {
  if (e.key !== "Enter" || e.shiftKey) return;
  // Not while an input method is composing a character (Chinese, Japanese,
  // Korean): there Enter picks the character, it does not send.
  if (e.isComposing || e.keyCode === 229) return;
  // On a touch keyboard Enter is a new line; the Send button sends.
  if (TOUCH) return;
  e.preventDefault();
  el.composer.requestSubmit();
});

el.input.addEventListener("input", autosize);
el.stop.addEventListener("click", stopGeneration);
el.newChat.addEventListener("click", newChat);
el.topNew.addEventListener("click", newChat);

/* ------------------------------------------------------------------ */
/* widgets                                                             */
/* ------------------------------------------------------------------ */

/* The model writes a widget; it renders here and the reply waits for
 * whatever the user does with it.
 *
 * It runs in a sandboxed iframe: scripts, but an opaque origin, so no
 * access to Stellar's cookies, storage or API. The frame is loaded from
 * /widget-frame, whose own Content-Security-Policy blocks every network
 * request, so a widget cannot send what it shows or what is typed into it
 * anywhere either. It talks to the page through postMessage alone, which
 * is a channel we define. */

const WIDGETS = new Map();   // interaction id -> widget record

function widgetPrefs() {
  try { return JSON.parse(localStorage.getItem("stellar:widgetPrefs") || "{}"); }
  catch (e) { return {}; }
}

function widgetDocument(html) {
  const prefs = JSON.stringify(widgetPrefs()).replace(/</g, "\\u003c");
  return `<!doctype html><html><head><meta charset="utf-8">
<style>
  :root{
    --bg:#14171f; --surface:#1a1e28; --border:#2c3240;
    --text:#e6e8ee; --text-dim:#9aa0b0; --accent:#6d8cff;
    --good:#4fc79f; --bad:#ff6b6b;
    --font:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;
    --mono:"SF Mono","Cascadia Code",Consolas,monospace;
    color-scheme: dark;
  }
  *{box-sizing:border-box}
  html,body{margin:0;padding:0;background:transparent;color:var(--text);
            font-family:var(--font);font-size:15px;line-height:1.55}
  /* Sized to its content by the page, so it must never scroll inside: a
     scrollbar would steal width on the first paint and reflow the layout. */
  html{overflow:hidden}
  body{padding:2px}
  button{font-family:inherit;cursor:pointer}
  a{color:var(--accent)}
  :focus-visible{outline:2px solid var(--accent);outline-offset:2px}
  html.closed body{opacity:.75}
  @media (prefers-reduced-motion: reduce){*{animation:none!important;transition:none!important}}
</style><script>window.stellarPrefs=${prefs};<\/script></head><body>
<div id="stellar-widget-root">${html}</div>
<script>
(function(){
  var done = false;
  function post(m){ try { parent.postMessage(m, "*"); } catch (e) {} }
  window.stellar = {
    finish: function(data){
      if (done) return;            // one answer per widget
      done = true;
      post({__stellar:"finish", data: data || {}});
    },
    pref: function(key, value){ post({__stellar:"pref", key: key, value: value}); },
    intent: function(name){ post({__stellar:"intent", name: name}); }
  };
  var root = document.getElementById("stellar-widget-root");
  function report(){
    var h = Math.max(root ? root.scrollHeight : 0,
                     root ? Math.ceil(root.getBoundingClientRect().height) : 0) + 4;
    if (h > 4) post({__stellar:"height", height: h});
  }
  window.addEventListener("message", function(e){
    var m = e.data;
    if (e.source !== parent || !m || typeof m !== "object") return;
    if (m.__stellar === "update") {
      done = false;
      try { window.dispatchEvent(new CustomEvent("stellar:update", {detail: m.data})); } catch (err) {}
      setTimeout(report, 30); setTimeout(report, 300);
    } else if (m.__stellar === "render" && typeof m.doc === "string") {
      document.open(); document.write(m.doc); document.close();
    } else if (m.__stellar === "rearm") {
      done = false;
      try { window.dispatchEvent(new CustomEvent("stellar:rearm")); } catch (err) {}
    } else if (m.__stellar === "closed") {
      var ev = new CustomEvent("stellar:closed", {cancelable: true});
      window.dispatchEvent(ev);
      if (!ev.defaultPrevented) {
        done = true;
        document.documentElement.classList.add("closed");
        document.documentElement.inert = true;
      }
    }
  });
  report();
  if (root) new ResizeObserver(report).observe(root);
  window.addEventListener("load", report);
  setTimeout(report, 60); setTimeout(report, 400); setTimeout(report, 1200);
})();
<\/script></body></html>`;
}

function widgetPost(w, message) {
  try { w.frame.contentWindow.postMessage(message, "*"); } catch (e) { /* gone */ }
}

/* Writes the latest document into the frame once it can take one. While
   the frame is loading, only the newest state is kept: a reload mid-game
   replays every move in a burst, and the board must show the last one, not
   the first (CH-2). */
function widgetFlush(w) {
  if (!w.ready) return;
  if (w.pendingDoc) {
    widgetPost(w, { __stellar: "render", doc: w.pendingDoc });
    w.pendingDoc = null;
  }
  if (w.closed) widgetPost(w, { __stellar: "closed" });
}

function renderInteraction(ev) {
  clearEmptyState();
  const prior = ev.replaces && WIDGETS.get(ev.replaces);
  if (prior) {
    // One frame that changes, not a stack of stale boards: a turn-based
    // widget calls this once per turn.
    WIDGETS.delete(ev.replaces);
    WIDGETS.set(ev.id, prior);
    prior.id = ev.id;
    prior.closed = false;
    prior.wrap.dataset.interaction = ev.id;
    prior.wrap.classList.remove("settled");
    prior.caption.textContent = "";
    prior.frame.classList.remove("awaiting");
    if (prior.ready && ev.update && !prior.pendingDoc) {
      widgetPost(prior, { __stellar: "update", data: ev.update });
    } else {
      prior.pendingDoc = widgetDocument(ev.html);
      widgetFlush(prior);
    }
    maybeScroll();
    return;
  }

  const wrap = document.createElement("div");
  wrap.className = "msg stellar interaction";
  wrap.dataset.interaction = ev.id;

  // Drawn by the page, outside the frame, so a widget cannot pass itself
  // off as part of Stellar's own interface.
  const label = document.createElement("div");
  label.className = "widget-label";
  label.textContent = "Interactive widget from Stellar";
  const frame = document.createElement("iframe");
  frame.className = "widget-frame";
  // allow-scripts WITHOUT allow-same-origin: the widget runs its own code
  // but is a foreign origin to us. Adding allow-same-origin here would
  // undo the whole protection.
  frame.setAttribute("sandbox", "allow-scripts");
  frame.setAttribute("title", ev.goal ? `Widget: ${ev.goal}` : "Interactive widget");
  frame.src = "/widget-frame";
  frame.style.height = "320px";
  const caption = document.createElement("div");
  caption.className = "widget-caption";
  caption.setAttribute("aria-live", "polite");

  wrap.append(label, frame, caption);
  el.messages.appendChild(wrap);
  maybeScroll();

  const w = {
    id: ev.id, frame, wrap, caption, ready: false, closed: false,
    kind: ev.goal === "chess" ? "chess" : "widget",
    pendingDoc: widgetDocument(ev.html), intentUsed: false,
  };
  WIDGETS.set(ev.id, w);
  // Some embedded browser views refuse sandboxed frames outright. Say so,
  // rather than leave an empty box.
  setTimeout(() => {
    if (!w.ready) {
      w.caption.textContent = "This widget couldn't load in this browser. "
        + "Open Stellar in Chrome, Edge, Firefox or Safari to use it.";
    }
  }, 6000);
}

function closeInteraction(id) {
  const w = WIDGETS.get(id);
  if (!w) return;
  w.closed = true;
  w.wrap.classList.add("settled");
  w.frame.classList.remove("awaiting");
  w.caption.textContent = w.kind === "chess" ? "This board is closed." : "Closed: this widget no longer takes input.";
  widgetFlush(w);
}

function widgetFor(source) {
  for (const w of WIDGETS.values()) {
    if (w.frame.contentWindow === source) return w;
  }
  return null;
}

/* One listener for every widget, matching the source frame to its record.
 * Widgets are foreign-origin, so event.source identity is the only thing
 * that can be trusted here - never the contents of the message. */
window.addEventListener("message", async (e) => {
  const msg = e.data;
  if (!msg || typeof msg !== "object" || !msg.__stellar) return;
  const w = widgetFor(e.source);
  if (!w) return;

  switch (msg.__stellar) {
    case "frame-ready":
      w.ready = true;
      widgetFlush(w);
      break;

    case "height": {
      const h = Math.min(Math.max(Number(msg.height) || 320, 80), 2400);
      w.frame.style.height = h + "px";
      break;
    }

    case "finish":
      if (w.closed) return;
      w.frame.classList.add("awaiting");
      w.caption.textContent = "Sent.";
      try {
        await api(`/api/interaction/${w.id}/finish`, {
          method: "POST",
          body: JSON.stringify(msg.data ?? {}),
        });
      } catch (err) {
        // Re-armed, so the same answer can be given again.
        w.frame.classList.remove("awaiting");
        w.caption.textContent = "Couldn't send your answer: " + err.message;
        widgetPost(w, { __stellar: "rearm" });
      }
      break;

    case "pref": {
      // Remembered for widgets here, because a sandboxed frame has no
      // storage of its own. Known keys only.
      if (!["chessMuted"].includes(msg.key)) return;
      const prefs = widgetPrefs();
      prefs[msg.key] = !!msg.value;
      try { localStorage.setItem("stellar:widgetPrefs", JSON.stringify(prefs)); } catch (err) { /* private mode */ }
      break;
    }

    case "intent":
      // A closed chess board's "Play again": the game's tool has returned,
      // so the rematch is asked for as an ordinary message. Only this one
      // fixed message, only once, and only from a closed chess board.
      if (msg.name === "chess:rematch" && w.kind === "chess" && w.closed && !w.intentUsed
          && !(state.turn && state.turn.chatId === state.chatId)) {
        w.intentUsed = true;
        el.input.value = "Let's play another game of chess.";
        el.composer.requestSubmit();
      }
      break;
  }
});

/* ------------------------------------------------------------------ */
/* the sandbox terminal (xterm.js over SSE)                            */
/* ------------------------------------------------------------------ */

const termState = {
  term: null,
  fitAddon: null,
  eventSource: null,
  open: false,
  connectedChatId: null,
};

const termEl = {
  drawer: document.getElementById("terminal-drawer"),
  screen: document.getElementById("terminal-screen"),
  status: document.getElementById("terminal-status"),
  toggleBtn: document.getElementById("btn-terminal-toggle"),
  closeBtn: document.getElementById("terminal-close"),
  clearBtn: document.getElementById("terminal-clear"),
  restartBtn: document.getElementById("terminal-restart"),
};

function termStatus(text, kind = "") {
  termEl.status.textContent = text;
  termEl.status.className = "terminal-status-badge" + (kind ? " " + kind : "");
}

function initTerminal() {
  if (termState.term || typeof Terminal === "undefined") return;

  termState.term = new Terminal({
    cursorBlink: true,
    fontFamily: 'Consolas, "Cascadia Code", "Courier New", monospace',
    fontSize: 13,
    lineHeight: 1.25,
    screenReaderMode: true,
    theme: {
      background: "#090d13", foreground: "#c9d1d9", cursor: "#58a6ff",
      selectionBackground: "#264f78",
      black: "#090d13", red: "#f85149", green: "#3fb950", yellow: "#d29922",
      blue: "#58a6ff", magenta: "#bc8cff", cyan: "#39c5cf", white: "#b1bac4",
      brightBlack: "#6e7681", brightRed: "#ff7b72", brightGreen: "#56d364",
      brightYellow: "#e3b341", brightBlue: "#79c0ff", brightMagenta: "#d2a8ff",
      brightCyan: "#56d4dd", brightWhite: "#f0f6fc",
    },
  });

  if (typeof FitAddon !== "undefined" && FitAddon.FitAddon) {
    termState.fitAddon = new FitAddon.FitAddon();
    termState.term.loadAddon(termState.fitAddon);
  }
  termState.term.open(termEl.screen);

  /* The terminal takes Tab (shells complete with it) and Escape (editors
     need it), so a keyboard user needs another way out: F6 returns to the
     message box. */
  termState.term.attachCustomKeyEventHandler((e) => {
    if (e.key === "F6" && e.type === "keydown") { e.preventDefault(); el.input.focus(); return false; }
    return true;
  });

  termState.term.onData((data) => {
    const chatId = termState.connectedChatId;
    if (!chatId || !termState.open) return;
    fetch("/api/terminal/input", {
      method: "POST",
      headers: jsonHeaders(),
      body: JSON.stringify({ chat_id: chatId, data }),
    }).then((r) => {
      if (r.status === 409) termStatus("Disconnected: reconnect to type", "disconnected");
    }).catch(() => termStatus("Couldn't send input", "disconnected"));
  });

  // The drawer can be resized by its handle; the terminal follows.
  new ResizeObserver(() => {
    if (termState.open && termState.fitAddon) {
      termState.fitAddon.fit();
      notifyTerminalResize();
    }
  }).observe(termEl.drawer);
}

let resizeTimer = null;
function notifyTerminalResize() {
  clearTimeout(resizeTimer);
  resizeTimer = setTimeout(() => {
    const chatId = termState.connectedChatId;
    if (!chatId || !termState.term) return;
    fetch("/api/terminal/resize", {
      method: "POST",
      headers: jsonHeaders(),
      body: JSON.stringify({ chat_id: chatId, cols: termState.term.cols, rows: termState.term.rows }),
    }).catch(() => {});
  }, 150);
}

async function connectTerminal(chatId) {
  if (!chatId) { termStatus("Open a chat first", "disconnected"); return; }
  initTerminal();

  /* Ask for the shell first. The stream below only attaches to a shell
     that was asked for this way, so a link from another site cannot start
     one. */
  termStatus("Connecting…");
  try {
    const res = await fetch("/api/terminal/open", {
      method: "POST",
      headers: jsonHeaders(),
      body: JSON.stringify({ chat_id: chatId }),
    });
    if (res.status === 401) { goToSignIn(); return; }
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
  } catch (err) {
    termStatus("Couldn't open the terminal", "disconnected");
    return;
  }

  if (termState.eventSource) {
    termState.eventSource.close();
    termState.eventSource = null;
  }
  termState.connectedChatId = chatId;

  const es = new EventSource(`/api/terminal/stream?chat_id=${chatId}`);
  termState.eventSource = es;

  es.addEventListener("ready", () => {
    termStatus("Connected", "connected");
    if (termState.fitAddon) { termState.fitAddon.fit(); notifyTerminalResize(); }
    if (termState.open && termState.term) termState.term.focus();
  });

  es.addEventListener("output", (e) => {
    try {
      const payload = JSON.parse(e.data);
      if (payload.b64 && termState.term) {
        // Bytes, not a string: atob() yields one character per BYTE, and a
        // Uint8Array lets xterm decode UTF-8 itself, even split across
        // chunks.
        const bin = atob(payload.b64);
        const bytes = new Uint8Array(bin.length);
        for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
        termState.term.write(bytes);
      } else if (payload.closed) {
        if (payload.reason === "idle" && termState.term) {
          termState.term.write("\r\n[Closed after 30 minutes without input. Restart to open a new shell.]\r\n");
        }
        termStatus("Session closed", "disconnected");
      }
    } catch (err) {
      termStatus("Couldn't read terminal output", "disconnected");
    }
  });

  /* The server ends the stream on purpose: the terminal was closed, never
     opened, or over the limit. Close our side too, or EventSource would
     reconnect every few seconds forever. */
  es.addEventListener("closed", (e) => {
    es.close();
    if (termState.eventSource === es) termState.eventSource = null;
    let message = "";
    try { message = JSON.parse(e.data || "{}").message || ""; } catch (err) { /* a plain close */ }
    if (message && termState.term) termState.term.write(`\r\n[${message}]\r\n`);
    termStatus(message ? "Not available" : "Closed", "disconnected");
  });

  es.onerror = () => {
    if (es.readyState === EventSource.CLOSED) termStatus("Disconnected", "disconnected");
    else termStatus("Reconnecting…");
  };
}

function openTerminal() {
  termEl.drawer.hidden = false;
  termState.open = true;
  termEl.toggleBtn.setAttribute("aria-expanded", "true");
  closeDrawer({ restoreFocus: false });
  if (!state.chatId) { termStatus("Open a chat first", "disconnected"); return; }
  if (termState.connectedChatId !== state.chatId || !termState.eventSource) {
    connectTerminal(state.chatId);
  } else {
    if (termState.fitAddon) termState.fitAddon.fit();
    if (termState.term) termState.term.focus();
  }
}

function closeTerminal() {
  termEl.drawer.hidden = true;
  termState.open = false;
  termEl.toggleBtn.setAttribute("aria-expanded", "false");
  termEl.toggleBtn.focus();
}

termEl.toggleBtn.addEventListener("click", () => (termState.open ? closeTerminal() : openTerminal()));
termEl.closeBtn.addEventListener("click", closeTerminal);
termEl.clearBtn.addEventListener("click", () => { if (termState.term) termState.term.clear(); });
termEl.restartBtn.addEventListener("click", async () => {
  const chatId = state.chatId;
  if (!chatId) return;
  await fetch("/api/terminal/close", {
    method: "POST",
    headers: jsonHeaders(),
    body: JSON.stringify({ chat_id: chatId }),
  }).catch(() => {});
  if (termState.term) termState.term.reset();
  connectTerminal(chatId);
});

/* ------------------------------------------------------------------ */
/* settings                                                            */
/* ------------------------------------------------------------------ */

const settingsEl = {
  name: document.getElementById("set-name"),
  tz: document.getElementById("set-tz"),
  model: document.getElementById("set-model"),
  memory: document.getElementById("set-memory"),
  tasks: document.getElementById("set-tasks"),
  status: document.getElementById("set-status"),
};

function settingsSay(text, kind = "") {
  settingsEl.status.textContent = text;
  settingsEl.status.className = "settings-status" + (kind ? " " + kind : "");
}

function zoneList(current) {
  let zones = [];
  try { zones = Intl.supportedValuesOf("timeZone"); } catch (e) { zones = []; }
  if (current && !zones.includes(current)) zones.unshift(current);
  if (!zones.length) zones = [current || "UTC"];
  return zones;
}

function listRow(text, sub, actionLabel, onAction) {
  const li = document.createElement("li");
  const body = document.createElement("div");
  body.className = "settings-row-text";
  const main = document.createElement("span");
  main.textContent = text;
  body.appendChild(main);
  if (sub) {
    const s = document.createElement("span");
    s.className = "settings-row-sub";
    s.textContent = sub;
    body.appendChild(s);
  }
  const b = document.createElement("button");
  b.type = "button";
  b.className = "btn-ghost btn-sm";
  b.textContent = actionLabel;
  b.addEventListener("click", () => onAction(li, b));
  li.append(body, b);
  return li;
}

function emptyRow(text) {
  const li = document.createElement("li");
  li.className = "settings-empty";
  li.textContent = text;
  return li;
}

async function loadMemory() {
  const notes = await api("/api/me/memory");
  settingsEl.memory.replaceChildren();
  if (!notes.length) { settingsEl.memory.appendChild(emptyRow("Nothing saved yet.")); return; }
  for (const n of notes) {
    settingsEl.memory.appendChild(listRow(n.note, null, "Forget", async (li, b) => {
      b.disabled = true;
      try {
        await api(`/api/me/memory/${n.id}`, { method: "DELETE" });
        li.remove();
        if (!settingsEl.memory.children.length) settingsEl.memory.appendChild(emptyRow("Nothing saved yet."));
        announce("Forgotten");
      } catch (err) { b.disabled = false; settingsSay("Couldn't forget that. " + err.message, "error"); }
    }));
  }
}

async function loadTasks() {
  const tasks = await api("/api/me/tasks");
  settingsEl.tasks.replaceChildren();
  if (!tasks.length) { settingsEl.tasks.appendChild(emptyRow("No scheduled tasks.")); return; }
  for (const t of tasks) {
    const when = `${t.next_run}` + (t.every_minutes ? `, every ${t.every_minutes} min` : ", once")
      + (t.status === "running" ? " (running now)" : "");
    settingsEl.tasks.appendChild(listRow(t.prompt, when, "Cancel", async (li, b) => {
      b.disabled = true;
      try {
        await api(`/api/me/tasks/${t.id}`, { method: "DELETE" });
        li.remove();
        if (!settingsEl.tasks.children.length) settingsEl.tasks.appendChild(emptyRow("No scheduled tasks."));
        announce("Task cancelled");
      } catch (err) { b.disabled = false; settingsSay("Couldn't cancel it. " + err.message, "error"); }
    }));
  }
}

async function openSettings() {
  closeDrawer({ restoreFocus: false });
  settingsSay("");
  el.settings.showModal();
  settingsEl.memory.replaceChildren(emptyRow("Loading…"));
  settingsEl.tasks.replaceChildren(emptyRow("Loading…"));
  try {
    const me = await api("/api/me?models=1");
    state.me = me;
    settingsEl.name.value = me.name || "";
    settingsEl.tz.replaceChildren(...zoneList(me.timezone).map((z) => new Option(z, z, false, z === me.timezone)));
    settingsEl.model.replaceChildren(
      new Option(`Default (${me.default_model})`, ""),
      ...(me.models || []).filter((m) => m !== me.default_model)
        .map((m) => new Option(m, m, false, m === me.preferred_model)),
    );
    if (!me.preferred_model) settingsEl.model.value = "";
    document.getElementById("set-model-help").textContent =
      (me.models || []).some((m) => /lite/.test(m))
        ? "Flash-Lite answers faster and has a larger daily allowance, with some loss in quality."
        : "The models these API keys can use.";
  } catch (err) {
    settingsSay("Couldn't load your settings. " + err.message, "error");
  }
  loadMemory().catch((err) => settingsEl.memory.replaceChildren(emptyRow("Couldn't load: " + err.message)));
  loadTasks().catch((err) => settingsEl.tasks.replaceChildren(emptyRow("Couldn't load: " + err.message)));
}

document.getElementById("settings-form").addEventListener("submit", async (e) => {
  if (e.submitter && e.submitter.value === "close") return;   // the dialog closes itself
  e.preventDefault();
  const save = document.getElementById("set-save");
  save.disabled = true;
  settingsSay("Saving…");
  try {
    await api("/api/me/preferences", {
      method: "POST",
      body: JSON.stringify({
        display_name: settingsEl.name.value,
        timezone: settingsEl.tz.value,
        preferred_model: settingsEl.model.value,
      }),
    });
    settingsSay("Saved.", "ok");
    const shown = settingsEl.name.value.trim() || (state.me && state.me.email) || "";
    el.userBtn.querySelector(".user-name").textContent = shown;
  } catch (err) {
    settingsSay("Couldn't save. " + err.message, "error");
  } finally {
    save.disabled = false;
  }
});

/* ------------------------------------------------------------------ */
/* the account menu                                                    */
/* ------------------------------------------------------------------ */

el.userBtn.addEventListener("click", (e) => { e.stopPropagation(); showMenu(el.userMenu, el.userBtn); });
document.getElementById("menu-settings").addEventListener("click", () => { closeMenu(); openSettings(); });

/* ------------------------------------------------------------------ */
/* boot                                                                */
/* ------------------------------------------------------------------ */

/* Tell the server this browser's time zone, so "every day at 9" means 9
   here rather than 9 UTC. Quiet on failure: scheduling still works, with
   times read as UTC. */
async function syncTimezone() {
  try {
    const tz = Intl.DateTimeFormat().resolvedOptions().timeZone;
    const me = await api("/api/me");
    state.me = me;
    if (tz && me && !me.timezone) {
      await api("/api/me/preferences", { method: "POST", body: JSON.stringify({ timezone: tz }) });
    }
  } catch (err) { /* not worth interrupting anyone over */ }
}

/* Replies that start without this page: a scheduled task firing, or a
   message sent from another tab. Every 15 seconds, while the tab is in
   view, the chat list is refreshed (its "replying" dots), and a reply
   running in the open chat is joined live, just as on a reload. */
async function appendNewMessages(chatId) {
  const messages = await api(`/api/chats/${chatId}/messages`);
  if (state.chatId !== chatId || state.turn) return;
  let added = 0;
  for (const m of messages) {
    if (!el.messages.querySelector(`.msg[data-id="${m.id}"]`)) {
      appendMessage(m, { markdown: true });
      added += 1;
    }
  }
  if (added) {
    maybeScroll();
    announce(added === 1 ? "A new message arrived in this chat" : `${added} new messages arrived in this chat`);
  }
}

async function watchForReplies() {
  if (document.hidden || state.chatId === null) return;
  try {
    const chats = await api("/api/chats");
    const before = state.chats.find((c) => c.id === state.chatId);
    let changed = chats.length !== state.chats.length;
    for (const fresh of chats) {
      const known = state.chats.find((c) => c.id === fresh.id);
      if (!known || known.generating !== !!fresh.generating || known.name !== fresh.name
          || known.updated_at !== fresh.updated_at) changed = true;
    }
    if (changed) {
      state.chats = chats;
      renderChatList();
      updateTitle();
    }
    const open = chats.find((c) => c.id === state.chatId);
    if (open && open.generating && !state.turn) {
      const chatId = state.chatId;
      const { query_id } = await api(`/api/chats/${chatId}/active`);
      if (query_id && !state.turn && state.chatId === chatId) {
        runTurn(query_id, chatId, { rejoin: true });
      }
    } else if (open && before && open.updated_at !== before.updated_at && !state.turn) {
      // A reply that started and finished between two checks - a short
      // scheduled reminder takes seconds - is shown from the history.
      await appendNewMessages(state.chatId);
    }
  } catch (err) { /* the next check will try again */ }
}
setInterval(watchForReplies, 15000);
document.addEventListener("visibilitychange", () => { if (!document.hidden) watchForReplies(); });

(async function init() {
  syncTimezone();
  try {
    await loadChats();
  } catch (err) {
    el.messages.replaceChildren();
    appendError("Couldn't load your chats. " + err.message, { retry: () => location.reload() });
    return;
  }
  let remembered = null;
  try { remembered = Number(localStorage.getItem("stellar:lastChat")); } catch (e) { /* private mode */ }
  if (remembered && state.chats.some((c) => c.id === remembered)) await selectChat(remembered);
  else await openFirstChat();
})();

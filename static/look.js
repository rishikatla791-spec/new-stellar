/* The pieces of Stellar's look that need a script (main.js stays as it is):

   - Home: the orbit with its star, a greeting for the time of day, and four
     starter cards. Built into main.js's empty chat when it appears.
   - The star in the composer opens the model menu: search, Auto, and the
     models this server's keys can use, saved as the same preferred_model as
     Settings.
   - The avatar's initials, the Settings link in the sidebar, and the
     "System online" card, which follows /healthz. */
(function () {
  "use strict";

  const CSRF = (document.querySelector('meta[name="csrf-token"]') || {}).content || "";
  const SVG = "http://www.w3.org/2000/svg";

  async function request(path, options = {}) {
    const res = await fetch(path, {
      credentials: "same-origin",
      ...options,
      headers: { Accept: "application/json", "Content-Type": "application/json", "X-CSRF-Token": CSRF },
    });
    const body = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(body.error || `HTTP ${res.status}`);
    return body;
  }

  /* "gemini-3-flash-preview" -> ["Gemini 3 Flash", "Preview"] */
  function modelName(id) {
    let rest = String(id || "").replace(/^models\//, "");
    let tag = "";
    rest = rest.replace(/-(preview|exp|experimental|latest)(-[\w.-]+)?$/i, (m, t) => {
      tag = t[0].toUpperCase() + t.slice(1).toLowerCase();
      return "";
    });
    const words = rest.split("-").filter(Boolean)
      .map((w) => (/^\d/.test(w) ? w : w[0].toUpperCase() + w.slice(1)));
    return [words.join(" ") || id, tag];
  }

  function el(tag, cls, text) {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    return n;
  }

  /* --- the model menu, opened by the composer's star -------------------- */
  const pill = document.getElementById("model-pill");
  const pillName = document.getElementById("model-pill-name");
  const menu = document.getElementById("model-menu");
  const search = document.getElementById("model-search");
  const autoBtn = document.getElementById("model-auto");
  const autoNote = document.getElementById("model-auto-note");
  const list = document.getElementById("model-list");
  const models = { list: null, fallback: "", chosen: "" };

  function showChoice() {
    if (!pill) return;
    const name = models.chosen ? modelName(models.chosen)[0] : "Auto";
    pillName.textContent = name;
    pill.title = `Model: ${name}`;
    pill.classList.toggle("is-auto", !models.chosen);
  }

  function note(text) {
    list.replaceChildren(el("li", "model-empty", text));
  }

  function renderList() {
    const q = search.value.trim().toLowerCase();
    autoBtn.setAttribute("aria-checked", String(!models.chosen));
    autoNote.textContent = models.fallback ? modelName(models.fallback)[0] : "";
    if (!models.list) return note("Loading models…");
    const shown = models.list.filter((m) => !q || m.toLowerCase().includes(q)
      || modelName(m).join(" ").toLowerCase().includes(q));
    if (!shown.length) return note("No model matches.");
    list.replaceChildren(...shown.map((m) => {
      const [name, tag] = modelName(m);
      const b = el("button", "model-option");
      b.type = "button";
      b.setAttribute("role", "option");
      b.setAttribute("aria-selected", String(m === models.chosen));
      b.dataset.model = m;
      b.innerHTML = '<svg class="model-spark" viewBox="0 0 24 24" width="15" height="15" aria-hidden="true">'
        + '<path d="M12 2l2.2 7.8L22 12l-7.8 2.2L12 22l-2.2-7.8L2 12l7.8-2.2z" fill="currentColor"/></svg>';
      const label = el("span", "model-option-name", name);
      if (tag) label.appendChild(el("span", "model-tag", " " + tag));
      b.append(label);
      b.insertAdjacentHTML("beforeend", '<svg class="model-check" viewBox="0 0 24 24" width="15" height="15" aria-hidden="true">'
        + '<path d="M5 12.5l4.5 4.5L19 7.5" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"/></svg>');
      const li = el("li");
      li.appendChild(b);
      return li;
    }));
  }

  async function loadModels() {
    if (models.list) return;
    try {
      const me = await request("/api/me?models=1");
      models.list = me.models || [];
      models.fallback = me.default_model || "";
      models.chosen = me.preferred_model || "";
    } catch (err) {
      models.list = [];
      note("Couldn't load models: " + err.message);
      return;
    }
    showChoice();
    if (!menu.hidden) renderList();
  }

  async function choose(model) {
    const before = models.chosen;
    models.chosen = model;
    showChoice();
    renderList();
    try {
      await request("/api/me/preferences", { method: "POST", body: JSON.stringify({ preferred_model: model }) });
      close();
    } catch (err) {
      models.chosen = before;
      showChoice();
      renderList();
      list.prepend(el("li", "model-empty", "Couldn't change the model: " + err.message));
    }
  }

  function open() {
    menu.hidden = false;
    pill.setAttribute("aria-expanded", "true");
    search.value = "";
    renderList();
    loadModels();
    search.focus();
  }

  function close() {
    if (menu.hidden) return;
    menu.hidden = true;
    pill.setAttribute("aria-expanded", "false");
  }

  if (pill && menu) {
    pill.addEventListener("click", () => (menu.hidden ? open() : close()));
    search.addEventListener("input", renderList);
    search.addEventListener("keydown", (e) => {
      const first = list.querySelector(".model-option");
      if (e.key === "Enter") { e.preventDefault(); if (first) choose(first.dataset.model); }
      if (e.key === "ArrowDown" && first) { e.preventDefault(); first.focus(); }
    });
    list.addEventListener("click", (e) => {
      const b = e.target.closest(".model-option");
      if (b) choose(b.dataset.model);
    });
    list.addEventListener("keydown", (e) => {
      const opts = [...list.querySelectorAll(".model-option")];
      const i = opts.indexOf(document.activeElement);
      if (e.key === "ArrowDown" && i < opts.length - 1) { e.preventDefault(); opts[i + 1].focus(); }
      if (e.key === "ArrowUp") { e.preventDefault(); (i > 0 ? opts[i - 1] : search).focus(); }
    });
    autoBtn.addEventListener("click", () => {
      // On: the server's default. Off: that same model, chosen by name.
      choose(models.chosen ? "" : (models.fallback || (models.list || [])[0] || ""));
    });
    menu.addEventListener("keydown", (e) => {
      if (e.key === "Escape") { e.preventDefault(); close(); pill.focus(); }
    });
    document.addEventListener("click", (e) => {
      if (!menu.hidden && !(e.target instanceof Element && e.target.closest(".model-wrap"))) close();
    });
  }

  /* --- the account ------------------------------------------------------- */
  const avatar = document.getElementById("avatar");
  const userName = document.querySelector("#user-btn .user-name");
  function initials() {
    if (!avatar || !userName) return;
    const words = userName.textContent.trim().replace(/@.*/, "").split(/[\s._-]+/).filter(Boolean);
    avatar.textContent = ((words[0] || "?")[0] + (words.length > 1 ? words[words.length - 1][0] : "")).toUpperCase();
  }
  if (userName) {
    initials();
    // Settings can rename the account; main.js rewrites this span when it does.
    new MutationObserver(initials).observe(userName, { childList: true, characterData: true, subtree: true });
  }

  request("/api/me").then((me) => { models.chosen = me.preferred_model || ""; showChoice(); }).catch(() => {});

  const navSettings = document.getElementById("nav-settings");
  if (navSettings) {
    navSettings.addEventListener("click", () => {
      const item = document.getElementById("menu-settings");
      if (item) item.click();
    });
  }

  /* --- "System online" --------------------------------------------------- */
  const card = document.getElementById("system-card");
  const stateText = document.getElementById("system-state");
  async function health() {
    let ok = false;
    try {
      const res = await fetch("/healthz", { cache: "no-store" });
      ok = res.ok;
    } catch (e) { ok = false; }
    if (!card) return;
    card.classList.toggle("is-down", !ok);
    stateText.textContent = ok ? "System Online" : "Can't reach Stellar";
  }
  if (card) {
    health();
    setInterval(() => { if (!document.hidden) health(); }, 60000);
  }

  /* --- Home ---------------------------------------------------------------- */
  const CARDS = [
    { key: "build", title: "Build a website", sub: "Turn your ideas into live websites",
      text: "Build me a website for ", icon: '<path d="M8.5 7.5L4 12l4.5 4.5M15.5 7.5L20 12l-4.5 4.5M13.5 5l-3 14"/>' },
    { key: "code", title: "Run code", sub: "Execute and debug instantly",
      text: "Write and run a Python script that ", icon: '<path d="M4 4.5h16v15H4zM8 9l3 3-3 3"/>' },
    { key: "image", title: "Create images", sub: "Bring your ideas to life",
      text: "Make an image of ", icon: '<rect x="3.5" y="4.5" width="17" height="15" rx="1.5"/><circle cx="9" cy="9.5" r="1.6"/><path d="M4 17l5-5 4 4 2.5-2.5L20 18"/>' },
    { key: "deploy", title: "Deploy", sub: "Publish your projects",
      text: "Deploy the site we built so I can share it", icon: '<path d="M7 18a4.5 4.5 0 0 1-.6-8.96A6 6 0 0 1 18 9.5a4.25 4.25 0 0 1-.25 8.5z"/><path d="M12 13.5v.01"/>' },
  ];
  const ARROW = '<svg viewBox="0 0 24 24" width="17" height="17" aria-hidden="true"><path d="M5 12h14M13.5 6.5L19 12l-5.5 5.5"/></svg>';

  function greeting() {
    const h = new Date().getHours();
    return h < 5 ? "Good night" : h < 12 ? "Good morning" : h < 17 ? "Good afternoon" : "Good evening";
  }

  function prefill(text) {
    const input = document.getElementById("input");
    if (!input) return;
    input.value = text;
    input.dispatchEvent(new Event("input", { bubbles: true }));
    input.focus();
    input.setSelectionRange(input.value.length, input.value.length);
  }

  function decorate(empty) {
    if (empty.dataset.looked) return;
    empty.dataset.looked = "1";
    const stage = el("div", "home-stage");
    stage.appendChild(el("div", "home-art"));
    stage.appendChild(el("p", "home-eyebrow", greeting()));
    const h = el("h1", "home-title", "How can I help ");
    h.appendChild(el("span", "home-you", "you "));
    h.appendChild(el("span", "home-today", "today?"));
    stage.appendChild(h);
    stage.appendChild(el("p", "home-sub", "Build, code, create, and explore — all in one place."));
    const grid = el("div", "home-cards");
    CARDS.forEach((c) => {
      const b = el("button", `home-card card-${c.key}`);
      b.type = "button";
      b.innerHTML = `<svg class="home-card-icon" viewBox="0 0 24 24" aria-hidden="true">${c.icon}</svg>`;
      b.appendChild(el("span", "home-card-title", c.title));
      const sub = el("span", "home-card-sub", c.sub);
      sub.insertAdjacentHTML("beforeend", ARROW);
      b.appendChild(sub);
      b.addEventListener("click", () => prefill(c.text));
      grid.appendChild(b);
    });
    stage.appendChild(grid);
    empty.replaceChildren(stage);
    fit();
  }

  /* The stage is drawn at the design's own size (1352x765, the area above
     the composer at 1672x940) and scaled down when the window is smaller. */
  function fit() {
    const box = document.getElementById("messages");
    if (!box || window.matchMedia("(max-width: 767px)").matches) return;
    const s = Math.min(1, box.clientWidth / 1120, box.clientHeight / 765);
    box.style.setProperty("--home-scale", s.toFixed(4));
  }
  window.addEventListener("resize", fit);

  /* Home is lit while the home screen shows and no panel is open. */
  const homeLink = document.getElementById("new-chat");
  function markHome() {
    if (homeLink) homeLink.classList.toggle("is-on", !!document.getElementById("empty-state")
      && !document.querySelector(".side-link[data-panel][aria-expanded='true']"));
  }

  const chat = document.getElementById("main");
  if (chat && !chat.querySelector(".brand-corner")) {
    const corner = el("div", "brand-corner");
    corner.setAttribute("aria-hidden", "true");
    corner.innerHTML = "<span>STELLAR</span><span>IDEAS &rarr; REALITY</span>";
    chat.appendChild(corner);
  }

  const messages = document.getElementById("messages");
  if (messages) {
    const look = () => {
      const empty = document.getElementById("empty-state");
      if (empty) decorate(empty);
      markHome();
    };
    new MutationObserver(look).observe(messages, { childList: true });
    look();
    if (window.ResizeObserver) new ResizeObserver(fit).observe(messages);
  }

  /* --- the panel: Chat, Deploy, Files, Tools ------------------------------- */
  const panel = document.getElementById("side-panel");
  const panelTitle = document.getElementById("side-panel-title");
  const TITLES = { chat: "Chats", deploy: "Deploy", files: "Files", tools: "Tools" };
  const ICONS = {
    cloud: '<path d="M7 18a4.5 4.5 0 0 1-.6-8.96A6 6 0 0 1 18 9.5a4.25 4.25 0 0 1-.25 8.5z"/>',
    upload: '<path d="M12 16V5M7.5 9.5L12 5l4.5 4.5M5 19h14"/>',
    folder: '<path d="M4 7a1 1 0 0 1 1-1h4.5l2 2H19a1 1 0 0 1 1 1v9a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1z"/>',
    search: '<circle cx="11" cy="11" r="6.5"/><path d="M20 20l-4-4"/>',
    code: '<path d="M8.5 7.5L4 12l4.5 4.5M15.5 7.5L20 12l-4.5 4.5"/>',
    term: '<path d="M4 4.5h16v15H4zM8 9l3 3-3 3"/>',
    image: '<rect x="3.5" y="4.5" width="17" height="15" rx="1.5"/><circle cx="9" cy="9.5" r="1.6"/><path d="M4 17l5-5 4 4 2.5-2.5L20 18"/>',
    clock: '<circle cx="12" cy="12" r="8"/><path d="M12 7.5V12l3 2"/>',
    globe: '<circle cx="12" cy="12" r="8"/><path d="M4 12h16M12 4a12 12 0 0 1 0 16M12 4a12 12 0 0 0 0 16"/>',
  };

  let current = null;
  function closePanel() {
    if (!panel || panel.hidden) return;
    panel.hidden = true;
    current = null;
    document.querySelectorAll(".side-link[data-panel]").forEach((b) => b.setAttribute("aria-expanded", "false"));
    markHome();
  }

  function action(icon, title, small, fn) {
    const b = el("button", "panel-action");
    b.type = "button";
    b.innerHTML = `<svg viewBox="0 0 24 24" width="20" height="20" aria-hidden="true">${ICONS[icon]}</svg>`;
    const t = el("span", null, title);
    if (small) t.appendChild(el("small", null, small));
    b.appendChild(t);
    b.addEventListener("click", () => { closePanel(); fn(); });
    return b;
  }

  function fillSections() {
    const sec = (name) => panel.querySelector(`[data-section="${name}"]`);
    const openTerminal = () => {
      const t = document.getElementById("btn-terminal-toggle");
      if (t && t.getAttribute("aria-expanded") !== "true") t.click();
    };
    sec("files").replaceChildren(
      el("p", "panel-note", "Files you add go into this chat; Stellar's sandbox keeps its work in /lab."),
      action("upload", "Upload files", "Attach them to your next message", () => {
        const a = document.getElementById("attach");
        if (a) a.click();
      }),
      action("folder", "Browse /lab in the terminal", "The sandbox's own files", openTerminal),
    );
    sec("tools").replaceChildren(
      el("p", "panel-note", "What Stellar can do. Pick one to start."),
      action("search", "Search the web", null, () => prefill("Search the web for ")),
      action("code", "Build a website", null, () => prefill("Build me a website for ")),
      action("term", "Run code", null, () => prefill("Write and run a Python script that ")),
      action("image", "Create images", null, () => prefill("Make an image of ")),
      action("cloud", "Deploy", null, () => prefill("Deploy the site we built so I can share it")),
      action("clock", "Schedule a task", null, () => prefill("Every day at 9am, ")),
    );
  }


  /* --- Deploy: the user's projects ------------------------------------------
     Every app Stellar has deployed, with its state. Apps stay on for 90 hours
     after their last visit, then sleep and wake on the next visit; from here
     they can also be woken, put to sleep, rolled back to a checkpoint or
     deleted. Destructive steps ask first, inline. */
  const STATE_TEXT = { running: "Running", sleeping: "Asleep", stopped: "Stopped",
    deploying: "Starting", failed: "Failed", waking: "Waking up" };

  function ago(ts) {
    if (!ts) return "";
    const s = Math.max(0, Date.now() / 1000 - ts);
    if (s < 3600) return "active in the last hour";
    if (s < 86400) return `last active ${Math.round(s / 3600)}h ago`;
    return `last active ${Math.round(s / 86400)}d ago`;
  }

  function button(label, cls, fn) {
    const b = el("button", `project-btn ${cls || ""}`, label);
    b.type = "button";
    b.addEventListener("click", (e) => { e.stopPropagation(); fn(b); });
    return b;
  }

  /* An inline "are you sure?": the button row is swapped for a question. */
  function confirmIn(row, question, yesLabel, onYes) {
    const keep = [...row.childNodes];
    const q = el("span", "project-confirm-text", question);
    const yes = button(yesLabel, "danger", async (b) => { b.disabled = true; await onYes(); });
    const no = button("Cancel", "", () => row.replaceChildren(...keep));
    row.replaceChildren(q, yes, no);
  }

  const waking = new Set();

  function projectCard(p, refresh) {
    const state = waking.has(p.id) && p.state !== "running" ? "waking" : p.state;
    const card = el("div", `project-card state-${state}`);
    const head = el("div", "project-head");
    head.append(el("span", "project-dot"), el("span", "project-name", p.name),
      el("span", "project-state", STATE_TEXT[state] || state));
    card.appendChild(head);

    const link = el("a", "project-url", p.url.replace(/^https?:\/\//, ""));
    link.href = p.url; link.target = "_blank"; link.rel = "noopener";
    card.appendChild(link);

    const meta = el("div", "project-meta", ago(p.last_active));
    if (p.start_command) {
      const code = el("code", null, p.start_command.length > 48 ? p.start_command.slice(0, 47) + "\u2026" : p.start_command);
      code.title = p.start_command;
      meta.append(" \u00b7 starts with ", code);
    } else {
      meta.append(" \u00b7 no start command yet");
    }
    card.appendChild(meta);

    const row = el("div", "project-actions");
    const say = (text) => { row.replaceChildren(el("span", "project-confirm-text", text)); };
    if (state === "running") {
      row.appendChild(button("Sleep", "", async () => {
        say("Putting it to sleep\u2026");
        try { await request(`/api/projects/${p.id}/sleep`, { method: "POST" }); } catch (err) { say(err.message); return; }
        refresh();
      }));
    } else if (state !== "waking" && state !== "deploying") {
      row.appendChild(button("Wake", "primary", async () => {
        say("Waking\u2026");
        try { await request(`/api/projects/${p.id}/wake`, { method: "POST" }); } catch (err) { say(err.message); return; }
        waking.add(p.id);
        setTimeout(() => waking.delete(p.id), 90000);
        refresh();
      }));
    }
    const history = el("div", "project-history");
    history.hidden = true;
    row.appendChild(button("History", "", async (b) => {
      if (!history.hidden) { history.hidden = true; return; }
      history.hidden = false;
      history.replaceChildren(el("p", "project-meta", "Loading checkpoints\u2026"));
      let list;
      try { list = await request(`/api/projects/${p.id}/history`); } catch (err) {
        history.replaceChildren(el("p", "project-meta", "Couldn't load history: " + err.message)); return;
      }
      if (!list.length) { history.replaceChildren(el("p", "project-meta", "No checkpoints yet.")); return; }
      history.replaceChildren(...list.map((c, i) => {
        const item = el("div", "checkpoint");
        const when = new Date(c.at);
        const label = el("div", "checkpoint-text");
        label.append(el("span", "checkpoint-msg", c.message),
          el("span", "checkpoint-when", `${c.sha} \u00b7 ${isNaN(when) ? "" : when.toLocaleString()}`));
        item.appendChild(label);
        if (i > 0) {
          const act = el("div", "checkpoint-act");
          act.appendChild(button("Restore", "", () => confirmIn(act, "Restore this version?", "Restore", async () => {
            try {
              await request(`/api/projects/${p.id}/restore`, { method: "POST", body: JSON.stringify({ commit: c.sha }) });
            } catch (err) { act.replaceChildren(el("span", "project-confirm-text", err.message)); return; }
            refresh();
          })));
          item.appendChild(act);
        } else {
          item.appendChild(el("span", "checkpoint-now", "current"));
        }
        return item;
      }));
    }));
    row.appendChild(button("Delete", "danger-quiet", () => confirmIn(row,
      "Delete this app, its files and history?", "Delete", async () => {
        try { await request(`/api/projects/${p.id}`, { method: "DELETE" }); } catch (err) { say(err.message); return; }
        refresh();
      })));
    card.append(row, history);
    return card;
  }

  let projectsTimer = null;
  async function renderProjects() {
    const sec = panel.querySelector('[data-section="deploy"]');
    if (!sec) return;
    clearTimeout(projectsTimer);
    if (!sec.childElementCount) sec.replaceChildren(el("p", "panel-note", "Loading your projects\u2026"));
    let list;
    try { list = await request("/api/projects"); } catch (err) {
      sec.replaceChildren(el("p", "panel-note", "Couldn't load your projects: " + err.message)); return;
    }
    const intro = el("p", "panel-note", list.length
      ? "Apps stay on for 90 hours after their last visit, then sleep and wake by themselves on the next visit."
      : "No projects yet. Ask Stellar to build and deploy one.");
    sec.replaceChildren(intro, ...list.map((pr) => projectCard(pr, renderProjects)),
      action("globe", "Build and deploy a new site", "Describe it and Stellar does the rest",
        () => prefill("Build and deploy a website for ")));
    // While anything is starting, look again shortly.
    if (current === "deploy" && list.some((pr) => pr.state === "deploying" || (waking.has(pr.id) && pr.state !== "running"))) {
      projectsTimer = setTimeout(() => { if (current === "deploy") renderProjects(); }, 4000);
    }
  }

  function openPanel(name) {
    if (!panel) return;
    if (current === name) { closePanel(); return; }
    current = name;
    panelTitle.textContent = TITLES[name];
    panel.querySelectorAll(".side-section").forEach((s) => { s.hidden = s.dataset.section !== name; });
    document.querySelectorAll(".side-link[data-panel]").forEach((b) =>
      b.setAttribute("aria-expanded", String(b.dataset.panel === name)));
    panel.hidden = false;
    markHome();
    if (name === "deploy") renderProjects();
  }

  if (panel) {
    fillSections();
    document.querySelectorAll(".side-link[data-panel]").forEach((b) =>
      b.addEventListener("click", (e) => { e.stopPropagation(); openPanel(b.dataset.panel); }));
    document.getElementById("side-panel-close").addEventListener("click", closePanel);
    panel.addEventListener("click", (e) => {
      // Picking a chat opens it; the panel steps aside.
      if (e.target.closest(".chat-open")) setTimeout(closePanel, 0);
    });
    document.addEventListener("keydown", (e) => { if (e.key === "Escape") closePanel(); });
    document.addEventListener("click", (e) => {
      if (!panel.hidden && e.target instanceof Element && !e.target.closest("#side-panel") && !e.target.closest(".menu")
          && !e.target.closest("dialog")) closePanel();
    });
    if (homeLink) homeLink.addEventListener("click", closePanel);
  }
})();

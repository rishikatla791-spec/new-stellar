/* The pieces of Stellar's look that need a script (main.js stays as it is):

   - The model pill in the composer and its menu: search, Auto, and the models
     this server's keys can use. It reads and saves the same preference as
     Settings (preferred_model), so the two always agree.
   - The empty chat: three rings of ticks that sweep in around the star, and
     starter chips under it. Hovering a chip brings its ring forward. */
(function () {
  "use strict";

  const CSRF = (document.querySelector('meta[name="csrf-token"]') || {}).content || "";

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

  /* --- the model pill -------------------------------------------------- */
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
    pillName.textContent = models.chosen ? modelName(models.chosen)[0] : "Auto";
    pill.classList.toggle("is-auto", !models.chosen);
  }

  function renderList() {
    const q = search.value.trim().toLowerCase();
    autoBtn.setAttribute("aria-checked", String(!models.chosen));
    autoNote.textContent = models.fallback ? modelName(models.fallback)[0] : "";
    if (!models.list) {
      list.replaceChildren(Object.assign(document.createElement("li"), {
        className: "model-empty", textContent: "Loading models…" }));
      return;
    }
    const shown = models.list.filter((m) => !q || m.toLowerCase().includes(q)
      || modelName(m).join(" ").toLowerCase().includes(q));
    if (!shown.length) {
      list.replaceChildren(Object.assign(document.createElement("li"), {
        className: "model-empty", textContent: "No model matches." }));
      return;
    }
    list.replaceChildren(...shown.map((m) => {
      const [name, tag] = modelName(m);
      const li = document.createElement("li");
      const b = document.createElement("button");
      b.type = "button";
      b.className = "model-option";
      b.setAttribute("role", "option");
      b.setAttribute("aria-selected", String(m === models.chosen));
      b.dataset.model = m;
      b.innerHTML = '<svg class="model-spark" viewBox="0 0 24 24" width="15" height="15" aria-hidden="true">'
        + '<path d="M12 2l2.2 7.8L22 12l-7.8 2.2L12 22l-2.2-7.8L2 12l7.8-2.2z" fill="currentColor"/></svg>';
      const label = document.createElement("span");
      label.className = "model-option-name";
      label.textContent = name;
      if (tag) {
        const t = document.createElement("span");
        t.className = "model-tag";
        t.textContent = " " + tag;
        label.appendChild(t);
      }
      b.append(label);
      b.insertAdjacentHTML("beforeend", '<svg class="model-check" viewBox="0 0 24 24" width="15" height="15" aria-hidden="true">'
        + '<path d="M5 12.5l4.5 4.5L19 7.5" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"/></svg>');
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
      list.replaceChildren(Object.assign(document.createElement("li"), {
        className: "model-empty", textContent: "Couldn't load models: " + err.message }));
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
      const li = Object.assign(document.createElement("li"), { className: "model-empty" });
      li.textContent = "Couldn't change the model: " + err.message;
      list.prepend(li);
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
      if (e.key === "Enter") {
        e.preventDefault();
        const first = list.querySelector(".model-option");
        if (first) choose(first.dataset.model);
      } else if (e.key === "ArrowDown") {
        e.preventDefault();
        const first = list.querySelector(".model-option");
        if (first) first.focus();
      }
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
      if (!menu.hidden && !e.target.closest(".model-wrap")) close();
    });
    // The label, quickly: /api/me without the model list does not wait on Google.
    request("/api/me").then((me) => { models.chosen = me.preferred_model || ""; showChoice(); })
      .catch(() => {});
  }

  /* --- the empty chat ------------------------------------------------- */
  const SVG = "http://www.w3.org/2000/svg";
  const RINGS = [
    { key: "build", r: 96, ticks: 64, fill: 0.72 },
    { key: "code", r: 80, ticks: 54, fill: 0.58 },
    { key: "image", r: 64, ticks: 44, fill: 0.46 },
  ];
  const STARTERS = [
    { key: "build", label: "Build a site", text: "Build me a website for ",
      icon: '<circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3a14 14 0 0 1 0 18M12 3a14 14 0 0 0 0 18"/>' },
    { key: "code", label: "Run code", text: "Write and run a Python script that ",
      icon: '<path d="M4 17l6-5-6-5M12 19h8"/>' },
    { key: "image", label: "Make an image", text: "Make an image of ",
      icon: '<rect x="3" y="3" width="18" height="18" rx="3"/><circle cx="9" cy="9" r="2"/><path d="M21 15l-5-5L5 21"/>' },
  ];
  const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  function dial() {
    const svg = document.createElementNS(SVG, "svg");
    svg.setAttribute("viewBox", "0 0 220 220");
    svg.setAttribute("class", "dial");
    svg.setAttribute("aria-hidden", "true");
    RINGS.forEach((ring, ri) => {
      const g = document.createElementNS(SVG, "g");
      g.setAttribute("class", `ring ring-${ring.key}`);
      const lit = Math.round(ring.ticks * ring.fill);
      for (let i = 0; i < ring.ticks; i++) {
        const a = (i / ring.ticks) * Math.PI * 2 - Math.PI / 2;
        const line = document.createElementNS(SVG, "line");
        const r1 = ring.r - 5, r2 = ring.r + 5;
        line.setAttribute("x1", (110 + Math.cos(a) * r1).toFixed(2));
        line.setAttribute("y1", (110 + Math.sin(a) * r1).toFixed(2));
        line.setAttribute("x2", (110 + Math.cos(a) * r2).toFixed(2));
        line.setAttribute("y2", (110 + Math.sin(a) * r2).toFixed(2));
        line.setAttribute("class", i < lit ? "tick lit" : "tick");
        if (i === lit - 1) line.classList.add("needle");
        // The sweep: each tick lights a moment after the one before it.
        line.style.transitionDelay = reduceMotion ? "0ms" : `${Math.round(ri * 120 + i * 11)}ms`;
        g.appendChild(line);
      }
      svg.appendChild(g);
    });
    return svg;
  }

  function fillRing(svg, key, fraction) {
    const g = svg.querySelector(`.ring-${key}`);
    if (!g) return;
    const ticks = [...g.children];
    const lit = Math.round(ticks.length * fraction);
    ticks.forEach((t, i) => {
      t.style.transitionDelay = reduceMotion ? "0ms" : `${i * 6}ms`;
      t.classList.toggle("lit", i < lit);
      t.classList.toggle("needle", i === lit - 1 && fraction < 1);
    });
  }

  function decorate(empty) {
    if (empty.dataset.looked) return;
    empty.dataset.looked = "1";
    const stage = document.createElement("div");
    stage.className = "dial-stage";
    const svg = dial();
    stage.appendChild(svg);
    stage.insertAdjacentHTML("beforeend", '<svg class="dial-star" viewBox="0 0 24 24" aria-hidden="true">'
      + '<path d="M12 2l2.2 7.8L22 12l-7.8 2.2L12 22l-2.2-7.8L2 12l7.8-2.2z" fill="currentColor"/></svg>');
    empty.prepend(stage);

    const chips = document.createElement("div");
    chips.className = "starter-chips";
    STARTERS.forEach((s) => {
      const b = document.createElement("button");
      b.type = "button";
      b.className = `starter-chip chip-${s.key}`;
      b.innerHTML = `<svg viewBox="0 0 24 24" width="17" height="17" aria-hidden="true" fill="none" stroke="currentColor"`
        + ` stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round">${s.icon}</svg>`;
      b.append(s.label);
      const focus = () => { svg.dataset.focus = s.key; fillRing(svg, s.key, 1); };
      const blur = () => {
        delete svg.dataset.focus;
        const ring = RINGS.find((r) => r.key === s.key);
        fillRing(svg, s.key, ring.fill);
      };
      b.addEventListener("mouseenter", focus);
      b.addEventListener("focus", focus);
      b.addEventListener("mouseleave", blur);
      b.addEventListener("blur", blur);
      b.addEventListener("click", () => {
        const input = document.getElementById("input");
        if (!input) return;
        input.value = s.text;
        input.dispatchEvent(new Event("input", { bubbles: true }));
        input.focus();
        input.setSelectionRange(input.value.length, input.value.length);
      });
      chips.appendChild(b);
    });
    empty.appendChild(chips);

    // Start dark, then sweep the ticks in on the next frame.
    svg.classList.add("unlit");
    requestAnimationFrame(() => requestAnimationFrame(() => svg.classList.remove("unlit")));
  }

  const messages = document.getElementById("messages");
  if (messages) {
    const look = () => {
      const empty = document.getElementById("empty-state");
      if (empty) decorate(empty);
    };
    new MutationObserver(look).observe(messages, { childList: true });
    look();
  }
})();

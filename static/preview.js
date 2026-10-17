/* Owned live previews. The frame never receives the Stellar session. */
(() => {
  "use strict";
  const pane = document.getElementById("preview-pane");
  const app = document.querySelector(".app");
  const byId = (id) => document.getElementById("preview-" + id);
  const nav = document.getElementById("nav-preview");
  let epoch = 0;
  let projects = [];
  let capabilities = null;
  const say = (text) => { byId("status").textContent = text; };

  function clearFrame() {
    byId("viewport").replaceChildren();
  }

  function close() {
    epoch++;
    clearFrame();
    pane.hidden = true;
    app.classList.remove("preview-open");
    nav.setAttribute("aria-expanded", "false");
    nav.focus();
  }

  async function refresh(selected = "") {
    const mine = ++epoch;
    clearFrame();
    say("Loading your projects…");
    try {
      const [list, config] = await Promise.all([api("/api/projects"), api("/api/preview/config")]);
      if (mine !== epoch || pane.hidden) return;
      if (!Array.isArray(list)) throw new Error("Invalid project list.");
      projects = list;
      capabilities = config;
      byId("project").replaceChildren(new Option("Choose a project", ""));
      for (const project of projects) {
        if (!/^[0-9a-f]{12}$/.test(project.id)) continue;
        byId("project").appendChild(new Option(`${project.name} · ${project.state}`, project.id));
      }
      byId("test").disabled = !config.browser_checks;
      say(config.enabled ? "Choose a running project. Wake stopped projects from Deploy."
        : "Preview setup required: configure STELLAR_PREVIEW_ORIGIN and STELLAR_APP_ORIGIN, then start the gateway.");
      if (selected && projects.some((p) => p.id === selected)) {
        byId("project").value = selected;
        await launch();
      }
    } catch (error) { if (mine === epoch) say(error.message); }
  }

  async function launch(external = false) {
    const id = byId("project").value;
    const project = projects.find((p) => p.id === id);
    const mine = ++epoch;
    clearFrame();
    byId("evidence").hidden = true;
    if (!project) { say("Choose a project first."); return; }
    if (project.state !== "running") { say("Wake this project from Deploy, then refresh projects."); return; }
    if (!capabilities?.enabled) { say("The preview gateway needs server configuration before it can be opened."); return; }
    // Create the tab during the gesture so popup blockers do not discard it.
    const tab = external ? window.open("about:blank", "_blank") : null;
    if (tab) tab.opener = null;
    say("Connecting to the project…");
    try {
      const result = await api(`/api/projects/${id}/preview`, { method: "POST", body: "{}" });
      if (mine !== epoch || pane.hidden) { tab?.close(); return; }
      const url = new URL(result.launch_url);
      const expected = new URL(result.origin);
      // The response is trusted server data, but never navigate to arbitrary
      // schemes, an unexpected project hostname or a malformed launch URL.
      if (!/^https?:$/.test(url.protocol) || url.origin !== expected.origin
          || !url.hostname.startsWith(`p-${id}.`) || url.pathname !== "/__stellar_preview/launch"
          || url.username || url.password) throw new Error("Invalid preview launch address.");
      if (external) {
        if (!tab) throw new Error("The browser blocked the tab. Allow popups and try again.");
        tab.location.replace(url.href);
        say("Preview opened in a separate tab. Reload here to embed it again.");
      } else {
        const frame = document.createElement("iframe");
        frame.title = `${project.name} live preview`;
        frame.setAttribute("sandbox", "allow-scripts allow-same-origin allow-forms");
        frame.referrerPolicy = "no-referrer";
        frame.src = url.href;
        frame.addEventListener("load", () => {
          if (mine === epoch) say("Preview frame loaded. Reload to renew access after 15 minutes. If embedding fails, open a separate tab or check Deploy.");
        });
        byId("viewport").appendChild(frame);
      }
    } catch (error) {
      tab?.close();
      if (mine === epoch) say(error.message);
    }
  }

  async function check() {
    const id = byId("project").value;
    const chatId = state.chatId;
    if (!chatId || !projects.some((p) => p.id === id)) { say("Choose a project and an active chat to save evidence."); return; }
    const mine = epoch;
    const button = byId("test");
    button.disabled = true;
    say("Checking the app in an isolated browser…");
    try {
      const report = await api(`/api/projects/${id}/browser-check`, {
        method: "POST", body: JSON.stringify({ chat_id: chatId, steps: [] }),
      });
      if (mine !== epoch || pane.hidden || state.chatId !== chatId) return;
      const evidence = byId("evidence");
      const label = document.createElement("p");
      label.textContent = report.passed ? "Browser check passed." : "Browser check failed. Review the evidence.";
      evidence.replaceChildren(label);
      for (const [key, text] of [["report", "Download report"], ["screenshot", "View screenshot"]]) {
        const path = report[key];
        if (typeof path !== "string" || !path.startsWith(`/api/outputs/${chatId}/browser-`) || path.includes("..")) continue;
        const link = document.createElement("a");
        link.href = path; link.textContent = text; link.target = "_blank"; link.rel = "noopener";
        evidence.appendChild(link);
      }
      evidence.hidden = false;
      say(report.passed ? "Browser check completed successfully." : "The browser found a failure. See the report.");
    } catch (error) { if (mine === epoch) say(error.message); }
    finally { button.disabled = !capabilities?.browser_checks; }
  }

  nav.addEventListener("click", () => {
    if (!pane.hidden) { close(); return; }
    pane.hidden = false;
    app.classList.add("preview-open");
    nav.setAttribute("aria-expanded", "true");
    const menu = document.getElementById("menu-btn");
    if (menu?.getAttribute("aria-expanded") === "true") menu.click();
    refresh();
    byId("project").focus();
  });
  document.addEventListener("stellar:preview", (event) => {
    const id = event.detail?.projectId;
    if (typeof id !== "string" || !/^[0-9a-f]{12}$/.test(id)) return;
    pane.hidden = false;
    app.classList.add("preview-open");
    nav.setAttribute("aria-expanded", "true");
    // The project list is an overlay on narrow screens.
    document.querySelector('#side-panel:not([hidden]) #side-panel-close')?.click();
    refresh(id);
  });
  byId("close").addEventListener("click", close);
  byId("refresh-projects").addEventListener("click", refresh);
  byId("reload").addEventListener("click", () => launch());
  byId("external").addEventListener("click", () => launch(true));
  byId("project").addEventListener("change", () => launch());
  byId("test").addEventListener("click", check);
  pane.addEventListener("keydown", (event) => { if (event.key === "Escape") { event.preventDefault(); close(); } });
})();

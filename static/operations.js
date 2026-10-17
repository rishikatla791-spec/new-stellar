/* Capabilities describe checked dependencies, not a promise that external providers are up. */
(function () {
  "use strict";
  const dashboard = document.getElementById("operations-dashboard");
  const card = document.getElementById("runtime-system-card");
  if (!dashboard && !card) return;
  let pending = false;
  const element = (tag, text, className) => {
    const node = document.createElement(tag);
    if (text !== undefined) node.textContent = text;
    if (className) node.className = className;
    return node;
  };
  const text = (id, value) => { const node = document.getElementById(id); if (node) node.textContent = value; };
  const bytes = value => (Number(value || 0) / 1048576).toFixed(1) + " MB";
  function item(label, state, detail, available) {
    const node = element("div", undefined, "ops-card");
    node.dataset.available = String(available);
    node.append(element("h3", label), element("strong", state));
    if (detail) node.append(element("p", detail));
    return node;
  }
  function render(data) {
    if (card) {
      card.classList.toggle("is-down", data.status === "unavailable");
      card.classList.toggle("is-limited", data.status === "limited");
      text("runtime-system-state", data.status === "operational" ? "Chat and runner ready" : data.status === "limited" ? "Some features unavailable" : "Services unavailable");
      text("runtime-system-detail", data.checks.docker === "ok" ? "Runner connected. View feature details in Admin." : "Chat can work. Container tools need a runner.");
      const notice = document.getElementById("runtime-notice");
      const problem = !data.features.chat.available ? data.features.chat.reason : !data.features.deploy.available ? data.features.deploy.reason : "";
      notice.hidden = !problem;
      notice.textContent = problem;
      // Existing file/history controls remain usable when the runner is away.
      for (const [id, feature] of [["btn-terminal-toggle", "terminal"], ["nav-preview", "preview"]]) {
        const button = document.getElementById(id);
        if (button) { button.disabled = !data.features[feature].available; button.title = data.features[feature].reason; }
      }
      document.dispatchEvent(new CustomEvent("stellar:runtime", { detail: data }));
    }
    if (!dashboard) return;
    text("operations-status", data.status === "operational" ? "Core services operational" : data.status === "limited" ? "Running with limited capabilities" : "Core services need attention");
    document.getElementById("operations-checks").replaceChildren(...Object.entries(data.checks).map(([name, state]) => item(name[0].toUpperCase() + name.slice(1), state === "ok" ? "Connected" : state, "", state === "ok")));
    document.getElementById("operations-features").replaceChildren(...Object.values(data.features).map(feature => item(feature.label, feature.available ? "Available" : "Unavailable", feature.reason, feature.available)));
    const provider = data.provider;
    text("operations-provider", `AI access: ${provider.personal_key ? "personal key configured; " : ""}${provider.shared_keys} shared keys; ${provider.usable_keys === null ? "quota status unknown" : provider.usable_keys + " tracked keys usable"}.${provider.backup_configured ? " Backup provider configured." : ""}`);
    text("operations-storage", `Account storage: ${bytes(data.storage.used_bytes)} of ${bytes(data.storage.limit_bytes)}${data.storage.blocked ? " — quota reached" : ""}.`);
    document.getElementById("operations-issues").replaceChildren(...(data.issues.length ? data.issues.map(issue => element("li", issue.message)) : [element("li", "No configuration blockers detected.")]));
    const activity = data.tool_activity;
    text("operations-tools", `${activity.total} recorded tool calls in ${activity.window}; ${activity.failed} failed; average ${activity.average_ms} ms. Failures are recorded outcomes, not proof that a task was completed.`);
    document.getElementById("operations-failures").replaceChildren(...(data.recent_failures.length ? data.recent_failures.map(run => element("li", `${run.tool_name} · ${run.duration_ms || 0} ms · ${run.timestamp} UTC`)) : [element("li", "No recorded tool failures.")]));
    text("operations-checked", `Checked ${new Date(data.checked_at).toLocaleTimeString()} · release ${data.commit || "unknown"} · ${data.execution_mode} mode`);
  }
  async function refresh() {
    if (pending || document.hidden) return;
    pending = true;
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 12000);
    const button = document.getElementById("operations-refresh");
    if (button) button.disabled = true;
    try {
      const response = await fetch(dashboard ? "/api/admin/health" : "/api/runtime", { credentials: "same-origin", cache: "no-store", signal: controller.signal });
      if (!response.ok) throw new Error(response.status === 401 || response.status === 403 ? "Sign in with the required account to view health." : "Health check unavailable. Retry shortly.");
      render(await response.json());
    } catch (error) {
      text("operations-status", error.name === "AbortError" ? "Health check timed out. Retry shortly." : error.message);
      text("runtime-system-state", "Status unavailable");
      text("runtime-system-detail", "Could not verify services. Check your connection.");
      if (card) card.classList.add("is-down");
    } finally {
      clearTimeout(timeout);
      pending = false;
      if (button) button.disabled = false;
    }
  }
  document.getElementById("operations-refresh")?.addEventListener("click", refresh);
  document.addEventListener("visibilitychange", () => { if (!document.hidden) refresh(); });
  refresh();
  setInterval(refresh, 60000);
})();

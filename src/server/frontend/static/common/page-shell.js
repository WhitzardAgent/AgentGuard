(function () {
  const state = {
    apiStatus: "Checking...",
    apiTone: "",
    toolStatus: "Waiting for first sync",
    pageTitle: "AgentGuard",
    pageDescription: "Shared frontend shell is ready.",
    selectedAgentId: "",
    selectedPluginName: "",
    currentUserLabel: "",
    isAdmin: false,
  };
  const SELECTED_AGENT_KEY = "agentguard.selectedAgentId";
  const AGENT_CATALOG_KEY = "agentguard.agentCatalog";
  const SELECTED_PLUGIN_KEY = "agentguard.selectedPluginName";
  const CURRENT_USER_KEY = "agentguard.currentUserLabel";
  const AGENT_SELECTION_PATH = "/agents.html";
  const PLUGIN_SELECTION_PATH = "/plugins.html";
  const LOGIN_PATH = "/login.html";
  const AGENT_REQUIRED_PATHS = new Set([
    "/plugins.html",
    "/skills.html",
    "/mcps.html",
    "/security-audit.html",
    "/labels.html",
    "/rules.html",
    "/runtime.html",
  ]);

  function getElement(id) {
    if (typeof document === "undefined" || typeof document.getElementById !== "function") {
      return null;
    }
    return document.getElementById(id);
  }

  function getBodyClassList() {
    if (typeof document === "undefined" || !document.body?.classList) {
      return null;
    }
    return document.body.classList;
  }

  function currentPath() {
    if (typeof window === "undefined" || !window.location) {
      return "";
    }
    return String(window.location.pathname || "").trim();
  }

  function isAgentRequiredPage(pathname = currentPath()) {
    return AGENT_REQUIRED_PATHS.has(String(pathname || "").trim());
  }

  function redirectToAgentSelection() {
    if (typeof window === "undefined" || !window.location) {
      return;
    }
    if (currentPath() === AGENT_SELECTION_PATH) {
      return;
    }
    window.location.replace(AGENT_SELECTION_PATH);
  }

  function redirectToLogin() {
    if (typeof window === "undefined" || !window.location) {
      return;
    }
    const path = currentPath() || "/home.html";
    const search = String(window.location.search || "");
    const next = encodeURIComponent(`${path}${search}`);
    window.location.replace(`${LOGIN_PATH}?next=${next}`);
  }

  function redirectToPluginSelection() {
    if (typeof window === "undefined" || !window.location) {
      return;
    }
    if (currentPath() === PLUGIN_SELECTION_PATH) {
      return;
    }
    window.location.replace(PLUGIN_SELECTION_PATH);
  }

  function enforceSelectedAgentAccess() {
    if (!state.selectedAgentId && isAgentRequiredPage()) {
      redirectToAgentSelection();
      return false;
    }
    return true;
  }

  function setText(id, value) {
    const element = getElement(id);
    if (!element) {
      return;
    }
    element.textContent = String(value || "");
  }

  function readText(id) {
    const element = getElement(id);
    if (!element) {
      return "";
    }
    return String(element.textContent || "").trim();
  }

  function interpolateText(template, variables = {}) {
    return String(template || "").replace(/\{(\w+)\}/g, (match, key) => {
      if (!Object.prototype.hasOwnProperty.call(variables, key)) {
        return match;
      }
      return String(variables[key] ?? "");
    });
  }

  function getPageCopy(key, fallback = "", variables = {}) {
    const value = readText(`agentguard-copy-${String(key || "").trim()}`) || String(fallback || "");
    return interpolateText(value, variables);
  }

  function translateText(value) {
    const text = String(value || "");
    return window.AgentGuardI18n?.t?.(text) || text;
  }

  function selectedAgentDisplayLabel() {
    const agentId = String(state.selectedAgentId || "").trim();
    if (!agentId) {
      return "";
    }
    try {
      const parsed = JSON.parse(window.localStorage?.getItem(AGENT_CATALOG_KEY) || "[]");
      if (Array.isArray(parsed)) {
        const match = parsed.find((item) => String(item?.agent_id || "").trim() === agentId);
        const label = String(match?.display_agent_id || match?.external_agent_id || "").trim();
        if (label) {
          return label;
        }
      }
    } catch {
      // Ignore localStorage read issues in preview mode.
    }
    return agentId;
  }

  function render() {
    setText("sidebar-api-status", state.apiStatus);
    setText("sidebar-tool-status", state.toolStatus);
    setText("sidebar-page-title", state.pageTitle);
    setText("sidebar-page-description", state.pageDescription);
    setText("sidebar-selected-agent", selectedAgentDisplayLabel());
    setText("sidebar-current-user", state.currentUserLabel || "");

    const selectedAgentWrap = getElement("sidebar-selected-agent-wrap");
    const selectedAgentPanel = getElement("sidebar-agent-panel");
    const clearSelectedAgentButton = getElement("sidebar-clear-agent");
    const selectedAgentValue = getElement("sidebar-selected-agent");
    if (selectedAgentWrap) {
      selectedAgentWrap.hidden = !state.selectedAgentId;
    }
    if (selectedAgentPanel) {
      selectedAgentPanel.hidden = !state.selectedAgentId;
    }
    if (selectedAgentValue) {
      selectedAgentValue.hidden = !state.selectedAgentId;
    }
    if (clearSelectedAgentButton) {
      clearSelectedAgentButton.hidden = !state.selectedAgentId;
    }

    if (typeof document !== "undefined" && typeof document.querySelectorAll === "function") {
      document.querySelectorAll("[data-agent-required='true']").forEach((element) => {
        element.hidden = !state.selectedAgentId;
      });
      document.querySelectorAll("[data-admin-required='true']").forEach((element) => {
        element.hidden = !state.isAdmin;
      });
    }

    const apiElement = getElement("sidebar-api-status");
    if (apiElement?.classList) {
      apiElement.classList.remove("success", "warning", "danger");
      if (state.apiTone) {
        apiElement.classList.add(state.apiTone);
      }
    }
  }

  function readSelectedAgentId() {
    try {
      return String(window.localStorage?.getItem(SELECTED_AGENT_KEY) || "").trim();
    } catch {
      return "";
    }
  }

  function readCurrentUserLabel() {
    try {
      return String(window.localStorage?.getItem(CURRENT_USER_KEY) || "").trim();
    } catch {
      return "";
    }
  }

  function readSelectedPluginName() {
    try {
      return String(window.localStorage?.getItem(SELECTED_PLUGIN_KEY) || "").trim();
    } catch {
      return "";
    }
  }

  function applySidebarState() {
    const bodyClassList = getBodyClassList();
    if (!bodyClassList) {
      return;
    }
    bodyClassList.add("sidebar-open");
    bodyClassList.remove("sidebar-collapsed");
  }

  function initTemplatePageContext() {
    const title = readText("agentguard-page-context-title");
    const description = readText("agentguard-page-context-description");
    if (title) {
      state.pageTitle = title;
    }
    if (description) {
      state.pageDescription = description;
    }
  }

  function initSelectedAgentState() {
    state.selectedAgentId = readSelectedAgentId();
    state.selectedPluginName = readSelectedPluginName();
    state.currentUserLabel = readCurrentUserLabel() || readText("sidebar-current-user") || translateText("Current User");

    const clearButton = getElement("sidebar-clear-agent");
    clearButton?.addEventListener("click", () => {
      setSelectedAgent("");
    });
  }

  async function requireAuthenticatedUser() {
    if (
      typeof window === "undefined"
      || !window.location
      || typeof fetch !== "function"
    ) {
      return true;
    }
    try {
      const response = await fetch("/api/user/me", {
        credentials: "same-origin",
        headers: { "Accept": "application/json" },
      });
      if (!response.ok) {
        throw new Error("not signed in");
      }
      const payload = await response.json().catch(() => ({}));
      if (!payload.user) {
        throw new Error("not signed in");
      }
      state.isAdmin = payload.user.is_admin === true;
      setCurrentUser(payload.user.username || "");
      enforceSelectedAgentAccess();
      return true;
    } catch {
      setCurrentUser("");
      redirectToLogin();
      return false;
    }
  }

  function setPageContext(nextState) {
    state.pageTitle = translateText(nextState?.title || state.pageTitle || "AgentGuard");
    state.pageDescription = translateText(nextState?.description || "");
    render();
  }

  function setApiStatus(label, tone = "") {
    state.apiStatus = String(label || "Checking...");
    state.apiTone = String(tone || "");
    render();
  }

  function setToolStatus(label) {
    state.toolStatus = String(label || "Waiting for first sync");
    render();
  }

  function setCurrentUser(label) {
    const normalized = String(label || "").trim();
    state.currentUserLabel = normalized || translateText("Current User");
    try {
      if (normalized) {
        window.localStorage?.setItem(CURRENT_USER_KEY, normalized);
      } else {
        window.localStorage?.removeItem(CURRENT_USER_KEY);
      }
    } catch {
      // Ignore localStorage write issues in preview mode.
    }
    render();
  }

  function dispatchSelectionEvent(name, detail) {
    if (
      typeof window !== "undefined"
      && typeof window.dispatchEvent === "function"
      && typeof CustomEvent === "function"
    ) {
      window.dispatchEvent(new CustomEvent(name, { detail }));
    }
  }

  function setSelectedPlugin(pluginName) {
    const normalized = String(pluginName || "").trim();
    state.selectedPluginName = normalized;
    try {
      if (normalized) {
        window.localStorage?.setItem(SELECTED_PLUGIN_KEY, normalized);
      } else {
        window.localStorage?.removeItem(SELECTED_PLUGIN_KEY);
      }
    } catch {
      // Ignore localStorage write issues in preview mode.
    }
    dispatchSelectionEvent("agentguard:selected-plugin-change", { pluginName: normalized });
    enforceSelectedAgentAccess();
    render();
  }

  function setSelectedAgent(agentId) {
    const normalized = String(agentId || "").trim();
    const changed = normalized !== state.selectedAgentId;
    state.selectedAgentId = normalized;
    try {
      if (normalized) {
        window.localStorage?.setItem(SELECTED_AGENT_KEY, normalized);
      } else {
        window.localStorage?.removeItem(SELECTED_AGENT_KEY);
      }
    } catch {
      // Ignore localStorage write issues in preview mode.
    }
    if (changed) {
      setSelectedPlugin("");
    }
    dispatchSelectionEvent("agentguard:selected-agent-change", {
      agentId: normalized,
      agentLabel: selectedAgentDisplayLabel(),
    });
    enforceSelectedAgentAccess();
    render();
  }

  applySidebarState();
  initTemplatePageContext();
  initSelectedAgentState();
  render();
  requireAuthenticatedUser();

  window.AgentGuardShell = {
    getState() {
      return { ...state, selectedAgentLabel: selectedAgentDisplayLabel() };
    },
    render,
    setApiStatus,
    setPageContext,
    setCurrentUser,
    getPageCopy,
    setSelectedAgent,
    setSelectedPlugin,
    setToolStatus,
    requireAuthenticatedUser,
  };
})();

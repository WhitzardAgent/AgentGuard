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
  };
  const SELECTED_AGENT_KEY = "agentguard.selectedAgentId";
  const SELECTED_PLUGIN_KEY = "agentguard.selectedPluginName";
  const CURRENT_USER_KEY = "agentguard.currentUserLabel";
  const AGENT_SELECTION_PATH = "/agents.html";
  const PLUGIN_SELECTION_PATH = "/plugins.html";
  const AGENT_REQUIRED_PATHS = new Set([
    "/plugins.html",
    "/labels.html",
    "/rules.html",
    "/runtime.html",
  ]);
  const RULE_BASED_REQUIRED_PATHS = new Set([
    "/labels.html",
    "/rules.html",
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
    if (
      state.selectedPluginName
      && state.selectedPluginName !== "rule_based_plugin"
      && RULE_BASED_REQUIRED_PATHS.has(currentPath())
    ) {
      redirectToPluginSelection();
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

  function render() {
    setText("sidebar-api-status", state.apiStatus);
    setText("sidebar-tool-status", state.toolStatus);
    setText("sidebar-page-title", state.pageTitle);
    setText("sidebar-page-description", state.pageDescription);
    setText("sidebar-selected-agent", state.selectedAgentId || "");
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
      document.querySelectorAll("[data-rule-based-required='true']").forEach((element) => {
        element.hidden = !state.selectedAgentId || state.selectedPluginName !== "rule_based_plugin";
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

  function initSelectedAgentState() {
    state.selectedAgentId = readSelectedAgentId();
    state.selectedPluginName = readSelectedPluginName();
    state.currentUserLabel = readCurrentUserLabel() || "Current User";
    enforceSelectedAgentAccess();

    const clearButton = getElement("sidebar-clear-agent");
    clearButton?.addEventListener("click", () => {
      setSelectedAgent("");
    });
  }

  function setPageContext(nextState) {
    state.pageTitle = String(nextState?.title || state.pageTitle || "AgentGuard");
    state.pageDescription = String(nextState?.description || "");
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
    dispatchSelectionEvent("agentguard:selected-agent-change", { agentId: normalized });
    enforceSelectedAgentAccess();
    render();
  }

  applySidebarState();
  initSelectedAgentState();
  render();

  window.AgentGuardShell = {
    getState() {
      return { ...state };
    },
    render,
    setApiStatus,
    setPageContext,
    setSelectedAgent,
    setSelectedPlugin,
    setToolStatus,
  };
})();

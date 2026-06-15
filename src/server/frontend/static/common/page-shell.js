(function () {
  const state = {
    apiStatus: "Checking...",
    apiTone: "",
    toolStatus: "Waiting for first sync",
    pageTitle: "AgentGuard",
    pageDescription: "Shared frontend shell is ready.",
    selectedAgentId: "",
    selectedCheckerName: "",
    currentUserLabel: "",
  };
  const SELECTED_AGENT_KEY = "agentguard.selectedAgentId";
  const SELECTED_CHECKER_KEY = "agentguard.selectedCheckerName";
  const CURRENT_USER_KEY = "agentguard.currentUserLabel";
  const AGENT_SELECTION_PATH = "/agents.html";
  const CHECKER_SELECTION_PATH = "/checkers.html";
  const AGENT_REQUIRED_PATHS = new Set([
    "/checkers.html",
    "/labels.html",
    "/rules.html",
    "/runtime.html",
  ]);
  const CHECKER_REQUIRED_PATHS = new Set([
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

  function redirectToCheckerSelection() {
    if (typeof window === "undefined" || !window.location) {
      return;
    }
    if (currentPath() === CHECKER_SELECTION_PATH) {
      return;
    }
    window.location.replace(CHECKER_SELECTION_PATH);
  }

  function enforceSelectedAgentAccess() {
    if (!state.selectedAgentId && isAgentRequiredPage()) {
      redirectToAgentSelection();
      return false;
    }
    if (!state.selectedCheckerName && CHECKER_REQUIRED_PATHS.has(currentPath())) {
      redirectToCheckerSelection();
      return false;
    }
    if (
      state.selectedCheckerName
      && state.selectedCheckerName !== "rule_based_check"
      && RULE_BASED_REQUIRED_PATHS.has(currentPath())
    ) {
      redirectToCheckerSelection();
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
    setText("sidebar-selected-checker", state.selectedCheckerName || "");
    setText("sidebar-current-user", state.currentUserLabel || "");

    const selectedAgentWrap = getElement("sidebar-selected-agent-wrap");
    const selectedCheckerWrap = getElement("sidebar-selected-checker-wrap");
    const selectedAgentPanel = getElement("sidebar-agent-panel");
    const clearSelectedAgentButton = getElement("sidebar-clear-agent");
    const selectedAgentValue = getElement("sidebar-selected-agent");
    if (selectedAgentWrap) {
      selectedAgentWrap.hidden = !state.selectedAgentId;
    }
    if (selectedAgentPanel) {
      selectedAgentPanel.hidden = !state.selectedAgentId;
    }
    if (selectedCheckerWrap) {
      selectedCheckerWrap.hidden = !state.selectedCheckerName;
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
      document.querySelectorAll("[data-checker-required='true']").forEach((element) => {
        element.hidden = !state.selectedAgentId || !state.selectedCheckerName;
      });
      document.querySelectorAll("[data-rule-based-required='true']").forEach((element) => {
        element.hidden = !state.selectedAgentId || state.selectedCheckerName !== "rule_based_check";
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

  function readSelectedCheckerName() {
    try {
      return String(window.localStorage?.getItem(SELECTED_CHECKER_KEY) || "").trim();
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
    state.selectedCheckerName = readSelectedCheckerName();
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

  function setSelectedChecker(checkerName) {
    const normalized = String(checkerName || "").trim();
    state.selectedCheckerName = normalized;
    try {
      if (normalized) {
        window.localStorage?.setItem(SELECTED_CHECKER_KEY, normalized);
      } else {
        window.localStorage?.removeItem(SELECTED_CHECKER_KEY);
      }
    } catch {
      // Ignore localStorage write issues in preview mode.
    }
    if (
      typeof window !== "undefined"
      && typeof window.dispatchEvent === "function"
      && typeof CustomEvent === "function"
    ) {
      window.dispatchEvent(new CustomEvent("agentguard:selected-checker-change", {
        detail: { checkerName: normalized },
      }));
    }
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
      setSelectedChecker("");
    }
    if (
      typeof window !== "undefined"
      && typeof window.dispatchEvent === "function"
      && typeof CustomEvent === "function"
    ) {
      window.dispatchEvent(new CustomEvent("agentguard:selected-agent-change", {
        detail: { agentId: normalized },
      }));
    }
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
    setSelectedChecker,
    setToolStatus,
  };
})();

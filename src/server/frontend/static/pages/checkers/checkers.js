(function () {
  const toolData = window.AgentGuardData;
  const shell = window.AgentGuardShell;
  const api = window.AgentGuardApi;

  const refreshButton = document.getElementById("refresh-checkers");
  const remoteCheckerList = document.getElementById("remote-checker-list");
  const localCheckerList = document.getElementById("local-checker-list");
  const remoteCheckerStatus = document.getElementById("remote-checker-status");
  const localCheckerStatus = document.getElementById("local-checker-status");
  const statusText = document.getElementById("checker-config-status");
  const nextStepStatus = document.getElementById("checker-next-step-status");
  const nextStepActions = document.getElementById("checker-next-step-actions");
  const selectedAgentLabel = document.getElementById("checker-selected-agent");

  const CHECKER_SCOPES = ["remote", "local"];
  const SCOPE_COPY = {
    remote: {
      availableKey: "remote_checkers",
      heading: "remote",
      empty: "No remote checkers are available for this agent yet.",
    },
    local: {
      availableKey: "local_checkers",
      heading: "local",
      empty: "No local checkers are available for this agent yet. Start a client config API to discover client-side checkers.",
    },
  };

  const state = {
    selectedAgentId: String(shell?.getState?.().selectedAgentId || "").trim(),
    selectedCheckerName: String(shell?.getState?.().selectedCheckerName || "").trim(),
    selections: {
      remote: [],
      local: [],
    },
    available: { remote_checkers: [], local_checkers: [] },
    config: null,
    loading: false,
  };

  shell?.setPageContext({
    title: "Checker Config",
    description: "Configure remote and local checker scopes for the selected agent.",
  });

  function showToast(message, tone) {
    window.AgentGuardUI.showToast(message, tone);
  }

  function scopeItems(scope) {
    const key = SCOPE_COPY[scope]?.availableKey || "remote_checkers";
    return Array.isArray(state.available[key]) ? state.available[key].slice() : [];
  }

  function scopeSelection(scope) {
    return toolData.collapseCheckerSelection(state.selections[scope] || []);
  }

  function activeCheckerNames() {
    return toolData.collapseCheckerSelection([
      ...scopeSelection("remote"),
      ...scopeSelection("local"),
    ]);
  }

  function updatePrimaryCheckerSelection() {
    const primary = toolData.primaryCheckerName(activeCheckerNames());
    state.selectedCheckerName = primary;
    return primary;
  }

  function renderActions() {
    nextStepActions.innerHTML = "";
    const backLink = document.createElement("a");
    backLink.className = "btn";
    backLink.href = "/agents.html";
    backLink.textContent = "Back To Agents";
    nextStepActions.appendChild(backLink);

    const activeNames = activeCheckerNames();
    const remoteNames = scopeSelection("remote");
    if (!activeNames.length) {
      nextStepStatus.textContent = "Enable at least one local or remote checker to unlock the next workspace.";
      return;
    }

    if (remoteNames.includes("rule_based_check")) {
      nextStepStatus.textContent = "Rule-based remote checker is active. You can now manage tool tags, publish rules, or inspect runtime.";
      [
        { href: "/labels.html", label: "Open Tags" },
        { href: "/rules.html", label: "Open Rules" },
        { href: "/runtime.html", label: "Open DashBoard" },
      ].forEach((item) => {
        const link = document.createElement("a");
        link.className = "btn primary";
        link.href = item.href;
        link.textContent = item.label;
        nextStepActions.appendChild(link);
      });
      return;
    }

    nextStepStatus.textContent = `${activeNames.join(", ")} active. This checker set unlocks the runtime dashboard.`;
    const runtimeLink = document.createElement("a");
    runtimeLink.className = "btn primary";
    runtimeLink.href = "/runtime.html";
    runtimeLink.textContent = "Open DashBoard";
    nextStepActions.appendChild(runtimeLink);
  }

  function renderScopeList(scope, container, statusNode) {
    if (!container) {
      return;
    }
    const copy = SCOPE_COPY[scope];
    const items = scopeItems(scope);
    const enabledNames = new Set(scopeSelection(scope));
    container.innerHTML = "";

    if (statusNode) {
      if (!state.selectedAgentId) {
        statusNode.textContent = `Select an agent to view ${copy.heading} checkers.`;
      } else if (!items.length) {
        statusNode.textContent = copy.empty;
      } else {
        statusNode.textContent = `${enabledNames.size} of ${items.length} ${copy.heading} checkers enabled.`;
      }
    }

    if (!items.length) {
      container.innerHTML = `<div class="empty-state">${copy.empty}</div>`;
      return;
    }

    items.forEach((checker) => {
      const card = document.createElement("div");
      const isEnabled = enabledNames.has(checker.name);
      const phaseText = checker.phases?.length ? checker.phases.join(", ") : "";
      const eventsText = checker.event_types.length ? checker.event_types.join(", ") : "";
      const pillText = phaseText || eventsText || "Phase not declared";
      const switchLabel = isEnabled ? "On" : "Off";
      const helperText = checker.description || "No checker description provided.";
      card.className = "agent-list-card checker-toggle-card";
      if (isEnabled) {
        card.classList.add("selected");
      }
      card.innerHTML = `
        <div class="checker-toggle-top">
          <div class="checker-toggle-copy">
            <div class="agent-list-top">
              <strong>${checker.name}</strong>
              <span class="pill">${pillText}</span>
            </div>
            <p class="subtle">${helperText}</p>
          </div>
          <label class="checker-switch" aria-label="Toggle ${checker.name}">
            <input
              type="checkbox"
              data-checker-name="${checker.name}"
              data-checker-scope="${scope}"
              ${isEnabled ? "checked" : ""}
              ${state.loading ? "disabled" : ""}
            >
            <span class="checker-switch-track">
              <span class="checker-switch-thumb"></span>
            </span>
            <span class="checker-switch-state">${switchLabel}</span>
          </label>
        </div>
      `;
      container.appendChild(card);
    });
  }

  function renderCheckerLists() {
    selectedAgentLabel.textContent = state.selectedAgentId || "the selected agent";
    renderScopeList("remote", remoteCheckerList, remoteCheckerStatus);
    renderScopeList("local", localCheckerList, localCheckerStatus);
    renderActions();
  }

  function renderStatus() {
    const remoteNames = scopeSelection("remote");
    const localNames = scopeSelection("local");
    const hasConfig = Boolean(state.config?.checker_config);
    const configSource = String(state.config?.config_source || "none").trim();
    if (!state.selectedAgentId) {
      statusText.textContent = "Select an agent first.";
      return;
    }
    if (state.loading) {
      statusText.textContent = `Updating checker config for ${state.selectedAgentId}...`;
      return;
    }
    if (remoteNames.length || localNames.length) {
      const sourceText = configSource === "server_default"
        ? "Using server default checker config"
        : "Current checkers";
      const remoteText = remoteNames.length ? remoteNames.join(", ") : "none";
      const localText = localNames.length ? localNames.join(", ") : "none";
      statusText.textContent = `${sourceText} for ${state.selectedAgentId}: remote [${remoteText}], local [${localText}].`;
      return;
    }
    if (!hasConfig) {
      statusText.textContent = `No checker config has been applied to ${state.selectedAgentId} yet.`;
      return;
    }
    if (configSource === "server_default") {
      statusText.textContent = `Using server default checker config for ${state.selectedAgentId}.`;
      return;
    }
    statusText.textContent = `Loaded checker config for ${state.selectedAgentId}.`;
  }

  async function loadCheckerState({ manual = false } = {}) {
    if (!state.selectedAgentId) {
      renderStatus();
      renderCheckerLists();
      return;
    }
    state.loading = true;
    refreshButton.disabled = true;
    statusText.textContent = manual ? "Refreshing checker catalog..." : "Loading checker catalog...";
    renderCheckerLists();
    let loadFailed = false;
    try {
      const [available, config] = await Promise.all([
        toolData.listAgentAvailableCheckers(state.selectedAgentId),
        toolData.getAgentCheckerConfig(state.selectedAgentId),
      ]);
      state.available = available;
      state.config = config;
      state.selections.remote = toolData.collapseCheckerSelection(
        toolData.selectedCheckersFromConfig(config, "remote"),
      );
      state.selections.local = toolData.collapseCheckerSelection(
        toolData.selectedCheckersFromConfig(config, "local"),
      );
      shell?.setSelectedChecker?.(updatePrimaryCheckerSelection());
      renderStatus();
      renderCheckerLists();
      if (manual) {
        showToast("Checker catalog refreshed.", "success");
      }
    } catch (error) {
      loadFailed = true;
      statusText.textContent = api.formatErrorMessage(error, "Failed to load checker catalog.");
      if (remoteCheckerList) {
        remoteCheckerList.innerHTML = `<div class="empty-state">${statusText.textContent}</div>`;
      }
      if (localCheckerList) {
        localCheckerList.innerHTML = `<div class="empty-state">${statusText.textContent}</div>`;
      }
      renderActions();
    } finally {
      state.loading = false;
      refreshButton.disabled = false;
      if (!loadFailed) {
        renderStatus();
        renderCheckerLists();
      }
    }
  }

  async function saveCheckerSelection(scope, nextCheckerNames) {
    if (!state.selectedAgentId) {
      return;
    }
    const previousSelections = {
      remote: [...state.selections.remote],
      local: [...state.selections.local],
    };
    state.selections[scope] = toolData.collapseCheckerSelection(nextCheckerNames);
    state.loading = true;
    refreshButton.disabled = true;
    renderStatus();
    renderCheckerLists();
    try {
      const enabledCheckers = scopeItems(scope).filter(
        (item) => state.selections[scope].includes(item.name),
      );
      const config = toolData.buildCheckerConfig(
        enabledCheckers,
        scopeItems(scope),
        state.config?.checker_config || null,
        scope,
      );
      await toolData.updateAgentCheckerConfig(state.selectedAgentId, config);
      state.config = {
        agent_id: state.selectedAgentId,
        checker_config: config,
        config_source: "agent_override",
      };
      shell?.setSelectedChecker?.(updatePrimaryCheckerSelection());
      renderStatus();
      renderCheckerLists();
      showToast("Checker config updated.", "success");
    } catch (error) {
      state.selections = previousSelections;
      updatePrimaryCheckerSelection();
      showToast(api.formatErrorMessage(error, "Failed to update checker config."), "warning");
    } finally {
      state.loading = false;
      refreshButton.disabled = false;
      renderStatus();
      renderCheckerLists();
    }
  }

  function handleCheckerToggle(event) {
    const target = event.target;
    if (!(target instanceof HTMLInputElement) || target.type !== "checkbox") {
      return;
    }
    const checkerName = String(target.dataset.checkerName || "").trim();
    const scope = String(target.dataset.checkerScope || "").trim();
    if (!checkerName || !CHECKER_SCOPES.includes(scope)) {
      return;
    }
    const next = new Set(state.selections[scope] || []);
    if (target.checked) {
      next.add(checkerName);
    } else {
      next.delete(checkerName);
    }
    saveCheckerSelection(scope, [...next]);
  }

  refreshButton?.addEventListener("click", () => {
    loadCheckerState({ manual: true });
  });

  remoteCheckerList?.addEventListener("change", handleCheckerToggle);
  localCheckerList?.addEventListener("change", handleCheckerToggle);

  window.addEventListener("agentguard:selected-agent-change", (event) => {
    state.selectedAgentId = String(event?.detail?.agentId || "").trim();
    state.selectedCheckerName = "";
    state.selections = { remote: [], local: [] };
    state.available = { remote_checkers: [], local_checkers: [] };
    state.config = null;
    loadCheckerState();
  });

  window.addEventListener("agentguard:selected-checker-change", (event) => {
    state.selectedCheckerName = String(event?.detail?.checkerName || "").trim();
    renderStatus();
  });

  renderStatus();
  renderCheckerLists();
  loadCheckerState();
})();

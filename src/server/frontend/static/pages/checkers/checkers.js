(function () {
  const toolData = window.AgentGuardData;
  const shell = window.AgentGuardShell;
  const api = window.AgentGuardApi;

  const refreshButton = document.getElementById("refresh-checkers");
  const checkerList = document.getElementById("checker-list");
  const statusText = document.getElementById("checker-config-status");
  const nextStepStatus = document.getElementById("checker-next-step-status");
  const nextStepActions = document.getElementById("checker-next-step-actions");
  const selectedAgentLabel = document.getElementById("checker-selected-agent");

  const state = {
    selectedAgentId: String(shell?.getState?.().selectedAgentId || "").trim(),
    selectedCheckerName: String(shell?.getState?.().selectedCheckerName || "").trim(),
    selectedCheckerNames: [],
    available: { remote_checkers: [], local_checkers: [] },
    config: null,
    loading: false,
  };

  shell?.setPageContext({
    title: "Checker Config",
    description: "Turn remote checkers on or off for the selected agent.",
  });

  function showToast(message, tone) {
    window.AgentGuardUI.showToast(message, tone);
  }

  function activeCheckerNames() {
    return toolData.expandCheckerSelection(state.selectedCheckerNames || []);
  }

  function activeCheckerSet() {
    return new Set(activeCheckerNames());
  }

  function visibleCheckerNames() {
    return activeCheckerNames();
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
    const names = visibleCheckerNames();
    if (!activeNames.length) {
      nextStepStatus.textContent = "Enable at least one checker to unlock the next workspace.";
      return;
    }

    if (activeNames.includes("rule_based_check")) {
      nextStepStatus.textContent = "Rule-based checker is active. You can now manage tool tags, publish rules, or inspect runtime.";
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

    nextStepStatus.textContent = `${names.join(", ")} active. This checker set unlocks the runtime dashboard.`;
    const runtimeLink = document.createElement("a");
    runtimeLink.className = "btn primary";
    runtimeLink.href = "/runtime.html";
    runtimeLink.textContent = "Open DashBoard";
    nextStepActions.appendChild(runtimeLink);
  }

  function renderCheckerList() {
    checkerList.innerHTML = "";
    selectedAgentLabel.textContent = state.selectedAgentId || "the selected agent";
    const items = Array.isArray(state.available.remote_checkers) ? state.available.remote_checkers.slice() : [];
    const enabledNames = activeCheckerSet();

    if (!items.length) {
      checkerList.innerHTML = '<div class="empty-state">No remote checkers are available for this agent yet.</div>';
      renderActions();
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
      checkerList.appendChild(card);
    });
    renderActions();
  }

  function renderStatus() {
    const activeNames = visibleCheckerNames();
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
    if (activeNames.length) {
      const sourceText = configSource === "server_default"
        ? "Using server default checker config"
        : "Current checkers";
      statusText.textContent = `${sourceText} for ${state.selectedAgentId}: ${activeNames.join(", ")}.`;
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
      renderCheckerList();
      return;
    }
    state.loading = true;
    refreshButton.disabled = true;
    statusText.textContent = manual ? "Refreshing checker catalog..." : "Loading checker catalog...";
    renderCheckerList();
    let loadFailed = false;
    try {
      const [available, config] = await Promise.all([
        toolData.listAgentAvailableCheckers(state.selectedAgentId),
        toolData.getAgentCheckerConfig(state.selectedAgentId),
      ]);
      state.available = available;
      state.config = config;
      state.selectedCheckerNames = toolData.collapseCheckerSelection(
        toolData.selectedCheckersFromConfig(config),
      );
      shell?.setSelectedChecker?.(updatePrimaryCheckerSelection());
      renderStatus();
      renderCheckerList();
      if (manual) {
        showToast("Checker catalog refreshed.", "success");
      }
    } catch (error) {
      loadFailed = true;
      statusText.textContent = api.formatErrorMessage(error, "Failed to load checker catalog.");
      checkerList.innerHTML = `<div class="empty-state">${statusText.textContent}</div>`;
      renderActions();
    } finally {
      state.loading = false;
      refreshButton.disabled = false;
      if (!loadFailed) {
        renderStatus();
        renderCheckerList();
      }
    }
  }

  async function saveCheckerSelection(nextCheckerNames) {
    if (!state.selectedAgentId) {
      return;
    }
    const previousCheckerNames = [...state.selectedCheckerNames];
    state.selectedCheckerNames = nextCheckerNames.slice();
    state.loading = true;
    refreshButton.disabled = true;
    renderStatus();
    renderCheckerList();
    try {
      const enabledCheckers = (state.available.remote_checkers || []).filter(
        (item) => nextCheckerNames.includes(item.name),
      );
      const config = toolData.buildCheckerConfig(
        enabledCheckers,
        state.available.remote_checkers || [],
        state.config?.checker_config || null,
      );
      await toolData.updateAgentCheckerConfig(state.selectedAgentId, config);
      state.config = {
        agent_id: state.selectedAgentId,
        checker_config: config,
        config_source: "agent_override",
      };
      shell?.setSelectedChecker?.(updatePrimaryCheckerSelection());
      renderStatus();
      renderCheckerList();
      showToast("Checker config updated.", "success");
    } catch (error) {
      state.selectedCheckerNames = previousCheckerNames;
      updatePrimaryCheckerSelection();
      showToast(api.formatErrorMessage(error, "Failed to update checker config."), "warning");
    } finally {
      state.loading = false;
      refreshButton.disabled = false;
      renderStatus();
      renderCheckerList();
    }
  }

  refreshButton?.addEventListener("click", () => {
    loadCheckerState({ manual: true });
  });

  checkerList?.addEventListener("change", (event) => {
    const target = event.target;
    if (!(target instanceof HTMLInputElement) || target.type !== "checkbox") {
      return;
    }
    const checkerName = String(target.dataset.checkerName || "").trim();
    if (!checkerName) {
      return;
    }
    const next = new Set(state.selectedCheckerNames || []);
    if (target.checked) {
      next.add(checkerName);
    } else {
      next.delete(checkerName);
    }
    saveCheckerSelection([...next]);
  });

  window.addEventListener("agentguard:selected-agent-change", (event) => {
    state.selectedAgentId = String(event?.detail?.agentId || "").trim();
    state.selectedCheckerName = "";
    state.selectedCheckerNames = [];
    state.available = { remote_checkers: [], local_checkers: [] };
    state.config = null;
    loadCheckerState();
  });

  window.addEventListener("agentguard:selected-checker-change", (event) => {
    state.selectedCheckerName = String(event?.detail?.checkerName || "").trim();
    renderStatus();
  });

  renderStatus();
  renderCheckerList();
  loadCheckerState();
})();

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
    available: { remote_checkers: [], local_checkers: [] },
    config: null,
    loading: false,
  };

  shell?.setPageContext({
    title: "Checker Selection",
    description: "Choose which checker workflow should be active for the selected agent.",
  });

  function showToast(message, tone) {
    window.AgentGuardUI.showToast(message, tone);
  }

  function selectedOption() {
    return (state.available.remote_checkers || []).find(
      (item) => String(item?.name || "").trim() === state.selectedCheckerName,
    ) || null;
  }

  function renderActions() {
    nextStepActions.innerHTML = "";
    const backLink = document.createElement("a");
    backLink.className = "btn";
    backLink.href = "/agents.html";
    backLink.textContent = "Back To Agents";
    nextStepActions.appendChild(backLink);

    const option = selectedOption();
    if (!option) {
      nextStepStatus.textContent = "Choose a checker to unlock the next workspace.";
      return;
    }

    if (option.name === "rule_based_check") {
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

    nextStepStatus.textContent = `${option.name} is active. This checker only unlocks runtime dashboard.`;
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

    if (!items.length) {
      checkerList.innerHTML = '<div class="empty-state">No remote checkers are available for this agent yet.</div>';
      renderActions();
      return;
    }

    items.forEach((checker) => {
      const card = document.createElement("div");
      card.className = "agent-list-card";
      if (checker.name === state.selectedCheckerName) {
        card.classList.add("selected");
      }
      const buttonLabel = checker.name === state.selectedCheckerName ? "Selected" : "Use This Checker";
      const eventsText = checker.event_types.length ? checker.event_types.join(", ") : "No event types declared.";
      card.innerHTML = `
        <div class="agent-list-top">
          <strong>${checker.name}</strong>
          <span class="pill">${eventsText}</span>
        </div>
        <p class="subtle">${checker.description || "No checker description provided."}</p>
        <div class="toolbar">
          <button class="btn primary" type="button" data-checker-name="${checker.name}">${buttonLabel}</button>
        </div>
      `;
      checkerList.appendChild(card);
    });
    renderActions();
  }

  function renderStatus() {
    const configStatus = String(state.config?.config_status || "none");
    const sessionCount = Number(state.config?.session_count || 0);
    if (!state.selectedAgentId) {
      statusText.textContent = "Select an agent first.";
      return;
    }
    if (configStatus === "mixed") {
      statusText.textContent = `Detected ${sessionCount} sessions with mixed checker configs. Saving here will align them to one checker flow.`;
      return;
    }
    if (configStatus === "consistent" && state.selectedCheckerName) {
      statusText.textContent = `Current checker for ${state.selectedAgentId}: ${state.selectedCheckerName}.`;
      return;
    }
    if (configStatus === "none") {
      statusText.textContent = `No checker config has been applied to ${state.selectedAgentId} yet.`;
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
    refreshButton.disabled = true;
    statusText.textContent = manual ? "Refreshing checker catalog..." : "Loading checker catalog...";
    try {
      const [available, config] = await Promise.all([
        toolData.listAgentAvailableCheckers(state.selectedAgentId),
        toolData.getAgentCheckerConfig(state.selectedAgentId),
      ]);
      state.available = available;
      state.config = config;
      const remoteNames = new Set((available.remote_checkers || []).map((item) => item.name));
      const inferred = toolData.selectedCheckerFromConfig(config);
      if (inferred && remoteNames.has(inferred)) {
        state.selectedCheckerName = inferred;
        shell?.setSelectedChecker?.(inferred);
      } else if (state.selectedCheckerName && !remoteNames.has(state.selectedCheckerName)) {
        state.selectedCheckerName = "";
        shell?.setSelectedChecker?.("");
      }
      renderStatus();
      renderCheckerList();
      if (manual) {
        showToast("Checker catalog refreshed.", "success");
      }
    } catch (error) {
      statusText.textContent = api.formatErrorMessage(error, "Failed to load checker catalog.");
      checkerList.innerHTML = `<div class="empty-state">${statusText.textContent}</div>`;
      renderActions();
    } finally {
      refreshButton.disabled = false;
    }
  }

  async function saveCheckerSelection(checkerName) {
    const checker = (state.available.remote_checkers || []).find((item) => item.name === checkerName);
    if (!checker || !state.selectedAgentId) {
      return;
    }
    refreshButton.disabled = true;
    try {
      const config = toolData.buildCheckerConfig(checker);
      await toolData.updateAgentCheckerConfig(state.selectedAgentId, config);
      state.selectedCheckerName = checker.name;
      shell?.setSelectedChecker?.(checker.name);
      renderStatus();
      renderCheckerList();
      showToast(`Applied checker ${checker.name}.`, "success");
    } catch (error) {
      showToast(api.formatErrorMessage(error, "Failed to update checker config."), "warning");
    } finally {
      refreshButton.disabled = false;
    }
  }

  refreshButton?.addEventListener("click", () => {
    loadCheckerState({ manual: true });
  });

  checkerList?.addEventListener("click", (event) => {
    const target = event.target;
    if (!(target instanceof HTMLElement)) {
      return;
    }
    const button = target.closest("[data-checker-name]");
    if (!(button instanceof HTMLElement)) {
      return;
    }
    saveCheckerSelection(String(button.dataset.checkerName || "").trim());
  });

  window.addEventListener("agentguard:selected-agent-change", (event) => {
    state.selectedAgentId = String(event?.detail?.agentId || "").trim();
    state.selectedCheckerName = "";
    state.available = { remote_checkers: [], local_checkers: [] };
    state.config = null;
    loadCheckerState();
  });

  window.addEventListener("agentguard:selected-checker-change", (event) => {
    state.selectedCheckerName = String(event?.detail?.checkerName || "").trim();
    renderStatus();
    renderCheckerList();
  });

  renderStatus();
  renderCheckerList();
  loadCheckerState();
})();

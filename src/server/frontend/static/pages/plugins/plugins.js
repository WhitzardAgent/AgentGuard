(function () {
  const toolData = window.AgentGuardData;
  const shell = window.AgentGuardShell;
  const api = window.AgentGuardApi;

  const refreshButton = document.getElementById("refresh-plugins");
  const remotePluginList = document.getElementById("remote-plugin-list");
  const localPluginList = document.getElementById("local-plugin-list");
  const remotePluginStatus = document.getElementById("remote-plugin-status");
  const localPluginStatus = document.getElementById("local-plugin-status");
  const statusText = document.getElementById("plugin-config-status");
  const selectedAgentLabel = document.getElementById("plugin-selected-agent");

  const PLUGIN_SCOPES = ["server", "client"];

  function copy(key, fallback, variables = {}) {
    return shell?.getPageCopy?.(key, fallback, variables) || String(fallback || "");
  }
  const SCOPE_COPY = {
    server: {
      availableKey: "remote_plugins",
      heading: "server",
      empty: copy("empty-server-plugins", "No server plugins are available for this agent yet."),
    },
    client: {
      availableKey: "local_plugins",
      heading: "client",
      empty: copy("empty-client-plugins", "No client plugins are available for this agent yet. Start a client config API to discover client-side plugins."),
    },
  };

  const state = {
    selectedAgentId: String(shell?.getState?.().selectedAgentId || "").trim(),
    selectedPluginName: String(shell?.getState?.().selectedPluginName || "").trim(),
    selections: {
      server: [],
      client: [],
    },
    available: { remote_plugins: [], local_plugins: [] },
    config: null,
    loading: false,
  };


  function showToast(message, tone) {
    window.AgentGuardUI.showToast(message, tone);
  }

  function selectedAgentDisplayName() {
    return String(shell?.getState?.().selectedAgentLabel || state.selectedAgentId || "").trim();
  }

  function scopeItems(scope) {
    const key = SCOPE_COPY[scope]?.availableKey || "remote_plugins";
    return Array.isArray(state.available[key]) ? state.available[key].slice() : [];
  }

  function scopeSelection(scope) {
    return toolData.collapsePluginSelection(state.selections[scope] || []);
  }

  function visibleScopeSelection(scope) {
    const visibleNames = new Set(scopeItems(scope).map((plugin) => plugin.name));
    return scopeSelection(scope).filter((name) => visibleNames.has(name));
  }

  function activePluginNames() {
    return toolData.collapsePluginSelection([
      ...visibleScopeSelection("server"),
      ...visibleScopeSelection("client"),
    ]);
  }

  function updatePrimaryPluginSelection() {
    const primary = toolData.primaryPluginName(activePluginNames());
    state.selectedPluginName = primary;
    return primary;
  }

  function renderScopeList(scope, container, statusNode) {
    if (!container) {
      return;
    }
    const scopeCopy = SCOPE_COPY[scope];
    const items = scopeItems(scope);
    const enabledNames = new Set(visibleScopeSelection(scope));
    container.innerHTML = "";

    if (statusNode) {
      if (!state.selectedAgentId) {
        statusNode.textContent = scopeCopy.heading === "server"
          ? copy("no-agent-server-plugins", "Select an agent to view server plugins.")
          : copy("no-agent-client-plugins", "Select an agent to view client plugins.");
      } else if (!items.length) {
        statusNode.textContent = scopeCopy.empty;
      } else {
        statusNode.textContent = `${enabledNames.size} of ${items.length} ${scopeCopy.heading} plugins enabled.`;
      }
    }

    if (!items.length) {
      const emptyText = !state.selectedAgentId
        ? (scopeCopy.heading === "server"
          ? copy("no-agent-server-plugins", "Select an agent to view server plugins.")
          : copy("no-agent-client-plugins", "Select an agent to view client plugins."))
        : scopeCopy.empty;
      container.innerHTML = `<div class="empty-state">${emptyText}</div>`;
      return;
    }

    items.forEach((plugin) => {
      const card = document.createElement("div");
      const isEnabled = enabledNames.has(plugin.name);
      const phaseText = plugin.phases?.length ? plugin.phases.join(", ") : "";
      const eventsText = plugin.event_types.length ? plugin.event_types.join(", ") : "";
      const pillText = phaseText || eventsText || copy("phase-not-declared", "Phase not declared");
      const switchLabel = isEnabled ? copy("switch-on", "On") : copy("switch-off", "Off");
      const helperText = plugin.description || copy("no-plugin-description", "No plugin description provided.");
      card.className = "agent-list-card plugin-toggle-card";
      if (isEnabled) {
        card.classList.add("selected");
      }
      card.innerHTML = `
        <div class="plugin-toggle-top">
          <div class="plugin-toggle-copy">
            <div class="agent-list-top">
              <strong>${plugin.name}</strong>
              <span class="pill plugin-phase-pill">${pillText}</span>
            </div>
            <p class="subtle">${helperText}</p>
          </div>
          <label class="plugin-switch" aria-label="Toggle plugin ${plugin.name}">
            <input
              type="checkbox"
              data-plugin-name="${plugin.name}"
              data-plugin-scope="${scope}"
              ${isEnabled ? "checked" : ""}
              ${state.loading ? "disabled" : ""}
            >
            <span class="plugin-switch-track">
              <span class="plugin-switch-thumb"></span>
            </span>
            <span class="plugin-switch-state">${switchLabel}</span>
          </label>
        </div>
      `;
      container.appendChild(card);
    });
  }

  function renderPluginLists() {
    selectedAgentLabel.textContent = selectedAgentDisplayName() || copy("selected-agent-fallback", "the selected agent");
    renderScopeList("server", remotePluginList, remotePluginStatus);
    renderScopeList("client", localPluginList, localPluginStatus);
  }

  function renderStatus() {
    const serverNames = visibleScopeSelection("server");
    const clientNames = visibleScopeSelection("client");
    const hasConfig = Boolean(state.config?.plugin_config);
    const configSource = String(state.config?.config_source || "none").trim();
    if (!state.selectedAgentId) {
      statusText.textContent = "Select an agent first.";
      return;
    }
    if (state.loading) {
      statusText.textContent = `Updating plugin config for ${selectedAgentDisplayName()}...`;
      return;
    }
    if (serverNames.length || clientNames.length) {
      const sourceText = configSource === "server_default"
        ? "Using server default plugin config"
        : "Current plugins";
      const serverText = serverNames.length ? serverNames.join(", ") : "none";
      const clientText = clientNames.length ? clientNames.join(", ") : "none";
      statusText.textContent = `${sourceText} for ${selectedAgentDisplayName()}: server [${serverText}], client [${clientText}].`;
      return;
    }
    if (!hasConfig) {
      statusText.textContent = `No plugin config has been applied to ${selectedAgentDisplayName()} yet.`;
      return;
    }
    if (configSource === "server_default") {
      statusText.textContent = `Using server default plugin config for ${selectedAgentDisplayName()}.`;
      return;
    }
    statusText.textContent = `Loaded plugin config for ${selectedAgentDisplayName()}.`;
  }

  async function loadPluginState({ manual = false } = {}) {
    if (!state.selectedAgentId) {
      renderStatus();
      renderPluginLists();
      return;
    }
    state.loading = true;
    refreshButton.disabled = true;
    statusText.textContent = manual ? "Refreshing plugin catalog..." : "Loading plugin catalog...";
    renderPluginLists();
    let loadFailed = false;
    try {
      const [available, config] = await Promise.all([
        toolData.listAgentAvailablePlugins(state.selectedAgentId),
        toolData.getAgentPluginConfig(state.selectedAgentId),
      ]);
      state.available = available;
      state.config = config;
      state.selections.server = toolData.collapsePluginSelection(
        toolData.selectedPluginsFromConfig(config, "server"),
      );
      state.selections.client = toolData.collapsePluginSelection(
        toolData.selectedPluginsFromConfig(config, "client"),
      );
      shell?.setSelectedPlugin?.(updatePrimaryPluginSelection());
      renderStatus();
      renderPluginLists();
      if (manual) {
        showToast("Plugin catalog refreshed.", "success");
      }
    } catch (error) {
      loadFailed = true;
      statusText.textContent = api.formatErrorMessage(error, "Failed to load plugin catalog.");
      if (remotePluginList) {
        remotePluginList.innerHTML = `<div class="empty-state">${statusText.textContent}</div>`;
      }
      if (localPluginList) {
        localPluginList.innerHTML = `<div class="empty-state">${statusText.textContent}</div>`;
      }
    } finally {
      state.loading = false;
      refreshButton.disabled = false;
      if (!loadFailed) {
        renderStatus();
        renderPluginLists();
      }
    }
  }

  async function savePluginSelection(scope, nextPluginNames) {
    if (!state.selectedAgentId) {
      return;
    }
    const previousSelections = {
      server: [...state.selections.server],
      client: [...state.selections.client],
    };
    state.selections[scope] = toolData.collapsePluginSelection(nextPluginNames);
    state.loading = true;
    refreshButton.disabled = true;
    renderStatus();
    renderPluginLists();
    try {
      const enabledPlugins = scopeItems(scope).filter(
        (item) => state.selections[scope].includes(item.name),
      );
      const config = toolData.buildPluginConfig(
        enabledPlugins,
        scopeItems(scope),
        state.config?.plugin_config || null,
        scope,
      );
      await toolData.updateAgentPluginConfig(state.selectedAgentId, config);
      state.config = {
        agent_id: state.selectedAgentId,
        plugin_config: config,
        config_source: "agent_override",
      };
      shell?.setSelectedPlugin?.(updatePrimaryPluginSelection());
      renderStatus();
      renderPluginLists();
      showToast("Plugin config updated.", "success");
    } catch (error) {
      state.selections = previousSelections;
      updatePrimaryPluginSelection();
      showToast(api.formatErrorMessage(error, "Failed to update plugin config."), "warning");
    } finally {
      state.loading = false;
      refreshButton.disabled = false;
      renderStatus();
      renderPluginLists();
    }
  }

  function handlePluginToggle(event) {
    const target = event.target;
    if (!(target instanceof HTMLInputElement) || target.type !== "checkbox") {
      return;
    }
    const pluginName = String(target.dataset.pluginName || "").trim();
    const scope = String(target.dataset.pluginScope || "").trim();
    if (!pluginName || !PLUGIN_SCOPES.includes(scope)) {
      return;
    }
    const next = new Set(state.selections[scope] || []);
    if (target.checked) {
      next.add(pluginName);
    } else {
      next.delete(pluginName);
    }
    savePluginSelection(scope, [...next]);
  }

  refreshButton?.addEventListener("click", () => {
    loadPluginState({ manual: true });
  });

  remotePluginList?.addEventListener("change", handlePluginToggle);
  localPluginList?.addEventListener("change", handlePluginToggle);

  window.addEventListener("agentguard:selected-agent-change", (event) => {
    state.selectedAgentId = String(event?.detail?.agentId || "").trim();
    state.selectedPluginName = "";
    state.selections = { server: [], client: [] };
    state.available = { remote_plugins: [], local_plugins: [] };
    state.config = null;
    loadPluginState();
  });

  window.addEventListener("agentguard:selected-plugin-change", (event) => {
    state.selectedPluginName = String(event?.detail?.pluginName || "").trim();
    renderStatus();
  });

  renderStatus();
  renderPluginLists();
  loadPluginState();
})();

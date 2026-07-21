(function () {
  const toolData = window.AgentGuardData;
  const shell = window.AgentGuardShell;
  const api = window.AgentGuardApi;

  const refreshAgentsButton = document.getElementById("refresh-agents");
  const agentSyncStatus = document.getElementById("agent-sync-status");
  const agentList = document.getElementById("agent-list");

  let agentCatalog = [];
  let selectedAgentId = shell?.getState?.().selectedAgentId || "";
  const deletingAgentIds = new Set();


  function showToast(message, tone) {
    window.AgentGuardUI.showToast(message, tone);
  }

  function copy(key, fallback, variables = {}) {
    return shell?.getPageCopy?.(key, fallback, variables) || String(fallback || "");
  }

  function updateSyncStatus(message) {
    agentSyncStatus.textContent = message;
  }

  function escapeHtml(value) {
    return String(value || "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function agentDisplayName(agent) {
    return String(agent?.display_agent_id || agent?.external_agent_id || agent?.agent_id || "").trim();
  }

  function agentSubtitle(agent, agentId) {
    const parts = [];
    const provider = String(agent?.external_provider || "").trim();
    const type = String(agent?.agent_type || "").trim();
    if (provider) {
      parts.push(provider);
    }
    if (type) {
      parts.push(type);
    }
    if (agentId && agentDisplayName(agent) !== agentId) {
      parts.push(`AgentGuard ${agentId}`);
    }
    return parts.join(" | ");
  }

  function canDeleteAgent(agent) {
    return String(agent?.external_provider || "").trim().toLowerCase() === "langchain";
  }

  function renderAgentList() {
    agentList.innerHTML = "";
    const items = Array.isArray(agentCatalog) ? agentCatalog.slice() : [];

    if (!items.length) {
      agentList.innerHTML = `<div class="empty-state">${escapeHtml(copy("empty-agent-list", "No agents are discoverable yet. Sync the tool catalog after agents register tools."))}</div>`;
      return;
    }

    items.forEach((agent) => {
      const agentId = String(agent?.agent_id || "").trim();
      const displayName = agentDisplayName(agent) || agentId;
      const subtitle = agentSubtitle(agent, agentId);
      const toolCount = Number(agent?.tool_count || 0);
      const skillCount = Number(agent?.skill_count || 0);
      const mcpCount = Number(agent?.mcp_count || 0);
      const toolPreviewText = Array.isArray(agent?.tool_names)
        ? agent.tool_names.join(", ")
        : "";
      const skillPreviewText = Array.isArray(agent?.skill_names)
        ? agent.skill_names.join(", ")
        : "";
      const mcpPreviewText = Array.isArray(agent?.mcp_names)
        ? agent.mcp_names.join(", ")
        : "";
      const showDelete = canDeleteAgent(agent);
      const card = document.createElement("div");
      card.className = "agent-list-card";
      if (agentId === selectedAgentId) {
        card.classList.add("selected");
      }
      if (deletingAgentIds.has(agentId)) {
        card.classList.add("pending-delete");
      }

      card.innerHTML = `
        <button class="agent-card-select" type="button" data-agent-action="select">
          <div class="agent-list-top">
            <div class="agent-list-heading">
              <strong>${escapeHtml(displayName)}</strong>
            </div>
            <div class="agent-list-counts" aria-label="Agent resource counts">
              <span class="pill agent-count-pill">${toolCount} tool${toolCount === 1 ? "" : "s"}</span>
              <span class="pill agent-count-pill">${skillCount} skill${skillCount === 1 ? "" : "s"}</span>
              <span class="pill agent-count-pill">${mcpCount} MCP${mcpCount === 1 ? "" : "s"}</span>
            </div>
          </div>
          ${subtitle ? `<p class="subtle">${escapeHtml(subtitle)}</p>` : ""}
          <p class="subtle">${escapeHtml(toolPreviewText || copy("no-tools-registered", "No tools registered."))}</p>
          <p class="subtle">${escapeHtml(skillPreviewText ? copy("skills-preview", "Skills: {items}", { items: skillPreviewText }) : copy("no-skills-registered", "No skills registered."))}</p>
          <p class="subtle">${escapeHtml(mcpPreviewText ? copy("mcps-preview", "MCP: {items}", { items: mcpPreviewText }) : copy("no-mcps-registered", "No MCP services registered."))}</p>
        </button>
        ${showDelete ? `
          <div class="agent-card-actions">
            <button class="link-button danger agent-delete-button" type="button" data-agent-action="delete" ${deletingAgentIds.has(agentId) ? "disabled" : ""}>
              ${deletingAgentIds.has(agentId) ? copy("deleting", "Deleting...") : copy("delete", "Delete")}
            </button>
          </div>
        ` : ""}
      `;

      card.querySelector('[data-agent-action="select"]')?.addEventListener("click", () => {
        shell?.setSelectedAgent?.(agentId);
        renderAgentList();
        showToast(copy("watching-agent", "Now watching {agent}.", { agent: displayName }), "success");
        if (typeof window !== "undefined" && window.location) {
          window.location.assign("/plugins.html");
        }
      });

      card.querySelector('[data-agent-action="delete"]')?.addEventListener("click", () => {
        deleteAgent(agent, displayName);
      });

      agentList.appendChild(card);
    });
  }

  async function deleteAgent(agent, displayName) {
    const agentId = String(agent?.agent_id || "").trim();
    if (!agentId || deletingAgentIds.has(agentId)) {
      return;
    }
    if (!canDeleteAgent(agent)) {
      showToast(copy("only-langchain-delete", "Only LangChain agents can be deleted from this page."), "warning");
      return;
    }
    const confirmed = window.confirm(
      copy("confirm-delete-agent", "Delete {agent}? This unregisters the agent and deletes its sessions.", { agent: displayName || agentId }),
    );
    if (!confirmed) {
      return;
    }

    deletingAgentIds.add(agentId);
    renderAgentList();
    updateSyncStatus(copy("deleting-agent", "Deleting {agent}...", { agent: displayName || agentId }));

    try {
      await api.fetchJson(`/api/agents/${encodeURIComponent(agentId)}`, {
        method: "DELETE",
      });
      agentCatalog = agentCatalog.filter((item) => String(item?.agent_id || "").trim() !== agentId);
      if (selectedAgentId === agentId) {
        selectedAgentId = "";
        shell?.setSelectedAgent?.("");
      }
      renderAgentList();
      showToast(copy("deleted-agent", "Deleted {agent}.", { agent: displayName || agentId }), "success");
      await refreshAgentCatalog();
    } catch (error) {
      showToast(api.formatErrorMessage(error, "Failed to delete agent."), "warning");
      renderAgentList();
      updateSyncStatus(copy("delete-agent-failed", "Delete failed. Agent catalog was not changed."));
    } finally {
      deletingAgentIds.delete(agentId);
      renderAgentList();
    }
  }

  async function refreshAgentCatalog({ manual = false } = {}) {
    refreshAgentsButton.disabled = true;
    updateSyncStatus(manual ? "Refreshing agent catalog..." : "Syncing agent catalog...");

    try {
      agentCatalog = await toolData.refreshAgentCatalog();
      const agentIds = toolData?.listAgentIds?.(agentCatalog) || [];
      if (selectedAgentId && !agentIds.includes(selectedAgentId)) {
        selectedAgentId = "";
        shell?.setSelectedAgent?.(selectedAgentId);
      }
      renderAgentList();
      const syncedAt = toolData.getLastAgentSyncTime();
      updateSyncStatus(`Synced ${agentIds.length} agents. Last updated: ${syncedAt || "just now"}`);
      shell?.setToolStatus(syncedAt ? `Last synced ${syncedAt}` : "Synced just now");
      if (manual) {
        showToast("Agent catalog refreshed.", "success");
      }
    } catch (error) {
      const cachedAt = toolData.getLastAgentSyncTime();
      updateSyncStatus(cachedAt
        ? `Showing cached catalog. Last successful sync: ${cachedAt}`
        : "Showing the built-in empty agent catalog fallback.");
      showToast(api.formatErrorMessage(error, "Failed to refresh agent catalog."), "warning");
      agentCatalog = toolData.loadAgentCatalog();
      shell?.setSelectedAgent?.(selectedAgentId);
      renderAgentList();
    } finally {
      refreshAgentsButton.disabled = false;
    }
  }

  refreshAgentsButton?.addEventListener("click", () => {
    refreshAgentCatalog({ manual: true });
  });

  window.addEventListener("agentguard:selected-agent-change", (event) => {
    selectedAgentId = String(event?.detail?.agentId || "").trim();
    renderAgentList();
  });

  agentCatalog = toolData.loadAgentCatalog();
  shell?.setSelectedAgent?.(selectedAgentId);
  renderAgentList();
  refreshAgentCatalog();
})();

(function () {
  const toolData = window.AgentGuardData;
  const shell = window.AgentGuardShell;
  const api = window.AgentGuardApi;

  const refreshAgentsButton = document.getElementById("refresh-agents");
  const agentSyncStatus = document.getElementById("agent-sync-status");
  const agentList = document.getElementById("agent-list");

  let agentCatalog = [];
  let selectedAgentId = shell?.getState?.().selectedAgentId || "";

  shell?.setPageContext({
    title: "Agent Selection",
    description: "Choose which registered agent you want to keep in view across the frontend.",
  });

  function showToast(message, tone) {
    window.AgentGuardUI.showToast(message, tone);
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

  function renderAgentList() {
    agentList.innerHTML = "";
    const items = Array.isArray(agentCatalog) ? agentCatalog.slice() : [];

    if (!items.length) {
      agentList.innerHTML = '<div class="empty-state">No agents are discoverable yet. Sync the tool catalog after agents register tools.</div>';
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
      const card = document.createElement("button");
      card.type = "button";
      card.className = "agent-list-card";
      if (agentId === selectedAgentId) {
        card.classList.add("selected");
      }

      card.innerHTML = `
        <div class="agent-list-top">
          <strong>${escapeHtml(displayName)}</strong>
          <span class="pill">${toolCount} tool${toolCount === 1 ? "" : "s"}</span>
          <span class="pill">${skillCount} skill${skillCount === 1 ? "" : "s"}</span>
          <span class="pill">${mcpCount} MCP${mcpCount === 1 ? "" : "s"}</span>
        </div>
        ${subtitle ? `<p class="subtle">${escapeHtml(subtitle)}</p>` : ""}
        <p class="subtle">${escapeHtml(toolPreviewText || "No tools registered.")}</p>
        <p class="subtle">${escapeHtml(skillPreviewText ? `Skills: ${skillPreviewText}` : "No skills registered.")}</p>
        <p class="subtle">${escapeHtml(mcpPreviewText ? `MCP: ${mcpPreviewText}` : "No MCP services registered.")}</p>
      `;

      card.addEventListener("click", () => {
        shell?.setSelectedAgent?.(agentId);
        renderAgentList();
        showToast(`Now watching ${displayName}.`, "success");
        if (typeof window !== "undefined" && window.location) {
          window.location.assign("/plugins.html");
        }
      });

      agentList.appendChild(card);
    });
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

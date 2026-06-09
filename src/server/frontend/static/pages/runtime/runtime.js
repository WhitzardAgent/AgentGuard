(function () {
  const REFRESH_INTERVALS = {
    fast: 2000,
    slow: 5000,
  };
  const api = window.AgentGuardApi;
  const shell = window.AgentGuardShell;
  const actionTone = window.AgentGuardUIHelpers?.actionTone || function fallbackActionTone(action) {
    const normalized = String(action || "").trim().toUpperCase();
    if (normalized === "DENY") {
      return "danger";
    }
    if (normalized === "HUMAN_CHECK" || normalized === "LLM_CHECK" || normalized === "DEGRADE") {
      return "warn";
    }
    return "";
  };

  const state = {
    health: null,
    agentStats: null,
    traffic: [],
    approvals: [],
    auditRows: [],
    selectedAuditIndex: 0,
    lastUpdatedAt: null,
    errors: {
      health: "",
      stats: "",
      traffic: "",
      approvals: "",
      audit: "",
    },
    actionInFlight: false,
  };

  const elements = {
    healthPill: document.getElementById("runtime-health-pill"),
    refreshButton: document.getElementById("runtime-refresh-button"),
    metricTotalRequests: document.getElementById("metric-total-requests"),
    metricDenyCount: document.getElementById("metric-deny-count"),
    metricPendingApprovals: document.getElementById("metric-pending-approvals"),
    metricDenyRate: document.getElementById("metric-deny-rate"),
    agentId: document.getElementById("runtime-agent-id"),
    ruleVersion: document.getElementById("runtime-rule-version"),
    mode: document.getElementById("runtime-mode"),
    runtimeMode: document.getElementById("runtime-runtime-mode"),
    uptime: document.getElementById("runtime-uptime"),
    timeline: document.getElementById("runtime-timeline"),
    approvalList: document.getElementById("runtime-approval-list"),
    auditBody: document.getElementById("runtime-audit-body"),
    auditDetail: document.getElementById("runtime-audit-detail"),
  };

  const pollers = [];

  shell?.setPageContext({
    title: "Runtime Overview",
    description: "Inspect agent-scoped runtime metrics, traffic, approvals, and audit activity for the selected agent.",
  });

  function getSelectedAgentId() {
    return String(shell?.getState?.().selectedAgentId || "").trim();
  }

  function showToast(message, tone) {
    window.AgentGuardUI.showToast(message, tone);
  }

  function formatAction(action) {
    return String(action || "unknown").toUpperCase();
  }

  function formatNumber(value) {
    if (typeof value !== "number" || Number.isNaN(value)) {
      return "--";
    }
    return value.toLocaleString("en-US");
  }

  function formatRisk(value) {
    if (typeof value !== "number" || Number.isNaN(value)) {
      return "--";
    }
    return value.toFixed(2);
  }

  function formatPercent(value) {
    if (typeof value !== "number" || Number.isNaN(value)) {
      return "--";
    }
    return `${(value * 100).toFixed(1)}%`;
  }

  function formatTimestamp(value) {
    if (typeof value !== "number" || Number.isNaN(value) || value <= 0) {
      return "--";
    }
    return new Date(value).toLocaleTimeString("zh-CN", {
      hour12: false,
    });
  }

  function formatUptime(seconds) {
    if (typeof seconds !== "number" || Number.isNaN(seconds)) {
      return "--";
    }
    if (seconds < 60) {
      return `${Math.round(seconds)}s`;
    }
    if (seconds < 3600) {
      return `${Math.floor(seconds / 60)}m ${Math.round(seconds % 60)}s`;
    }
    const hours = Math.floor(seconds / 3600);
    const minutes = Math.floor((seconds % 3600) / 60);
    return `${hours}h ${minutes}m`;
  }

  function escapeHtml(value) {
    return String(value || "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function fetchHealth() {
    return api.fetchJson("/api/health");
  }

  function fetchAgentStats() {
    const agentId = getSelectedAgentId();
    return api.fetchJson(`/api/agents/${encodeURIComponent(agentId)}/runtime/stats`);
  }

  function fetchTraffic({ n = 30, action = "", tool = "" } = {}) {
    const agentId = getSelectedAgentId();
    return api.fetchJson(`/api/agents/${encodeURIComponent(agentId)}/runtime/traffic${api.buildQuery({ n, action, tool })}`);
  }

  function fetchApprovals() {
    const agentId = getSelectedAgentId();
    return api.fetchJson(`/api/agents/${encodeURIComponent(agentId)}/runtime/approvals`);
  }

  function fetchAuditRecent({ n = 20 } = {}) {
    const agentId = getSelectedAgentId();
    return api.fetchJson(`/api/agents/${encodeURIComponent(agentId)}/runtime/audit/recent${api.buildQuery({ n })}`);
  }

  function approveTicket(ticketId, note = "") {
    return api.fetchJson(`/api/approvals/${encodeURIComponent(ticketId)}/approve`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ note }),
    });
  }

  function denyTicket(ticketId, note = "") {
    return api.fetchJson(`/api/approvals/${encodeURIComponent(ticketId)}/deny`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ note }),
    });
  }

  function normalizeTrafficItem(item) {
    const rules = Array.isArray(item?.rules) ? item.rules.map(String) : [];
    return {
      time: formatTimestamp(typeof item?.ts === "number" ? item.ts * 1000 : NaN),
      tool: String(item?.tool || "-"),
      action: String(item?.action || "unknown").toLowerCase(),
      session: String(item?.session || "-"),
      risk: typeof item?.risk === "number" ? item.risk : Number(item?.risk || 0),
      rules,
      reason: String(item?.reason || "").trim(),
    };
  }

  function approvalTargetSummary(event) {
    const target = event?.tool_call?.target;
    const args = event?.tool_call?.args;
    if (target && Object.keys(target).length) {
      return JSON.stringify(target);
    }
    if (args && Object.keys(args).length) {
      const previewEntries = Object.entries(args).slice(0, 2);
      return previewEntries.map(([key, value]) => `${key}=${String(value)}`).join(", ");
    }
    return "No target summary available.";
  }

  function normalizeApprovalItem(item) {
    const event = item?.event || {};
    const decision = item?.decision || {};
    const rules = Array.isArray(decision?.matched_rules) ? decision.matched_rules.map(String) : [];
    return {
      ticketId: String(item?.ticket_id || "-"),
      createdAt: formatTimestamp(typeof item?.created_ms === "number" ? item.created_ms : NaN),
      tool: String(event?.tool_call?.tool_name || "-"),
      agent: String(event?.principal?.agent_id || "-"),
      session: String(event?.principal?.session_id || "-"),
      action: String(decision?.action || "human_check").toLowerCase(),
      rules,
      reason: String(decision?.reason || "").trim(),
      targetSummary: approvalTargetSummary(event),
    };
  }

  function normalizeAuditRow(item) {
    const event = item?.event || {};
    const decision = item?.decision || {};
    const rules = Array.isArray(decision?.matched_rules) ? decision.matched_rules.map(String) : [];
    return {
      session: String(event?.principal?.session_id || "-"),
      agent: String(event?.principal?.agent_id || "-"),
      tool: String(event?.tool_call?.tool_name || "-"),
      action: String(decision?.action || "unknown").toLowerCase(),
      risk: typeof decision?.risk_score === "number" ? decision.risk_score : Number(decision?.risk_score || 0),
      matchedRules: rules,
      raw: item,
    };
  }

  function buildOverview() {
    const stats = state.agentStats || {};

    return {
      totalRequests: Number(stats.total_requests || 0),
      denyCount: Number(stats.deny_count || 0),
      pendingApprovals: state.approvals.length,
      denyRate: typeof stats.deny_rate === "number" ? stats.deny_rate : Number(stats.deny_rate || 0),
    };
  }

  function collectErrors() {
    return Object.values(state.errors).filter(Boolean);
  }

  function setStatusMessage() {
    const errors = collectErrors();
    if (errors.length === Object.keys(state.errors).length) {
      elements.healthPill.textContent = "Unreachable";
      elements.healthPill.className = "pill danger";
      return;
    }
    if (errors.length) {
      elements.healthPill.textContent = "Partial";
      elements.healthPill.className = "pill warn";
      return;
    }
    const updatedText = state.lastUpdatedAt
      ? new Date(state.lastUpdatedAt).toLocaleTimeString("zh-CN", { hour12: false })
      : "--";
    elements.healthPill.textContent = state.health?.ok ? "Healthy" : "Connected";
    elements.healthPill.className = "pill";
  }

  function renderOverview() {
    const overview = buildOverview();
    elements.metricTotalRequests.textContent = formatNumber(overview.totalRequests);
    elements.metricDenyCount.textContent = formatNumber(overview.denyCount);
    elements.metricPendingApprovals.textContent = formatNumber(overview.pendingApprovals);
    elements.metricDenyRate.textContent = formatPercent(overview.denyRate);
    elements.agentId.textContent = getSelectedAgentId() || "--";
    elements.ruleVersion.textContent = state.health?.rule_version || "--";
    elements.mode.textContent = state.health?.mode || "--";
    elements.runtimeMode.textContent = state.health?.runtime_mode || "--";
    elements.uptime.textContent = formatUptime(
      typeof state.agentStats?.uptime_s === "number" ? state.agentStats.uptime_s : state.health?.uptime_s,
    );
  }

  function renderTimeline() {
    elements.timeline.innerHTML = "";
    if (state.errors.traffic) {
      const error = document.createElement("div");
      error.className = "empty-state";
      error.textContent = state.errors.traffic;
      elements.timeline.appendChild(error);
      return;
    }
    if (!state.traffic.length) {
      const empty = document.createElement("div");
      empty.className = "empty-state";
      empty.textContent = "No recent traffic in the current runtime window.";
      elements.timeline.appendChild(empty);
      return;
    }

    state.traffic.forEach((item) => {
      const entry = document.createElement("div");
      entry.className = "timeline-item";
      const firstRule = item.rules[0] || "";
      const detailSegments = [
        `session=${item.session}`,
        `risk=${formatRisk(item.risk)}`,
      ];
      if (firstRule) {
        detailSegments.push(`matched=${firstRule}`);
      } else if (item.reason) {
        detailSegments.push(`reason=${item.reason}`);
      }
      entry.innerHTML = `
        <div class="timeline-time">${escapeHtml(item.time)}</div>
        <div>
          <div class="runtime-line">
            <strong>${escapeHtml(item.tool)} -> ${escapeHtml(formatAction(item.action))}</strong>
            <span class="pill ${actionTone(item.action)}">${escapeHtml(formatAction(item.action))}</span>
          </div>
          <p class="subtle">${escapeHtml(detailSegments.join(" | "))}</p>
        </div>
      `;
      elements.timeline.appendChild(entry);
    });
  }

  function renderApprovals() {
    elements.approvalList.innerHTML = "";
    if (state.errors.approvals) {
      const error = document.createElement("div");
      error.className = "empty-state";
      error.textContent = state.errors.approvals;
      elements.approvalList.appendChild(error);
      return;
    }

    if (!state.approvals.length) {
      const empty = document.createElement("div");
      empty.className = "empty-state";
      empty.textContent = "No pending human-check tickets right now.";
      elements.approvalList.appendChild(empty);
      return;
    }

    state.approvals.forEach((item) => {
      const row = document.createElement("div");
      row.className = "list-item";
      const matched = item.rules[0] ? `matched=${item.rules[0]}` : item.reason || "No rule detail";
      row.innerHTML = `
        <strong>${escapeHtml(item.ticketId)} | ${escapeHtml(item.tool)}</strong>
        <p class="subtle">agent=${escapeHtml(item.agent)} | session=${escapeHtml(item.session)} | created=${escapeHtml(item.createdAt)}</p>
        <p class="subtle">${escapeHtml(item.targetSummary)}</p>
        <p class="subtle">${escapeHtml(matched)}</p>
        <div class="toolbar runtime-approval-actions">
          <button class="btn primary" type="button" data-approval-action="approve" data-ticket-id="${escapeHtml(item.ticketId)}">Approve</button>
          <button class="btn" type="button" data-approval-action="deny" data-ticket-id="${escapeHtml(item.ticketId)}">Deny</button>
        </div>
      `;
      elements.approvalList.appendChild(row);
    });
  }

  function renderAuditTable() {
    elements.auditBody.innerHTML = "";
    if (state.errors.audit) {
      const row = document.createElement("tr");
      row.innerHTML = `<td colspan="6"><div class="empty-state">${escapeHtml(state.errors.audit)}</div></td>`;
      elements.auditBody.appendChild(row);
      elements.auditDetail.textContent = "Audit data is unavailable.";
      return;
    }
    if (!state.auditRows.length) {
      const row = document.createElement("tr");
      row.innerHTML = `<td colspan="6"><div class="empty-state">No audit records have been captured yet.</div></td>`;
      elements.auditBody.appendChild(row);
      elements.auditDetail.textContent = "No audit detail available.";
      return;
    }

    if (state.selectedAuditIndex >= state.auditRows.length) {
      state.selectedAuditIndex = 0;
    }

    state.auditRows.forEach((item, index) => {
      const row = document.createElement("tr");
      row.className = "runtime-audit-row";
      if (index === state.selectedAuditIndex) {
        row.classList.add("selected");
      }
      row.dataset.auditIndex = String(index);
      row.innerHTML = `
        <td>${escapeHtml(item.session)}</td>
        <td>${escapeHtml(item.agent)}</td>
        <td>${escapeHtml(item.tool)}</td>
        <td><span class="pill ${actionTone(item.action)}">${escapeHtml(formatAction(item.action))}</span></td>
        <td>${escapeHtml(formatRisk(item.risk))}</td>
        <td>${escapeHtml(item.matchedRules.join(", ") || "-")}</td>
      `;
      elements.auditBody.appendChild(row);
    });

    renderAuditDetail();
  }

  function renderAuditDetail() {
    const selected = state.auditRows[state.selectedAuditIndex];
    if (!selected) {
      elements.auditDetail.textContent = "Select an audit row to inspect event and decision JSON.";
      return;
    }
    const payload = {
      event: selected.raw?.event || {},
      decision: selected.raw?.decision || {},
      matched_rules: selected.matchedRules,
    };
    elements.auditDetail.textContent = JSON.stringify(payload, null, 2);
  }

  function renderAll() {
    renderOverview();
    renderTimeline();
    renderApprovals();
    renderAuditTable();
    setStatusMessage();
    elements.refreshButton.disabled = state.actionInFlight;
  }

  async function runSectionLoad(sectionName, loader, transform, assign) {
    try {
      const payload = await loader();
      assign(transform ? transform(payload) : payload);
      state.errors[sectionName] = "";
    } catch (error) {
      state.errors[sectionName] = error instanceof Error ? error.message : `Failed to load ${sectionName}.`;
      if (sectionName === "health") {
        state.health = null;
      } else if (sectionName === "stats") {
        state.agentStats = null;
      } else if (sectionName === "traffic") {
        state.traffic = [];
      } else if (sectionName === "approvals") {
        state.approvals = [];
      } else if (sectionName === "audit") {
        state.auditRows = [];
      }
    }
  }

  async function refreshOverview() {
    await Promise.all([
      runSectionLoad("health", fetchHealth, null, (payload) => {
        state.health = payload;
      }),
      runSectionLoad("stats", fetchAgentStats, null, (payload) => {
        state.agentStats = payload;
      }),
    ]);
    state.lastUpdatedAt = Date.now();
    renderAll();
  }

  async function refreshTraffic() {
    await runSectionLoad("traffic", () => fetchTraffic({ n: 30 }), (items) => {
      if (!Array.isArray(items)) {
        throw new Error("Traffic payload has an unexpected format.");
      }
      return items.map(normalizeTrafficItem);
    }, (items) => {
      state.traffic = items;
    });
    state.lastUpdatedAt = Date.now();
    renderAll();
  }

  async function refreshApprovals() {
    await runSectionLoad("approvals", fetchApprovals, (items) => {
      if (!Array.isArray(items)) {
        throw new Error("Approvals payload has an unexpected format.");
      }
      return items.map(normalizeApprovalItem);
    }, (items) => {
      state.approvals = items;
    });
    state.lastUpdatedAt = Date.now();
    renderAll();
  }

  async function refreshAudit() {
    await runSectionLoad("audit", () => fetchAuditRecent({ n: 20 }), (items) => {
      if (!Array.isArray(items)) {
        throw new Error("Audit payload has an unexpected format.");
      }
      return items.map(normalizeAuditRow);
    }, (items) => {
      state.auditRows = items;
    });
    state.lastUpdatedAt = Date.now();
    renderAll();
  }

  async function refreshAll() {
    await Promise.all([
      runSectionLoad("health", fetchHealth, null, (payload) => {
        state.health = payload;
      }),
      runSectionLoad("stats", fetchAgentStats, null, (payload) => {
        state.agentStats = payload;
      }),
      runSectionLoad("traffic", () => fetchTraffic({ n: 30 }), (items) => {
        if (!Array.isArray(items)) {
          throw new Error("Traffic payload has an unexpected format.");
        }
        return items.map(normalizeTrafficItem);
      }, (items) => {
        state.traffic = items;
      }),
      runSectionLoad("approvals", fetchApprovals, (items) => {
        if (!Array.isArray(items)) {
          throw new Error("Approvals payload has an unexpected format.");
        }
        return items.map(normalizeApprovalItem);
      }, (items) => {
        state.approvals = items;
      }),
      runSectionLoad("audit", () => fetchAuditRecent({ n: 20 }), (items) => {
        if (!Array.isArray(items)) {
          throw new Error("Audit payload has an unexpected format.");
        }
        return items.map(normalizeAuditRow);
      }, (items) => {
        state.auditRows = items;
      }),
    ]);
    state.lastUpdatedAt = Date.now();
    renderAll();
  }

  async function handleApprovalAction(ticketId, action) {
    if (!ticketId || state.actionInFlight) {
      return;
    }
    state.actionInFlight = true;
    renderAll();
    try {
      if (action === "approve") {
        await approveTicket(ticketId);
        showToast(`Approved ticket ${ticketId}.`, "success");
      } else {
        await denyTicket(ticketId);
        showToast(`Denied ticket ${ticketId}.`, "success");
      }
      await refreshAll();
    } catch (error) {
      showToast(error instanceof Error ? error.message : "Failed to resolve approval ticket.", "warning");
      renderAll();
    } finally {
      state.actionInFlight = false;
      renderAll();
    }
  }

  function startPolling() {
    pollers.push(window.setInterval(() => {
      refreshOverview().catch(() => {});
      refreshAudit().catch(() => {});
    }, REFRESH_INTERVALS.slow));

    pollers.push(window.setInterval(() => {
      refreshTraffic().catch(() => {});
      refreshApprovals().catch(() => {});
    }, REFRESH_INTERVALS.fast));
  }

  function handleSelectedAgentChange(event) {
    state.selectedAuditIndex = 0;
    shell?.setPageContext({
      title: "Runtime Overview",
      description: `Inspect agent-scoped runtime metrics, traffic, approvals, and audit activity for ${String(event?.detail?.agentId || getSelectedAgentId() || "the selected agent")}.`,
    });
    refreshAll().catch(() => {
      renderAll();
    });
  }

  function bindEvents() {
    elements.refreshButton.addEventListener("click", () => {
      refreshAll()
        .then(() => {
          showToast("Runtime data refreshed.", "success");
        })
        .catch((error) => {
          showToast(error instanceof Error ? error.message : "Failed to refresh runtime data.", "warning");
        });
    });

    elements.approvalList.addEventListener("click", (event) => {
      const target = event.target;
      if (!(target instanceof HTMLElement)) {
        return;
      }
      const button = target.closest("[data-approval-action]");
      if (!(button instanceof HTMLElement)) {
        return;
      }
      handleApprovalAction(
        String(button.dataset.ticketId || ""),
        String(button.dataset.approvalAction || ""),
      );
    });

    elements.auditBody.addEventListener("click", (event) => {
      const target = event.target;
      if (!(target instanceof HTMLElement)) {
        return;
      }
      const row = target.closest("[data-audit-index]");
      if (!(row instanceof HTMLElement)) {
        return;
      }
      const index = Number(row.dataset.auditIndex);
      if (Number.isNaN(index)) {
        return;
      }
      state.selectedAuditIndex = index;
      renderAuditTable();
    });

    window.addEventListener("agentguard:selected-agent-change", handleSelectedAgentChange);
  }

  bindEvents();
  renderAll();
  refreshAll().catch(() => {
    renderAll();
  });
  startPolling();

  window.AgentGuardRuntimeMonitor = {
    fetchHealth,
    fetchStats: fetchAgentStats,
    fetchAgentStats,
    fetchTraffic,
    fetchApprovals,
    approveTicket,
    denyTicket,
    fetchAuditRecent,
    refreshAll,
  };
})();

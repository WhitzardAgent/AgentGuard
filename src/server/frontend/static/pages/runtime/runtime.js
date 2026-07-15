(function () {
  const REFRESH_INTERVALS = {
    fast: 5000,
    slow: 15000,
  };
  const api = window.AgentGuardApi;
  const shell = window.AgentGuardShell;
  const i18n = window.AgentGuardI18n;
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
    sessions: [],
    approvals: [],
    auditRows: [],
    selectedAuditIndex: -1,
    lastUpdatedAt: null,
    errors: {
      health: "",
      stats: "",
      traffic: "",
      sessions: "",
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
    sessionBody: document.getElementById("runtime-session-body"),
    timeline: document.getElementById("runtime-timeline"),
    approvalList: document.getElementById("runtime-approval-list"),
    auditBody: document.getElementById("runtime-audit-body"),
    auditDetail: document.getElementById("runtime-audit-detail"),
  };

  const pollers = [];
  const pendingLoads = new Map();


  function getSelectedAgentId() {
    return String(shell?.getState?.().selectedAgentId || "").trim();
  }

  function getSelectedAgentLabel() {
    return String(shell?.getState?.().selectedAgentLabel || getSelectedAgentId() || "").trim();
  }

  function showToast(message, tone) {
    window.AgentGuardUI.showToast(message, tone);
  }

  function isPageVisible() {
    return typeof document === "undefined" || document.visibilityState !== "hidden";
  }

  function currentLocaleTag() {
    return i18n?.getLocale?.() || "en-US";
  }

  function copy(key, fallback, variables = {}) {
    return shell?.getPageCopy?.(key, fallback, variables) || String(fallback || "");
  }

  function formatAction(action) {
    return String(action || "unknown").toUpperCase();
  }

  function formatEventType(eventType) {
    const value = String(eventType || "").trim().toLowerCase();
    if (value === "tool_invoke") {
      return "Tool Invoke";
    }
    if (value === "tool_result") {
      return "Tool Result";
    }
    if (value === "llm_input") {
      return "LLM Input";
    }
    if (value === "llm_output") {
      return "LLM Output";
    }
    return value ? value.replace(/_/g, " ") : "Unknown";
  }

  function formatRuntimeSource(source) {
    const value = String(source || "").trim().toLowerCase();
    if (value === "mcp") {
      return "MCP Tool";
    }
    if (value === "tool") {
      return "Tool";
    }
    if (value === "llm") {
      return "LLM";
    }
    return value ? value.toUpperCase() : "Unknown";
  }

  function auditTypeDisplay(item) {
    const eventType = String(item?.eventType || item?.runtimeState?.event_type || "").trim().toLowerCase();
    if (eventType === "llm_input") {
      return { label: "LLM_Input", pill: "LLM", meta: "" };
    }
    if (eventType === "llm_output") {
      return { label: "LLM_Output", pill: "LLM", meta: "" };
    }
    if (eventType === "tool_invoke" || eventType === "tool_result") {
      return {
        label: String(item?.tool || "-").trim() || "-",
        pill: "Tool",
        meta: String(item?.toolLabel || "").trim(),
      };
    }
    return {
      label: formatEventType(eventType).replace(/\s+/g, "_"),
      pill: formatRuntimeSource(item?.sourceLabel),
      meta: String(item?.toolLabel || "").trim(),
    };
  }

  function formatNumber(value) {
    if (typeof value !== "number" || Number.isNaN(value)) {
      return "--";
    }
    return value.toLocaleString(currentLocaleTag());
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
    return new Date(value).toLocaleTimeString(currentLocaleTag(), {
      hour12: false,
    });
  }

  function formatDateTime(value) {
    if (!value) {
      return "--";
    }
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) {
      return "--";
    }
    return date.toLocaleString(currentLocaleTag(), {
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

  function stringifyDetailValue(value, fallback = "-") {
    if (value === undefined || value === null) {
      return fallback;
    }
    if (typeof value === "string") {
      const trimmed = value.trim();
      return trimmed || fallback;
    }
    return JSON.stringify(value, null, 2);
  }

  function parseJsonString(value) {
    if (typeof value !== "string") {
      return null;
    }
    const trimmed = value.trim();
    if (!trimmed || !/^[{\[]/.test(trimmed)) {
      return null;
    }
    try {
      return JSON.parse(trimmed);
    } catch {
      return null;
    }
  }

  function extractMessageContent(value) {
    if (value === undefined || value === null) {
      return "";
    }
    if (typeof value === "string") {
      return value.trim();
    }
    if (Array.isArray(value)) {
      return value
        .map(extractMessageContent)
        .filter(Boolean)
        .join("\n");
    }
    if (typeof value === "object") {
      const role = String(value.role || value.type || "").trim().toLowerCase();
      const toolCalls = Array.isArray(value.tool_calls) ? value.tool_calls : (
        Array.isArray(value.toolCalls) ? value.toolCalls : []
      );
      if ((role === "ai" || role === "assistant") && toolCalls.length) {
        return toolCalls.map(formatToolCallSummary).filter(Boolean).join("\n");
      }
      if ((role === "tool" || role === "toolresult" || role === "tool_result") && value.name) {
        const result = extractMessageContent(value.content || value.result || value.output);
        return result ? `[toolResult ${value.name}] ${result}` : `[toolResult ${value.name}]`;
      }
      const content = extractMessageContent(value.content);
      if (content) {
        return content;
      }
      const text = extractMessageContent(value.text);
      if (text) {
        return text;
      }
      const input = extractMessageContent(value.input);
      if (input) {
        return input;
      }
      if (value.data && typeof value.data === "object") {
        const dataContent = extractMessageContent(value.data.content);
        if (dataContent) {
          return dataContent;
        }
      }
      return JSON.stringify(value, null, 2);
    }
    return String(value).trim();
  }

  function formatToolCallSummary(toolCall) {
    if (!toolCall || typeof toolCall !== "object") {
      return "";
    }
    const name = String(toolCall.name || toolCall.tool_name || toolCall.function?.name || "tool").trim();
    const args = toolCall.args ?? toolCall.arguments ?? toolCall.function?.arguments ?? {};
    return `[toolCall ${name || "tool"}] ${stringifyDetailValue(args, "{}")}`;
  }

  function latestLlmInputMessage(messages) {
    if (!Array.isArray(messages) || !messages.length) {
      return null;
    }
    for (let index = messages.length - 1; index >= 0; index -= 1) {
      const message = messages[index];
      if (extractMessageContent(message) || stringifyDetailValue(message, "")) {
        return message;
      }
    }
    return messages[messages.length - 1];
  }

  function formatLlmInputMessages(messages) {
    const message = latestLlmInputMessage(messages);
    if (!message) {
      return "No LLM input content captured.";
    }
    const role = String(message?.role || message?.type || "message").trim();
    const content = extractMessageContent(message);
    return `${role}: ${content || stringifyDetailValue(message, "-")}`;
  }

  function extractToolCalls(value) {
    const source = parseJsonString(value) || value;
    if (!source || typeof source !== "object") {
      return [];
    }
    const data = source.data && typeof source.data === "object" ? source.data : source;
    if (Array.isArray(data.tool_calls)) {
      return data.tool_calls;
    }
    if (Array.isArray(data.toolCalls)) {
      return data.toolCalls;
    }
    return [];
  }

  function extractLlmOutputText(payload) {
    const candidates = [
      payload?.final_output,
      payload?.output,
      payload?.content,
      payload?.message,
      payload?.text,
      payload?.thought,
    ];
    for (const candidate of candidates) {
      const parsed = parseJsonString(candidate);
      const structured = parsed && typeof parsed === "object"
        ? (parsed.data && typeof parsed.data === "object" ? parsed.data : parsed)
        : (candidate && typeof candidate === "object"
          ? (candidate.data && typeof candidate.data === "object" ? candidate.data : candidate)
          : null);
      const structuredContent = structured
        ? extractMessageContent(structured.content || structured.final_output || structured.output || structured.message || structured.text)
        : "";
      const text = structured ? structuredContent : extractMessageContent(candidate);
      if (text) {
        return text;
      }
    }
    return "";
  }

  function auditExpansionContent(item) {
    const runtimeState = item?.runtimeState || {};
    const payload = runtimeState.payload && typeof runtimeState.payload === "object" ? runtimeState.payload : {};
    const eventType = String(runtimeState.event_type || item?.eventType || "").trim().toLowerCase();

    if (eventType === "llm_input") {
      return {
        label: "LLM Input",
        body: formatLlmInputMessages(payload.messages || item?.raw?.event?.payload?.messages || []),
      };
    }

    if (eventType === "llm_output") {
      const outputText = extractLlmOutputText(payload);
      const toolCalls = [
        ...extractToolCalls(payload.output),
        ...extractToolCalls(payload.final_output),
        ...extractToolCalls(payload),
      ];
      return {
        label: "LLM Output",
        body: outputText || (toolCalls.length ? "[Construct A Tool Invoke]" : "No LLM output content captured."),
      };
    }

    if (eventType === "tool_invoke") {
      const argsValue = runtimeState.arguments ?? item?.raw?.event?.tool_call?.args ?? item?.raw?.event?.payload?.arguments ?? {};
      return {
        label: "Tool Arguments",
        body: stringifyDetailValue(argsValue, "{}"),
      };
    }

    if (eventType === "tool_result") {
      const resultValue = runtimeState.result ?? item?.raw?.event?.tool_call?.result ?? item?.raw?.event?.payload?.result ?? null;
      return {
        label: "Tool Result",
        body: stringifyDetailValue(resultValue, "null"),
      };
    }

    return {
      label: "Event Payload",
      body: stringifyDetailValue(payload, "{}"),
    };
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

  function fetchSessions({ n = 50, status = "all" } = {}) {
    const agentId = getSelectedAgentId();
    return api.fetchJson(`/api/agents/${encodeURIComponent(agentId)}/runtime/sessions${api.buildQuery({ n, status })}`);
  }

  function closeRuntimeSession(sessionId) {
    const agentId = getSelectedAgentId();
    return api.fetchJson(
      `/api/agents/${encodeURIComponent(agentId)}/runtime/sessions/${encodeURIComponent(sessionId)}/close`,
      { method: "POST" },
    );
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
    const pluginSummary = Array.isArray(item?.plugin_summary) ? item.plugin_summary : [];
    return {
      time: formatTimestamp(typeof item?.ts === "number" ? item.ts * 1000 : NaN),
      tool: String(item?.tool || "-"),
      action: String(item?.action || "unknown").toLowerCase(),
      session: String(item?.session || "-"),
      risk: typeof item?.risk === "number" ? item.risk : Number(item?.risk || 0),
      rules,
      reason: String(item?.reason || "").trim(),
      pluginSummary: pluginSummary.map(normalizePluginSummaryItem).filter((entry) => entry.name),
    };
  }

  function normalizeSessionItem(item) {
    return {
      sessionId: String(item?.session_id || "-"),
      provider: String(item?.provider || "-"),
      externalSessionId: String(item?.external_session_id || "-"),
      externalAccountEmail: String(item?.external_account_email || "-"),
      status: String(item?.status || "unknown").toLowerCase(),
      closedAt: formatDateTime(item?.closed_at),
      activeTokenCount: Number(item?.active_token_count || 0),
      latestTokenExpiresAt: formatDateTime(item?.latest_token_expires_at),
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
    const runtimeState = item?.runtime_state && typeof item.runtime_state === "object" ? item.runtime_state : {};
    const rules = Array.isArray(decision?.matched_rules) ? decision.matched_rules.map(String) : [];
    const pluginSummary = Array.isArray(decision?.plugin_summary) ? decision.plugin_summary : [];
    const eventType = String(runtimeState?.event_type || event?.event_type || "").toLowerCase();
    const runtimeSource = String(runtimeState?.source || event?.tool_call?.source || "").toLowerCase();
    return {
      session: String(event?.principal?.session_id || "-"),
      agent: String(event?.principal?.agent_id || "-"),
      tool: String(event?.tool_call?.tool_name || "-"),
      toolLabel: String(runtimeState?.mcp?.mcp_tool_name || event?.tool_call?.mcp?.mcp_tool_name || "").trim(),
      sourceLabel: runtimeSource,
      eventType,
      action: String(decision?.action || "unknown").toLowerCase(),
      risk: typeof decision?.risk_score === "number" ? decision.risk_score : Number(decision?.risk_score || 0),
      matchedRules: rules,
      pluginSummary: pluginSummary.map(normalizePluginSummaryItem).filter((entry) => entry.name),
      runtimeState,
      raw: item,
    };
  }

  function normalizePluginSummaryItem(item) {
    return {
      name: String(item?.name || "").trim(),
      label: String(item?.label || "").trim(),
      prediction: item?.prediction,
      reason: String(item?.reason || item?.error || "").trim(),
      error: String(item?.error || "").trim(),
    };
  }

  function formatPluginSummary(items) {
    const summaries = Array.isArray(items) ? items : [];
    if (!summaries.length) {
      return "";
    }
    return summaries.map((item) => {
      const label = item.label ? `: ${item.label}` : "";
      const prediction = item.prediction === undefined || item.prediction === null ? "" : ` (${item.prediction})`;
      return `${item.name}${label}${prediction}`;
    }).join(", ");
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
      elements.healthPill.textContent = copy("runtime-unreachable", "Unreachable");
      elements.healthPill.className = "pill danger";
      return;
    }
    if (errors.length) {
      elements.healthPill.textContent = copy("runtime-partial", "Partial");
      elements.healthPill.className = "pill warn";
      return;
    }
    const updatedText = state.lastUpdatedAt
      ? new Date(state.lastUpdatedAt).toLocaleTimeString(currentLocaleTag(), { hour12: false })
      : "--";
    elements.healthPill.textContent = state.health?.ok ? copy("runtime-healthy", "Healthy") : copy("runtime-connected", "Connected");
    elements.healthPill.className = "pill";
  }

  function renderOverview() {
    const overview = buildOverview();
    elements.metricTotalRequests.textContent = formatNumber(overview.totalRequests);
    elements.metricDenyCount.textContent = formatNumber(overview.denyCount);
    elements.metricPendingApprovals.textContent = formatNumber(overview.pendingApprovals);
    elements.metricDenyRate.textContent = formatPercent(overview.denyRate);
    elements.agentId.textContent = getSelectedAgentLabel() || "--";
    elements.ruleVersion.textContent = state.health?.rule_version || "--";
    elements.mode.textContent = state.health?.mode || "--";
    elements.runtimeMode.textContent = state.health?.runtime_mode || "--";
    elements.uptime.textContent = formatUptime(
      typeof state.agentStats?.uptime_s === "number" ? state.agentStats.uptime_s : state.health?.uptime_s,
    );
  }

  function renderTimeline() {
    if (!elements.timeline) {
      return;
    }
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
      empty.textContent = copy("runtime-empty-traffic", "No recent traffic in the current runtime window.");
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
      const pluginText = formatPluginSummary(item.pluginSummary);
      if (firstRule) {
        detailSegments.push(`matched=${firstRule}`);
      } else if (pluginText) {
        detailSegments.push(`plugin=${pluginText}`);
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

  function renderSessions() {
    elements.sessionBody.innerHTML = "";
    if (state.errors.sessions) {
      const row = document.createElement("tr");
      row.innerHTML = `<td colspan="9"><div class="empty-state">${escapeHtml(state.errors.sessions)}</div></td>`;
      elements.sessionBody.appendChild(row);
      return;
    }
    if (!state.sessions.length) {
      const row = document.createElement("tr");
      row.innerHTML = `<td colspan="9"><div class="empty-state">${escapeHtml(copy("runtime-empty-sessions", "No runtime sessions have been created for this agent yet."))}</div></td>`;
      elements.sessionBody.appendChild(row);
      return;
    }

    state.sessions.forEach((item) => {
      const row = document.createElement("tr");
      const canClose = item.status === "active";
      row.innerHTML = `
        <td>${escapeHtml(item.sessionId)}</td>
        <td>${escapeHtml(item.provider)}</td>
        <td>${escapeHtml(item.externalSessionId)}</td>
        <td>${escapeHtml(item.externalAccountEmail)}</td>
        <td><span class="pill ${canClose ? "" : "muted"}">${escapeHtml(item.status.toUpperCase())}</span></td>
        <td>${escapeHtml(item.closedAt)}</td>
        <td>${escapeHtml(formatNumber(item.activeTokenCount))}</td>
        <td>${escapeHtml(item.latestTokenExpiresAt)}</td>
        <td>
          ${canClose ? `<button class="btn" type="button" data-session-action="close" data-session-id="${escapeHtml(item.sessionId)}">${escapeHtml(copy("runtime-close", "Close"))}</button>` : "-"}
        </td>
      `;
      elements.sessionBody.appendChild(row);
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
      empty.textContent = copy("runtime-empty-approvals", "No pending human-check tickets right now.");
      elements.approvalList.appendChild(empty);
      return;
    }

    state.approvals.forEach((item) => {
      const row = document.createElement("div");
      row.className = "list-item";
      const matched = item.rules[0] ? `matched=${item.rules[0]}` : item.reason || copy("runtime-no-rule-detail", "No rule detail");
      row.innerHTML = `
        <strong>${escapeHtml(item.ticketId)} | ${escapeHtml(item.tool)}</strong>
        <p class="subtle">agent=${escapeHtml(item.agent)} | session=${escapeHtml(item.session)} | created=${escapeHtml(item.createdAt)}</p>
        <p class="subtle">${escapeHtml(item.targetSummary)}</p>
        <p class="subtle">${escapeHtml(matched)}</p>
        <div class="toolbar runtime-approval-actions">
          <button class="btn primary" type="button" data-approval-action="approve" data-ticket-id="${escapeHtml(item.ticketId)}">${escapeHtml(copy("runtime-approve", "Approve"))}</button>
          <button class="btn" type="button" data-approval-action="deny" data-ticket-id="${escapeHtml(item.ticketId)}">${escapeHtml(copy("runtime-deny", "Deny"))}</button>
        </div>
      `;
      elements.approvalList.appendChild(row);
    });
  }

  function renderAuditTable() {
    elements.auditBody.innerHTML = "";
    if (state.errors.audit) {
      const row = document.createElement("tr");
      row.innerHTML = `<td colspan="5"><div class="empty-state">${escapeHtml(state.errors.audit)}</div></td>`;
      elements.auditBody.appendChild(row);
      renderAuditDetail();
      elements.auditDetail.textContent = copy("runtime-audit-unavailable", "Audit data is unavailable.");
      return;
    }
    if (!state.auditRows.length) {
      const row = document.createElement("tr");
      row.innerHTML = `<td colspan="5"><div class="empty-state">${escapeHtml(copy("runtime-empty-audit", "No audit records have been captured yet."))}</div></td>`;
      elements.auditBody.appendChild(row);
      renderAuditDetail();
      elements.auditDetail.textContent = copy("runtime-no-audit-detail", "No audit detail available.");
      return;
    }

    if (state.selectedAuditIndex >= state.auditRows.length) {
      state.selectedAuditIndex = -1;
    }

    state.auditRows.forEach((item, index) => {
      const row = document.createElement("tr");
      const typeDisplay = auditTypeDisplay(item);
      row.className = "runtime-audit-row";
      if (index === state.selectedAuditIndex) {
        row.classList.add("selected");
      }
      row.dataset.auditIndex = String(index);
      row.innerHTML = `
        <td>${escapeHtml(item.session)}</td>
        <td>${escapeHtml(item.agent)}</td>
        <td>
          <div class="runtime-tool-cell">
            <strong>${escapeHtml(typeDisplay.label)}</strong>
            <div class="runtime-tool-meta">
              <span class="pill runtime-source-pill">${escapeHtml(typeDisplay.pill)}</span>
              ${typeDisplay.meta ? `<span class="subtle">${escapeHtml(typeDisplay.meta)}</span>` : ""}
            </div>
          </div>
        </td>
        <td><span class="pill ${actionTone(item.action)}">${escapeHtml(formatAction(item.action))}</span></td>
        <td>${escapeHtml(item.matchedRules.join(", ") || "-")}</td>
      `;
      elements.auditBody.appendChild(row);
      if (index === state.selectedAuditIndex) {
        const expansion = auditExpansionContent(item);
        const detailRow = document.createElement("tr");
        detailRow.className = "runtime-audit-expanded-row";
        detailRow.innerHTML = `
          <td colspan="5">
            <div class="runtime-audit-expanded">
              <div class="runtime-detail-label">${escapeHtml(expansion.label)}</div>
              <div class="runtime-audit-expanded-body">${escapeHtml(expansion.body)}</div>
            </div>
          </td>
        `;
        elements.auditBody.appendChild(detailRow);
      }
    });

    renderAuditDetail();
  }

  function renderAuditDetail() {
    const selected = state.auditRows[state.selectedAuditIndex];
    if (!selected) {
      elements.auditDetail.textContent = copy("runtime-select-audit-detail", "Select an audit row to inspect event and decision JSON.");
      return;
    }
    const runtimeState = selected.runtimeState || {};
    const payload = {
      runtime_state: runtimeState,
      event: selected.raw?.event || {},
      decision: selected.raw?.decision || {},
      matched_rules: selected.matchedRules,
      plugin_summary: selected.pluginSummary,
      plugin_result: selected.raw?.decision?.plugin_result || {},
    };
    elements.auditDetail.textContent = JSON.stringify(payload, null, 2);
  }

  function renderAll() {
    renderOverview();
    renderSessions();
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
      } else if (sectionName === "sessions") {
        state.sessions = [];
      } else if (sectionName === "approvals") {
        state.approvals = [];
      } else if (sectionName === "audit") {
        state.auditRows = [];
      }
    }
  }

  async function runSectionLoadOnce(sectionName, loader, transform, assign) {
    if (pendingLoads.has(sectionName)) {
      return pendingLoads.get(sectionName);
    }
    const request = (async () => {
      try {
        await runSectionLoad(sectionName, loader, transform, assign);
      } finally {
        pendingLoads.delete(sectionName);
      }
    })();
    pendingLoads.set(sectionName, request);
    return request;
  }

  async function refreshOverview() {
    await Promise.all([
      runSectionLoadOnce("health", fetchHealth, null, (payload) => {
        state.health = payload;
      }),
      runSectionLoadOnce("stats", fetchAgentStats, null, (payload) => {
        state.agentStats = payload;
      }),
    ]);
    state.lastUpdatedAt = Date.now();
    renderAll();
  }

  async function refreshTraffic() {
    await runSectionLoadOnce("traffic", () => fetchTraffic({ n: 30 }), (items) => {
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

  async function refreshSessions() {
    await runSectionLoadOnce("sessions", () => fetchSessions({ n: 50, status: "all" }), (items) => {
      if (!Array.isArray(items)) {
        throw new Error("Sessions payload has an unexpected format.");
      }
      return items.map(normalizeSessionItem);
    }, (items) => {
      state.sessions = items;
    });
    state.lastUpdatedAt = Date.now();
    renderAll();
  }

  async function refreshApprovals() {
    await runSectionLoadOnce("approvals", fetchApprovals, (items) => {
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
    await runSectionLoadOnce("audit", () => fetchAuditRecent({ n: 20 }), (items) => {
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
      runSectionLoadOnce("health", fetchHealth, null, (payload) => {
        state.health = payload;
      }),
      runSectionLoadOnce("stats", fetchAgentStats, null, (payload) => {
        state.agentStats = payload;
      }),
      runSectionLoadOnce("sessions", () => fetchSessions({ n: 50, status: "all" }), (items) => {
        if (!Array.isArray(items)) {
          throw new Error("Sessions payload has an unexpected format.");
        }
        return items.map(normalizeSessionItem);
      }, (items) => {
        state.sessions = items;
      }),
      runSectionLoadOnce("approvals", fetchApprovals, (items) => {
        if (!Array.isArray(items)) {
          throw new Error("Approvals payload has an unexpected format.");
        }
        return items.map(normalizeApprovalItem);
      }, (items) => {
        state.approvals = items;
      }),
      runSectionLoadOnce("audit", () => fetchAuditRecent({ n: 20 }), (items) => {
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

  async function handleSessionAction(sessionId) {
    if (!sessionId || state.actionInFlight) {
      return;
    }
    state.actionInFlight = true;
    renderAll();
    try {
      await closeRuntimeSession(sessionId);
      showToast(`Closed session ${sessionId}.`, "success");
      await refreshAll();
    } catch (error) {
      showToast(error instanceof Error ? error.message : "Failed to close runtime session.", "warning");
      renderAll();
    } finally {
      state.actionInFlight = false;
      renderAll();
    }
  }

  function startPolling() {
    pollers.push(window.setInterval(() => {
      if (!isPageVisible()) {
        return;
      }
      refreshOverview().catch(() => {});
      refreshSessions().catch(() => {});
      refreshAudit().catch(() => {});
    }, REFRESH_INTERVALS.slow));

    pollers.push(window.setInterval(() => {
      if (!isPageVisible()) {
        return;
      }
      refreshApprovals().catch(() => {});
    }, REFRESH_INTERVALS.fast));
  }

  function handleSelectedAgentChange(event) {
    state.selectedAuditIndex = -1;
    shell?.setPageContext({
      title: "Runtime Overview",
      description: `Inspect agent-scoped runtime metrics, traffic, approvals, and audit activity for ${String(event?.detail?.agentLabel || getSelectedAgentLabel() || "the selected agent")}.`,
    });
    refreshAll().catch(() => {
      renderAll();
    });
  }

  function bindEvents() {
    elements.refreshButton.addEventListener("click", () => {
      refreshAll()
        .then(() => {
          showToast(copy("runtime-refresh-success", "Runtime data refreshed."), "success");
        })
        .catch((error) => {
          showToast(error instanceof Error ? error.message : copy("runtime-refresh-failed", "Failed to refresh runtime data."), "warning");
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

    elements.sessionBody.addEventListener("click", (event) => {
      const target = event.target;
      if (!(target instanceof HTMLElement)) {
        return;
      }
      const button = target.closest("[data-session-action]");
      if (!(button instanceof HTMLElement)) {
        return;
      }
      handleSessionAction(String(button.dataset.sessionId || ""));
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
      state.selectedAuditIndex = state.selectedAuditIndex === index ? -1 : index;
      renderAuditTable();
    });

    window.addEventListener("agentguard:selected-agent-change", handleSelectedAgentChange);
    if (typeof document !== "undefined" && typeof document.addEventListener === "function") {
      document.addEventListener("visibilitychange", () => {
        if (isPageVisible()) {
          refreshAll().catch(() => {
            renderAll();
          });
        }
      });
    }
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
    fetchSessions,
    closeRuntimeSession,
    fetchApprovals,
    approveTicket,
    denyTicket,
    fetchAuditRecent,
    refreshAll,
    auditExpansionContent,
  };
})();

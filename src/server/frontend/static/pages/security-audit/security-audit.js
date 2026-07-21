(function () {
  const shell = window.AgentGuardShell;
  const i18n = window.AgentGuardI18n;
  const LLM_CONFIG_STORAGE_KEY = "agentguard.securityAuditLlmConfig";
  const state = {
    selectedAgentId: String(shell?.getState?.().selectedAgentId || window.localStorage.getItem("agentguard.selectedAgentId") || "").trim(),
    runs: [],
    selectedRunId: "",
    polling: null,
    llmConfig: loadStoredLlmConfig(),
  };
  const elements = {
    selectedAgent: document.getElementById("security-audit-selected-agent"),
    auditor: document.getElementById("security-audit-auditor"),
    start: document.getElementById("security-audit-start"),
    end: document.getElementById("security-audit-end"),
    run: document.getElementById("security-audit-run"),
    refresh: document.getElementById("security-audit-refresh"),
    message: document.getElementById("security-audit-message"),
    runs: document.getElementById("security-audit-runs"),
    report: document.getElementById("security-audit-report"),
    findings: document.getElementById("security-audit-findings"),
    latestStatus: document.getElementById("security-audit-latest-status"),
    latestRisk: document.getElementById("security-audit-latest-risk"),
    latestEvents: document.getElementById("security-audit-latest-events"),
    settingsOpen: document.getElementById("security-audit-settings-open"),
    settingsBackdrop: document.getElementById("security-audit-settings-backdrop"),
    settingsClose: document.getElementById("security-audit-settings-close"),
    settingsCancel: document.getElementById("security-audit-settings-cancel"),
    settingsSave: document.getElementById("security-audit-settings-save"),
    settingsStatus: document.getElementById("security-audit-settings-status"),
    llmModel: document.getElementById("security-audit-llm-model"),
    llmBaseUrl: document.getElementById("security-audit-llm-base-url"),
    llmApiKey: document.getElementById("security-audit-llm-api-key"),
    llmTimeout: document.getElementById("security-audit-llm-timeout"),
    llmChunkEvents: document.getElementById("security-audit-llm-chunk-events"),
  };

  function t(value, variables = {}) {
    return i18n?.t?.(value, variables) || value;
  }

  function optionalNumber(value, min, max) {
    const text = String(value ?? "").trim();
    if (!text) return null;
    const number = Number(text);
    if (!Number.isFinite(number) || number < min || number > max) return null;
    return number;
  }

  function normalizeLlmConfig(input) {
    const config = input && typeof input === "object" ? input : {};
    return {
      model: String(config.model || "").trim(),
      base_url: String(config.base_url || "").trim(),
      api_key: String(config.api_key || "").trim(),
      timeout_s: optionalNumber(config.timeout_s, 1, 600),
      chunk_events: optionalNumber(config.chunk_events, 1, 500),
    };
  }

  function loadStoredLlmConfig() {
    try {
      return normalizeLlmConfig(JSON.parse(window.localStorage.getItem(LLM_CONFIG_STORAGE_KEY) || "{}"));
    } catch {
      return normalizeLlmConfig({});
    }
  }

  function requestLlmConfig() {
    const normalized = normalizeLlmConfig(state.llmConfig);
    const payload = {};
    Object.entries(normalized).forEach(([key, value]) => {
      if (value !== "" && value !== null) payload[key] = value;
    });
    return Object.keys(payload).length ? payload : null;
  }

  function hydrateSettings() {
    const config = normalizeLlmConfig(state.llmConfig);
    elements.llmModel.value = config.model;
    elements.llmBaseUrl.value = config.base_url;
    elements.llmApiKey.value = config.api_key;
    elements.llmTimeout.value = config.timeout_s ?? "";
    elements.llmChunkEvents.value = config.chunk_events ?? "";
    elements.settingsStatus.textContent = requestLlmConfig()
      ? t("Browser override configured")
      : t("Using server defaults");
  }

  function openSettings() {
    hydrateSettings();
    elements.settingsBackdrop.hidden = false;
    elements.llmModel.focus();
  }

  function closeSettings() {
    elements.settingsBackdrop.hidden = true;
  }

  function saveSettings() {
    state.llmConfig = normalizeLlmConfig({
      model: elements.llmModel.value,
      base_url: elements.llmBaseUrl.value,
      api_key: elements.llmApiKey.value,
      timeout_s: elements.llmTimeout.value,
      chunk_events: elements.llmChunkEvents.value,
    });
    window.localStorage.setItem(LLM_CONFIG_STORAGE_KEY, JSON.stringify(state.llmConfig));
    elements.message.textContent = t("LLM settings saved in this browser.");
    closeSettings();
  }

  async function json(url, options = {}) {
    const response = await fetch(url, {
      credentials: "same-origin",
      headers: { "Accept": "application/json", "Content-Type": "application/json", ...(options.headers || {}) },
      ...options,
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(payload.detail || payload.error || `HTTP ${response.status}`);
    return payload;
  }

  function option(select, value, label) {
    const item = document.createElement("option");
    item.value = value;
    item.textContent = label;
    select.appendChild(item);
  }

  function formatDate(value) {
    if (!value) return "—";
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleString(i18n?.getLocale?.(), {
      timeZone: "Asia/Shanghai",
      hour12: false,
    });
  }

  function beijingLocalToIso(value) {
    const matched = String(value || "").match(/^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})$/);
    if (!matched) return "";
    const [, year, month, day, hour, minute] = matched;
    return new Date(Date.UTC(
      Number(year),
      Number(month) - 1,
      Number(day),
      Number(hour) - 8,
      Number(minute),
    )).toISOString();
  }

  function elapsedSeconds(run) {
    const start = new Date(run.started_at || run.created_at || "").getTime();
    const end = new Date(run.completed_at || Date.now()).getTime();
    if (!Number.isFinite(start) || !Number.isFinite(end) || end < start) return 0;
    return Math.round((end - start) / 1000);
  }

  function formatDuration(run) {
    const seconds = elapsedSeconds(run);
    if (seconds < 60) return t("{seconds}s", { seconds });
    const minutes = Math.floor(seconds / 60);
    const remaining = seconds % 60;
    return t("{minutes}m {seconds}s", { minutes, seconds: remaining });
  }

  function escapeHtml(value) {
    return String(value ?? "").replace(/[&<>"']/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[char]);
  }

  function modeLabel(value) {
    return t({
      rule_agent_security: "Rules only",
      llm_agent_security: "LLM only",
      hybrid_agent_security: "Rules + LLM",
    }[value] || value || "—");
  }

  function statusLabel(value) {
    return t({ queued: "Queued", running: "Running", completed: "Completed", failed: "Failed" }[value] || value || "—");
  }

  function stageLabel(value, status = "") {
    const effective = value || (status === "completed" || status === "failed" ? status : "");
    return t({
      collecting_traces: "Collecting traces",
      preparing_sessions: "Preparing sessions",
      rule_analysis: "Running deterministic rules",
      llm_analysis: "Running LLM analysis",
      saving_report: "Saving report",
      interrupted: "Interrupted by server restart",
      completed: "Report ready",
      failed: "Audit failed",
    }[effective] || effective || "Waiting to start");
  }

  function riskLabel(value) {
    return t({ critical: "Critical", high: "High", warning: "Warning", ok: "OK" }[value] || value || "—");
  }

  async function loadCatalogs() {
    const [me, agents, auditors] = await Promise.all([
      json("/api/user/me"), json("/api/agents"), json("/api/security-audits/auditors"),
    ]);
    if (me.user?.is_admin !== true) {
      window.location.replace("/home.html");
      return false;
    }
    if (!state.selectedAgentId) {
      window.location.replace("/agents.html");
      return false;
    }
    const agent = agents.find((item) => item.agent_id === state.selectedAgentId);
    if (!agent) {
      window.location.replace("/agents.html");
      return false;
    }
    elements.selectedAgent.textContent = agent.display_agent_id || agent.name || agent.agent_id;
    elements.auditor.innerHTML = "";
    (auditors.auditors || []).forEach((auditor) => option(
      elements.auditor,
      auditor.name,
      `${modeLabel(auditor.name)} — ${t(auditor.description)}`,
    ));
    elements.auditor.value = "hybrid_agent_security";
    return true;
  }

  async function loadRuns() {
    const payload = await json(`/api/security-audits?agent_id=${encodeURIComponent(state.selectedAgentId)}`);
    state.runs = payload.runs || [];
    if (state.selectedRunId && !state.runs.some((run) => run.run_id === state.selectedRunId)) {
      state.selectedRunId = "";
    }
    if (!state.selectedRunId && state.runs.length) state.selectedRunId = state.runs[0].run_id;
    renderRuns();
    renderLatestMetrics();
    if (state.selectedRunId) await selectRun(state.selectedRunId, false);
    const active = state.runs.some((run) => run.status === "queued" || run.status === "running");
    if (active && !state.polling) state.polling = window.setInterval(() => loadRuns().catch(showError), 2000);
    if (!active && state.polling) { window.clearInterval(state.polling); state.polling = null; }
  }

  function renderLatestMetrics() {
    const latest = state.runs[0];
    elements.latestStatus.textContent = latest ? statusLabel(latest.status) : "—";
    elements.latestRisk.textContent = latest ? riskLabel(latest.risk_level) : "—";
    elements.latestEvents.textContent = String(latest?.trace_count || 0);
  }

  function renderRuns() {
    elements.runs.innerHTML = "";
    if (!state.runs.length) {
      elements.runs.innerHTML = `<tr><td colspan="6" class="subtle">${escapeHtml(t("No security audit runs yet."))}</td></tr>`;
      return;
    }
    state.runs.forEach((run) => {
      const row = document.createElement("tr");
      row.className = `security-audit-row${run.run_id === state.selectedRunId ? " is-selected" : ""}`;
      row.dataset.runId = run.run_id;
      row.innerHTML = `
        <td>${escapeHtml(formatDate(run.created_at))}</td>
        <td>${escapeHtml(modeLabel(run.auditor_name))}</td>
        <td><div class="security-audit-status-cell"><strong>${escapeHtml(statusLabel(run.status))}</strong><span class="security-audit-stage">${escapeHtml(stageLabel(run.progress_stage, run.status))}</span></div></td>
        <td><span class="pill">${escapeHtml(riskLabel(run.risk_level))}</span></td>
        <td><div class="security-audit-coverage">${escapeHtml(t("{users} users · {sessions} sessions", { users: run.user_count || 0, sessions: run.session_count || 0 }))}<br>${escapeHtml(t("{events} trace events", { events: run.trace_count || 0 }))}</div></td>
        <td>${escapeHtml(formatDuration(run))}</td>`;
      row.addEventListener("click", () => selectRun(run.run_id));
      elements.runs.appendChild(row);
    });
  }

  async function selectRun(runId, refreshRows = true) {
    state.selectedRunId = runId;
    if (refreshRows) renderRuns();
    const [run, findingPayload] = await Promise.all([
      json(`/api/security-audits/${encodeURIComponent(runId)}`),
      json(`/api/security-audits/${encodeURIComponent(runId)}/findings`),
    ]);
    const summary = run.summary || {};
    const auditScope = summary.metadata?.audit_scope || {};
    elements.report.textContent = [
      `${t("Status")}: ${statusLabel(run.status)}`,
      `${t("Stage")}: ${stageLabel(run.progress_stage, run.status)}`,
      `${t("Risk")}: ${riskLabel(run.risk_level)}`,
      `${t("Audit mode")}: ${modeLabel(run.auditor_name)}`,
      `${t("Model")}: ${run.model || t("Not configured")}`,
      `${t("Duration")}: ${formatDuration(run)}`,
      `${t("Coverage")}: ${t("{users} users · {sessions} sessions · {events} events", { users: run.user_count || 0, sessions: run.session_count || 0, events: run.trace_count || 0 })}`,
      auditScope.coverage === "full_snapshot" ? t("Input scope: complete stable snapshot for this agent") : "",
      run.status === "running" && run.progress_stage === "llm_analysis" ? t("The model is reviewing session evidence and the final aggregate report. This can take several minutes.") : "",
      run.error_message ? `${t("Error")}: ${run.error_message}` : "",
      summary.summary ? `\n${summary.summary}` : "",
      ...(summary.limitations || []).map((item) => `${t("Limitation")}: ${item}`),
    ].filter(Boolean).join("\n");
    renderFindings(findingPayload.findings || []);
  }

  function renderFindings(findings) {
    elements.findings.innerHTML = "";
    if (!findings.length) {
      elements.findings.innerHTML = `<div class="empty-state">${escapeHtml(t("No findings recorded."))}</div>`;
      return;
    }
    findings.forEach((finding) => {
      const item = document.createElement("article");
      item.className = "security-audit-finding";
      item.dataset.severity = finding.severity || "ok";
      const evidence = (finding.evidence || []).map((entry) => (
        `${t("Session")}: ${entry.session_id}\n${t("Events")}: ${(entry.event_ids || []).join(", ") || "—"}`
      )).join("\n\n");
      item.innerHTML = `
        <div class="security-audit-finding-meta">
          <span class="pill">${escapeHtml(riskLabel(finding.severity))}</span>
          <span class="pill">${escapeHtml(t(finding.verification === "confirmed" ? "Confirmed" : "Needs review"))}</span>
          <span class="pill">${escapeHtml(t(finding.source))}</span>
          <span class="pill">${escapeHtml(t(finding.category))}</span>
        </div>
        <h4>${escapeHtml(t(finding.title))}</h4>
        <p>${escapeHtml(t(finding.description))}</p>
        <div class="security-audit-evidence">${escapeHtml(evidence || t("No evidence detail"))}</div>
        <p><strong>${escapeHtml(t("Recommendation"))}:</strong> ${escapeHtml(t(finding.recommendation || "—"))}</p>`;
      elements.findings.appendChild(item);
    });
  }

  async function startAudit() {
    if (!state.selectedAgentId) throw new Error(t("Select an agent first."));
    const body = { agent_id: state.selectedAgentId, auditor_name: elements.auditor.value };
    if (elements.auditor.value !== "rule_agent_security") {
      const llmConfig = requestLlmConfig();
      if (llmConfig) body.llm_config = llmConfig;
    }
    if (elements.start.value) body.start_at = beijingLocalToIso(elements.start.value);
    if (elements.end.value) body.end_at = beijingLocalToIso(elements.end.value);
    elements.run.disabled = true;
    try {
      const run = await json("/api/security-audits", { method: "POST", body: JSON.stringify(body) });
      state.selectedRunId = run.run_id;
      elements.message.textContent = t("Audit {run_id} queued.", { run_id: run.run_id });
      await loadRuns();
    } finally {
      elements.run.disabled = false;
    }
  }

  function showError(error) {
    elements.message.textContent = error?.message || String(error);
  }

  elements.run.addEventListener("click", () => startAudit().catch(showError));
  elements.refresh.addEventListener("click", () => loadRuns().catch(showError));
  elements.settingsOpen.addEventListener("click", openSettings);
  elements.settingsClose.addEventListener("click", closeSettings);
  elements.settingsCancel.addEventListener("click", closeSettings);
  elements.settingsSave.addEventListener("click", saveSettings);
  elements.settingsBackdrop.addEventListener("click", (event) => {
    if (event.target === elements.settingsBackdrop) closeSettings();
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && !elements.settingsBackdrop.hidden) closeSettings();
  });
  window.addEventListener("beforeunload", () => {
    if (state.polling) window.clearInterval(state.polling);
  });

  (async () => {
    try {
      if (await loadCatalogs()) await loadRuns();
    } catch (error) {
      showError(error);
    }
  })();
})();

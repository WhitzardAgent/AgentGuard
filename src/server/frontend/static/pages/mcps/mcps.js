(function () {
  const data = window.AgentGuardData;
  const shell = window.AgentGuardShell;
  const api = window.AgentGuardApi;
  const i18n = window.AgentGuardI18n;

  const selectedAgentLabel = document.getElementById("mcp-selected-agent");
  const syncStatus = document.getElementById("mcp-sync-status");
  const refreshButton = document.getElementById("refresh-mcps");
  const selectAllButton = document.getElementById("select-all-mcps");
  const clearSelectionButton = document.getElementById("clear-mcp-selection");
  const detectButton = document.getElementById("detect-selected-mcps");
  const llmConcurrencyInput = document.getElementById("mcp-llm-concurrency");
  const mcpList = document.getElementById("mcp-list");
  const selectionCount = document.getElementById("mcp-selection-count");
  const totalCount = document.getElementById("mcp-count-total");
  const riskyCount = document.getElementById("mcp-count-risky");
  const fileCount = document.getElementById("mcp-count-files");

  const state = {
    selectedAgentId: String(shell?.getState?.().selectedAgentId || "").trim(),
    mcps: [],
    selected: new Set(),
    expanded: new Set(),
    loading: false,
    detecting: false,
    detectStartedAt: 0,
    detectElapsedS: 0,
    detectTimer: null,
    pendingMcpIds: new Set(),
    waitingMcpIds: new Set(),
    selectedFiles: new Map(),
    detectionError: "",
  };

  shell?.setPageContext({
    title: "MCP Security",
    description: "Inspect reported MCP services and run LLM detection for the selected agent.",
  });

  function t(value) {
    return i18n?.t?.(value) || value;
  }

  function showToast(message, tone) {
    window.AgentGuardUI?.showToast?.(message, tone);
  }

  function escapeHtml(value) {
    return String(value ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function formatBytes(value) {
    const bytes = Number(value || 0);
    if (!Number.isFinite(bytes) || bytes <= 0) {
      return "0 B";
    }
    if (bytes < 1024) {
      return `${bytes} B`;
    }
    if (bytes < 1024 * 1024) {
      return `${(bytes / 1024).toFixed(1)} KB`;
    }
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  }

  function formatElapsed(seconds) {
    const normalized = Math.max(0, Math.floor(Number(seconds || 0)));
    const minutes = Math.floor(normalized / 60);
    const rest = normalized % 60;
    return `${String(minutes).padStart(2, "0")}:${String(rest).padStart(2, "0")}`;
  }

  function clampConcurrency(value) {
    const number = Number(value);
    return [1, 2, 4, 8].includes(number) ? number : 1;
  }

  function plural(count, singular, pluralValue) {
    return count === 1 ? singular : pluralValue;
  }

  function mcpId(mcp) {
    return String(mcp?.mcp_unique_id || "").trim();
  }

  function withoutDetectionResults(mcps) {
    return (Array.isArray(mcps) ? mcps : []).map((mcp) => ({
      ...mcp,
      detect_result: null,
    }));
  }

  function isPendingMcp(mcp) {
    const id = mcpId(mcp);
    return Boolean(id && state.pendingMcpIds.has(id));
  }

  function isWaitingMcp(mcp) {
    const id = mcpId(mcp);
    return Boolean(id && state.waitingMcpIds.has(id));
  }

  function hasDetection(mcp) {
    return Boolean(mcp?.detect_result);
  }

  function detectLabel(mcp) {
    if (isPendingMcp(mcp) || isWaitingMcp(mcp)) {
      return "running";
    }
    if (!hasDetection(mcp)) {
      return "not_detected";
    }
    return String(mcp?.detect_result?.label || "").trim().toLowerCase() || "not_detected";
  }

  function labelClass(label) {
    if (label === "malicious") {
      return "malicious";
    }
    if (label === "suspicious") {
      return "suspicious";
    }
    if (label === "benign") {
      return "benign";
    }
    if (label === "running") {
      return "running";
    }
    return "none";
  }

  function renderStatusLabel(label, className = "") {
    const normalized = String(label || "not_detected").trim().toLowerCase();
    const labels = {
      malicious: t("malicious"),
      suspicious: t("suspicious"),
      benign: t("benign"),
      running: t("Detection running"),
      not_detected: t("not detected"),
      failed: t("failed"),
    };
    return `
      <span class="skill-dual-label skill-dual-label-${labelClass(normalized)} ${className}">
        <span>${escapeHtml(labels[normalized] || normalized || t("not detected"))}</span>
      </span>
    `;
  }

  function filesForMcp(mcp) {
    return Array.isArray(mcp?.mcp_resource?.files) ? mcp.mcp_resource.files : [];
  }

  function toolsForMcp(mcp) {
    return Array.isArray(mcp?.mcp_resource?.tools) ? mcp.mcp_resource.tools : [];
  }

  function selectedFilePath(mcp) {
    const id = mcpId(mcp);
    const files = filesForMcp(mcp);
    if (!files.length) {
      return "";
    }
    const stored = state.selectedFiles.get(id);
    if (stored && files.some((file) => file.relative_path === stored)) {
      return stored;
    }
    const preferred = files.find((file) => file.relative_path === mcp.entry_file)
      || files.find((file) => typeof file.content === "string")
      || files[0];
    return preferred?.relative_path || "";
  }

  function selectedFile(mcp) {
    const path = selectedFilePath(mcp);
    return filesForMcp(mcp).find((file) => file.relative_path === path) || null;
  }

  function excerpt(value, maxChars = 520) {
    const text = String(value || "").trim();
    if (!text) {
      return "";
    }
    return text.length > maxChars ? `${text.slice(0, maxChars)}...` : text;
  }

  function llmReview(mcp) {
    const review = mcp?.detect_result?.metadata?.llm_review;
    return review && typeof review === "object" ? review : null;
  }

  function llmLabel(mcp) {
    if (isPendingMcp(mcp) || isWaitingMcp(mcp)) {
      return "running";
    }
    if (!hasDetection(mcp)) {
      return "not_detected";
    }
    const review = llmReview(mcp);
    if (review?.error) {
      return "failed";
    }
    return String(mcp?.detect_result?.label || review?.label || "completed").trim().toLowerCase();
  }

  function mainReason(mcp) {
    if (isWaitingMcp(mcp)) {
      return `${t("Waiting for LLM response...")} ${t("Elapsed")}: ${formatElapsed(state.detectElapsedS)}.`;
    }
    if (isPendingMcp(mcp)) {
      return `${t("MCP LLM detection is running.")} ${t("Elapsed")}: ${formatElapsed(state.detectElapsedS)}.`;
    }
    if (!hasDetection(mcp)) {
      return t("No detection has run. Select this MCP service and click Detect selected.");
    }
    const review = llmReview(mcp);
    if (review?.error) {
      return `${t("LLM review failed")}: ${review.error}`;
    }
    if (review?.reason) {
      return String(review.reason);
    }
    return excerpt(mcp?.detect_result?.reason || t("LLM detection completed."), 260);
  }

  function llmSummaryText(mcp) {
    if (isWaitingMcp(mcp)) {
      return `${t("Waiting for an LLM review slot.")} ${t("Elapsed")}: ${formatElapsed(state.detectElapsedS)}.`;
    }
    if (isPendingMcp(mcp)) {
      return `${t("Waiting for LLM response. Do not click Detect again.")} ${t("Elapsed")}: ${formatElapsed(state.detectElapsedS)}.`;
    }
    if (!hasDetection(mcp)) {
      return t("Run detection to see the LLM conclusion.");
    }
    return mainReason(mcp);
  }

  function renderResultBanner(mcp) {
    const label = detectLabel(mcp);
    const llm = llmReview(mcp);
    const metadata = mcp?.detect_result?.metadata || {};
    const riskLevel = String(mcp?.detect_result?.risk_level || "").trim();
    const riskLabels = Array.isArray(mcp?.detect_result?.risk_labels) ? mcp.detect_result.risk_labels : [];
    const model = String(llm?.model || metadata?.model || "").trim();
    return `
      <div class="skill-result-banner skill-result-${labelClass(label)}">
        <div class="skill-final-label-card">
          <div class="skill-result-kicker">${escapeHtml(t("Detection Result"))}</div>
          <div class="skill-final-label">
            ${renderStatusLabel(label)}
          </div>
        </div>
        <div class="skill-conclusion-grid mcp-conclusion-grid">
          <section class="skill-conclusion-card skill-conclusion-${labelClass(llmLabel(mcp))}">
            <div class="skill-conclusion-head">
              <span>${escapeHtml(t("LLM conclusion"))}</span>
              ${renderStatusLabel(llmLabel(mcp))}
            </div>
            <p>${escapeHtml(llmSummaryText(mcp))}</p>
            <div class="skill-conclusion-meta">
              ${riskLevel ? `<span>${escapeHtml(t("Risk"))}: ${escapeHtml(riskLevel)}</span>` : ""}
              ${model ? `<span>${escapeHtml(t("Model"))}: ${escapeHtml(model)}</span>` : ""}
              ${riskLabels.slice(0, 4).map((item) => `<span>${escapeHtml(item)}</span>`).join("")}
            </div>
          </section>
        </div>
      </div>
    `;
  }

  function renderToolList(mcp) {
    const tools = toolsForMcp(mcp);
    if (!tools.length) {
      return `<div class="empty-state">${escapeHtml(t("No MCP tools were reported for this service."))}</div>`;
    }
    return tools.map((tool) => {
      const schema = tool.input_schema && Object.keys(tool.input_schema).length
        ? JSON.stringify(tool.input_schema, null, 2)
        : "";
      return `
        <div class="skill-signal-row mcp-tool-row">
          <div class="skill-signal-head">
            <strong>${escapeHtml(tool.name || t("Unnamed MCP tool"))}</strong>
            <span>${escapeHtml(t("Tool"))}</span>
          </div>
          <p>${escapeHtml(tool.description || t("No description reported."))}</p>
          ${schema ? `<pre class="mcp-schema-preview">${escapeHtml(schema)}</pre>` : ""}
        </div>
      `;
    }).join("");
  }

  function renderRawDetectionDetails(mcp) {
    if (!hasDetection(mcp)) {
      return "";
    }
    return `
      <details class="skill-raw-details">
        <summary>${escapeHtml(t("Raw detector output"))}</summary>
        <pre>${escapeHtml(JSON.stringify(mcp.detect_result, null, 2))}</pre>
      </details>
    `;
  }

  function renderSelectedFilePreview(mcp) {
    const file = selectedFile(mcp);
    if (!file) {
      return `<pre class="skill-code-preview skill-code-empty">${escapeHtml(t("Select a file to preview its content."))}</pre>`;
    }
    const path = String(file.relative_path || "").trim();
    const kind = String(file.kind || "file").trim();
    const size = formatBytes(file.size);
    let body = "";
    if (file.binary) {
      body = t("Binary files cannot be previewed in the browser.");
    } else if (typeof file.content === "string") {
      body = file.content;
    } else if (file.content_omitted) {
      body = `${t("File content was omitted by the adapter")}: ${file.content_omitted}`;
    } else {
      body = t("No previewable content was reported for this file.");
    }
    return `
      <div class="skill-preview-head">
        <code>${escapeHtml(path || t("unknown file"))}</code>
        <span>${escapeHtml(kind)} | ${escapeHtml(size)}</span>
      </div>
      <pre class="skill-code-preview ${typeof file.content === "string" && !file.binary ? "" : "skill-code-empty"}">${escapeHtml(body)}</pre>
    `;
  }

  function renderFileList(mcp) {
    const files = filesForMcp(mcp);
    const selected = selectedFilePath(mcp);
    if (!files.length) {
      return `<div class="empty-state">${escapeHtml(t("No files were reported for this MCP service."))}</div>`;
    }
    return `
      <div class="skill-file-table">
        <div class="skill-file-row skill-file-head">
          <span>${escapeHtml(t("Path"))}</span>
          <span>${escapeHtml(t("Type"))}</span>
          <span>${escapeHtml(t("Size"))}</span>
        </div>
        ${files.map((file) => {
          const path = String(file.relative_path || "").trim();
          return `
            <button class="skill-file-row skill-file-button ${path === selected ? "active" : ""}" type="button" data-action="select-file" data-mcp-id="${escapeHtml(mcpId(mcp))}" data-file-path="${escapeHtml(path)}">
              <code>${escapeHtml(path || t("unknown file"))}</code>
              <span>${escapeHtml(t(file.kind || "file"))}</span>
              <span>${escapeHtml(formatBytes(file.size))}</span>
            </button>
          `;
        }).join("")}
      </div>
    `;
  }

  function renderContentPreview(mcp) {
    const files = filesForMcp(mcp);
    const rootPath = mcp.root_path || mcp.url || t("No root path reported.");
    return `
      <div class="skill-content-section mcp-content-section">
        <div class="skill-content-header">
          <div>
            <h4>${escapeHtml(t("MCP source"))}</h4>
            <p class="subtle">${escapeHtml(t("Original files and configuration collected by the adapter for this MCP service."))}</p>
          </div>
          <div class="skill-content-meta">
            <span class="pill">${escapeHtml(t(`${Number(mcp.file_count || files.length)} files`))}</span>
            <span class="pill">${formatBytes(mcp.total_size)}</span>
            <button class="btn skill-download-button" type="button" data-action="download-mcp" data-mcp-id="${escapeHtml(mcpId(mcp))}">${escapeHtml(t("Download MCP"))}</button>
          </div>
        </div>
        <div class="skill-content-layout">
          <section class="skill-content-panel skill-markdown-panel">
            <h5>${escapeHtml(t("File preview"))}</h5>
            ${renderSelectedFilePreview(mcp)}
          </section>
          <section class="skill-content-panel skill-files-panel">
            <div class="skill-content-root">
              <strong>${escapeHtml(mcp.remote ? t("Remote endpoint") : t("Root path"))}</strong>
              <code>${escapeHtml(rootPath)}</code>
              ${mcp.entry_file ? `<strong>${escapeHtml(t("Entry file"))}</strong><code>${escapeHtml(mcp.entry_file)}</code>` : ""}
            </div>
            <h5>${escapeHtml(t("File list"))}</h5>
            <div class="skill-file-list">${renderFileList(mcp)}</div>
          </section>
        </div>
      </div>
    `;
  }

  function renderMcpDetails(mcp) {
    return `
      <div class="skill-details mcp-details">
        <section class="skill-detail-panel skill-findings-panel">
          <div class="skill-section-heading">
            <h4>${escapeHtml(t("MCP tools"))}</h4>
            <span>${escapeHtml(t(`${toolsForMcp(mcp).length} tools`))}</span>
          </div>
          <div class="mcp-tool-list">${renderToolList(mcp)}</div>
          ${renderRawDetectionDetails(mcp)}
        </section>
        ${renderContentPreview(mcp)}
      </div>
    `;
  }

  function renderMcpCard(mcp) {
    const id = mcpId(mcp);
    const label = detectLabel(mcp);
    const isSelected = state.selected.has(id);
    const isExpanded = state.expanded.has(id);
    const confidence = String(mcp?.extraction?.confidence || "").trim();
    const sourceStatus = String(mcp?.extraction?.source_status || mcp?.source_status || "").trim();
    const sdkDetected = Boolean(mcp?.extraction?.sdk_detected || mcp?.mcp_resource?.sdk?.detected);
    const description = mcp.description || t("No description reported.");

    return `
      <article class="skill-card mcp-card ${isSelected ? "selected" : ""} ${label !== "not_detected" ? `skill-card-${labelClass(label)}` : ""}" data-mcp-id="${escapeHtml(id)}">
        <div class="skill-card-main">
          <label class="skill-select-box" aria-label="${escapeHtml(t(`Select MCP ${mcp.name || ""}`))}">
            <input type="checkbox" data-action="select" data-mcp-id="${escapeHtml(id)}" ${isSelected ? "checked" : ""}>
            <span></span>
          </label>
          <div class="skill-card-body">
            <div class="skill-card-title-row">
              <div class="skill-card-title-copy">
                <h3>${escapeHtml(mcp.name || t("Unnamed MCP service"))}</h3>
                <p class="subtle">${escapeHtml(description)}</p>
              </div>
              <button class="btn skill-detail-toggle" type="button" data-action="toggle" data-mcp-id="${escapeHtml(id)}">
                ${escapeHtml(isExpanded ? t("Hide MCP content") : t("View MCP content"))}
              </button>
            </div>
            <div class="pill-row skill-meta-row">
              <span class="pill">${escapeHtml(mcp.source_framework || t("unknown framework"))}</span>
              <span class="pill">${escapeHtml(mcp.transport || t("unknown transport"))}</span>
              <span class="pill">${escapeHtml(mcp.remote ? t("remote") : t("local"))}</span>
              <span class="pill">${escapeHtml(t(`${Number(mcp.tool_count || toolsForMcp(mcp).length)} tools`))}</span>
              <span class="pill">${escapeHtml(t(`${Number(mcp.file_count || filesForMcp(mcp).length)} files`))}</span>
              <span class="pill">${formatBytes(mcp.total_size)}</span>
              ${sourceStatus ? `<span class="pill">${escapeHtml(sourceStatus)}</span>` : ""}
              ${confidence ? `<span class="pill">${escapeHtml(t("confidence"))}:${escapeHtml(confidence)}</span>` : ""}
              ${sdkDetected ? `<span class="pill">${escapeHtml(t("MCP SDK detected"))}</span>` : ""}
            </div>
            ${renderResultBanner(mcp)}
          </div>
        </div>
        ${isExpanded ? renderMcpDetails(mcp) : ""}
      </article>
    `;
  }

  function sortedMcps() {
    return state.mcps.slice().sort((a, b) => {
      const labelOrder = { running: 0, malicious: 1, suspicious: 2, not_detected: 3, benign: 4 };
      const left = labelOrder[detectLabel(a)] ?? 4;
      const right = labelOrder[detectLabel(b)] ?? 4;
      if (left !== right) {
        return left - right;
      }
      return String(a.name || "").localeCompare(String(b.name || ""));
    });
  }

  function renderMetrics() {
    if (totalCount) {
      totalCount.textContent = String(state.mcps.length);
    }
    if (riskyCount) {
      riskyCount.textContent = String(state.mcps.filter((mcp) => ["malicious", "suspicious"].includes(detectLabel(mcp))).length);
    }
    if (fileCount) {
      fileCount.textContent = String(state.mcps.reduce((sum, mcp) => sum + Number(mcp.file_count || filesForMcp(mcp).length || 0), 0));
    }
  }

  function renderSelection() {
    if (selectedAgentLabel) {
      selectedAgentLabel.textContent = state.selectedAgentId || t("the selected agent");
    }
    if (selectionCount) {
      selectionCount.textContent = t(`${state.selected.size} selected`);
    }
    if (detectButton) {
      detectButton.disabled = state.detecting || !state.selected.size || !state.selectedAgentId;
      detectButton.textContent = state.detecting ? t("Detecting MCPs") : t("Detect selected MCPs");
    }
    if (selectAllButton) {
      selectAllButton.disabled = state.detecting || !state.mcps.length;
    }
    if (clearSelectionButton) {
      clearSelectionButton.disabled = state.detecting || !state.selected.size;
    }
  }

  function renderStatus(message = "") {
    if (!syncStatus) {
      return;
    }
    if (message) {
      syncStatus.textContent = t(message);
      return;
    }
    if (!state.selectedAgentId) {
      syncStatus.textContent = t("Choose an agent first.");
      return;
    }
    if (state.loading) {
      syncStatus.textContent = t(`Loading MCP services for ${state.selectedAgentId}...`);
      return;
    }
    const syncedAt = data.getLastMcpSyncTime?.();
    syncStatus.textContent = state.mcps.length
      ? t(`Loaded ${state.mcps.length} MCP services for ${state.selectedAgentId}. Last updated: ${syncedAt || "just now"}`)
      : t(`Loaded MCP catalog just now.`);
  }

  function renderDetectionError() {
    if (!state.detectionError) {
      return "";
    }
    return `
      <div class="skill-error-banner" role="alert">
        <strong>${escapeHtml(t("MCP detection failed."))}</strong>
        <span>${escapeHtml(state.detectionError)}</span>
      </div>
    `;
  }

  function renderMcpList() {
    renderMetrics();
    renderSelection();
    renderStatus();

    if (!state.selectedAgentId) {
      mcpList.innerHTML = `<div class="empty-state">${escapeHtml(t("Choose an agent first to inspect MCP services."))}</div>`;
      return;
    }
    if (!state.mcps.length) {
      mcpList.innerHTML = `<div class="empty-state">${escapeHtml(t("No MCP services have been reported for this agent yet. Run an adapter or the MCP test agent fixture first."))}</div>`;
      return;
    }
    mcpList.innerHTML = `${renderDetectionError()}${sortedMcps().map(renderMcpCard).join("")}`;
  }

  function pruneSelection() {
    const ids = new Set(state.mcps.map(mcpId));
    [...state.selected].forEach((id) => {
      if (!ids.has(id)) {
        state.selected.delete(id);
      }
    });
    [...state.expanded].forEach((id) => {
      if (!ids.has(id)) {
        state.expanded.delete(id);
      }
    });
  }

  function mergeMcps(freshMcps, cachedMcps) {
    const cachedById = new Map((cachedMcps || []).map((mcp) => [mcpId(mcp), mcp]));
    return (freshMcps || []).map((fresh) => {
      const id = mcpId(fresh);
      const cached = cachedById.get(id);
      if (!cached) {
        return fresh;
      }
      const freshFiles = filesForMcp(fresh);
      const cachedFiles = filesForMcp(cached);
      return {
        ...cached,
        ...fresh,
        detect_result: fresh.detect_result || null,
        mcp_resource: {
          ...(cached.mcp_resource || {}),
          ...(fresh.mcp_resource || {}),
          files: freshFiles.length ? freshFiles : cachedFiles,
          tools: toolsForMcp(fresh).length ? toolsForMcp(fresh) : toolsForMcp(cached),
        },
      };
    });
  }

  function findMcp(id) {
    return state.mcps.find((mcp) => mcpId(mcp) === id) || null;
  }

  async function loadMcps({ manual = false } = {}) {
    if (!state.selectedAgentId) {
      state.mcps = [];
      renderMcpList();
      return;
    }

    const cachedBeforeRefresh = data.loadMcpList(state.selectedAgentId);
    if (!state.mcps.length && cachedBeforeRefresh.length) {
      state.mcps = withoutDetectionResults(cachedBeforeRefresh);
    }

    state.loading = true;
    refreshButton.disabled = true;
    if (!state.detecting) {
      state.detectionError = "";
    }
    renderStatus(manual ? "Refreshing MCP catalog..." : "Loading MCP catalog...");
    renderMcpList();
    try {
      const freshMcps = await data.refreshMcpList(state.selectedAgentId);
      state.mcps = mergeMcps(freshMcps, cachedBeforeRefresh);
      data.persistMcpList(state.mcps, state.selectedAgentId);
      pruneSelection();
      renderMcpList();
      if (manual) {
        showToast(t("MCP catalog refreshed."), "success");
      }
    } catch (error) {
      state.mcps = cachedBeforeRefresh.length ? cachedBeforeRefresh : data.loadMcpList(state.selectedAgentId);
      pruneSelection();
      renderMcpList();
      const cachedAt = data.getLastMcpSyncTime?.();
      renderStatus(cachedAt
        ? `Showing cached MCP catalog. Last successful sync: ${cachedAt}`
        : api.formatErrorMessage(error, "Failed to load MCP catalog."));
      showToast(t(api.formatErrorMessage(error, "Failed to load MCP catalog.")), "warning");
    } finally {
      state.loading = false;
      refreshButton.disabled = false;
      renderMcpList();
    }
  }

  async function detectSelectedMcps() {
    if (state.detecting) {
      return;
    }
    const ids = [...state.selected];
    if (!ids.length || !state.selectedAgentId) {
      showToast(t("Select at least one MCP service first."), "warning");
      return;
    }
    state.detecting = true;
    state.pendingMcpIds = new Set();
    state.waitingMcpIds = new Set(ids);
    state.detectionError = "";
    state.mcps = state.mcps.map((mcp) => (
      state.waitingMcpIds.has(mcpId(mcp))
        ? { ...mcp, detect_result: null }
        : mcp
    ));
    startDetectTimer();
    renderMcpList();
    try {
      const results = await runDetectionQueue(ids, {
        concurrency: Math.min(clampConcurrency(llmConcurrencyInput?.value), ids.length),
      });
      const detected = results.filter((result) => result.ok).length;
      showToast(t(`Detected ${detected} ${plural(detected, "MCP service", "MCP services")}.`), "success");
    } catch (error) {
      state.detectionError = api.formatErrorMessage(error, "MCP detection failed.");
      showToast(t(state.detectionError), "warning");
    } finally {
      state.detecting = false;
      state.pendingMcpIds.clear();
      state.waitingMcpIds.clear();
      stopDetectTimer();
      renderMcpList();
    }
  }

  async function runDetectionQueue(ids, { concurrency }) {
    const queue = ids.slice();
    const results = [];
    const workerCount = Math.max(1, Math.min(concurrency || 1, queue.length || 1));
    async function worker() {
      while (queue.length) {
        const id = queue.shift();
        if (!id) {
          continue;
        }
        state.waitingMcpIds.delete(id);
        state.pendingMcpIds.add(id);
        renderMcpList();
        try {
          const payload = await data.detectMcps(state.selectedAgentId, [id], {
            timeoutMs: 80000,
          });
          applyDetectPayload(payload);
          results.push({ id, ok: true, payload });
        } catch (error) {
          state.detectionError = api.formatErrorMessage(error, "MCP detection failed.");
          results.push({ id, ok: false, error });
          showToast(t(`${findMcp(id)?.name || id}: ${state.detectionError}`), "warning");
        } finally {
          state.pendingMcpIds.delete(id);
          state.waitingMcpIds.delete(id);
          renderMcpList();
        }
      }
    }
    await Promise.all(Array.from({ length: workerCount }, () => worker()));
    return results;
  }

  function applyDetectPayload(result) {
    const updates = new Map();
    (result?.results || []).forEach((item) => {
      const nextMcp = item.mcp || null;
      if (nextMcp?.mcp_unique_id) {
        updates.set(nextMcp.mcp_unique_id, nextMcp);
      } else if (item.mcp_unique_id) {
        updates.set(item.mcp_unique_id, {
          ...(state.mcps.find((mcp) => mcp.mcp_unique_id === item.mcp_unique_id) || {}),
          detect_result: item.detect_result,
        });
      }
    });
    if (!updates.size) {
      return;
    }
    state.mcps = state.mcps.map((mcp) => updates.get(mcp.mcp_unique_id) || mcp);
    data.persistMcpList(state.mcps, state.selectedAgentId);
  }

  function startDetectTimer() {
    stopDetectTimer();
    state.detectStartedAt = Date.now();
    state.detectElapsedS = 0;
    state.detectTimer = window.setInterval(() => {
      state.detectElapsedS = Math.floor((Date.now() - state.detectStartedAt) / 1000);
      renderMcpList();
    }, 1000);
  }

  function stopDetectTimer() {
    if (state.detectTimer) {
      window.clearInterval(state.detectTimer);
      state.detectTimer = null;
    }
  }

  function sanitizeFileName(value, fallback = "mcp") {
    return String(value || fallback)
      .replace(/[<>:"/\\|?*\x00-\x1F]/g, "_")
      .replace(/\s+/g, "_")
      .replace(/^_+|_+$/g, "")
      .slice(0, 120) || fallback;
  }

  function downloadMcp(mcp) {
    const files = filesForMcp(mcp);
    if (!files.length) {
      showToast(t("No files are available to download for this MCP service."), "warning");
      return;
    }
    const zipEntries = [];
    const notices = [];
    files.forEach((file) => {
      const relativePath = String(file.relative_path || "").trim();
      if (!relativePath) {
        return;
      }
      if (typeof file.content === "string" && !file.binary) {
        zipEntries.push({ path: relativePath, content: file.content });
      } else {
        notices.push(`${relativePath}: ${file.binary ? "binary file" : file.content_omitted || "content not reported"}`);
      }
    });
    if (notices.length) {
      zipEntries.push({
        path: "AGENTGUARD_DOWNLOAD_NOTICE.txt",
        content: [
          "Some files could not be exported because their content was not reported by the adapter.",
          "",
          ...notices,
          "",
        ].join("\n"),
      });
    }
    if (!zipEntries.length) {
      showToast(t("No previewable files are available to download for this MCP service."), "warning");
      return;
    }
    const blob = buildZipBlob(zipEntries);
    const link = document.createElement("a");
    const objectUrl = URL.createObjectURL(blob);
    link.href = objectUrl;
    link.download = `${sanitizeFileName(mcp.name || mcpId(mcp))}.zip`;
    document.body.appendChild(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(objectUrl);
  }

  function buildZipBlob(entries) {
    const encoder = new TextEncoder();
    const chunks = [];
    const central = [];
    let offset = 0;
    entries.forEach((entry) => {
      const nameBytes = encoder.encode(safeZipPath(entry.path));
      const dataBytes = encoder.encode(String(entry.content ?? ""));
      const crc = crc32(dataBytes);
      const local = new Uint8Array(30 + nameBytes.length);
      const view = new DataView(local.buffer);
      view.setUint32(0, 0x04034b50, true);
      view.setUint16(4, 20, true);
      view.setUint16(8, 0, true);
      view.setUint16(10, 0, true);
      view.setUint32(14, crc, true);
      view.setUint32(18, dataBytes.length, true);
      view.setUint32(22, dataBytes.length, true);
      view.setUint16(26, nameBytes.length, true);
      local.set(nameBytes, 30);
      chunks.push(local, dataBytes);

      const header = new Uint8Array(46 + nameBytes.length);
      const centralView = new DataView(header.buffer);
      centralView.setUint32(0, 0x02014b50, true);
      centralView.setUint16(4, 20, true);
      centralView.setUint16(6, 20, true);
      centralView.setUint16(10, 0, true);
      centralView.setUint16(12, 0, true);
      centralView.setUint32(16, crc, true);
      centralView.setUint32(20, dataBytes.length, true);
      centralView.setUint32(24, dataBytes.length, true);
      centralView.setUint16(28, nameBytes.length, true);
      centralView.setUint32(42, offset, true);
      header.set(nameBytes, 46);
      central.push(header);
      offset += local.length + dataBytes.length;
    });
    const centralSize = central.reduce((sum, chunk) => sum + chunk.length, 0);
    const end = new Uint8Array(22);
    const endView = new DataView(end.buffer);
    endView.setUint32(0, 0x06054b50, true);
    endView.setUint16(8, entries.length, true);
    endView.setUint16(10, entries.length, true);
    endView.setUint32(12, centralSize, true);
    endView.setUint32(16, offset, true);
    return new Blob([...chunks, ...central, end], { type: "application/zip" });
  }

  function safeZipPath(value) {
    const parts = String(value || "file.txt")
      .replace(/\\/g, "/")
      .split("/")
      .filter((part) => part && part !== "." && part !== "..")
      .map((part) => sanitizeFileName(part, "file"));
    return parts.join("/") || "file.txt";
  }

  function crc32(bytes) {
    let crc = 0xffffffff;
    for (const byte of bytes) {
      crc = (crc >>> 8) ^ CRC32_TABLE[(crc ^ byte) & 0xff];
    }
    return (crc ^ 0xffffffff) >>> 0;
  }

  const CRC32_TABLE = (() => {
    const table = new Uint32Array(256);
    for (let i = 0; i < 256; i += 1) {
      let value = i;
      for (let bit = 0; bit < 8; bit += 1) {
        value = value & 1 ? 0xedb88320 ^ (value >>> 1) : value >>> 1;
      }
      table[i] = value >>> 0;
    }
    return table;
  })();

  if (llmConcurrencyInput) {
    llmConcurrencyInput.value = String(clampConcurrency(llmConcurrencyInput.value));
  }

  refreshButton?.addEventListener("click", () => {
    loadMcps({ manual: true });
  });

  selectAllButton?.addEventListener("click", () => {
    state.mcps.forEach((mcp) => {
      const id = mcpId(mcp);
      if (id) {
        state.selected.add(id);
      }
    });
    renderMcpList();
  });

  clearSelectionButton?.addEventListener("click", () => {
    const selected = state.selected.size;
    state.selected.clear();
    renderMcpList();
    if (selected > 0) {
      showToast(t("MCP selection cleared."), "success");
    }
  });

  detectButton?.addEventListener("click", detectSelectedMcps);

  mcpList?.addEventListener("change", (event) => {
    const target = event.target;
    if (!(target instanceof HTMLInputElement) || target.dataset.action !== "select") {
      return;
    }
    const id = String(target.dataset.mcpId || "").trim();
    if (!id) {
      return;
    }
    if (target.checked) {
      state.selected.add(id);
    } else {
      state.selected.delete(id);
    }
    renderMcpList();
  });

  mcpList?.addEventListener("click", (event) => {
    const button = event.target instanceof Element ? event.target.closest("[data-action]") : null;
    if (!button) {
      return;
    }
    const action = String(button.dataset.action || "").trim();
    const id = String(button.dataset.mcpId || "").trim();
    if (!id) {
      return;
    }
    if (action === "select-file") {
      state.selectedFiles.set(id, String(button.dataset.filePath || "").trim());
      renderMcpList();
      return;
    }
    if (action === "download-mcp") {
      const mcp = findMcp(id);
      if (mcp) {
        downloadMcp(mcp);
      }
      return;
    }
    if (action !== "toggle") {
      return;
    }
    if (state.expanded.has(id)) {
      state.expanded.delete(id);
    } else {
      state.expanded.add(id);
    }
    renderMcpList();
  });

  window.addEventListener("agentguard:selected-agent-change", (event) => {
    state.selectedAgentId = String(event?.detail?.agentId || "").trim();
    state.mcps = withoutDetectionResults(data.loadMcpList(state.selectedAgentId));
    state.selected.clear();
    state.expanded.clear();
    state.pendingMcpIds.clear();
    state.waitingMcpIds.clear();
    state.detectionError = "";
    loadMcps();
  });

  state.mcps = withoutDetectionResults(data.loadMcpList(state.selectedAgentId));
  renderMcpList();
  loadMcps();
})();

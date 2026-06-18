(function () {
  let toastTimer = null;
  const AGENT_CATALOG_KEY = "agentguard.agentCatalog";
  const AGENT_SYNC_KEY = "agentguard.agentCatalogSyncedAt";
  const AGENT_SCOPE_KEY = "agentguard.agentCatalogApiBase";
  const SCOPED_AGENT_ID_KEY = "agentguard.scopedAgentId";
  const SCOPED_TOOL_CATALOG_KEY = "agentguard.scopedToolCatalog";
  const SCOPED_TOOL_SYNC_KEY = "agentguard.scopedToolCatalogSyncedAt";
  const SCOPED_RULE_LIST_KEY = "agentguard.scopedRuleList";
  const SCOPED_RULE_SYNC_KEY = "agentguard.scopedRuleListSyncedAt";
  const LEGACY_TOOL_CATALOG_KEY = "agentguard.toolCatalog";
  const LEGACY_TOOL_SYNC_KEY = "agentguard.toolCatalogSyncedAt";
  const LEGACY_TOOL_SCOPE_KEY = "agentguard.toolCatalogApiBase";
  const DEFAULT_REQUEST_TIMEOUT_MS = 6000;
  const text = window.AgentGuardText || {};
  const shell = window.AgentGuardShell || null;
  const EVENT_TYPE_PHASE_MAP = {
    tool_invoke: "tool_before",
    tool_result: "tool_after",
    llm_input: "llm_before",
    llm_output: "llm_after",
    llm_thought: "llm_after",
    final_response: "llm_after",
  };
  const PLUGIN_PHASE_ORDER = ["llm_before", "llm_after", "tool_before", "tool_after", "global"];
  const PLUGIN_SCOPES = new Set(["local", "remote"]);

  function buildQuery(params) {
    const search = new URLSearchParams();
    Object.entries(params || {}).forEach(([key, value]) => {
      if (value === undefined || value === null || value === "") {
        return;
      }
      search.set(key, String(value));
    });
    const query = search.toString();
    return query ? `?${query}` : "";
  }

  function getSelectedAgentId() {
    return String(shell?.getState?.().selectedAgentId || "").trim();
  }

  function normalizePluginOption(item) {
    return {
      name: String(item?.name || "").trim(),
      description: String(item?.description || "").trim(),
      event_types: Array.isArray(item?.event_types) ? item.event_types.map(String).filter(Boolean) : [],
      phases: Array.isArray(item?.phases) ? item.phases.map(String).filter(Boolean) : [],
    };
  }

  function normalizeAgentPluginConfig(item) {
    return {
      agent_id: String(item?.agent_id || "").trim(),
      plugin_config: item?.plugin_config && typeof item.plugin_config === "object"
        ? item.plugin_config
        : null,
      config_source: String(item?.config_source || "none").trim() || "none",
    };
  }

  function pluginNameFromSpec(spec) {
    if (typeof spec === "string") {
      return String(spec).trim();
    }
    if (spec && typeof spec === "object") {
      return String(spec.name || spec.plugin || spec.class || "").trim();
    }
    return "";
  }

  function uniquePluginNames(names) {
    const seen = new Set();
    return (Array.isArray(names) ? names : [])
      .map((name) => String(name || "").trim())
      .filter((name) => {
        if (!name || seen.has(name)) {
          return false;
        }
        seen.add(name);
        return true;
      });
  }

  function normalizePhaseConfig(phaseConfig) {
    return {
      local: Array.isArray(phaseConfig?.local) ? [...phaseConfig.local] : [],
      remote: Array.isArray(phaseConfig?.remote) ? [...phaseConfig.remote] : [],
    };
  }

  function normalizePluginScope(scope) {
    return PLUGIN_SCOPES.has(scope) ? scope : "remote";
  }

  function expandPluginSelection(names) {
    return uniquePluginNames(names);
  }

  function collapsePluginSelection(names) {
    return uniquePluginNames(names);
  }

  function primaryPluginName(names) {
    const activeNames = uniquePluginNames(names);
    if (activeNames.includes("rule_based_plugin")) {
      return "rule_based_plugin";
    }
    return activeNames.find((name) => name !== "tool_invoke") || activeNames[0] || "";
  }

  function pluginPhases(option) {
    const phases = new Set();
    const normalized = normalizePluginOption(option);
    normalized.phases.forEach((phase) => {
      const phaseName = String(phase || "").trim();
      if (phaseName) {
        phases.add(phaseName);
      }
    });
    normalized.event_types.forEach((eventType) => {
      const phase = EVENT_TYPE_PHASE_MAP[String(eventType || "").trim()];
      if (phase) {
        phases.add(phase);
      }
    });
    const inferredPhase = EVENT_TYPE_PHASE_MAP[normalized.name];
    if (inferredPhase) {
      phases.add(inferredPhase);
    }
    return [...phases];
  }

  function ensurePhase(phases, phase, basePhases) {
    if (!phases[phase]) {
      phases[phase] = normalizePhaseConfig(basePhases?.[phase]);
    }
    return phases[phase];
  }

  function buildPluginConfig(plugins, availablePlugins = null, existingConfig = null, scope = "remote") {
    const targetScope = normalizePluginScope(scope);
    const selectedOptions = (Array.isArray(plugins) ? plugins : [plugins])
      .map(normalizePluginOption)
      .filter((option) => option.name);
    const catalog = (Array.isArray(availablePlugins) ? availablePlugins : selectedOptions)
      .map(normalizePluginOption)
      .filter((option) => option.name);
    const catalogByName = new Map(catalog.map((option) => [option.name, option]));
    const manageableNames = new Set(catalog.map((option) => option.name));
    const baseConfig = existingConfig && typeof existingConfig === "object" ? existingConfig : null;
    const basePhases = baseConfig?.phases && typeof baseConfig.phases === "object" ? baseConfig.phases : {};
    const phases = {};

    Object.keys(basePhases).forEach((phase) => {
      const normalized = normalizePhaseConfig(basePhases[phase]);
      normalized[targetScope] = normalized[targetScope].filter((spec) => {
        const name = pluginNameFromSpec(spec);
        return !name || !manageableNames.has(name);
      });
      if (normalized.local.length || normalized.remote.length) {
        phases[phase] = normalized;
      }
    });

    const expandedNames = expandPluginSelection(selectedOptions.map((option) => option.name));
    expandedNames.forEach((name) => {
      const option = catalogByName.get(name) || normalizePluginOption({ name, event_types: [name] });
      const phaseNames = pluginPhases(option);
      if (!phaseNames.length) {
        return;
      }
      phaseNames.forEach((phase) => {
        const phaseConfig = ensurePhase(phases, phase, basePhases);
        if (!phaseConfig[targetScope].some((spec) => pluginNameFromSpec(spec) === name)) {
          phaseConfig[targetScope].push(name);
        }
      });
    });

    const orderedPhases = {};
    const phaseNames = new Set([...PLUGIN_PHASE_ORDER, ...Object.keys(phases)]);
    [...phaseNames].forEach((phase) => {
      const value = phases[phase];
      if (!value) {
        return;
      }
      if (!value.local.length && !value.remote.length) {
        return;
      }
      orderedPhases[phase] = value;
    });

    return { phases: orderedPhases };
  }

  function selectedPluginsFromConfig(configResponse, scope = "remote") {
    const targetScope = normalizePluginScope(scope);
    const pluginConfig = normalizeAgentPluginConfig(configResponse).plugin_config || {};
    const phases = pluginConfig?.phases;
    if (!phases || typeof phases !== "object") {
      return [];
    }
    const found = Object.values(phases).flatMap((phase) => {
      if (!phase || typeof phase !== "object" || !Array.isArray(phase[targetScope])) {
        return [];
      }
      return phase[targetScope].map(pluginNameFromSpec).filter(Boolean);
    });
    return uniquePluginNames(found);
  }

  function activePluginsFromConfig(configResponse) {
    return uniquePluginNames([
      ...selectedPluginsFromConfig(configResponse, "remote"),
      ...selectedPluginsFromConfig(configResponse, "local"),
    ]);
  }

  function selectedPluginFromConfig(configResponse) {
    return primaryPluginName(activePluginsFromConfig(configResponse));
  }

  function clearLegacyToolCache() {
    localStorage.removeItem(LEGACY_TOOL_CATALOG_KEY);
    localStorage.removeItem(LEGACY_TOOL_SYNC_KEY);
  }

  function clearAgentCatalogCache() {
    localStorage.removeItem(AGENT_CATALOG_KEY);
    localStorage.removeItem(AGENT_SYNC_KEY);
  }

  function clearScopedAgentCache() {
    localStorage.removeItem(SCOPED_AGENT_ID_KEY);
    localStorage.removeItem(SCOPED_TOOL_CATALOG_KEY);
    localStorage.removeItem(SCOPED_TOOL_SYNC_KEY);
    localStorage.removeItem(SCOPED_RULE_LIST_KEY);
    localStorage.removeItem(SCOPED_RULE_SYNC_KEY);
  }

  function syncCacheScopes() {
    const currentApiBase = String(window.AgentGuardConfig?.apiBase || "");
    const cachedAgentApiBase = localStorage.getItem(AGENT_SCOPE_KEY) || "";
    const cachedLegacyApiBase = localStorage.getItem(LEGACY_TOOL_SCOPE_KEY) || "";
    const apiBaseChanged = Boolean(
      currentApiBase
      && ((cachedAgentApiBase && cachedAgentApiBase !== currentApiBase)
        || (cachedLegacyApiBase && cachedLegacyApiBase !== currentApiBase)),
    );

    if (apiBaseChanged) {
      clearAgentCatalogCache();
      clearScopedAgentCache();
      clearLegacyToolCache();
    }

    if (currentApiBase) {
      localStorage.setItem(AGENT_SCOPE_KEY, currentApiBase);
      localStorage.setItem(LEGACY_TOOL_SCOPE_KEY, currentApiBase);
    }
  }

  function agentSyncSummary() {
    const syncedAt = localStorage.getItem(AGENT_SYNC_KEY);
    if (!syncedAt) {
      return text.sidebarToolUnsynced || "Not synced yet";
    }
    return `Last synced ${syncedAt}`;
  }

  syncCacheScopes();
  if (shell?.setToolStatus) {
    shell.setToolStatus(agentSyncSummary());
  }

  function showToast(message, tone) {
    const toast = document.getElementById("toast");
    if (!toast) {
      return;
    }

    toast.textContent = message;
    toast.classList.remove("success", "warning", "danger");
    toast.classList.add(tone || "success");
    toast.classList.add("show");

    if (toastTimer) {
      clearTimeout(toastTimer);
    }

    toastTimer = setTimeout(() => {
      toast.classList.remove("show");
    }, 1000);
  }

  function buildToolKey(ownerAgentId, toolName) {
    const agentId = String(ownerAgentId || "").trim();
    const name = String(toolName || "").trim();
    return agentId && name ? `${agentId}::${name}` : "";
  }

  function normalizeTool(item) {
    const ownerAgentId = String(item?.owner_agent_id || "").trim();
    const name = String(item?.name || "").trim();
    return {
      owner_agent_id: ownerAgentId,
      name,
      tool_key: buildToolKey(ownerAgentId, name),
      labels: {
        boundary: String(item?.labels?.boundary || "internal"),
        sensitivity: String(item?.labels?.sensitivity || "low"),
        integrity: String(item?.labels?.integrity || "trusted"),
        tags: Array.isArray(item?.labels?.tags) ? item.labels.tags.map(String) : [],
      },
      input_params: Array.isArray(item?.input_params) ? item.input_params.map(String) : [],
    };
  }

  function normalizeRule(item) {
    return {
      ...item,
      id: String(item?.id || item?.rule_id || item?.name || "").trim(),
      name: String(item?.name || item?.rule_id || item?.id || "").trim(),
      rule_id: String(item?.rule_id || item?.name || item?.id || "").trim(),
      tool_pattern: String(item?.tool_pattern || "*").trim() || "*",
      action: String(item?.action || "").trim(),
      source: String(item?.source || "").trim(),
      severity: String(item?.severity || "").trim(),
      category: String(item?.category || "").trim(),
      version: String(item?.version || "").trim(),
      status: String(item?.status || "published").trim() || "published",
      degrade_profile: String(item?.degrade_profile || "").trim(),
    };
  }

  function buildAgentSummary(agentId, tools) {
    const sortedTools = (Array.isArray(tools) ? tools : [])
      .map((tool) => String(tool?.name || "").trim())
      .filter(Boolean)
      .sort((a, b) => a.localeCompare(b));
    return {
      agent_id: agentId,
      tool_count: sortedTools.length,
      tool_names: sortedTools.slice(0, 4),
    };
  }

  function buildAgentCatalogFromTools(tools) {
    const grouped = (Array.isArray(tools) ? tools : []).reduce((acc, tool) => {
      const agentId = String(tool?.owner_agent_id || "").trim();
      if (!agentId) {
        return acc;
      }
      if (!acc[agentId]) {
        acc[agentId] = [];
      }
      acc[agentId].push(tool);
      return acc;
    }, {});
    return Object.keys(grouped)
      .sort((a, b) => a.localeCompare(b))
      .map((agentId) => buildAgentSummary(agentId, grouped[agentId]));
  }

  function normalizeAgentSummary(item) {
    const agentId = String(item?.agent_id || item?.agentId || "").trim();
    return {
      agent_id: agentId,
      tool_count: Number.isFinite(Number(item?.tool_count)) ? Number(item.tool_count) : 0,
      tool_names: Array.isArray(item?.tool_names) ? item.tool_names.map(String).filter(Boolean) : [],
    };
  }

  function loadAgentCatalog() {
    try {
      const raw = localStorage.getItem(AGENT_CATALOG_KEY);
      if (!raw) {
        return [];
      }
      const parsed = JSON.parse(raw);
      if (!Array.isArray(parsed)) {
        return [];
      }
      return parsed
        .map(normalizeAgentSummary)
        .filter((agent) => agent.agent_id);
    } catch {
      return [];
    }
  }

  function persistAgentCatalog(catalog) {
    localStorage.setItem(AGENT_CATALOG_KEY, JSON.stringify(catalog));
    localStorage.setItem(AGENT_SYNC_KEY, new Date().toISOString());
    if (shell?.setToolStatus) {
      shell.setToolStatus(agentSyncSummary());
    }
  }

  function matchesScopedAgent(agentId) {
    const normalized = String(agentId || "").trim();
    if (!normalized) {
      return false;
    }
    return localStorage.getItem(SCOPED_AGENT_ID_KEY) === normalized;
  }

  function loadScopedToolCatalog(agentId = getSelectedAgentId()) {
    if (!matchesScopedAgent(agentId)) {
      return [];
    }
    try {
      const raw = localStorage.getItem(SCOPED_TOOL_CATALOG_KEY);
      if (!raw) {
        return [];
      }
      const parsed = JSON.parse(raw);
      if (!Array.isArray(parsed) || !parsed.length) {
        return [];
      }
      return parsed
        .map(normalizeTool)
        .filter((tool) => tool.owner_agent_id && tool.name && tool.tool_key);
    } catch {
      return [];
    }
  }

  function persistScopedToolCatalog(agentId, catalog) {
    const normalizedAgentId = String(agentId || "").trim();
    if (!normalizedAgentId) {
      clearScopedAgentCache();
      return;
    }
    localStorage.setItem(SCOPED_AGENT_ID_KEY, normalizedAgentId);
    localStorage.setItem(SCOPED_TOOL_CATALOG_KEY, JSON.stringify(catalog));
    localStorage.setItem(SCOPED_TOOL_SYNC_KEY, new Date().toISOString());
  }

  function loadScopedRuleList(agentId = getSelectedAgentId()) {
    if (!matchesScopedAgent(agentId)) {
      return [];
    }
    try {
      const raw = localStorage.getItem(SCOPED_RULE_LIST_KEY);
      if (!raw) {
        return [];
      }
      const parsed = JSON.parse(raw);
      if (!Array.isArray(parsed)) {
        return [];
      }
      return parsed.map(normalizeRule).filter((rule) => rule.rule_id);
    } catch {
      return [];
    }
  }

  function persistScopedRuleList(agentId, rules) {
    const normalizedAgentId = String(agentId || "").trim();
    if (!normalizedAgentId) {
      clearScopedAgentCache();
      return;
    }
    localStorage.setItem(SCOPED_AGENT_ID_KEY, normalizedAgentId);
    localStorage.setItem(SCOPED_RULE_LIST_KEY, JSON.stringify(rules));
    localStorage.setItem(SCOPED_RULE_SYNC_KEY, new Date().toISOString());
  }

  function formatErrorMessage(error, fallback) {
    if (error instanceof Error && error.message) {
      return error.message;
    }
    return String(fallback || text.genericRequestError || "Request failed.");
  }

  function buildTimedFetchOptions(url, options = {}) {
    const timeoutMs = Number(options?.timeoutMs);
    const normalizedTimeoutMs = Number.isFinite(timeoutMs) && timeoutMs > 0
      ? timeoutMs
      : DEFAULT_REQUEST_TIMEOUT_MS;
    const controller = typeof AbortController === "function" ? new AbortController() : null;
    const upstreamSignal = options?.signal;
    const fetchOptions = {
      cache: "no-store",
      ...options,
    };
    let timeoutId = null;
    let abortHandler = null;

    delete fetchOptions.timeoutMs;

    if (!controller) {
      return {
        fetchOptions,
        cleanup() {},
        didTimeout() {
          return false;
        },
      };
    }

    let timedOut = false;

    if (upstreamSignal instanceof AbortSignal) {
      if (upstreamSignal.aborted) {
        controller.abort(upstreamSignal.reason);
      } else {
        abortHandler = () => controller.abort(upstreamSignal.reason);
        upstreamSignal.addEventListener("abort", abortHandler, { once: true });
      }
    }

    timeoutId = setTimeout(() => {
      if (!controller.signal.aborted) {
        timedOut = true;
        controller.abort(new Error(`Request timed out after ${normalizedTimeoutMs}ms`));
      }
    }, normalizedTimeoutMs);

    fetchOptions.signal = controller.signal;

    return {
      fetchOptions,
      cleanup() {
        if (timeoutId !== null) {
          clearTimeout(timeoutId);
        }
        if (abortHandler && upstreamSignal instanceof AbortSignal) {
          upstreamSignal.removeEventListener("abort", abortHandler);
        }
      },
      didTimeout() {
        return timedOut;
      },
      timeoutMessage: `Request timed out after ${normalizedTimeoutMs}ms while fetching ${url}.`,
    };
  }

  async function fetchJson(url, options = {}) {
    let response;
    let payload;
    const timedFetch = buildTimedFetchOptions(url, options);

    try {
      response = await fetch(url, timedFetch.fetchOptions);
    } catch (error) {
      timedFetch.cleanup();
      if (shell?.setApiStatus) {
        shell.setApiStatus(text.sidebarApiUnavailable || "Unavailable", "danger");
      }
      if (timedFetch.didTimeout()) {
        throw new Error(timedFetch.timeoutMessage);
      }
      throw new Error(formatErrorMessage(error, text.unreachableApi || "Cannot reach the AgentGuard API."));
    }
    timedFetch.cleanup();

    try {
      payload = await response.json();
    } catch {
      payload = null;
    }

    if (!response.ok) {
      if (shell?.setApiStatus) {
        shell.setApiStatus(text.sidebarApiPartial || "Partial", "warning");
      }
      throw new Error(
        payload?.error || payload?.detail || text.genericRequestError || "Request failed.",
      );
    }

    if (shell?.setApiStatus) {
      shell.setApiStatus(text.sidebarApiConnected || "Connected", "success");
    }
    return payload;
  }

  async function refreshAgentCatalog() {
    const payload = await fetchJson("/api/tools");
    if (!Array.isArray(payload)) {
      throw new Error("Agent catalog payload has an unexpected format.");
    }
    const tools = payload.map(normalizeTool);
    const catalog = buildAgentCatalogFromTools(tools);
    persistAgentCatalog(catalog);
    return catalog;
  }

  async function refreshScopedToolCatalog(agentId = getSelectedAgentId()) {
    const normalizedAgentId = String(agentId || "").trim();
    if (!normalizedAgentId) {
      clearScopedAgentCache();
      return [];
    }
    const payload = await fetchJson(`/api/agents/${encodeURIComponent(normalizedAgentId)}/tools`);
    if (!Array.isArray(payload)) {
      throw new Error("Tool catalog payload has an unexpected format.");
    }
    const catalog = payload.map(normalizeTool);
    persistScopedToolCatalog(normalizedAgentId, catalog);
    return catalog;
  }

  async function updateScopedToolLabels(agentId, toolName, labels) {
    const normalizedAgentId = String(agentId || "").trim();
    const normalizedToolName = String(toolName || "").trim();
    if (!normalizedAgentId || !normalizedToolName) {
      throw new Error("agent_id and tool_name are required.");
    }

    const payload = await fetchJson(
      `/api/agents/${encodeURIComponent(normalizedAgentId)}/tools/${encodeURIComponent(normalizedToolName)}/labels`,
      {
        method: "PATCH",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          boundary: String(labels?.boundary || "internal"),
          sensitivity: String(labels?.sensitivity || "low"),
          integrity: String(labels?.integrity || "trusted"),
          tags: Array.isArray(labels?.tags) ? labels.tags.map(String) : [],
        }),
      },
    );
    const normalizedTool = normalizeTool(payload?.tool || {});
    const currentCatalog = loadScopedToolCatalog(normalizedAgentId);
    const nextCatalog = currentCatalog.slice();
    const existingIndex = nextCatalog.findIndex(
      (tool) => tool.owner_agent_id === normalizedAgentId && tool.name === normalizedToolName,
    );
    if (existingIndex >= 0) {
      nextCatalog[existingIndex] = {
        ...nextCatalog[existingIndex],
        ...normalizedTool,
      };
    } else if (normalizedTool.owner_agent_id && normalizedTool.name) {
      nextCatalog.push(normalizedTool);
    }
    persistScopedToolCatalog(normalizedAgentId, nextCatalog);
    return normalizedTool;
  }

  async function refreshScopedRuleList(agentId = getSelectedAgentId()) {
    const normalizedAgentId = String(agentId || "").trim();
    if (!normalizedAgentId) {
      clearScopedAgentCache();
      return [];
    }
    const payload = await fetchJson(`/api/agents/${encodeURIComponent(normalizedAgentId)}/rules`);
    if (!Array.isArray(payload)) {
      throw new Error("Rule list payload has an unexpected format.");
    }
    const rules = payload.map(normalizeRule);
    persistScopedRuleList(normalizedAgentId, rules);
    return rules;
  }

  async function listAgentAvailablePlugins(agentId = getSelectedAgentId()) {
    const normalizedAgentId = String(agentId || "").trim();
    if (!normalizedAgentId) {
      return { agent_id: "", local_plugins: [], remote_plugins: [] };
    }
    const payload = await fetchJson(`/api/agents/${encodeURIComponent(normalizedAgentId)}/plugins/available`);
    return {
      agent_id: String(payload?.agent_id || normalizedAgentId).trim(),
      local_plugins: Array.isArray(payload?.local_plugins) ? payload.local_plugins.map(normalizePluginOption) : [],
      remote_plugins: Array.isArray(payload?.remote_plugins) ? payload.remote_plugins.map(normalizePluginOption) : [],
    };
  }

  async function getAgentPluginConfig(agentId = getSelectedAgentId()) {
    const normalizedAgentId = String(agentId || "").trim();
    if (!normalizedAgentId) {
      return normalizeAgentPluginConfig({});
    }
    const payload = await fetchJson(`/api/agents/${encodeURIComponent(normalizedAgentId)}/plugins/config`);
    return normalizeAgentPluginConfig(payload);
  }

  async function updateAgentPluginConfig(agentId, config, clientConfig = null) {
    const normalizedAgentId = String(agentId || "").trim();
    if (!normalizedAgentId) {
      throw new Error("agent_id is required.");
    }
    if (!config || typeof config !== "object") {
      throw new Error("config is required.");
    }
    return fetchJson(`/api/agents/${encodeURIComponent(normalizedAgentId)}/plugins/config`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        config,
        client_config: clientConfig,
      }),
    });
  }

  function groupToolsByAgent(catalog) {
    return (Array.isArray(catalog) ? catalog : []).reduce((acc, tool) => {
      const agentId = String(tool?.owner_agent_id || "").trim();
      if (!agentId) {
        return acc;
      }
      if (!acc[agentId]) {
        acc[agentId] = [];
      }
      acc[agentId].push(tool);
      return acc;
    }, {});
  }

  function findToolByKey(catalog, toolKey) {
    const normalizedKey = String(toolKey || "").trim();
    if (!normalizedKey) {
      return null;
    }
    return (Array.isArray(catalog) ? catalog : []).find(
      (tool) => String(tool?.tool_key || "").trim() === normalizedKey,
    ) || null;
  }

  function listAgentIds(catalog) {
    const normalized = (Array.isArray(catalog) ? catalog : [])
      .map((item) => String(item?.agent_id || item?.owner_agent_id || "").trim())
      .filter(Boolean);
    return Array.from(new Set(normalized)).sort((a, b) => a.localeCompare(b));
  }

  if (typeof window !== "undefined" && typeof window.addEventListener === "function") {
    window.addEventListener("agentguard:selected-agent-change", (event) => {
      const nextAgentId = String(event?.detail?.agentId || "").trim();
      if (!nextAgentId || !matchesScopedAgent(nextAgentId)) {
        clearScopedAgentCache();
      }
    });
  }

  window.AgentGuardUI = {
    showToast,
  };

  window.AgentGuardApi = {
    buildQuery,
    fetchJson,
    formatErrorMessage,
  };

  window.AgentGuardData = {
    buildToolKey,
    findToolByKey,
    groupToolsByAgent,
    listAgentIds,
    normalizeAgentSummary,
    normalizePluginOption,
    normalizeRule,
    normalizeTool,
    buildPluginConfig,
    collapsePluginSelection,
    expandPluginSelection,
    activePluginsFromConfig,
    primaryPluginName,
    selectedPluginFromConfig,
    selectedPluginsFromConfig,
    loadAgentCatalog,
    persistAgentCatalog,
    refreshAgentCatalog,
    loadToolCatalog: loadScopedToolCatalog,
    persistToolCatalog(catalog, agentId = getSelectedAgentId()) {
      persistScopedToolCatalog(agentId, catalog);
    },
    refreshToolCatalog(agentId = getSelectedAgentId()) {
      return refreshScopedToolCatalog(agentId);
    },
    updateToolLabels(agentId, toolName, labels) {
      return updateScopedToolLabels(agentId, toolName, labels);
    },
    loadRuleList: loadScopedRuleList,
    persistRuleList(rules, agentId = getSelectedAgentId()) {
      persistScopedRuleList(agentId, rules);
    },
    refreshRuleList(agentId = getSelectedAgentId()) {
      return refreshScopedRuleList(agentId);
    },
    listAgentAvailablePlugins(agentId = getSelectedAgentId()) {
      return listAgentAvailablePlugins(agentId);
    },
    getAgentPluginConfig(agentId = getSelectedAgentId()) {
      return getAgentPluginConfig(agentId);
    },
    updateAgentPluginConfig(agentId, config, clientConfig = null) {
      return updateAgentPluginConfig(agentId, config, clientConfig);
    },
    clearToolCache: clearScopedAgentCache,
    clearScopedAgentCache,
    getLastAgentSyncTime() {
      return localStorage.getItem(AGENT_SYNC_KEY);
    },
    getLastToolSyncTime() {
      return localStorage.getItem(SCOPED_TOOL_SYNC_KEY);
    },
    getLastRuleSyncTime() {
      return localStorage.getItem(SCOPED_RULE_SYNC_KEY);
    },
    getScopedAgentId() {
      return localStorage.getItem(SCOPED_AGENT_ID_KEY) || "";
    },
  };
})();

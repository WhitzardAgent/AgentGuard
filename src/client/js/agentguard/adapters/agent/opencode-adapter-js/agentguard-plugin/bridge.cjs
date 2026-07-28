"use strict";

const crypto = require("node:crypto");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");

const {
  AuditLogger,
  AuditRecorder,
  ClientSyncBuffer,
  DecisionType,
  EventType,
  GuardDecision,
  agentIdentityKeyId,
  buildAgentRegistrationPayload,
  loadOrCreateDPoPKey,
  loadOrCreateAgentKey,
  PluginManager,
  PolicySnapshot,
  RemoteGuardClient,
  RuntimeContext,
  RuntimeEvent,
  UGuardEnforcer,
} = require("./agentguard-runtime.cjs");
const {
  DEFAULT_OPTIONS: DEFAULT_SKILL_SCAN_OPTIONS,
  resolveScanPath,
  scanSkillRoots,
} = require("../../shared/inventory/skill_scanner.cjs");
const {
  DEFAULT_OPTIONS: DEFAULT_MCP_SCAN_OPTIONS,
  scanMcpServerMap,
} = require("../../shared/inventory/mcp_scanner.cjs");

const DEFAULT_WINDOW_SIZE = 8;
const DEFAULT_POLICY = "builtin";
const DEFAULT_REMOTE_UNAVAILABLE_MODE = "fail_closed";
const DEFAULT_BLOCK_MESSAGE = "Request blocked by AgentGuard policy.";
const DEFAULT_SANITIZED_MESSAGE = "Content removed by AgentGuard.";
const DEFAULT_PHASE_CONFIG_PATH = path.resolve(__dirname, "../../../../../../../../config/plugins.json");
const DEFAULT_RUNTIME_REFRESH_LEAD_S = 45;
const DEFAULT_PROVIDER_INSTANCE_ID = "opencode-local";
const DEFAULT_INVENTORY_MONITOR_DEBOUNCE_MS = 750;
const DEFAULT_INVENTORY_MONITOR_POLL_INTERVAL_MS = 5_000;
const AGENT_REGISTRATION_CACHE_FILE = "opencode-agent-registrations.json";
const MAX_TIMER_DELAY_MS = 2_147_483_647;
const PRE_GUARD_PHASES = new Set(["tool_before", "llm_before"]);

function normalizePluginConfig(raw = {}) {
  const { config, configDir } = loadPluginConfigSource(raw);
  const phases = resolvePhaseConfig(config, configDir);
  const serverUrl = asNonEmptyString(config.serverUrl);
  return {
    serverUrl,
    apiKey: resolveEnvBackedValue(config.apiKey, config.apiKeyEnvVar),
    userTicket: resolveEnvBackedValue(config.userTicket, config.userTicketEnvVar),
    policy: asNonEmptyString(config.policy) || DEFAULT_POLICY,
    auditPath: resolveOptionalPath(config.auditPath, configDir),
    phases,
    remoteUnavailableMode:
      asNonEmptyString(config.remoteUnavailableMode) || DEFAULT_REMOTE_UNAVAILABLE_MODE,
    remoteTimeoutS: asPositiveNumber(config.remoteTimeoutS ?? config.timeoutS, 5.0),
    remoteRetries: asNonNegativeInteger(config.remoteRetries ?? config.retries, 2),
    runtimeRefreshLeadS: asPositiveNumber(config.runtimeRefreshLeadS, DEFAULT_RUNTIME_REFRESH_LEAD_S),
    providerInstanceId:
      asNonEmptyString(config.providerInstanceId || config.provider_instance_id) ||
      DEFAULT_PROVIDER_INSTANCE_ID,
    opencodeAgent: asNonEmptyString(config.opencodeAgent || config.openCodeAgent),
    opencodeAgents: normalizeOpenCodeAgentCatalog(
      config.opencodeAgents || config.openCodeAgents || config.agents,
    ),
    opencodeModel: asNonEmptyString(config.opencodeModel || config.openCodeModel),
    agentDisplayName: asNonEmptyString(config.agentDisplayName || config.displayAgentId),
    agentDescription: asNonEmptyString(config.agentDescription),
    skillScan: normalizeSkillScanConfig(config.skillScan, configDir),
    mcpScan: normalizeMcpScanConfig(config.mcpScan, configDir),
    windowSize: asPositiveInteger(config.windowSize, DEFAULT_WINDOW_SIZE),
    blockMessage: asNonEmptyString(config.blockMessage) || DEFAULT_BLOCK_MESSAGE,
    hasRemoteConfigured: Boolean(serverUrl),
  };
}

function normalizeSkillScanConfig(value, configDir) {
  const input = value && typeof value === "object" && !Array.isArray(value) ? value : {};
  const monitor = input.monitor && typeof input.monitor === "object" && !Array.isArray(input.monitor)
    ? input.monitor
    : {};
  const baseDir = configDir || process.cwd();
  return {
    enabled: input.enabled === true,
    roots: normalizeStringArray(input.roots).map((item) => resolveScanPath(item, baseDir)),
    agentIds: normalizeStringArray(input.agentIds || input.agents),
    discoverDefaults: input.discoverDefaults !== false,
    baseDir,
    maxFileBytes: asPositiveInteger(input.maxFileBytes, DEFAULT_SKILL_SCAN_OPTIONS.maxFileBytes),
    maxTotalBytesPerSkill: asPositiveInteger(
      input.maxTotalBytesPerSkill,
      DEFAULT_SKILL_SCAN_OPTIONS.maxTotalBytesPerSkill,
    ),
    maxFilesPerSkill: asPositiveInteger(
      input.maxFilesPerSkill,
      DEFAULT_SKILL_SCAN_OPTIONS.maxFilesPerSkill,
    ),
    excludeDirs: normalizeStringArray(input.excludeDirs, DEFAULT_SKILL_SCAN_OPTIONS.excludeDirs),
    excludeFiles: normalizeStringArray(input.excludeFiles, DEFAULT_SKILL_SCAN_OPTIONS.excludeFiles),
    textExtensions: normalizeStringArray(
      input.textExtensions,
      DEFAULT_SKILL_SCAN_OPTIONS.textExtensions,
    ),
    followSymlinks: input.followSymlinks === true,
    monitor: input.monitor === false ? false : monitor.enabled !== false,
    monitorDebounceMs: asPositiveInteger(
      input.monitorDebounceMs ?? input.debounceMs ?? monitor.debounceMs,
      DEFAULT_INVENTORY_MONITOR_DEBOUNCE_MS,
    ),
    monitorPollIntervalMs: asPositiveInteger(
      input.monitorPollIntervalMs ?? input.pollIntervalMs ?? monitor.pollIntervalMs,
      DEFAULT_INVENTORY_MONITOR_POLL_INTERVAL_MS,
    ),
  };
}

function normalizeMcpScanConfig(value, configDir) {
  const input = value && typeof value === "object" && !Array.isArray(value) ? value : {};
  const monitor = input.monitor && typeof input.monitor === "object" && !Array.isArray(input.monitor)
    ? input.monitor
    : {};
  const baseDir = configDir || process.cwd();
  return {
    enabled: input.enabled === true,
    agentIds: normalizeStringArray(input.agentIds || input.agents),
    baseDir,
    maxFileBytes: asPositiveInteger(input.maxFileBytes, DEFAULT_MCP_SCAN_OPTIONS.maxFileBytes),
    maxTotalBytesPerServer: asPositiveInteger(
      input.maxTotalBytesPerServer,
      DEFAULT_MCP_SCAN_OPTIONS.maxTotalBytesPerServer,
    ),
    maxFilesPerServer: asPositiveInteger(
      input.maxFilesPerServer,
      DEFAULT_MCP_SCAN_OPTIONS.maxFilesPerServer,
    ),
    excludeDirs: normalizeStringArray(input.excludeDirs, DEFAULT_MCP_SCAN_OPTIONS.excludeDirs),
    excludeFiles: normalizeStringArray(input.excludeFiles, DEFAULT_MCP_SCAN_OPTIONS.excludeFiles),
    textExtensions: normalizeStringArray(
      input.textExtensions,
      DEFAULT_MCP_SCAN_OPTIONS.textExtensions,
    ),
    followSymlinks: input.followSymlinks === true,
    monitor: input.monitor === false ? false : monitor.enabled !== false,
    monitorDebounceMs: asPositiveInteger(
      input.monitorDebounceMs ?? input.debounceMs ?? monitor.debounceMs,
      DEFAULT_INVENTORY_MONITOR_DEBOUNCE_MS,
    ),
    monitorPollIntervalMs: asPositiveInteger(
      input.monitorPollIntervalMs ?? input.pollIntervalMs ?? monitor.pollIntervalMs,
      DEFAULT_INVENTORY_MONITOR_POLL_INTERVAL_MS,
    ),
  };
}

function loadPluginConfigSource(raw = {}) {
  const config = raw && typeof raw === "object" && !Array.isArray(raw) ? { ...raw } : {};
  const configPath = asNonEmptyString(config.configPath);
  if (!configPath) {
    return { config, configDir: undefined };
  }
  const resolvedPath = path.resolve(configPath);
  return {
    config: loadJsonObject(resolvedPath, "AgentGuard OpenCode config"),
    configDir: path.dirname(resolvedPath),
  };
}

function resolvePhaseConfig(config, configDir) {
  if (config.phases && typeof config.phases === "object" && !Array.isArray(config.phases)) {
    return normalizePhaseConfig(config.phases);
  }
  const phaseConfigPath = resolveOptionalPath(config.phaseConfigPath, configDir) || DEFAULT_PHASE_CONFIG_PATH;
  const parsed = loadJsonObject(phaseConfigPath, "AgentGuard phase config");
  return normalizePhaseConfig(parsed.phases);
}

function normalizePhaseConfig(phases) {
  const result = {};
  for (const phase of ["llm_before", "llm_after", "tool_before", "tool_after"]) {
    const value = phases && typeof phases === "object" ? phases[phase] : null;
    result[phase] = normalizePhaseEntry(value);
  }
  return result;
}

function normalizePhaseEntry(value) {
  const data = value && typeof value === "object" && !Array.isArray(value) ? value : {};
  return {
    client: Array.isArray(data.client) ? [...data.client] : Array.isArray(data.local) ? [...data.local] : [],
    server: Array.isArray(data.server) ? [...data.server] : Array.isArray(data.remote) ? [...data.remote] : [],
  };
}

function loadJsonObject(filePath, label) {
  const source = fs.readFileSync(filePath, "utf8");
  let parsed;
  try {
    parsed = JSON.parse(source);
  } catch (error) {
    error.message = `Failed to parse ${label} at ${filePath}: ${error.message}`;
    throw error;
  }
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
    throw new TypeError(`${label} at ${filePath} must be a JSON object.`);
  }
  return parsed;
}

function resolveEnvBackedValue(value, envVar) {
  const direct = asNonEmptyString(value);
  if (direct) {
    return direct;
  }
  const envName = asNonEmptyString(envVar);
  return envName ? asNonEmptyString(process.env[envName]) : null;
}

function resolveOptionalPath(value, configDir) {
  const text = asNonEmptyString(value);
  if (!text) {
    return null;
  }
  if (path.isAbsolute(text)) {
    return text;
  }
  return path.resolve(configDir || process.cwd(), text);
}

function normalizeStringArray(value, fallback = []) {
  if (!Array.isArray(value)) {
    return [...fallback];
  }
  return value
    .filter((item) => typeof item === "string" && item.trim())
    .map((item) => item.trim());
}

function buildPluginConfigPayload(config) {
  return { phases: config.phases };
}

function createRuntimeEvent({ eventType, context, payload, metadata = {} }) {
  return new RuntimeEvent({
    event_type: eventType,
    context,
    payload,
    metadata,
  });
}

function shouldFailClosed(config, phase) {
  return (
    config.hasRemoteConfigured &&
    config.remoteUnavailableMode === "fail_closed" &&
    PRE_GUARD_PHASES.has(phase)
  );
}

function isRemoteUnavailableDecision(decision) {
  return (
    decision &&
    decision.decision_type === DecisionType.REQUIRE_REMOTE_REVIEW &&
    decision.metadata &&
    decision.metadata.route === "remote_unavailable"
  );
}

function runtimeAuthFresh(runtimeAuth) {
  return Boolean(
    runtimeAuth &&
      runtimeAuth.session_token &&
      runtimeAuth.expires_at &&
      runtimeAuth.expires_at > Date.now() / 1000 + 60,
  );
}

function clearRuntimeAuthSession(runtimeAuth) {
  if (!runtimeAuth) {
    return;
  }
  runtimeAuth.session_id = null;
  runtimeAuth.agent_id = null;
  runtimeAuth.user_id = null;
  runtimeAuth.session_token = null;
  runtimeAuth.expires_at = 0;
  runtimeAuth.external_session_id = null;
}

function resetRemoteBreaker(remote) {
  if (!remote || !remote.breaker) {
    return;
  }
  remote.breaker.failures = 0;
  remote.breaker.opened_at = 0;
}

function isRuntimeAuthUnavailableError(error) {
  const message = String(error && error.message ? error.message : error || "");
  return (
    /remote guard call failed:\s*HTTP 401/i.test(message) ||
    /runtime token expired/i.test(message) ||
    /runtime token is not active/i.test(message) ||
    /runtime session is not active/i.test(message) ||
    /DPoP key does not match runtime token/i.test(message)
  );
}

function buildRuntimeAuthState(config, key, identity = {}) {
  if (!config.serverUrl || !config.userTicket) {
    return null;
  }
  const state = {
    provider: "opencode",
    key,
    user_ticket: config.userTicket,
    ticket_consumed: false,
    session_id: null,
    agent_id: null,
    user_id: null,
    session_token: null,
    expires_at: 0,
    dpop_key_id: null,
    dpop_key: null,
    startup: null,
    refresh_timer: null,
    proof(method, url, accessToken = null) {
      if (!state.dpop_key) {
        state.dpop_key_id = state.dpop_key_id || `opencode:${key}`;
        state.dpop_key = loadOrCreateDPoPKey(state.dpop_key_id);
      }
      return state.dpop_key.proof(method, url, accessToken);
    },
  };
  return state;
}

function epochSeconds(value) {
  if (Number.isFinite(Number(value))) {
    return Number(value);
  }
  const parsed = Date.parse(String(value || ""));
  return Number.isFinite(parsed) ? parsed / 1000 : 0;
}

function runtimeAuthRefreshDelayMs(runtimeAuth, refreshLeadS = DEFAULT_RUNTIME_REFRESH_LEAD_S) {
  const expiresAt = Number(runtimeAuth && runtimeAuth.expires_at);
  if (!Number.isFinite(expiresAt) || expiresAt <= 0) {
    return null;
  }
  const lead = Number.isFinite(Number(refreshLeadS)) && Number(refreshLeadS) > 0
    ? Number(refreshLeadS)
    : DEFAULT_RUNTIME_REFRESH_LEAD_S;
  const delayS = Math.max(5, expiresAt - Date.now() / 1000 - lead);
  return Math.min(Math.floor(delayS * 1000), MAX_TIMER_DELAY_MS);
}

function openCodeSessionIdentityFromEvent(input = {}) {
  const event = input && typeof input === "object" ? input.event || input : {};
  const properties = event && typeof event === "object" ? event.properties || event.data || {} : {};
  const info = properties && typeof properties.info === "object" ? properties.info : {};
  return {
    sessionID:
      asNonEmptyString(properties.sessionID) ||
      asNonEmptyString(properties.sessionId) ||
      asNonEmptyString(info.id),
    agent: asNonEmptyString(info.agent),
    model: normalizeModel(info.model),
    projectID:
      asNonEmptyString(info.projectID) ||
      asNonEmptyString(info.projectId),
    directory: asNonEmptyString(info.directory),
    worktree: asNonEmptyString(info.worktree),
    projectName: asNonEmptyString(info.project && info.project.name),
  };
}

function discoverOpenCodeAgents(config, opencode = null) {
  const configured = normalizeOpenCodeAgentCatalog(config && config.opencodeAgents);
  if (configured.length) {
    return configured;
  }
  const runtimeAgents = [];
  const runtime = opencode && typeof opencode === "object" ? opencode : {};
  if (Array.isArray(runtime.agents)) {
    runtimeAgents.push(...runtime.agents);
  }
  if (runtime.agent && typeof runtime.agent === "object") {
    runtimeAgents.push(runtime.agent);
  }
  const normalizedRuntimeAgents = normalizeOpenCodeAgentCatalog(runtimeAgents);
  if (normalizedRuntimeAgents.length) {
    return normalizedRuntimeAgents;
  }
  const configuredAgent = asNonEmptyString(config && config.opencodeAgent);
  if (configuredAgent) {
    return normalizeOpenCodeAgentCatalog([{ id: configuredAgent }]);
  }
  return [];
}

function normalizeOpenCodeAgentCatalog(items) {
  const list = Array.isArray(items) ? items : items ? [items] : [];
  const byExternalId = new Map();
  for (const item of list) {
    const agent = normalizeOpenCodeCatalogAgent(item);
    if (agent && !byExternalId.has(agent.externalAgentId)) {
      byExternalId.set(agent.externalAgentId, agent);
    }
  }
  return [...byExternalId.values()];
}

function normalizeOpenCodeCatalogAgent(item) {
  const source = item && typeof item === "object" && !Array.isArray(item)
    ? item
    : { id: item };
  const id = asNonEmptyString(
    source.id ||
      source.agent ||
      source.agentID ||
      source.agentId ||
      source.name,
  );
  if (!id) {
    return null;
  }
  const externalAgentId =
    asNonEmptyString(source.external_agent_id || source.externalAgentId) ||
    openCodeExternalAgentId(id);
  return {
    id,
    externalAgentId,
    name: asNonEmptyString(source.name) || `OpenCode ${id}`,
    description: asNonEmptyString(source.description) || `OpenCode agent: ${id}`,
    model: asNonEmptyString(source.model),
    projectID: asNonEmptyString(source.projectID || source.projectId),
    directory: asNonEmptyString(source.directory),
    worktree: asNonEmptyString(source.worktree),
    isDefault: source.isDefault === true,
    permission:
      source.permission && typeof source.permission === "object" && !Array.isArray(source.permission)
        ? { ...source.permission }
        : {},
    tools:
      source.tools && typeof source.tools === "object" && !Array.isArray(source.tools)
        ? { ...source.tools }
        : {},
  };
}

function ensureCatalogContainsAgent(catalog, agentName) {
  const id = asNonEmptyString(agentName) || "default";
  const existing = normalizeOpenCodeAgentCatalog(catalog);
  const externalAgentId = openCodeExternalAgentId(id);
  if (existing.some((agent) => agent.id === id || agent.externalAgentId === externalAgentId)) {
    return existing;
  }
  return [
    ...existing,
    {
      id,
      externalAgentId,
      name: `OpenCode ${id}`,
      description: `OpenCode agent: ${id}`,
    },
  ];
}

function buildOpenCodeAgentRegistration(config, agent, extraMetadata = {}) {
  const providerInstanceId = asNonEmptyString(config && config.providerInstanceId) || DEFAULT_PROVIDER_INSTANCE_ID;
  const agentType = "agent";
  const externalAgentId =
    asNonEmptyString(agent && agent.externalAgentId) ||
    openCodeExternalAgentId(agent && agent.id);
  const keyId = agentIdentityKeyId({
    provider: "opencode",
    provider_instance_id: providerInstanceId,
    tenant_id: null,
    external_agent_id: externalAgentId,
    agent_type: agentType,
  });
  const payload = buildAgentRegistrationPayload({
    provider: "opencode",
    provider_instance_id: providerInstanceId,
    tenant_id: null,
    external_agent_id: externalAgentId,
    agent_type: agentType,
    name: asNonEmptyString(agent && agent.name) || `OpenCode ${(agent && agent.id) || "default"}`,
    description: asNonEmptyString(agent && agent.description) || `OpenCode agent: ${(agent && agent.id) || "default"}`,
    metadata: {
      adapter: "opencode",
      external_provider: "opencode",
      external_agent_id: externalAgentId,
      display_agent_id: externalAgentId,
      provider_instance_id: providerInstanceId,
      opencode_agent: asNonEmptyString(agent && agent.id) || "default",
      opencode_agent_name: asNonEmptyString(agent && agent.name) || asNonEmptyString(agent && agent.id) || "default",
      opencode_model: asNonEmptyString(agent && agent.model) || null,
      opencode_project_id: asNonEmptyString(agent && agent.projectID) || null,
      opencode_directory: asNonEmptyString(agent && agent.directory) || null,
      opencode_worktree: asNonEmptyString(agent && agent.worktree) || null,
      opencode_is_default: Boolean(agent && agent.isDefault),
      ...(extraMetadata && typeof extraMetadata === "object" && !Array.isArray(extraMetadata)
        ? extraMetadata
        : {}),
    },
  });
  return { payload, keyId, externalAgentId };
}

function registrationMapFromBootstrap(response, registrations) {
  const byExternalId = new Map();
  const keyIds = new Map(registrations.map((item) => [item.externalAgentId, item.keyId]));
  const agents = Array.isArray(response && response.agents) ? response.agents : [];
  for (const agent of agents) {
    const externalAgentId = asNonEmptyString(agent.external_agent_id || agent.externalAgentId);
    const agentId = asNonEmptyString(agent.agent_id || agent.agentId);
    if (!externalAgentId || !agentId) {
      continue;
    }
    byExternalId.set(externalAgentId, {
      external_agent_id: externalAgentId,
      agent_id: agentId,
      agent_identity_code: asNonEmptyString(agent.agent_identity_code),
      public_key_thumbprint: asNonEmptyString(agent.public_key_thumbprint),
      agent_identity_key_id: keyIds.get(externalAgentId),
      user_bound: agent.user_bound !== false,
    });
  }
  return byExternalId;
}

function agentRegistrationCacheKey(config, externalAgentId) {
  const providerInstanceId = asNonEmptyString(config && config.providerInstanceId) || DEFAULT_PROVIDER_INSTANCE_ID;
  return `${providerInstanceId}\x1f${asNonEmptyString(externalAgentId) || ""}`;
}

function agentRegistrationCachePath() {
  const rawDir = String(
    process.env.AGENTGUARD_AGENT_KEY_DIR || path.join(os.homedir(), ".agentguard", "agent_keys"),
  ).replace(/^~(?=$|\/)/, os.homedir());
  return path.join(path.resolve(rawDir), AGENT_REGISTRATION_CACHE_FILE);
}

function normalizeCachedAgentRegistration(item) {
  if (!item || typeof item !== "object" || Array.isArray(item)) {
    return null;
  }
  const externalAgentId = asNonEmptyString(item.external_agent_id || item.externalAgentId);
  const agentId = asNonEmptyString(item.agent_id || item.agentId);
  if (!externalAgentId || !agentId) {
    return null;
  }
  return {
    external_agent_id: externalAgentId,
    agent_id: agentId,
    agent_identity_code: asNonEmptyString(item.agent_identity_code || item.agentIdentityCode),
    public_key_thumbprint: asNonEmptyString(item.public_key_thumbprint || item.publicKeyThumbprint),
    agent_identity_key_id: asNonEmptyString(item.agent_identity_key_id || item.agentIdentityKeyId)
      || agentIdentityKeyId({
        provider: "opencode",
        provider_instance_id: asNonEmptyString(item.provider_instance_id || item.providerInstanceId) || DEFAULT_PROVIDER_INSTANCE_ID,
        tenant_id: null,
        external_agent_id: externalAgentId,
        agent_type: "agent",
      }),
    user_bound: item.user_bound !== false && item.userBound !== false,
  };
}

function loadPersistedAgentRegistrations(config, logger = console) {
  const registrations = new Map();
  const cachePath = agentRegistrationCachePath();
  if (!fs.existsSync(cachePath)) {
    return registrations;
  }
  try {
    const parsed = JSON.parse(fs.readFileSync(cachePath, "utf8"));
    const items = Array.isArray(parsed && parsed.registrations) ? parsed.registrations : [];
    for (const item of items) {
      const registration = normalizeCachedAgentRegistration(item);
      if (!registration) {
        continue;
      }
      registrations.set(agentRegistrationCacheKey(config, registration.external_agent_id), registration);
    }
  } catch (error) {
    logger.warn?.("AgentGuard OpenCode adapter failed to read cached agent registrations.", error);
  }
  return registrations;
}

function persistAgentRegistrations(config, registrations, logger = console) {
  const cachePath = agentRegistrationCachePath();
  const merged = loadPersistedAgentRegistrations(config, logger);
  for (const registration of registrations instanceof Map ? registrations.values() : []) {
    const normalized = normalizeCachedAgentRegistration({
      ...registration,
      provider_instance_id: asNonEmptyString(config && config.providerInstanceId) || DEFAULT_PROVIDER_INSTANCE_ID,
    });
    if (!normalized) {
      continue;
    }
    merged.set(agentRegistrationCacheKey(config, normalized.external_agent_id), normalized);
  }
  const payload = {
    version: 1,
    registrations: [...merged.values()].sort((a, b) =>
      String(a.external_agent_id).localeCompare(String(b.external_agent_id))),
  };
  try {
    fs.mkdirSync(path.dirname(cachePath), { recursive: true });
    const tmpPath = `${cachePath}.${process.pid}.tmp`;
    fs.writeFileSync(tmpPath, `${JSON.stringify(payload, null, 2)}\n`, { mode: 0o600 });
    fs.renameSync(tmpPath, cachePath);
    try {
      fs.chmodSync(cachePath, 0o600);
    } catch (_) {
      // Best effort on platforms that do not support chmod.
    }
  } catch (error) {
    logger.warn?.("AgentGuard OpenCode adapter failed to persist cached agent registrations.", error);
  }
}

function emptySkillScanResult(config, roots = [], diagnostics = []) {
  return {
    enabled: Boolean(config && config.enabled),
    skills: [],
    diagnostics,
    summary: {
      roots,
      skill_count: 0,
      diagnostic_count: diagnostics.length,
    },
  };
}

function emptyMcpScanResult(config, diagnostics = []) {
  return {
    enabled: Boolean(config && config.enabled),
    mcps: [],
    diagnostics,
    summary: {
      mcp_count: 0,
      diagnostic_count: diagnostics.length,
    },
  };
}

function pathContains(parentPath, childPath) {
  const parent = asNonEmptyString(parentPath);
  const child = asNonEmptyString(childPath);
  if (!parent || !child) {
    return false;
  }
  const relative = path.relative(path.resolve(parent), path.resolve(child));
  return relative === "" || (!relative.startsWith("..") && !path.isAbsolute(relative));
}

function ancestorDirectories(startPath, stopPath) {
  const start = asNonEmptyString(startPath);
  if (!start) {
    return [];
  }
  const resolvedStart = path.resolve(start);
  const resolvedStop = asNonEmptyString(stopPath) ? path.resolve(stopPath) : resolvedStart;
  if (!pathContains(resolvedStop, resolvedStart)) {
    return [resolvedStart];
  }
  const directories = [];
  let current = resolvedStart;
  while (true) {
    directories.push(current);
    if (current === resolvedStop) {
      break;
    }
    const parent = path.dirname(current);
    if (parent === current) {
      break;
    }
    current = parent;
  }
  return directories;
}

function isDirectory(filePath) {
  try {
    return fs.statSync(filePath).isDirectory();
  } catch (_) {
    return false;
  }
}

function uniqueExistingDirectories(items) {
  const seen = new Set();
  const result = [];
  for (const item of items) {
    const resolved = asNonEmptyString(item) ? path.resolve(item) : null;
    if (!resolved || seen.has(resolved) || !isDirectory(resolved)) {
      continue;
    }
    seen.add(resolved);
    result.push(resolved);
  }
  return result.sort((left, right) => left.localeCompare(right));
}

function discoverOpenCodeSkillRoots(scanConfig, openCodeConfig, opencode) {
  const config = scanConfig || {};
  const runtime = opencode && typeof opencode === "object" ? opencode : {};
  const mergedConfig = openCodeConfig && typeof openCodeConfig === "object" ? openCodeConfig : {};
  const directory =
    asNonEmptyString(runtime.directory) ||
    asNonEmptyString(runtime.worktree) ||
    config.baseDir ||
    process.cwd();
  const worktree = asNonEmptyString(runtime.worktree) || directory;
  const roots = [...(Array.isArray(config.roots) ? config.roots : [])];

  if (config.discoverDefaults !== false) {
    for (const ancestor of ancestorDirectories(directory, worktree)) {
      roots.push(
        path.join(ancestor, ".opencode", "skill"),
        path.join(ancestor, ".opencode", "skills"),
        path.join(ancestor, ".claude", "skills"),
        path.join(ancestor, ".agents", "skills"),
      );
    }
    const configHome = asNonEmptyString(process.env.XDG_CONFIG_HOME)
      ? path.resolve(process.env.XDG_CONFIG_HOME)
      : path.join(os.homedir(), ".config");
    roots.push(
      path.join(configHome, "opencode", "skill"),
      path.join(configHome, "opencode", "skills"),
      path.join(os.homedir(), ".claude", "skills"),
      path.join(os.homedir(), ".agents", "skills"),
    );
  }

  const customPaths =
    mergedConfig.skills &&
    typeof mergedConfig.skills === "object" &&
    !Array.isArray(mergedConfig.skills)
      ? normalizeStringArray(mergedConfig.skills.paths)
      : [];
  for (const customPath of customPaths) {
    roots.push(resolveScanPath(customPath, directory));
  }
  return uniqueExistingDirectories(roots);
}

function stripJsoncComments(source) {
  let output = "";
  let inString = false;
  let escaped = false;
  let lineComment = false;
  let blockComment = false;
  for (let index = 0; index < source.length; index += 1) {
    const character = source[index];
    const next = source[index + 1];
    if (lineComment) {
      if (character === "\n" || character === "\r") {
        lineComment = false;
        output += character;
      } else {
        output += " ";
      }
      continue;
    }
    if (blockComment) {
      if (character === "*" && next === "/") {
        output += "  ";
        index += 1;
        blockComment = false;
      } else {
        output += character === "\n" || character === "\r" ? character : " ";
      }
      continue;
    }
    if (inString) {
      output += character;
      if (escaped) {
        escaped = false;
      } else if (character === "\\") {
        escaped = true;
      } else if (character === "\"") {
        inString = false;
      }
      continue;
    }
    if (character === "\"") {
      inString = true;
      output += character;
      continue;
    }
    if (character === "/" && next === "/") {
      output += "  ";
      index += 1;
      lineComment = true;
      continue;
    }
    if (character === "/" && next === "*") {
      output += "  ";
      index += 1;
      blockComment = true;
      continue;
    }
    output += character;
  }
  return output;
}

function stripJsoncTrailingCommas(source) {
  let output = "";
  let inString = false;
  let escaped = false;
  for (let index = 0; index < source.length; index += 1) {
    const character = source[index];
    if (inString) {
      output += character;
      if (escaped) {
        escaped = false;
      } else if (character === "\\") {
        escaped = true;
      } else if (character === "\"") {
        inString = false;
      }
      continue;
    }
    if (character === "\"") {
      inString = true;
      output += character;
      continue;
    }
    if (character === ",") {
      let cursor = index + 1;
      while (cursor < source.length && /\s/.test(source[cursor])) {
        cursor += 1;
      }
      if (source[cursor] === "}" || source[cursor] === "]") {
        continue;
      }
    }
    output += character;
  }
  return output;
}

function parseOpenCodeConfigSource(source, sourcePath) {
  try {
    const parsed = JSON.parse(stripJsoncTrailingCommas(stripJsoncComments(source)));
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
      throw new TypeError("configuration must be a JSON object");
    }
    return parsed;
  } catch (error) {
    error.message = `Failed to parse OpenCode config at ${sourcePath}: ${error.message}`;
    throw error;
  }
}

function existingOpenCodeConfigFiles(opencode) {
  const runtime = opencode && typeof opencode === "object" ? opencode : {};
  const directory =
    asNonEmptyString(runtime.directory) ||
    asNonEmptyString(runtime.worktree) ||
    process.cwd();
  const worktree = asNonEmptyString(runtime.worktree) || directory;
  const ancestors = ancestorDirectories(directory, worktree).reverse();
  const configHome = asNonEmptyString(process.env.XDG_CONFIG_HOME)
    ? path.resolve(process.env.XDG_CONFIG_HOME)
    : path.join(os.homedir(), ".config");
  const candidates = [
    path.join(configHome, "opencode", "opencode.json"),
    path.join(configHome, "opencode", "opencode.jsonc"),
  ];
  const customConfigPath = asNonEmptyString(process.env.OPENCODE_CONFIG);
  if (customConfigPath) {
    candidates.push(resolveScanPath(customConfigPath, directory));
  }
  for (const ancestor of ancestors) {
    candidates.push(
      path.join(ancestor, "opencode.json"),
      path.join(ancestor, "opencode.jsonc"),
    );
  }
  for (const ancestor of ancestors) {
    candidates.push(
      path.join(ancestor, ".opencode", "opencode.json"),
      path.join(ancestor, ".opencode", "opencode.jsonc"),
    );
  }
  const seen = new Set();
  return candidates.filter((candidate) => {
    const resolved = path.resolve(candidate);
    if (seen.has(resolved) || !fs.existsSync(resolved)) {
      return false;
    }
    seen.add(resolved);
    return true;
  });
}

function mergeOpenCodeMcpServerMaps(target, source) {
  const output = { ...(target || {}) };
  if (!source || typeof source !== "object" || Array.isArray(source)) {
    return output;
  }
  for (const [name, config] of Object.entries(source)) {
    const previous = output[name];
    output[name] =
      previous &&
      typeof previous === "object" &&
      !Array.isArray(previous) &&
      config &&
      typeof config === "object" &&
      !Array.isArray(config)
        ? { ...previous, ...config }
        : config;
  }
  return output;
}

function loadCurrentOpenCodeMcpConfig(opencode) {
  const configPaths = existingOpenCodeConfigFiles(opencode);
  const diagnostics = [];
  let servers = {};
  let hasMcpConfig = false;
  let lastMcpConfigPath = "";
  for (const configPath of configPaths) {
    try {
      const parsed = parseOpenCodeConfigSource(fs.readFileSync(configPath, "utf8"), configPath);
      if (Object.hasOwn(parsed, "mcp")) {
        hasMcpConfig = true;
        lastMcpConfigPath = configPath;
        servers = mergeOpenCodeMcpServerMaps(servers, parsed.mcp);
      }
    } catch (error) {
      diagnostics.push({
        level: "error",
        path: configPath,
        reason: "opencode_config_parse_failed",
        message: String(error && error.message ? error.message : error),
      });
    }
  }
  const inlineSource = asNonEmptyString(process.env.OPENCODE_CONFIG_CONTENT);
  if (inlineSource) {
    try {
      const parsed = parseOpenCodeConfigSource(inlineSource, "OPENCODE_CONFIG_CONTENT");
      if (Object.hasOwn(parsed, "mcp")) {
        hasMcpConfig = true;
        lastMcpConfigPath = "OPENCODE_CONFIG_CONTENT";
        servers = mergeOpenCodeMcpServerMaps(servers, parsed.mcp);
      }
    } catch (error) {
      diagnostics.push({
        level: "error",
        path: "OPENCODE_CONFIG_CONTENT",
        reason: "opencode_config_parse_failed",
        message: String(error && error.message ? error.message : error),
      });
    }
  }
  return {
    ok: diagnostics.length === 0,
    hasMcpConfig,
    servers,
    names: new Set(Object.keys(servers)),
    configPaths,
    lastMcpConfigPath,
    diagnostics,
  };
}

function scanConfiguredOpenCodeSkills(scanConfig, openCodeConfig, opencode, logger = console) {
  const roots = discoverOpenCodeSkillRoots(scanConfig, openCodeConfig, opencode);
  if (!scanConfig || !scanConfig.enabled) {
    return emptySkillScanResult(scanConfig, roots);
  }
  try {
    const result = scanSkillRoots({
      roots,
      baseDir: scanConfig.baseDir,
      maxFileBytes: scanConfig.maxFileBytes,
      maxTotalBytesPerSkill: scanConfig.maxTotalBytesPerSkill,
      maxFilesPerSkill: scanConfig.maxFilesPerSkill,
      excludeDirs: scanConfig.excludeDirs,
      excludeFiles: scanConfig.excludeFiles,
      textExtensions: scanConfig.textExtensions,
      followSymlinks: scanConfig.followSymlinks,
      sourceFramework: "opencode",
    });
    return { enabled: true, ...result };
  } catch (error) {
    logger.warn?.("AgentGuard OpenCode adapter failed to scan skills.", error);
    return emptySkillScanResult(scanConfig, roots, [{
      level: "error",
      reason: "skill_scan_failed",
      message: String(error && error.message ? error.message : error),
    }]);
  }
}

function sanitizeOpenCodeToolName(value) {
  return String(value || "").replace(/[^a-zA-Z0-9_-]/g, "_");
}

function enrichMcpDescriptorsWithTools(mcps, toolDefinitions) {
  const definitions = toolDefinitions instanceof Map ? [...toolDefinitions.values()] : [];
  return (Array.isArray(mcps) ? mcps : []).map((descriptor) => {
    const prefix = `${sanitizeOpenCodeToolName(descriptor.name)}_`;
    const discovered = definitions
      .filter((tool) => tool.name.startsWith(prefix))
      .map((tool) => ({
        ...tool,
        runtime_name: tool.name,
        mcp_tool_name: tool.mcp_tool_name || tool.name.slice(prefix.length),
      }));
    const byName = new Map(
      [...(Array.isArray(descriptor.tools) ? descriptor.tools : []), ...discovered]
        .map((tool) => [tool.runtime_name || tool.name, tool]),
    );
    const tools = [...byName.values()].sort((left, right) => left.name.localeCompare(right.name));
    return {
      ...descriptor,
      tools,
      tool_count: tools.length,
    };
  });
}

function matchOpenCodeMcpRuntimeTool(mcpScan, runtimeToolName) {
  const toolName = asNonEmptyString(runtimeToolName);
  if (!toolName) {
    return null;
  }
  const candidates = (Array.isArray(mcpScan && mcpScan.mcps) ? mcpScan.mcps : [])
    .map((mcp) => ({
      mcp,
      prefix: `${sanitizeOpenCodeToolName(mcp.name)}_`,
    }))
    .filter((candidate) => toolName.startsWith(candidate.prefix))
    .sort((left, right) => right.prefix.length - left.prefix.length);
  if (!candidates.length) {
    return null;
  }
  const match = candidates[0];
  return {
    mcp: match.mcp,
    runtimeToolName: toolName,
    mcpToolName: toolName.slice(match.prefix.length),
  };
}

function mcpUniqueIdForRuntimeContext(context, mcp) {
  const explicit = asNonEmptyString(mcp && (mcp.mcp_unique_id || mcp.id));
  if (explicit) {
    return explicit;
  }
  const agentId = asNonEmptyString(context && context.agent_id);
  const sha256 = asNonEmptyString(mcp && mcp.sha256);
  if (agentId && sha256) {
    return `${agentId}:${sha256}`;
  }
  return "";
}

function buildOpenCodeMcpRuntimeMetadata(state, mcpScan, runtimeToolName) {
  const match = matchOpenCodeMcpRuntimeTool(mcpScan, runtimeToolName);
  if (!match) {
    return {};
  }
  const { mcp, mcpToolName } = match;
  return {
    toolSource: "mcp",
    sourceFramework: "opencode",
    mcp_unique_id: mcpUniqueIdForRuntimeContext(state && state.context, mcp),
    mcp_name: asNonEmptyString(mcp.name),
    mcp_tool_name: mcpToolName,
    mcp_match_confidence: "qualified_tool",
    mcp_transport: asNonEmptyString(mcp.transport),
    mcp_remote: Boolean(mcp.remote),
    mcp_config_path: asNonEmptyString(mcp.config_path),
    mcp_config_key: asNonEmptyString(mcp.config_key),
    mcp_root_path: asNonEmptyString(mcp.root_path),
    mcp_entry_file: asNonEmptyString(mcp.entry_file),
    mcp_url: asNonEmptyString(mcp.url),
    mcp_sha256: asNonEmptyString(mcp.sha256),
    mcp_source_status: asNonEmptyString(mcp.source_status),
  };
}

function jsonSchemaForObservedValue(value) {
  if (Array.isArray(value)) {
    return { type: "array" };
  }
  if (value === null) {
    return {};
  }
  if (typeof value === "number") {
    return { type: Number.isInteger(value) ? "integer" : "number" };
  }
  if (typeof value === "boolean") {
    return { type: "boolean" };
  }
  if (value && typeof value === "object") {
    return { type: "object" };
  }
  return { type: "string" };
}

function observedToolInputSchema(args) {
  const input = args && typeof args === "object" && !Array.isArray(args) ? args : {};
  const properties = Object.fromEntries(
    Object.entries(input).map(([name, value]) => [name, jsonSchemaForObservedValue(value)]),
  );
  return {
    type: "object",
    properties,
    required: Object.keys(properties),
  };
}

function scanConfiguredOpenCodeMcps(
  scanConfig,
  openCodeConfig,
  opencode,
  toolDefinitions,
  logger = console,
) {
  if (!scanConfig || !scanConfig.enabled) {
    return emptyMcpScanResult(scanConfig);
  }
  const runtime = opencode && typeof opencode === "object" ? opencode : {};
  const mergedConfig = openCodeConfig && typeof openCodeConfig === "object" ? openCodeConfig : {};
  const directory =
    asNonEmptyString(runtime.directory) ||
    asNonEmptyString(runtime.worktree) ||
    scanConfig.baseDir ||
    process.cwd();
  const configuredServers =
    mergedConfig.mcp && typeof mergedConfig.mcp === "object" && !Array.isArray(mergedConfig.mcp)
      ? mergedConfig.mcp
      : {};
  const enabledServers = Object.fromEntries(
    Object.entries(configuredServers).filter(([, server]) =>
      server && typeof server === "object" && !Array.isArray(server) && server.enabled !== false),
  );
  try {
    const result = scanMcpServerMap(enabledServers, {
      baseDir: directory,
      configDir: directory,
      configKey: "mcp",
      maxFileBytes: scanConfig.maxFileBytes,
      maxTotalBytesPerServer: scanConfig.maxTotalBytesPerServer,
      maxFilesPerServer: scanConfig.maxFilesPerServer,
      excludeDirs: scanConfig.excludeDirs,
      excludeFiles: scanConfig.excludeFiles,
      textExtensions: scanConfig.textExtensions,
      followSymlinks: scanConfig.followSymlinks,
      sourceFramework: "opencode",
    });
    const mcps = enrichMcpDescriptorsWithTools(result.servers, toolDefinitions);
    return {
      enabled: true,
      mcps,
      diagnostics: result.diagnostics,
      summary: {
        mcp_count: mcps.length,
        diagnostic_count: result.diagnostics.length,
      },
    };
  } catch (error) {
    logger.warn?.("AgentGuard OpenCode adapter failed to scan MCP servers.", error);
    return emptyMcpScanResult(scanConfig, [{
      level: "error",
      reason: "mcp_scan_failed",
      message: String(error && error.message ? error.message : error),
    }]);
  }
}

function inventoryDiagnosticsSignature(diagnostics) {
  return (Array.isArray(diagnostics) ? diagnostics : [])
    .map((item) => ({
      level: asNonEmptyString(item && item.level) || "",
      path: asNonEmptyString(item && item.path) || "",
      server: asNonEmptyString(item && item.server) || "",
      reason: asNonEmptyString(item && item.reason) || "",
      message: asNonEmptyString(item && item.message) || "",
    }))
    .sort((left, right) =>
      `${left.path}\x1f${left.server}\x1f${left.reason}`
        .localeCompare(`${right.path}\x1f${right.server}\x1f${right.reason}`),
    );
}

function skillScanSignature(skillScan) {
  const skills = (Array.isArray(skillScan && skillScan.skills) ? skillScan.skills : [])
    .map((skill) => ({
      name: asNonEmptyString(skill.name) || "",
      root_path: asNonEmptyString(skill.root_path) || "",
      sha256: asNonEmptyString(skill.sha256) || "",
      file_count: Number.isFinite(skill.file_count) ? skill.file_count : 0,
      total_size: Number.isFinite(skill.total_size) ? skill.total_size : 0,
    }))
    .sort((left, right) =>
      `${left.root_path}\x1f${left.name}`.localeCompare(`${right.root_path}\x1f${right.name}`),
    );
  const roots =
    skillScan && skillScan.summary && Array.isArray(skillScan.summary.roots)
      ? [...skillScan.summary.roots].sort()
      : [];
  return JSON.stringify({
    enabled: Boolean(skillScan && skillScan.enabled),
    roots,
    skills,
    diagnostics: inventoryDiagnosticsSignature(skillScan && skillScan.diagnostics),
  });
}

function mcpScanSignature(mcpScan) {
  const mcps = (Array.isArray(mcpScan && mcpScan.mcps) ? mcpScan.mcps : [])
    .map((mcp) => ({
      name: asNonEmptyString(mcp.name) || "",
      transport: asNonEmptyString(mcp.transport) || "",
      root_path: asNonEmptyString(mcp.root_path) || "",
      url: asNonEmptyString(mcp.url) || "",
      sha256: asNonEmptyString(mcp.sha256) || "",
      tool_names: (Array.isArray(mcp.tools) ? mcp.tools : [])
        .map((tool) => asNonEmptyString(tool && tool.name))
        .filter(Boolean)
        .sort(),
      file_count: Number.isFinite(mcp.file_count) ? mcp.file_count : 0,
      total_size: Number.isFinite(mcp.total_size) ? mcp.total_size : 0,
    }))
    .sort((left, right) =>
      `${left.root_path}\x1f${left.name}`.localeCompare(`${right.root_path}\x1f${right.name}`),
    );
  return JSON.stringify({
    enabled: Boolean(mcpScan && mcpScan.enabled),
    mcps,
    diagnostics: inventoryDiagnosticsSignature(mcpScan && mcpScan.diagnostics),
  });
}

function wildcardMatch(value, pattern) {
  const input = String(value || "").replaceAll("\\", "/");
  const matcher = String(pattern || "").replaceAll("\\", "/");
  let escaped = matcher
    .replace(/[.+^${}()|[\]\\]/g, "\\$&")
    .replace(/\*/g, ".*")
    .replace(/\?/g, ".");
  if (escaped.endsWith(" .*")) {
    escaped = `${escaped.slice(0, -3)}( .*)?`;
  }
  return new RegExp(`^${escaped}$`, process.platform === "win32" ? "si" : "s").test(input);
}

function permissionRulesFromConfig(config) {
  const source = config && typeof config === "object" && !Array.isArray(config) ? config : {};
  const rules = [];
  const tools = source.tools && typeof source.tools === "object" && !Array.isArray(source.tools)
    ? source.tools
    : {};
  for (const [permission, enabled] of Object.entries(tools)) {
    if (typeof enabled === "boolean") {
      rules.push({ permission, pattern: "*", action: enabled ? "allow" : "deny" });
    }
  }
  const permission =
    source.permission && typeof source.permission === "object" && !Array.isArray(source.permission)
      ? source.permission
      : {};
  for (const [name, value] of Object.entries(permission)) {
    if (typeof value === "string") {
      rules.push({ permission: name, pattern: "*", action: value });
      continue;
    }
    if (value && typeof value === "object" && !Array.isArray(value)) {
      for (const [pattern, action] of Object.entries(value)) {
        if (typeof action === "string") {
          rules.push({ permission: name, pattern, action });
        }
      }
    }
  }
  return rules;
}

function permissionAction(permission, pattern, ...configs) {
  const rules = configs.flatMap(permissionRulesFromConfig);
  const match = [...rules].reverse().find((rule) =>
    wildcardMatch(permission, rule.permission) && wildcardMatch(pattern, rule.pattern),
  );
  return match ? match.action : "ask";
}

function openCodeAgentConfig(openCodeConfig, agent) {
  const agents =
    openCodeConfig &&
    typeof openCodeConfig === "object" &&
    openCodeConfig.agent &&
    typeof openCodeConfig.agent === "object" &&
    !Array.isArray(openCodeConfig.agent)
      ? openCodeConfig.agent
      : {};
  const configured = agents[agent.id];
  return {
    ...(agent && typeof agent === "object" ? agent : {}),
    ...(configured && typeof configured === "object" && !Array.isArray(configured) ? configured : {}),
  };
}

function filterSkillScanForAgent(skillScan, openCodeConfig, agent) {
  const globalConfig = openCodeConfig && typeof openCodeConfig === "object" ? openCodeConfig : {};
  const agentConfig = openCodeAgentConfig(openCodeConfig, agent);
  const skills = (Array.isArray(skillScan && skillScan.skills) ? skillScan.skills : [])
    .filter((skill) => permissionAction("skill", skill.name, globalConfig, agentConfig) !== "deny");
  return {
    ...skillScan,
    skills,
    summary: {
      ...((skillScan && skillScan.summary) || {}),
      skill_count: skills.length,
    },
  };
}

function filterMcpScanForAgent(mcpScan, openCodeConfig, agent) {
  const globalConfig = openCodeConfig && typeof openCodeConfig === "object" ? openCodeConfig : {};
  const agentConfig = openCodeAgentConfig(openCodeConfig, agent);
  const mcps = (Array.isArray(mcpScan && mcpScan.mcps) ? mcpScan.mcps : [])
    .map((mcp) => {
      const tools = Array.isArray(mcp.tools) ? mcp.tools : [];
      const allowedTools = tools.filter((tool) =>
        permissionAction(tool.name, "*", globalConfig, agentConfig) !== "deny",
      );
      if (tools.length > 0 && allowedTools.length === 0) {
        return null;
      }
      if (tools.length === 0) {
        const probe = `${sanitizeOpenCodeToolName(mcp.name)}_agentguard_probe`;
        if (permissionAction(probe, "*", globalConfig, agentConfig) === "deny") {
          return null;
        }
      }
      return {
        ...mcp,
        tools: allowedTools,
        tool_count: allowedTools.length,
      };
    })
    .filter(Boolean);
  return {
    ...mcpScan,
    mcps,
    summary: {
      ...((mcpScan && mcpScan.summary) || {}),
      mcp_count: mcps.length,
    },
  };
}

function configuredOpenCodeAgents(openCodeConfig) {
  const agents =
    openCodeConfig &&
    typeof openCodeConfig === "object" &&
    openCodeConfig.agent &&
    typeof openCodeConfig.agent === "object" &&
    !Array.isArray(openCodeConfig.agent)
      ? openCodeConfig.agent
      : {};
  return normalizeOpenCodeAgentCatalog(
    Object.entries(agents)
      .filter(([, config]) => !config || config.disable !== true)
      .map(([id, config]) => ({ id, ...(config || {}) })),
  );
}

function isOpenCodeSkillPath(filePath, roots) {
  const candidate = asNonEmptyString(filePath);
  if (!candidate) {
    return false;
  }
  if ((Array.isArray(roots) ? roots : []).some((root) => pathContains(root, candidate))) {
    return true;
  }
  const normalized = path.resolve(candidate).split(path.sep).join("/");
  return [
    "/.opencode/skill/",
    "/.opencode/skills/",
    "/.claude/skills/",
    "/.agents/skills/",
  ].some((marker) => normalized.includes(marker));
}

function isOpenCodeMcpPath(filePath, mcpScan) {
  const candidate = asNonEmptyString(filePath);
  if (!candidate) {
    return false;
  }
  const baseName = path.basename(candidate).toLowerCase();
  if (["opencode.json", "opencode.jsonc"].includes(baseName)) {
    return true;
  }
  return (Array.isArray(mcpScan && mcpScan.mcps) ? mcpScan.mcps : [])
    .some((mcp) => pathContains(mcp.root_path, candidate));
}

class AgentGuardOpenCodeBridge {
  constructor(options = {}) {
    this.pluginId = options.pluginId || "agentguard-opencode";
    this.config = normalizePluginConfig(options.pluginConfig || {});
    this.opencode = options.opencode || {};
    this.logger = options.logger || console;
    this.openCodeConfig = null;
    this.openCodeLocalMcpNames = new Set();
    this.mcpConfigDiagnostics = [];
    this.opencodeAgents = discoverOpenCodeAgents(this.config, this.opencode, this.logger);
    this.agentBootstrap = null;
    this.agentRegistrations = loadPersistedAgentRegistrations(this.config, this.logger);
    this.agentRegistrationPayloads = new Map();
    this.bootstrapTicketConsumed = false;
    this.sessions = new Map();
    this.sessionAgents = new Map();
    this.clientRuntimeAuth = null;
    this.toolDefinitions = new Map();
    this.skillScan = emptySkillScanResult(this.config.skillScan, this.config.skillScan.roots);
    this.mcpScan = emptyMcpScanResult(this.config.mcpScan);
    this.skillScanSignature = skillScanSignature(this.skillScan);
    this.mcpScanSignature = mcpScanSignature(this.mcpScan);
    this.inventoryReports = {
      skills: new Map(),
      mcps: new Map(),
    };
    this.runtimeSessionNonce =
      asNonEmptyString(options.runtimeSessionNonce) || crypto.randomUUID();
    this.inventorySessionNonce = crypto.randomUUID();
    this.inventoryMonitor = {
      debounceTimer: null,
      pollTimer: null,
      pendingKinds: new Set(),
      pendingForce: false,
      pendingReason: "",
      disposed: false,
    };
  }

  async onConfig(config) {
    this.openCodeConfig = config && typeof config === "object" ? { ...config } : config;
    this.opencodeAgents = normalizeOpenCodeAgentCatalog([
      ...this.opencodeAgents,
      ...configuredOpenCodeAgents(this.openCodeConfig),
    ]);
    this.startInventoryMonitor();
    const kinds = [];
    if (this.config.skillScan.enabled) {
      kinds.push("skills");
    }
    if (this.config.mcpScan.enabled) {
      kinds.push("mcps");
    }
    if (kinds.length) {
      this.scheduleInventoryRefresh("config_startup", kinds, 0, { forceReport: true });
    }
  }

  getSkillScanResult() {
    return this.skillScan;
  }

  getMcpScanResult() {
    return this.mcpScan;
  }

  startInventoryMonitor() {
    if (this.inventoryMonitor.disposed || this.inventoryMonitor.pollTimer) {
      return false;
    }
    const monitored = [
      ["skills", this.config.skillScan],
      ["mcps", this.config.mcpScan],
    ].filter(([, config]) => config && config.enabled && config.monitor);
    if (!monitored.length) {
      return false;
    }
    const intervalMs = Math.min(
      ...monitored.map(([, config]) =>
        asPositiveInteger(
          config.monitorPollIntervalMs,
          DEFAULT_INVENTORY_MONITOR_POLL_INTERVAL_MS,
        )),
    );
    this.inventoryMonitor.pollTimer = setInterval(() => {
      this.scheduleInventoryRefresh(
        "poll",
        monitored.map(([kind]) => kind),
        0,
      );
    }, intervalMs);
    this.inventoryMonitor.pollTimer.unref?.();
    return true;
  }

  stopInventoryMonitor() {
    this.inventoryMonitor.disposed = true;
    if (this.inventoryMonitor.debounceTimer) {
      clearTimeout(this.inventoryMonitor.debounceTimer);
      this.inventoryMonitor.debounceTimer = null;
    }
    if (this.inventoryMonitor.pollTimer) {
      clearInterval(this.inventoryMonitor.pollTimer);
      this.inventoryMonitor.pollTimer = null;
    }
    this.inventoryMonitor.pendingKinds.clear();
    this.inventoryMonitor.pendingForce = false;
    this.inventoryMonitor.pendingReason = "";
  }

  scheduleInventoryRefresh(
    reason = "change",
    kinds = ["skills", "mcps"],
    delayMs = DEFAULT_INVENTORY_MONITOR_DEBOUNCE_MS,
    options = {},
  ) {
    if (this.inventoryMonitor.disposed) {
      return false;
    }
    const enabledKinds = new Set();
    for (const kind of Array.isArray(kinds) ? kinds : [kinds]) {
      if (kind === "skills" && this.config.skillScan.enabled) {
        enabledKinds.add(kind);
      }
      if (kind === "mcps" && this.config.mcpScan.enabled) {
        enabledKinds.add(kind);
      }
    }
    if (!enabledKinds.size) {
      return false;
    }
    for (const kind of enabledKinds) {
      this.inventoryMonitor.pendingKinds.add(kind);
    }
    this.inventoryMonitor.pendingForce =
      this.inventoryMonitor.pendingForce || options.forceReport === true;
    this.inventoryMonitor.pendingReason =
      asNonEmptyString(reason) || this.inventoryMonitor.pendingReason || "change";
    if (this.inventoryMonitor.debounceTimer) {
      clearTimeout(this.inventoryMonitor.debounceTimer);
    }
    const waitMs = Math.max(0, Number.isFinite(Number(delayMs)) ? Number(delayMs) : 0);
    this.inventoryMonitor.debounceTimer = setTimeout(() => {
      const pendingKinds = [...this.inventoryMonitor.pendingKinds];
      const forceReport = this.inventoryMonitor.pendingForce;
      const pendingReason = this.inventoryMonitor.pendingReason || "change";
      this.inventoryMonitor.debounceTimer = null;
      this.inventoryMonitor.pendingKinds.clear();
      this.inventoryMonitor.pendingForce = false;
      this.inventoryMonitor.pendingReason = "";
      try {
        this.refreshInventories(pendingReason, pendingKinds, { forceReport });
      } catch (error) {
        this.logger.warn?.("AgentGuard OpenCode adapter inventory refresh failed.", error);
      }
    }, waitMs);
    this.inventoryMonitor.debounceTimer.unref?.();
    return true;
  }

  refreshInventories(reason = "manual", kinds = ["skills", "mcps"], options = {}) {
    const requested = new Set(Array.isArray(kinds) ? kinds : [kinds]);
    const result = {};
    if (requested.has("skills") && this.config.skillScan.enabled) {
      const next = scanConfiguredOpenCodeSkills(
        this.config.skillScan,
        this.openCodeConfig,
        this.opencode,
        this.logger,
      );
      const signature = skillScanSignature(next);
      const changed = signature !== this.skillScanSignature;
      this.skillScan = next;
      this.skillScanSignature = signature;
      result.skills = { changed, scan: next };
      void this.reportInventoryKind("skills", reason, {
        force: options.forceReport === true || changed,
      });
    }
    if (requested.has("mcps") && this.config.mcpScan.enabled) {
      const currentMcpConfig = loadCurrentOpenCodeMcpConfig(this.opencode);
      if (currentMcpConfig.ok && currentMcpConfig.hasMcpConfig) {
        const mergedConfig =
          this.openCodeConfig && typeof this.openCodeConfig === "object"
            ? { ...this.openCodeConfig }
            : {};
        const configuredServers =
          mergedConfig.mcp && typeof mergedConfig.mcp === "object" && !Array.isArray(mergedConfig.mcp)
            ? { ...mergedConfig.mcp }
            : {};
        for (const name of this.openCodeLocalMcpNames) {
          delete configuredServers[name];
        }
        mergedConfig.mcp = mergeOpenCodeMcpServerMaps(
          configuredServers,
          currentMcpConfig.servers,
        );
        this.openCodeConfig = mergedConfig;
        this.openCodeLocalMcpNames = currentMcpConfig.names;
      }
      this.mcpConfigDiagnostics = currentMcpConfig.diagnostics;
      const next = scanConfiguredOpenCodeMcps(
        this.config.mcpScan,
        this.openCodeConfig,
        this.opencode,
        this.toolDefinitions,
        this.logger,
      );
      if (this.mcpConfigDiagnostics.length) {
        next.diagnostics.push(...this.mcpConfigDiagnostics);
        next.summary.diagnostic_count = next.diagnostics.length;
      }
      const signature = mcpScanSignature(next);
      const changed = signature !== this.mcpScanSignature;
      this.mcpScan = next;
      this.mcpScanSignature = signature;
      result.mcps = { changed, scan: next };
      void this.reportInventoryKind("mcps", reason, {
        force: options.forceReport === true || changed,
      });
    }
    return result;
  }

  inventoryAgents(kind) {
    const scanConfig = kind === "skills" ? this.config.skillScan : this.config.mcpScan;
    const configuredIds = new Set(scanConfig.agentIds || []);
    const catalog = normalizeOpenCodeAgentCatalog(this.opencodeAgents);
    if (configuredIds.size) {
      const byId = new Map(catalog.map((agent) => [agent.id, agent]));
      return [...configuredIds].map((id) => byId.get(id) || {
        id,
        externalAgentId: openCodeExternalAgentId(id),
        name: `OpenCode ${id}`,
        description: `OpenCode agent: ${id}`,
      });
    }
    if (catalog.length) {
      return catalog;
    }
    const id = this.config.opencodeAgent || "default";
    return normalizeOpenCodeAgentCatalog([{ id }]);
  }

  ensureInventoryStateForAgent(agent, kind) {
    const agentId = asNonEmptyString(agent && agent.id);
    if (!agentId) {
      return null;
    }
    return this.getState({
      agent: agentId,
      sessionID: [
        "agentguard-inventory",
        this.config.providerInstanceId,
        agentId,
        this.inventorySessionNonce,
      ].join(":"),
      directory: asNonEmptyString(agent.directory) || asNonEmptyString(this.opencode.directory),
      worktree: asNonEmptyString(agent.worktree) || asNonEmptyString(this.opencode.worktree),
      projectID: asNonEmptyString(agent.projectID),
      inventory: true,
      inventoryKind: kind,
    });
  }

  reportInventoryKind(kind, reason = "monitor", options = {}) {
    const reports = this.inventoryReports[kind];
    if (!reports) {
      return Promise.resolve(false);
    }
    const promises = this.inventoryAgents(kind).map((agent) => {
      const state = this.ensureInventoryStateForAgent(agent, kind);
      if (!state) {
        return Promise.resolve(false);
      }
      const scan = kind === "skills"
        ? filterSkillScanForAgent(this.skillScan, this.openCodeConfig, agent)
        : filterMcpScanForAgent(this.mcpScan, this.openCodeConfig, agent);
      const signature = kind === "skills" ? skillScanSignature(scan) : mcpScanSignature(scan);
      const reportKey = agent.externalAgentId || openCodeExternalAgentId(agent.id);
      let report = reports.get(reportKey);
      if (!report) {
        report = {
          lastSignature: null,
          pending: null,
          promise: null,
        };
        reports.set(reportKey, report);
      }
      if (!options.force && report.lastSignature === signature && !report.promise) {
        return Promise.resolve(true);
      }
      report.pending = { scan, signature, reason };
      if (!report.promise) {
        report.promise = this.runInventoryReportQueue(kind, state, report)
          .finally(() => {
            report.promise = null;
          });
      }
      return report.promise;
    });
    return Promise.all(promises).then((values) => values.some(Boolean));
  }

  async runInventoryReportQueue(kind, state, report) {
    let reported = false;
    while (report.pending) {
      const pending = report.pending;
      report.pending = null;
      const remote = state.enforcer && state.enforcer.remote;
      if (!remote || !remote.enabled || !state.runtimeAuth) {
        continue;
      }
      try {
        await this.reportWithRuntimeAuthRetry(state, () => {
          if (kind === "skills") {
            return remote.report_skills(
              state.context,
              pending.scan.skills || [],
              {
                source_framework: "opencode",
                summary: pending.scan.summary || {},
                diagnostics: pending.scan.diagnostics || [],
                sync_inventory: true,
                report_reason: pending.reason,
              },
            );
          }
          return remote.report_mcps(
            state.context,
            pending.scan.mcps || [],
            {
              source_framework: "opencode",
              summary: pending.scan.summary || {},
              diagnostics: pending.scan.diagnostics || [],
              sync_inventory: true,
              report_reason: pending.reason,
            },
          );
        });
        report.lastSignature = pending.signature;
        reported = true;
      } catch (error) {
        this.logger.warn?.(
          `AgentGuard OpenCode adapter failed to report ${kind === "skills" ? "skills" : "MCPs"}.`,
          error,
        );
      }
    }
    return reported;
  }

  resetRuntimeAuthAfterReportAuthFailure(state) {
    if (!state || !state.runtimeAuth) {
      return;
    }
    this.stopRuntimeAuthRefresh(state.runtimeAuth);
    clearRuntimeAuthSession(state.runtimeAuth);
    state.runtimeAuth.startup = null;
    state.runtimeAuthStartup = null;
    const remote = state.enforcer && state.enforcer.remote;
    if (remote) {
      remote.session_token = null;
      resetRemoteBreaker(remote);
    }
  }

  async reportWithRuntimeAuthRetry(state, reportFn) {
    await this.ensureRuntimeAuth(state);
    try {
      await reportFn();
      return true;
    } catch (error) {
      if (!isRuntimeAuthUnavailableError(error) || !state.runtimeAuth) {
        throw error;
      }
      this.resetRuntimeAuthAfterReportAuthFailure(state);
      await this.ensureRuntimeAuth(state);
      await reportFn();
      return true;
    }
  }

  async waitForInventoryReports() {
    const pending = [];
    for (const reports of Object.values(this.inventoryReports)) {
      for (const report of reports.values()) {
        if (report.promise) {
          pending.push(report.promise);
        }
      }
    }
    await Promise.allSettled(pending);
  }

  async startRuntimeAuthSession(identity = {}) {
    const resolved = this.resolveIdentity(identity);
    const catalog = normalizeOpenCodeAgentCatalog(this.opencodeAgents);
    if (!resolved.sessionID && catalog.length > 0) {
      const bootstrapState = this.getState({ ...resolved, agent: catalog[0].id });
      try {
        return Boolean(await this.ensureAgentBootstrap(bootstrapState));
      } catch (error) {
        bootstrapState.runtimeAuthError = error;
        this.logger.warn?.("AgentGuard OpenCode adapter failed to bootstrap OpenCode agents.", error);
        return false;
      }
    }
    if (!resolved.sessionID) {
      return false;
    }

    const state = this.getState(resolved);
    try {
      return await this.ensureRuntimeAuth(state);
    } catch (error) {
      state.runtimeAuthError = error;
      this.logger.warn?.("AgentGuard OpenCode adapter failed to start runtime auth session.", error);
      return false;
    }
  }

  getState(identity = {}) {
    const resolved = this.resolveIdentity(identity);
    const key = this.resolveStateKey(resolved);
    let state = this.sessions.get(key);
    if (state) {
      this.updateStateContext(state, resolved);
      this.rememberSessionAgent(resolved);
      return state;
    }

    const context = this.buildRuntimeContext(resolved);
    const runtimeAuth = buildRuntimeAuthState(this.config, key, resolved);
    if (this.bootstrapTicketConsumed) {
      runtimeAuth.ticket_consumed = true;
      runtimeAuth.user_ticket = null;
    }
    if (!this.clientRuntimeAuth) {
      this.clientRuntimeAuth = runtimeAuth;
    }
    const remote = new RemoteGuardClient(this.config.serverUrl || null, {
      api_key: this.config.apiKey || null,
      session_id: context.session_id,
      agent_id: context.agent_id,
      user_id: context.user_id,
      session_key: key,
      user_ticket: runtimeAuth ? runtimeAuth.user_ticket : null,
      session_token: runtimeAuth ? runtimeAuth.session_token : null,
      dpop_proof_factory: runtimeAuth ? runtimeAuth.proof : null,
      use_dpop_auth: Boolean(runtimeAuth),
      legacy_identity_headers: !runtimeAuth,
      timeout_s: this.config.remoteTimeoutS,
      retries: this.config.remoteRetries,
    });
    const pluginManager = new PluginManager({
      config: buildPluginConfigPayload(this.config),
    });
    const enforcer = new UGuardEnforcer({
      snapshot: new PolicySnapshot({ version: this.config.policy, rules: [] }),
      remote,
      plugin_manager: pluginManager,
      sync_buffer: new ClientSyncBuffer(),
    });
    const audit = new AuditRecorder(
      context.session_id,
      new AuditLogger(this.config.auditPath || null),
    );
    enforcer.trace_window_provider = () => audit.trace.window(this.config.windowSize);

    state = {
      key,
      identity: resolved,
      context,
      enforcer,
      audit,
      runtimeAuth,
      runtimeAuthStartup: null,
      runtimeAuthError: null,
      reportedTools: new Set(),
    };
    this.sessions.set(key, state);
    this.rememberSessionAgent(resolved);
    return state;
  }

  resolveStateKey(identity) {
    if (identity.sessionID) {
      return `session:${identity.sessionID}:agent:${openCodeExternalAgentId(openCodeAgentName(identity, this.config))}`;
    }
    const onlyKey = this.singleExistingSessionKey();
    if (onlyKey) {
      return onlyKey;
    }
    return `fallback:${identity.fallbackID}`;
  }

  singleExistingSessionKey() {
    const eligible = [...this.sessions.entries()]
      .filter(([, state]) => !(state.identity && state.identity.inventory));
    if (eligible.length !== 1) {
      return null;
    }
    return eligible[0][0];
  }

  resolveIdentity(identity = {}) {
    const sessionID =
      asNonEmptyString(identity.sessionID) ||
      asNonEmptyString(identity.sessionId) ||
      asNonEmptyString(identity.session_id) ||
      findSessionId(identity.output) ||
      findSessionId(identity.messages);
    const project = this.opencode && typeof this.opencode.project === "object" ? this.opencode.project : {};
    const directory =
      asNonEmptyString(identity.directory) ||
      asNonEmptyString(this.opencode.directory) ||
      asNonEmptyString(this.opencode.worktree);
    const projectID =
      asNonEmptyString(identity.projectID) ||
      asNonEmptyString(identity.projectId) ||
      asNonEmptyString(project.id) ||
      asNonEmptyString(project.projectID) ||
      asNonEmptyString(project.projectId);
    const fallbackSource = projectID || directory || process.cwd();
    return {
      sessionID,
      callID: asNonEmptyString(identity.callID) || asNonEmptyString(identity.callId),
      agent:
        asNonEmptyString(identity.agent) ||
        (sessionID ? this.sessionAgents.get(sessionID) : null) ||
        this.config.opencodeAgent,
      model: normalizeModel(identity.model) || this.config.opencodeModel,
      messageID: asNonEmptyString(identity.messageID) || asNonEmptyString(identity.messageId),
      partID: asNonEmptyString(identity.partID) || asNonEmptyString(identity.partId),
      directory,
      worktree: asNonEmptyString(identity.worktree) || asNonEmptyString(this.opencode.worktree),
      projectID,
      projectName: asNonEmptyString(identity.projectName) || asNonEmptyString(project.name),
      inventory: identity.inventory === true,
      inventoryKind: asNonEmptyString(identity.inventoryKind),
      fallbackID: stableShortId(fallbackSource),
    };
  }

  buildRuntimeContext(identity) {
    const externalSessionID = identity.sessionID || identity.fallbackID;
    const opencodeAgent = openCodeAgentName(identity, this.config);
    const externalAgentID = openCodeExternalAgentId(opencodeAgent);
    return new RuntimeContext({
      session_id: `opencode:${externalSessionID}`,
      agent_id: externalAgentID,
      policy: this.config.policy,
      metadata: this.buildContextMetadata(identity),
    });
  }

  updateStateContext(state, identity) {
    state.identity = { ...(state.identity || {}), ...withoutEmpty(identity) };
    state.context.metadata = {
      ...(state.context.metadata || {}),
      ...this.buildContextMetadata(state.identity),
    };
    const remote = state.enforcer && state.enforcer.remote;
    if (remote) {
      remote.session_id = state.context.session_id;
      remote.agent_id = state.context.agent_id;
      remote.user_id = state.context.user_id;
      remote.session_key = state.key;
    }
  }

  rememberSessionAgent(identity) {
    const sessionID = asNonEmptyString(identity && identity.sessionID);
    const agent = asNonEmptyString(identity && identity.agent);
    if (sessionID && agent) {
      this.sessionAgents.set(sessionID, agent);
    }
  }

  buildContextMetadata(identity) {
    const opencodeAgent = openCodeAgentName(identity, this.config);
    const externalAgentID = openCodeExternalAgentId(opencodeAgent);
    return {
      adapter: "opencode",
      source_framework: "opencode",
      external_provider: "opencode",
      external_agent_id: externalAgentID,
      display_agent_id: externalAgentID,
      provider_instance_id: this.config.providerInstanceId,
      runtime_auth_provider: "opencode",
      client_session_key: identity.sessionID ? `opencode:${identity.sessionID}` : `opencode:fallback:${identity.fallbackID}`,
      client_plugin_config: buildPluginConfigPayload(this.config),
      remote_plugin_config: buildPluginConfigPayload(this.config),
      opencode: {
        sessionID: identity.sessionID || null,
        callID: identity.callID || null,
        agent: opencodeAgent,
        model: identity.model || null,
        messageID: identity.messageID || null,
        partID: identity.partID || null,
        projectID: identity.projectID || null,
        projectName: identity.projectName || null,
        directory: identity.directory || null,
        worktree: identity.worktree || null,
      },
      opencode_session_id: identity.sessionID || null,
      opencode_agent: opencodeAgent,
      opencode_project_id: identity.projectID || null,
      opencode_directory: identity.directory || null,
    };
  }

  buildRuntimeAuthMetadata(state) {
    const identity = state.identity || {};
    const opencodeAgent = openCodeAgentName(identity, this.config);
    const externalAgentID = openCodeExternalAgentId(opencodeAgent);
    const displayAgentID = this.config.agentDisplayName || externalAgentID;
    return {
      adapter: "opencode",
      source_framework: "opencode",
      runtime_auth_provider: "opencode",
      external_agent_id: externalAgentID,
      display_agent_id: displayAgentID,
      provider_instance_id: this.config.providerInstanceId,
      agent_type: "agent",
      name: this.config.agentDisplayName || `OpenCode ${opencodeAgent}`,
      description: this.config.agentDescription || `OpenCode agent '${opencodeAgent}' guarded by AgentGuard.`,
      client_session_key: state.key,
      client_plugin_config: buildPluginConfigPayload(this.config),
      remote_plugin_config: buildPluginConfigPayload(this.config),
      opencode_session_id: identity.sessionID || null,
      opencode_call_id: identity.callID || null,
      opencode_agent: opencodeAgent,
      opencode_model: identity.model || null,
      opencode_project_id: identity.projectID || null,
      opencode_project_name: identity.projectName || null,
      opencode_directory: identity.directory || null,
      opencode_worktree: identity.worktree || null,
    };
  }

  async ensureAgentBootstrap(state) {
    const opencodeAgent = openCodeAgentName(state && state.identity, this.config);
    const externalAgentID = openCodeExternalAgentId(opencodeAgent);
    const discoveredCatalog = normalizeOpenCodeAgentCatalog(this.opencodeAgents);
    const catalog = ensureCatalogContainsAgent(discoveredCatalog, opencodeAgent);
    this.opencodeAgents = catalog;
    const registrationKey = agentRegistrationCacheKey(this.config, externalAgentID);
    const existing = this.agentRegistrations.get(registrationKey);
    const missingCatalogRegistration = catalog.some((agent) =>
      !this.agentRegistrations.has(agentRegistrationCacheKey(this.config, agent.externalAgentId)),
    );
    if (existing && !missingCatalogRegistration) {
      this.applyCanonicalAgentRegistration(state, existing);
      return existing;
    }
    if (this.agentBootstrap) {
      await this.agentBootstrap;
      const registered = this.agentRegistrations.get(registrationKey);
      if (registered) {
        this.applyCanonicalAgentRegistration(state, registered);
      }
      return registered || null;
    }
    const remote = state.enforcer && state.enforcer.remote;
    if (!remote || !remote.enabled) {
      return existing || null;
    }
    if (!state.runtimeAuth.user_ticket || state.runtimeAuth.ticket_consumed) {
      return existing || null;
    }

    const registrations = catalog.map((agent) =>
      buildOpenCodeAgentRegistration(this.config, agent, {
        registration_reason: "bootstrap",
      }),
    );
    for (const registration of registrations) {
      this.agentRegistrationPayloads.set(registration.externalAgentId, registration);
    }

    this.agentBootstrap = remote.bootstrap_agents({
      provider: "opencode",
      user_ticket: state.runtimeAuth.user_ticket,
      provider_instance_id: this.config.providerInstanceId,
      agents: registrations.map((item) => item.payload),
      metadata: {
        adapter: "opencode",
        runtime_auth_provider: "opencode",
        provider_instance_id: this.config.providerInstanceId,
      },
    })
      .then((response) => {
        const mapped = registrationMapFromBootstrap(response, registrations);
        for (const [key, value] of mapped.entries()) {
          this.agentRegistrations.set(agentRegistrationCacheKey(this.config, key), value);
        }
        persistAgentRegistrations(this.config, this.agentRegistrations, this.logger);
        this.markBootstrapTicketConsumed();
        return mapped;
      })
      .catch((error) => {
        this.logger.warn?.("AgentGuard OpenCode adapter failed to bootstrap OpenCode agents.", error);
        throw error;
      })
      .finally(() => {
        this.agentBootstrap = null;
      });

    await this.agentBootstrap;
    const registered = this.agentRegistrations.get(registrationKey);
    if (registered) {
      this.applyCanonicalAgentRegistration(state, registered);
    }
    return registered || null;
  }

  markBootstrapTicketConsumed() {
    this.bootstrapTicketConsumed = true;
    for (const state of this.sessions.values()) {
      const runtimeAuth = state.runtimeAuth;
      if (!runtimeAuth) {
        continue;
      }
      runtimeAuth.ticket_consumed = true;
      runtimeAuth.user_ticket = null;
      const remote = state.enforcer && state.enforcer.remote;
      if (remote) {
        remote.user_ticket = null;
      }
    }
  }

  applyCanonicalAgentRegistration(state, registration) {
    if (!state || !registration || !registration.agent_id) {
      return;
    }
    state.context.agent_id = registration.agent_id;
    state.context.metadata = {
      ...(state.context.metadata || {}),
      agentguard_agent_id: registration.agent_id,
      agent_identity_code: registration.agent_identity_code || null,
      agent_public_key_thumbprint: registration.public_key_thumbprint || null,
      agent_identity_key_id: registration.agent_identity_key_id || null,
      external_provider: "opencode",
      external_agent_id: registration.external_agent_id || null,
      display_agent_id: registration.external_agent_id || null,
      agentguard_user_bound: Boolean(registration.user_bound),
    };
    if (state.runtimeAuth) {
      state.runtimeAuth.agent_id = registration.agent_id;
      state.runtimeAuth.external_agent_id = registration.external_agent_id || null;
      state.runtimeAuth.agent_identity_key_id = registration.agent_identity_key_id || null;
    }
    const remote = state.enforcer && state.enforcer.remote;
    if (remote) {
      remote.agent_id = registration.agent_id;
    }
  }

  applyRuntimeAuthIssue(state, result, { ticketConsumed = false, externalSessionId = null } = {}) {
    const runtimeAuth = state.runtimeAuth;
    runtimeAuth.session_id = asNonEmptyString(result && result.session_id) || runtimeAuth.session_id;
    runtimeAuth.agent_id = asNonEmptyString(result && result.agent_id) || runtimeAuth.agent_id;
    runtimeAuth.user_id = asNonEmptyString(result && result.user_id) || runtimeAuth.user_id;
    runtimeAuth.session_token = asNonEmptyString(result && result.session_token) || runtimeAuth.session_token;
    runtimeAuth.expires_at = epochSeconds(result && result.expires_at);
    runtimeAuth.external_session_id = asNonEmptyString(externalSessionId) || runtimeAuth.external_session_id || null;
    if (ticketConsumed) {
      runtimeAuth.ticket_consumed = true;
      runtimeAuth.user_ticket = null;
    }
    state.runtimeAuthError = null;

    state.context.session_id = runtimeAuth.session_id || state.context.session_id;
    state.context.agent_id = runtimeAuth.agent_id || state.context.agent_id;
    state.context.user_id = runtimeAuth.user_id || state.context.user_id;
    state.context.metadata = {
      ...(state.context.metadata || {}),
      agentguard_session_id: runtimeAuth.session_id || null,
      agentguard_agent_id: runtimeAuth.agent_id || null,
      agentguard_user_id: runtimeAuth.user_id || null,
      runtime_auth_provider: "opencode",
    };

    const remote = state.enforcer && state.enforcer.remote;
    if (remote) {
      remote.session_id = state.context.session_id;
      remote.agent_id = state.context.agent_id;
      remote.user_id = state.context.user_id;
      remote.session_token = runtimeAuth.session_token || null;
      remote.dpop_proof_factory = runtimeAuth.proof;
      remote.use_dpop_auth = true;
      remote.legacy_identity_headers = false;
      resetRemoteBreaker(remote);
    }
    this.scheduleRuntimeAuthRefresh(state);
  }

  async ensureRuntimeAuth(state, options = {}) {
    const runtimeAuth = state && state.runtimeAuth;
    if (!runtimeAuth) {
      return false;
    }
    const forceRefresh = options.forceRefresh === true;
    const allowCreate = options.allowCreate !== false;
    const desiredExternalSessionId = openCodeRuntimeExternalSessionId(
      state,
      this.runtimeSessionNonce,
    );
    if (
      runtimeAuthFresh(runtimeAuth) &&
      runtimeAuth.external_session_id &&
      runtimeAuth.external_session_id !== desiredExternalSessionId
    ) {
      clearRuntimeAuthSession(runtimeAuth);
      const remote = state.enforcer && state.enforcer.remote;
      if (remote) {
        remote.session_token = null;
      }
    }
    if (!forceRefresh && runtimeAuthFresh(runtimeAuth)) {
      this.applyRuntimeAuthIssue(state, runtimeAuth);
      return true;
    }
    if (runtimeAuth.startup) {
      await runtimeAuth.startup;
      return Boolean(runtimeAuth.session_token);
    }
    const remote = state.enforcer && state.enforcer.remote;
    if (!remote || !remote.enabled) {
      return false;
    }

    runtimeAuth.startup = (async () => {
      const identity = state.identity || {};
      const bootstrapDpopKeyID = ["opencode", runtimeAuth.key || identity.fallbackID || state.key].join("\x1f");
      if (!runtimeAuth.dpop_key) {
        runtimeAuth.dpop_key = loadOrCreateDPoPKey(bootstrapDpopKeyID);
        runtimeAuth.dpop_key_id = bootstrapDpopKeyID;
      }
      remote.user_ticket = runtimeAuth.user_ticket;
      remote.dpop_proof_factory = runtimeAuth.proof;
      remote.use_dpop_auth = true;
      remote.legacy_identity_headers = false;

      if (runtimeAuth.session_token) {
        remote.session_token = runtimeAuth.session_token;
        try {
          const refreshed = await remote.refresh_runtime_session();
          this.applyRuntimeAuthIssue(state, refreshed);
          return true;
        } catch (error) {
          clearRuntimeAuthSession(runtimeAuth);
          remote.session_token = null;
          this.logger.warn?.("AgentGuard OpenCode adapter discarded stale runtime auth session.", error);
          if (!allowCreate) {
            throw error;
          }
        }
      }
      if (!allowCreate) {
        throw new Error("AgentGuard OpenCode runtime session is not active.");
      }
      const registration = await this.ensureAgentBootstrap(state);
      if (!registration || !registration.agent_id) {
        throw new Error("AgentGuard OpenCode agent bootstrap did not return a canonical AgentGuard agent.");
      }

      const externalSessionId = openCodeRuntimeExternalSessionId(
        state,
        this.runtimeSessionNonce,
      );
      const dpopKeyID = [
        "opencode",
        registration.agent_id,
        openCodeExternalSessionId(state),
      ].join("\x1f");
      if (runtimeAuth.dpop_key_id !== dpopKeyID) {
        runtimeAuth.dpop_key = loadOrCreateDPoPKey(dpopKeyID);
        runtimeAuth.dpop_key_id = dpopKeyID;
      }
      remote.dpop_proof_factory = runtimeAuth.proof;
      remote.use_dpop_auth = true;
      remote.legacy_identity_headers = false;
      const body = {
        provider: "opencode",
        agent_id: registration.agent_id,
        external_session_id: externalSessionId,
        external_user_id: asNonEmptyString(state.context.user_id) || null,
        metadata: this.buildRuntimeAuthMetadata(state),
      };
      const agentKey = loadOrCreateAgentKey(registration.agent_identity_key_id);
      const result = await remote.create_runtime_session({
        ...body,
      }, {
        extra_headers_factory: (method, url, requestBody) => ({
          "X-AgentGuard-Agent-Proof": agentKey.signSessionCreateProof({
            agent_id: registration.agent_id,
            method,
            url,
            body: requestBody,
            dpop_jkt: runtimeAuth.dpop_key.thumbprint,
          }),
        }),
      });
      this.applyRuntimeAuthIssue(state, result, { externalSessionId });
      return true;
    })()
      .catch((error) => {
        state.runtimeAuthError = error;
        throw error;
      })
      .finally(() => {
        runtimeAuth.startup = null;
        state.runtimeAuthStartup = null;
      });
    state.runtimeAuthStartup = runtimeAuth.startup;

    await runtimeAuth.startup;
    return Boolean(runtimeAuth.session_token);
  }

  scheduleRuntimeAuthRefresh(state) {
    const runtimeAuth = state && state.runtimeAuth;
    if (!runtimeAuth || !runtimeAuth.session_token) {
      return;
    }
    if (runtimeAuth.refresh_timer) {
      clearTimeout(runtimeAuth.refresh_timer);
      runtimeAuth.refresh_timer = null;
    }
    const delayMs = runtimeAuthRefreshDelayMs(runtimeAuth, this.config.runtimeRefreshLeadS);
    if (delayMs == null) {
      return;
    }
    runtimeAuth.refresh_timer = setTimeout(() => {
      runtimeAuth.refresh_timer = null;
      this.ensureRuntimeAuth(state, { forceRefresh: true, allowCreate: false })
        .catch((error) => {
          state.runtimeAuthError = error;
          this.logger.warn?.("AgentGuard OpenCode adapter runtime auth refresh failed.", error);
        });
    }, delayMs);
    runtimeAuth.refresh_timer.unref?.();
  }

  stopRuntimeAuthRefresh(runtimeAuth = this.clientRuntimeAuth) {
    if (!runtimeAuth || !runtimeAuth.refresh_timer) {
      return;
    }
    clearTimeout(runtimeAuth.refresh_timer);
    runtimeAuth.refresh_timer = null;
  }

  async closeRuntimeAuthSession(runtimeAuth = null) {
    if (runtimeAuth) {
      const state = [...this.sessions.values()].find((item) => item.runtimeAuth === runtimeAuth);
      return this.closeStateRuntimeAuthSession(state);
    }
    const results = [];
    for (const state of this.sessions.values()) {
      results.push(await this.closeStateRuntimeAuthSession(state));
    }
    return results.some(Boolean);
  }

  async closeStateRuntimeAuthSession(state) {
    if (!state) {
      return false;
    }
    const runtimeAuth = state.runtimeAuth;
    this.stopRuntimeAuthRefresh(runtimeAuth);
    if (!runtimeAuth || !runtimeAuth.session_token) {
      return false;
    }
    const remote = state && state.enforcer && state.enforcer.remote;
    if (!remote || !remote.enabled) {
      clearRuntimeAuthSession(runtimeAuth);
      return false;
    }
    this.applyRuntimeAuthIssue(state, runtimeAuth);
    try {
      await remote.close_runtime_session();
      clearRuntimeAuthSession(runtimeAuth);
      remote.session_token = null;
      return true;
    } catch (error) {
      this.logger.warn?.("AgentGuard OpenCode adapter failed to close runtime auth session.", error);
      clearRuntimeAuthSession(runtimeAuth);
      remote.session_token = null;
      return false;
    }
  }

  async enforce(state, runtimeEvent, options = {}) {
    if (state.runtimeAuth) {
      try {
        await this.ensureRuntimeAuth(state);
      } catch (error) {
        if (shouldFailClosed(this.config, options.phase)) {
          const decision = GuardDecision.deny("AgentGuard runtime authentication failed.", {
            metadata: {
              fail_closed: true,
              route: "runtime_auth_failed",
              runtime_auth_provider: "opencode",
              error: String(error && error.message ? error.message : error),
            },
          });
          state.audit.record(runtimeEvent, decision);
          return { event: runtimeEvent, decision, route: "runtime_auth_failed" };
        }
        this.logger.warn?.("AgentGuard OpenCode adapter runtime authentication failed.", error);
      }
    }

    let result;
    try {
      result = await state.enforcer.enforce(runtimeEvent, state.context, {
        extensions: options.extensions || {},
      });
    } catch (error) {
      await this.flushNow(state, "client_error");
      throw error;
    }

    let decision = result.decision;
    if (shouldFailClosed(this.config, options.phase) && isRemoteUnavailableDecision(decision)) {
      decision = GuardDecision.deny(decision.reason || "Remote AgentGuard review unavailable.", {
        risk_signals: [...(decision.risk_signals || [])],
        metadata: {
          ...(decision.metadata || {}),
          fail_closed: true,
          original_decision_type: DecisionType.REQUIRE_REMOTE_REVIEW,
        },
      });
    }

    state.audit.record(result.event, decision);
    return { ...result, decision };
  }

  async flushAsync(state, reason = "round_complete") {
    const remote = state.enforcer && state.enforcer.remote;
    const buffer = state.enforcer && state.enforcer.sync_buffer;
    if (!remote || !remote.enabled || !buffer || !buffer.has_entries()) {
      return false;
    }
    if (state.runtimeAuth) {
      try {
        await this.ensureRuntimeAuth(state);
      } catch (error) {
        this.logger.warn?.("AgentGuard OpenCode adapter skipped async trace upload after runtime auth failure.", error);
        return false;
      }
    }
    const entries = buffer.snapshot();
    if (!entries.length) {
      return false;
    }
    const trace = buffer.build_trace_upload({
      context: state.context,
      entries,
      reason,
    });
    remote.upload_trace_async(trace, {
      on_success: () => buffer.remove_entries(entries),
    });
    return true;
  }

  async flushNow(state, reason = "client_decision") {
    const remote = state.enforcer && state.enforcer.remote;
    const buffer = state.enforcer && state.enforcer.sync_buffer;
    if (!remote || !remote.enabled || !buffer || !buffer.has_entries()) {
      return false;
    }
    if (state.runtimeAuth) {
      try {
        await this.ensureRuntimeAuth(state);
      } catch (error) {
        this.logger.warn?.("AgentGuard OpenCode adapter skipped trace upload after runtime auth failure.", error);
        return false;
      }
    }
    const entries = buffer.pop_all();
    if (!entries.length) {
      return false;
    }
    const trace = buffer.build_trace_upload({
      context: state.context,
      entries,
      reason,
    });
    try {
      await remote.upload_trace(trace);
      return true;
    } catch (_) {
      buffer.restore_front(entries);
      return false;
    }
  }

  async runToolExecuteBefore({ input = {}, output = {} } = {}) {
    const state = this.getState({
      ...input,
      output,
    });
    const mcpMetadata = this.observeMcpRuntimeTool(
      state,
      input.tool,
      output.args,
    );
    const runtimeEvent = createRuntimeEvent({
      eventType: EventType.TOOL_INVOKE,
      context: state.context,
      payload: {
        tool_name: input.tool,
        arguments: output.args || {},
        capabilities: [],
      },
      metadata: {
        phase: "tool_before",
        sourceHook: "tool.execute.before",
        opencode_session_id: input.sessionID || null,
        callID: input.callID || null,
        ...mcpMetadata,
      },
    });
    const result = await this.enforce(state, runtimeEvent, { phase: "tool_before" });
    const decision = result.decision;

    if (decisionAllows(decision) || isRemoteUnavailableDecision(decision)) {
      return undefined;
    }
    if (
      decision.decision_type === DecisionType.MODIFY_TOOL_INVOKE ||
      decision.decision_type === DecisionType.REWRITE ||
      decision.decision_type === DecisionType.REPAIR ||
      decision.decision_type === DecisionType.SANITIZE
    ) {
      const args = buildModifiedArgs(decision, output.args || {});
      if (args && typeof args === "object" && !Array.isArray(args)) {
        output.args = args;
        return undefined;
      }
    }

    await this.flushNow(state, "tool_before_block");
    throw new Error(decision.reason || this.config.blockMessage || DEFAULT_BLOCK_MESSAGE);
  }

  async runToolExecuteAfter({ input = {}, output = {} } = {}) {
    const state = this.getState({
      ...input,
      output,
    });
    const runtimeEvent = createRuntimeEvent({
      eventType: EventType.TOOL_RESULT,
      context: state.context,
      payload: {
        tool_name: input.tool,
        result: normalizeToolResultOutput(output),
      },
      metadata: {
        phase: "tool_after",
        sourceHook: "tool.execute.after",
        opencode_session_id: input.sessionID || null,
        callID: input.callID || null,
        arguments: input.args || {},
        ...buildOpenCodeMcpRuntimeMetadata(state, this.mcpScan, input.tool),
      },
    });
    const result = await this.enforce(state, runtimeEvent, { phase: "tool_after" });
    const decision = result.decision;

    if (decision.decision_type === DecisionType.MODIFY_TOOL_RESULT) {
      applyModifiedToolResult(output, decision);
    } else if (
      decision.decision_type === DecisionType.SANITIZE ||
      decision.decision_type === DecisionType.REWRITE ||
      decision.decision_type === DecisionType.REPAIR
    ) {
      output.output = buildReplacementText(decision);
    } else if (decision.is_blocking) {
      output.output = decision.reason || this.config.blockMessage || DEFAULT_BLOCK_MESSAGE;
      output.metadata = {
        ...(output.metadata || {}),
        agentguard: { decisionType: decision.decision_type, reason: decision.reason },
      };
    }

    await this.flushAsync(state);
    return undefined;
  }

  observeMcpRuntimeTool(state, runtimeToolName, args = {}) {
    const match = matchOpenCodeMcpRuntimeTool(this.mcpScan, runtimeToolName);
    if (!match) {
      return {};
    }
    const previous = this.toolDefinitions.get(match.runtimeToolName);
    if (!previous) {
      this.toolDefinitions.set(match.runtimeToolName, {
        name: match.runtimeToolName,
        runtime_name: match.runtimeToolName,
        mcp_tool_name: match.mcpToolName,
        description: "",
        input_schema: observedToolInputSchema(args),
      });
      this.scheduleInventoryRefresh(
        "mcp_tool_observed",
        ["mcps"],
        this.config.mcpScan.monitorDebounceMs,
      );
    }
    return buildOpenCodeMcpRuntimeMetadata(state, this.mcpScan, match.runtimeToolName);
  }

  async runChatMessagesTransform({ input = {}, output = {} } = {}) {
    const state = this.getState({
      ...input,
      output,
      messages: output.messages,
    });
    const runtimeEvent = createRuntimeEvent({
      eventType: EventType.LLM_INPUT,
      context: state.context,
      payload: {
        messages: normalizeOpenCodeMessages(output.messages),
      },
      metadata: {
        phase: "llm_before",
        sourceHook: "experimental.chat.messages.transform",
        opencode_session_id: state.identity.sessionID || null,
        messageCount: Array.isArray(output.messages) ? output.messages.length : 0,
      },
    });
    const result = await this.enforce(state, runtimeEvent, { phase: "llm_before" });
    const decision = result.decision;

    if (decisionAllows(decision) || isRemoteUnavailableDecision(decision)) {
      return undefined;
    }
    if (decision.decision_type === DecisionType.MODIFY_LLM_INPUT) {
      applyModifiedLlmInput(output, decision);
      return undefined;
    }
    if (
      decision.decision_type === DecisionType.SANITIZE ||
      decision.decision_type === DecisionType.REWRITE ||
      decision.decision_type === DecisionType.REPAIR
    ) {
      replaceLatestUserText(output.messages, buildReplacementText(decision));
      return undefined;
    }
    replaceLatestUserText(
      output.messages,
      [
        "AgentGuard policy blocked the previous request.",
        `Reason: ${decision.reason || this.config.blockMessage || DEFAULT_BLOCK_MESSAGE}`,
        "Respond with the reason above and do not continue the blocked task.",
      ].join("\n"),
    );
    await this.flushNow(state, "llm_before_block");
    return undefined;
  }

  async runTextComplete({ input = {}, output = {} } = {}) {
    const state = this.getState({
      ...input,
      output,
    });
    const outputText = asText(output.text);
    const runtimeEvent = createRuntimeEvent({
      eventType: EventType.LLM_OUTPUT,
      context: state.context,
      payload: {
        output: outputText,
        final_output: outputText,
      },
      metadata: {
        phase: "llm_after",
        sourceHook: "experimental.text.complete",
        opencode_session_id: input.sessionID || null,
        messageID: input.messageID || null,
        partID: input.partID || null,
      },
    });
    const result = await this.enforce(state, runtimeEvent, { phase: "llm_after" });
    const decision = result.decision;

    if (decision.decision_type === DecisionType.MODIFY_LLM_OUTPUT) {
      output.text = buildReplacementText(decision, outputText);
    } else if (
      decision.decision_type === DecisionType.SANITIZE ||
      decision.decision_type === DecisionType.REWRITE ||
      decision.decision_type === DecisionType.REPAIR
    ) {
      output.text = buildReplacementText(decision, DEFAULT_SANITIZED_MESSAGE);
    } else if (decision.is_blocking) {
      output.text = decision.reason || this.config.blockMessage || DEFAULT_BLOCK_MESSAGE;
      await this.flushNow(state, "llm_after_block");
    }

    await this.flushAsync(state);
    return undefined;
  }

  async runToolDefinition({ input = {}, output = {} } = {}) {
    const toolID = asNonEmptyString(input.toolID || input.toolId || input.tool);
    if (!toolID) {
      return undefined;
    }
    const definition = {
      name: toolID,
      description: asText(output.description),
      input_schema: output.parameters || {},
    };
    const previousDefinition = this.toolDefinitions.get(toolID);
    const definitionChanged =
      !previousDefinition ||
      JSON.stringify(previousDefinition) !== JSON.stringify(definition);
    if (definitionChanged) {
      this.toolDefinitions.set(toolID, definition);
      const mcpServers =
        this.openCodeConfig &&
        typeof this.openCodeConfig === "object" &&
        this.openCodeConfig.mcp &&
        typeof this.openCodeConfig.mcp === "object" &&
        !Array.isArray(this.openCodeConfig.mcp)
          ? Object.keys(this.openCodeConfig.mcp)
          : [];
      if (this.config.mcpScan.monitor && mcpServers.some((server) =>
        toolID.startsWith(`${sanitizeOpenCodeToolName(server)}_`))) {
        this.scheduleInventoryRefresh(
          "tool_definition",
          ["mcps"],
          this.config.mcpScan.monitorDebounceMs,
        );
      }
    }
    const state = this.getState({ ...input, output });
    if (state.reportedTools.has(toolID)) {
      return undefined;
    }
    const remote = state.enforcer && state.enforcer.remote;
    if (!remote || !remote.enabled || !state.runtimeAuth) {
      return undefined;
    }
    try {
      await this.ensureRuntimeAuth(state);
      await remote.report_tool(state.context, {
        name: toolID,
        description: asText(output.description),
        parameters: output.parameters || {},
        source: "opencode",
      });
      state.reportedTools.add(toolID);
    } catch (error) {
      this.logger.warn?.("AgentGuard OpenCode adapter failed to report tool definition.", error);
    }
    return undefined;
  }

  async runEvent(input = {}) {
    const event = input && typeof input === "object" ? input.event || input : {};
    const type = asNonEmptyString(event.type);
    if (!type) {
      return undefined;
    }
    if (type === "mcp.tools.changed") {
      this.scheduleInventoryRefresh(
        "mcp_tools_changed",
        ["mcps"],
        this.config.mcpScan.monitorDebounceMs,
      );
      return undefined;
    }
    if (type === "file.watcher.updated") {
      const properties =
        event.properties && typeof event.properties === "object"
          ? event.properties
          : event.data && typeof event.data === "object"
            ? event.data
            : {};
      const filePath = asNonEmptyString(properties.file);
      const kinds = [];
      const delays = [];
      const skillRoots =
        this.skillScan && this.skillScan.summary && Array.isArray(this.skillScan.summary.roots)
          ? this.skillScan.summary.roots
          : [];
      if (
        this.config.skillScan.enabled &&
        this.config.skillScan.monitor &&
        isOpenCodeSkillPath(filePath, [
          ...skillRoots,
          ...(this.config.skillScan.roots || []),
        ])
      ) {
        kinds.push("skills");
        delays.push(this.config.skillScan.monitorDebounceMs);
      }
      if (
        this.config.mcpScan.enabled &&
        this.config.mcpScan.monitor &&
        isOpenCodeMcpPath(filePath, this.mcpScan)
      ) {
        kinds.push("mcps");
        delays.push(this.config.mcpScan.monitorDebounceMs);
      }
      if (kinds.length) {
        this.scheduleInventoryRefresh(
          `file_${asNonEmptyString(properties.event) || "updated"}`,
          kinds,
          Math.min(...delays),
        );
      }
      return undefined;
    }
    if (type === "session.created") {
      const identity = openCodeSessionIdentityFromEvent(input);
      const state = this.getState(identity);
      try {
        await this.ensureRuntimeAuth(state);
      } catch (error) {
        state.runtimeAuthError = error;
        this.logger.warn?.("AgentGuard OpenCode adapter failed to create runtime auth session on OpenCode session.created.", error);
      }
      return undefined;
    }
    if (type === "session.updated") {
      const identity = openCodeSessionIdentityFromEvent(input);
      if (identity.sessionID) {
        this.getState(identity);
      }
      return undefined;
    }
    if (type === "session.deleted") {
      const identity = openCodeSessionIdentityFromEvent(input);
      if (identity.sessionID) {
        const keyPrefix = `session:${identity.sessionID}:agent:`;
        const matching = [...this.sessions.entries()].filter(([key]) => key.startsWith(keyPrefix));
        for (const [key, state] of matching) {
          await this.flushNow(state, "session_deleted");
          await this.closeRuntimeAuthSession(state.runtimeAuth);
          this.sessions.delete(key);
        }
        this.sessionAgents.delete(identity.sessionID);
      }
      return undefined;
    }
    return undefined;
  }

  async dispose() {
    this.stopInventoryMonitor();
    const flushes = [];
    for (const state of this.sessions.values()) {
      flushes.push(this.flushNow(state, "dispose"));
    }
    await Promise.allSettled(flushes);
    await this.closeRuntimeAuthSession();
    this.sessions.clear();
    this.sessionAgents.clear();
  }
}

function decisionAllows(decision) {
  return (
    !decision ||
    decision.decision_type === DecisionType.ALLOW ||
    decision.decision_type === DecisionType.LOG_ONLY
  );
}

function buildModifiedArgs(decision, fallback) {
  const payload = decisionPayload(decision);
  if (payload && typeof payload === "object" && !Array.isArray(payload)) {
    const candidate = payload.args || payload.arguments || payload.params;
    if (candidate && typeof candidate === "object" && !Array.isArray(candidate)) {
      return candidate;
    }
    if (!("output" in payload) && !("result" in payload) && !("messages" in payload)) {
      return payload;
    }
  }
  return fallback;
}

function applyModifiedToolResult(output, decision) {
  const payload = decisionPayload(decision);
  if (!payload || typeof payload !== "object" || Array.isArray(payload)) {
    output.output = buildReplacementText(decision, output.output);
    return;
  }
  const result = payload.result && typeof payload.result === "object" ? payload.result : payload;
  if (result.title !== undefined) {
    output.title = asText(result.title);
  }
  if (result.output !== undefined) {
    output.output = asText(result.output);
  } else if (result.text !== undefined) {
    output.output = asText(result.text);
  }
  if (result.metadata !== undefined && result.metadata && typeof result.metadata === "object") {
    output.metadata = result.metadata;
  }
}

function applyModifiedLlmInput(output, decision) {
  const payload = decisionPayload(decision);
  if (!payload || typeof payload !== "object") {
    return;
  }
  const messages = Array.isArray(payload.messages) ? payload.messages : null;
  if (messages && isOpenCodeMessages(messages)) {
    output.messages = messages;
    return;
  }
  const replacement = textFromLlmInputPayload(payload);
  if (replacement) {
    replaceLatestUserText(output.messages, replacement);
  }
}

function textFromLlmInputPayload(payload) {
  if (payload == null) {
    return "";
  }
  if (typeof payload === "string") {
    return payload;
  }
  if (Array.isArray(payload.messages)) {
    const texts = payload.messages.map((item) => {
      if (item && typeof item === "object") {
        return asText(item.content ?? item.text ?? item.message);
      }
      return asText(item);
    }).filter(Boolean);
    return texts.join("\n");
  }
  return asText(payload.prompt ?? payload.content ?? payload.text ?? payload.message);
}

function buildReplacementText(decision, fallback = DEFAULT_SANITIZED_MESSAGE) {
  const payload = decisionPayload(decision);
  if (typeof payload === "string" && payload) {
    return payload;
  }
  if (payload && typeof payload === "object" && !Array.isArray(payload)) {
    const value =
      payload.output ??
      payload.text ??
      payload.content ??
      payload.message ??
      payload.result ??
      payload.replacement;
    if (value !== undefined) {
      return asText(value);
    }
  }
  if (decision.processed_content) {
    return asText(decision.processed_content);
  }
  return decision.reason || fallback;
}

function decisionPayload(decision) {
  const raw = decision ? decision.processed_content : null;
  if (raw == null || raw === "") {
    return null;
  }
  if (typeof raw !== "string") {
    return raw;
  }
  const trimmed = raw.trim();
  if (!trimmed) {
    return null;
  }
  try {
    return JSON.parse(trimmed);
  } catch (_) {
    return trimmed;
  }
}

function normalizeToolResultOutput(output) {
  return {
    title: output.title || "",
    output: output.output || "",
    metadata: output.metadata || {},
    attachments: output.attachments || [],
  };
}

function normalizeOpenCodeMessages(messages) {
  if (!Array.isArray(messages)) {
    return [];
  }
  return messages.map((message) => ({
    role: normalizeRole(message && message.info ? message.info.role : message && message.role),
    content: collectMessageText(message),
  }));
}

function collectMessageText(message) {
  if (!message || typeof message !== "object") {
    return asText(message);
  }
  const parts = Array.isArray(message.parts) ? message.parts : Array.isArray(message.info && message.info.parts) ? message.info.parts : null;
  if (parts) {
    return parts.map(partText).filter(Boolean).join("\n");
  }
  return asText(message.text ?? message.content ?? message.message);
}

function partText(part) {
  if (part == null) {
    return "";
  }
  if (typeof part === "string") {
    return part;
  }
  if (typeof part !== "object") {
    return asText(part);
  }
  if (part.type === "text" || part.type === "reasoning") {
    return asText(part.text);
  }
  if (part.type === "file") {
    return `[file: ${asText(part.filename || part.url || "attachment")}]`;
  }
  if (part.type === "tool") {
    return `[tool:${asText(part.tool || part.name || "unknown")}]`;
  }
  if (part.text !== undefined) {
    return asText(part.text);
  }
  return "";
}

function replaceLatestUserText(messages, text) {
  if (!Array.isArray(messages)) {
    return false;
  }
  for (let i = messages.length - 1; i >= 0; i -= 1) {
    const message = messages[i];
    const role = normalizeRole(message && message.info ? message.info.role : message && message.role);
    if (role !== "user") {
      continue;
    }
    const parts = Array.isArray(message.parts) ? message.parts : null;
    if (!parts) {
      continue;
    }
    for (let j = parts.length - 1; j >= 0; j -= 1) {
      if (parts[j] && typeof parts[j] === "object" && parts[j].type === "text") {
        parts[j].text = text;
        return true;
      }
    }
    parts.push({ type: "text", text });
    return true;
  }
  return false;
}

function isOpenCodeMessages(messages) {
  return messages.every((message) => {
    return Boolean(
      message &&
        typeof message === "object" &&
        !Array.isArray(message) &&
        message.info &&
        typeof message.info === "object" &&
        Array.isArray(message.parts),
    );
  });
}

function findSessionId(value) {
  if (!value) {
    return null;
  }
  if (Array.isArray(value)) {
    for (const item of value) {
      const found = findSessionId(item);
      if (found) {
        return found;
      }
    }
    return null;
  }
  if (typeof value !== "object") {
    return null;
  }
  const direct =
    asNonEmptyString(value.sessionID) ||
    asNonEmptyString(value.sessionId) ||
    asNonEmptyString(value.session_id);
  if (direct) {
    return direct;
  }
  const metadata = value.metadata && typeof value.metadata === "object" ? value.metadata : null;
  const nested =
    asNonEmptyString(metadata && metadata.sessionID) ||
    asNonEmptyString(metadata && metadata.sessionId) ||
    asNonEmptyString(metadata && metadata.session_id);
  if (nested) {
    return nested;
  }
  return findSessionId(value.info) || findSessionId(value.parts);
}

function normalizeRole(role) {
  const text = asNonEmptyString(role);
  if (!text) {
    return "user";
  }
  if (["assistant", "system", "tool"].includes(text)) {
    return text;
  }
  return "user";
}

function normalizeModel(model) {
  if (!model) {
    return null;
  }
  if (typeof model === "string") {
    return model;
  }
  if (typeof model === "object") {
    const provider = asNonEmptyString(model.providerID || model.providerId || model.provider);
    const id = asNonEmptyString(model.modelID || model.modelId || model.id);
    return provider && id ? `${provider}/${id}` : provider || id || null;
  }
  return asText(model);
}

function openCodeAgentName(identity = {}, config = {}) {
  return (
    asNonEmptyString(identity && identity.agent) ||
    asNonEmptyString(config && config.opencodeAgent) ||
    "default"
  );
}

function openCodeExternalAgentId(agentName) {
  const name = asNonEmptyString(agentName) || "default";
  return `opencode:${slug(name) || stableShortId(name)}`;
}

function openCodeExternalSessionId(state) {
  const identity = state && state.identity ? state.identity : {};
  return (
    asNonEmptyString(identity.sessionID) ||
    asNonEmptyString(
      state &&
        state.context &&
        state.context.metadata &&
        state.context.metadata.opencode_session_id,
    ) ||
    asNonEmptyString(identity.fallbackID) ||
    asNonEmptyString(state && state.key) ||
    "default"
  );
}

function openCodeRuntimeExternalSessionId(state, runtimeSessionNonce) {
  const externalSessionId = openCodeExternalSessionId(state);
  const nonce = asNonEmptyString(runtimeSessionNonce);
  return nonce
    ? `${externalSessionId}:runtime:${nonce}`
    : externalSessionId;
}

function withoutEmpty(value) {
  return Object.fromEntries(
    Object.entries(value || {}).filter(([, item]) => item !== null && item !== undefined && item !== ""),
  );
}

function slug(value) {
  return String(value || "")
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9_.:-]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 80);
}

function stableShortId(value) {
  return crypto.createHash("sha256").update(String(value || "")).digest("hex").slice(0, 12);
}

function asText(value) {
  if (value == null) {
    return "";
  }
  if (typeof value === "string") {
    return value;
  }
  if (typeof value === "object") {
    try {
      return JSON.stringify(value);
    } catch (_) {
      return String(value);
    }
  }
  return String(value);
}

function asNonEmptyString(value) {
  if (value == null) {
    return null;
  }
  const text = String(value).trim();
  return text || null;
}

function asPositiveNumber(value, fallback = null) {
  const number = Number(value);
  return Number.isFinite(number) && number > 0 ? number : fallback;
}

function asPositiveInteger(value, fallback) {
  const number = Number.parseInt(String(value), 10);
  return Number.isInteger(number) && number > 0 ? number : fallback;
}

function asNonNegativeInteger(value, fallback) {
  const number = Number.parseInt(String(value), 10);
  return Number.isInteger(number) && number >= 0 ? number : fallback;
}

module.exports = {
  AgentGuardOpenCodeBridge,
  __testing: {
    buildModifiedArgs,
    buildOpenCodeAgentRegistration,
    buildReplacementText,
    decisionPayload,
    discoverOpenCodeSkillRoots,
    discoverOpenCodeAgents,
    filterMcpScanForAgent,
    filterSkillScanForAgent,
    normalizeOpenCodeAgentCatalog,
    findSessionId,
    isRemoteUnavailableDecision,
    normalizeOpenCodeMessages,
    normalizePhaseConfig,
    normalizePluginConfig,
    mcpScanSignature,
    openCodeRuntimeExternalSessionId,
    replaceLatestUserText,
    runtimeAuthRefreshDelayMs,
    scanConfiguredOpenCodeMcps,
    scanConfiguredOpenCodeSkills,
    shouldFailClosed,
    skillScanSignature,
    wildcardMatch,
  },
};

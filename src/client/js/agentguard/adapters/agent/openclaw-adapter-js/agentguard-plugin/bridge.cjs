"use strict";

const fs = require("node:fs");
const path = require("node:path");

const {
  DEFAULT_OPTIONS: DEFAULT_SKILL_SCAN_OPTIONS,
  resolveScanPath,
  scanSkillRoots,
} = require("./skill_scanner.cjs");
const {
  DEFAULT_OPTIONS: DEFAULT_MCP_SCAN_OPTIONS,
  scanMcpConfigs,
} = require("./mcp_scanner.cjs");
const {
  AuditLogger,
  AuditRecorder,
  ClientConfigAPIServer,
  ClientSyncBuffer,
  DecisionType,
  DPoPKey,
  EventType,
  GuardDecision,
  agentIdentityKeyId,
  buildAgentRegistrationPayload,
  loadOrCreateAgentKey,
  loadOrCreateDPoPKey,
  PluginManager,
  PolicySnapshot,
  RemoteGuardClient,
  RuntimeContext,
  RuntimeEvent,
  UGuardEnforcer,
} = require("./agentguard-runtime.cjs");

const DEFAULT_WINDOW_SIZE = 8;
const DEFAULT_POLICY = "builtin";
const DEFAULT_REMOTE_UNAVAILABLE_MODE = "fail_closed";
const DEFAULT_BLOCK_MESSAGE = "Request blocked by AgentGuard policy.";
const CLOSED_SESSION_BLOCK_MESSAGE =
  "AgentGuard closed this OpenClaw session. Start a new OpenClaw conversation before continuing.";
const DEFAULT_BLOCK_PREPEND_CONTEXT = [
  "AgentGuard policy blocked this request.",
  "Do not call tools, do not continue the requested task, and respond only with the block message below.",
].join(" ");
const DEFAULT_SANITIZED_MESSAGE = "Response removed by AgentGuard.";
const DEFAULT_PHASE_CONFIG_PATH = path.resolve(__dirname, "../../../../../../../../config/plugins.json");
const DEFAULT_TOOL_CATALOG_PATH = path.resolve(
  __dirname,
  "../../../../../../../../config/openclaw-default-tools.json",
);
const LLM_OUTPUT_DEDUP_WINDOW_MS = 2 * 60 * 1000;

const PRE_GUARD_PHASES = new Set(["tool_before", "llm_before"]);

function normalizePluginConfig(raw = {}) {
  const { config, configDir } = loadPluginConfigSource(raw);
  const serverUrl = asNonEmptyString(config.serverUrl);
  return {
    serverUrl,
    apiKey: resolveApiKey(config),
    policy: asNonEmptyString(config.policy) || DEFAULT_POLICY,
    auditPath: asNonEmptyString(config.auditPath),
    providerInstanceId: resolveProviderInstanceId(config),
    openclawConfigPath: resolveOpenClawConfigPath(config),
    phases: resolvePhaseConfig(config),
    toolCapabilities: normalizeToolCapabilities(config.toolCapabilities),
    identity: normalizeIdentity(config.identity),
    runtimeAuth: normalizeRuntimeAuthConfig(config),
    defaultTools: resolveDefaultTools(config, configDir),
    skillScan: normalizeSkillScanConfig(config.skillScan, configDir),
    mcpScan: normalizeMcpScanConfig(config.mcpScan, configDir),
    remoteUnavailableMode:
      asNonEmptyString(config.remoteUnavailableMode) || DEFAULT_REMOTE_UNAVAILABLE_MODE,
    windowSize: asPositiveInteger(config.windowSize, DEFAULT_WINDOW_SIZE),
    hasRemoteConfigured: Boolean(serverUrl),
  };
}

function loadPluginConfigSource(raw = {}) {
  const config = raw && typeof raw === "object" ? { ...raw } : {};
  const configPath = asNonEmptyString(config.configPath);
  if (configPath) {
    return loadConfigFile(configPath);
  }
  return { config, configDir: undefined };
}

function loadConfigFile(configPath) {
  const resolvedPath = path.resolve(configPath);
  return {
    config: loadJsonObject({
      filePath: resolvedPath,
      label: "AgentGuard config",
    }),
    configDir: path.dirname(resolvedPath),
  };
}

function loadPhaseConfigFile(configPath = DEFAULT_PHASE_CONFIG_PATH) {
  const resolvedPath = path.resolve(configPath);
  const parsed = loadJsonObject({
    filePath: resolvedPath,
    label: "AgentGuard phase config",
  });
  return normalizePhaseConfig(parsed.phases);
}

function loadJsonObject({ filePath, label }) {
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

function resolvePhaseConfig(config) {
  if (config.phases && typeof config.phases === "object" && !Array.isArray(config.phases)) {
    return normalizePhaseConfig(config.phases);
  }
  return loadPhaseConfigFile();
}

function normalizePhaseConfig(phases) {
  if (!phases || typeof phases !== "object" || Array.isArray(phases)) {
    return {};
  }
  return { ...phases };
}

function normalizeToolCapabilities(value) {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    return {};
  }
  return Object.fromEntries(
    Object.entries(value).map(([toolName, capabilities]) => [
      toolName,
      Array.isArray(capabilities)
        ? capabilities.filter((item) => typeof item === "string" && item.trim())
        : [],
    ]),
  );
}

function normalizeStringArray(value, fallback = []) {
  if (!Array.isArray(value)) {
    return [...fallback];
  }
  return value
    .filter((item) => typeof item === "string" && item.trim())
    .map((item) => item.trim());
}

function resolveConfigRelativePath(filePath, baseDir) {
  const normalizedPath = asNonEmptyString(filePath);
  if (!normalizedPath) {
    return undefined;
  }
  if (path.isAbsolute(normalizedPath)) {
    return normalizedPath;
  }
  return path.resolve(baseDir || process.cwd(), normalizedPath);
}

function normalizeToolCatalogEntry(entry, index) {
  if (!entry || typeof entry !== "object" || Array.isArray(entry)) {
    throw new TypeError(`OpenClaw default tool catalog entry ${index} must be an object.`);
  }
  const metadata =
    entry.metadata && typeof entry.metadata === "object" && !Array.isArray(entry.metadata)
      ? { ...entry.metadata }
      : {};
  const name = asNonEmptyString(entry.name);
  if (!name) {
    throw new TypeError(`OpenClaw default tool catalog entry ${index} is missing tool.name.`);
  }
  return {
    name,
    description: asNonEmptyString(entry.description) || "",
    input_params: Array.isArray(entry.input_params)
      ? entry.input_params.filter((item) => typeof item === "string" && item.trim())
      : [],
    capabilities: Array.isArray(entry.capabilities)
      ? entry.capabilities.filter((item) => typeof item === "string" && item.trim())
      : [],
    metadata,
  };
}

function normalizeToolCatalog(value) {
  if (!Array.isArray(value)) {
    throw new TypeError("OpenClaw default tool catalog must define a tools array.");
  }
  const tools = value.map((entry, index) => normalizeToolCatalogEntry(entry, index));
  if (tools.length === 0) {
    throw new TypeError("OpenClaw default tool catalog must contain at least one tool.");
  }
  return tools;
}

function resolveDefaultTools(config, configDir) {
  const catalogPath =
    resolveConfigRelativePath(config.defaultToolCatalogPath, configDir) || DEFAULT_TOOL_CATALOG_PATH;
  const catalog = loadJsonObject({
    filePath: catalogPath,
    label: "OpenClaw default tool catalog",
  });
  return normalizeToolCatalog(catalog.tools);
}

function normalizeSkillScanConfig(value, configDir) {
  const input = value && typeof value === "object" && !Array.isArray(value) ? value : {};
  const baseDir = configDir || process.cwd();
  return {
    enabled: input.enabled === true,
    roots: normalizeStringArray(input.roots).map((item) => resolveScanPath(item, baseDir)),
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
  };
}

function normalizeMcpScanConfig(value, configDir) {
  const input = value && typeof value === "object" && !Array.isArray(value) ? value : {};
  const baseDir = configDir || process.cwd();
  return {
    enabled: input.enabled === true,
    roots: normalizeStringArray(input.roots).map((item) => resolveScanPath(item, baseDir)),
    configPaths: normalizeStringArray(input.configPaths).map((item) => resolveScanPath(item, baseDir)),
    configNames: normalizeStringArray(input.configNames, DEFAULT_MCP_SCAN_OPTIONS.configNames),
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
  };
}

function normalizeIdentity(value) {
  const identity = value && typeof value === "object" && !Array.isArray(value) ? { ...value } : {};
  return {
    userId: asNonEmptyString(identity.userId),
    userIdFrom: asNonEmptyString(identity.userIdFrom) || "accountId",
    agentId: asNonEmptyString(identity.agentId),
    agentIdFrom: asNonEmptyString(identity.agentIdFrom) || "agentId",
    environment: asNonEmptyString(identity.environment),
    role: asNonEmptyString(identity.role),
    trustLevel:
      typeof identity.trustLevel === "number" && Number.isFinite(identity.trustLevel)
        ? identity.trustLevel
        : typeof identity.trust_level === "number" && Number.isFinite(identity.trust_level)
          ? identity.trust_level
          : undefined,
  };
}

function normalizeRuntimeAuthConfig(config) {
  const userTicket = resolveUserTicket(config || {});
  return {
    provider: "openclaw",
    userTicket,
    configured: Boolean(userTicket),
  };
}

function resolveApiKey(config) {
  const direct = asNonEmptyString(config.apiKey);
  if (direct) {
    return direct;
  }
  const envVar = asNonEmptyString(config.apiKeyEnvVar);
  return envVar ? asNonEmptyString(process.env[envVar]) : undefined;
}

function resolveUserTicket(config) {
  const direct = asNonEmptyString(config.userTicket);
  if (direct) {
    return direct;
  }
  const envVar = asNonEmptyString(config.userTicketEnvVar);
  return envVar ? asNonEmptyString(process.env[envVar]) : undefined;
}

function resolveProviderInstanceId(config) {
  return (
    asNonEmptyString(config.providerInstanceId) ||
    asNonEmptyString(process.env.AGENTGUARD_OPENCLAW_PROVIDER_INSTANCE_ID) ||
    "openclaw-local"
  );
}

function resolveOpenClawConfigPath(config) {
  const configured =
    asNonEmptyString(config.openclawConfigPath) ||
    asNonEmptyString(process.env.AGENTGUARD_OPENCLAW_CONFIG_PATH);
  if (configured) {
    return path.resolve(expandHome(configured));
  }
  const home = process.env.HOME || process.env.USERPROFILE;
  return home ? path.join(home, ".openclaw", "openclaw.json") : undefined;
}

function asNonEmptyString(value) {
  return typeof value === "string" && value.trim() ? value.trim() : undefined;
}

function expandHome(value) {
  const text = String(value || "");
  if (!text.startsWith("~")) {
    return text;
  }
  const home = process.env.HOME || process.env.USERPROFILE || "";
  return home ? text.replace(/^~(?=$|\/|\\)/, home) : text;
}

function asPositiveInteger(value, fallback) {
  if (typeof value !== "number" || !Number.isFinite(value) || value <= 0) {
    return fallback;
  }
  return Math.floor(value);
}

function safeJSONStringify(value) {
  try {
    return JSON.stringify(value);
  } catch (_) {
    return String(value);
  }
}

function deriveIdentityValue(source, identityContext) {
  switch (source) {
    case "accountId":
      return asNonEmptyString(identityContext.accountId);
    case "senderId":
      return asNonEmptyString(identityContext.senderId);
    case "channelId":
      return asNonEmptyString(identityContext.channelId);
    case "conversationId":
      return asNonEmptyString(identityContext.conversationId);
    case "sessionKey":
      return asNonEmptyString(identityContext.sessionKey);
    case "sessionId":
      return asNonEmptyString(identityContext.sessionId);
    case "runId":
      return asNonEmptyString(identityContext.runId);
    case "agentId":
    default:
      return asNonEmptyString(identityContext.agentId);
  }
}

function buildRuntimeContext(config, identityContext) {
  const openclawSessionId = asNonEmptyString(identityContext.sessionId);
  const sessionId = openclawSessionId || asNonEmptyString(identityContext.sessionKey) || "unknown";
  const sessionKey = asNonEmptyString(identityContext.sessionKey) || sessionId;
  const derivedUserId =
    config.identity.userId ||
    deriveIdentityValue(config.identity.userIdFrom, identityContext) ||
    null;
  const derivedAgentId =
    config.identity.agentId ||
    deriveIdentityValue(config.identity.agentIdFrom, identityContext) ||
    openClawAgentIdFromSessionKey(sessionKey) ||
    null;

  return new RuntimeContext({
    session_id: sessionId,
    user_id: derivedUserId,
    agent_id: derivedAgentId,
    task_id: asNonEmptyString(identityContext.runId) || null,
    policy: config.policy,
    policy_version: config.policy,
    environment: config.identity.environment || "openclaw",
    metadata: {
      ...(config.identity.role ? { role: config.identity.role } : {}),
      ...(config.identity.trustLevel !== undefined
        ? { trust_level: config.identity.trustLevel }
        : {}),
      principal: {
        ...(derivedAgentId ? { agent_id: derivedAgentId } : {}),
        ...(derivedUserId ? { user_id: derivedUserId } : {}),
        ...(config.identity.role ? { role: config.identity.role } : {}),
        ...(config.identity.trustLevel !== undefined
          ? { trust_level: config.identity.trustLevel }
          : {}),
      },
      client_session_key: sessionKey,
      client_plugin_config: { phases: config.phases },
      remote_plugin_config: { phases: config.phases },
      openclaw: {
        agentId: derivedAgentId,
        sessionId: openclawSessionId || null,
        sessionKey,
        channelId: identityContext.channelId || null,
        accountId: identityContext.accountId || null,
        conversationId: identityContext.conversationId || null,
        senderId: identityContext.senderId || null,
        runId: identityContext.runId || null,
      },
    },
  });
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

function pickMetadata(decision, keyCandidates) {
  const metadata =
    decision && decision.metadata && typeof decision.metadata === "object" ? decision.metadata : {};
  for (const key of keyCandidates) {
    if (metadata[key] !== undefined && metadata[key] !== null) {
      return metadata[key];
    }
  }
  return undefined;
}

function buildApproval(decision) {
  const metadata =
    decision && decision.metadata && typeof decision.metadata === "object" ? decision.metadata : {};
  const approval =
    metadata.approval && typeof metadata.approval === "object" && !Array.isArray(metadata.approval)
      ? metadata.approval
      : {};
  return {
    title: asNonEmptyString(approval.title) || "AgentGuard approval required",
    description:
      asNonEmptyString(approval.description) ||
      asNonEmptyString(metadata.userMessage) ||
      decision.reason ||
      "Approval required by AgentGuard.",
    severity: asNonEmptyString(approval.severity) || "warning",
    timeoutMs: Number.isFinite(approval.timeoutMs) ? Math.max(0, approval.timeoutMs) : 60_000,
    timeoutBehavior: asNonEmptyString(approval.timeoutBehavior) || "deny",
    allowedDecisions: Array.isArray(approval.allowedDecisions)
      ? approval.allowedDecisions.filter((value) => typeof value === "string")
      : ["allow-once", "deny"],
  };
}

function buildRewrittenParams(decision) {
  const direct = pickMetadata(decision, [
    "params",
    "rewrittenParams",
    "rewriteParams",
    "replacementParams",
    "toolParams",
  ]);
  if (direct && typeof direct === "object" && !Array.isArray(direct)) {
    return { ...direct };
  }
  const nested = pickMetadata(decision, ["rewrite", "replacement", "tool"]);
  if (nested && typeof nested === "object" && !Array.isArray(nested)) {
    const params =
      nested.params ||
      nested.rewrittenParams ||
      nested.rewriteParams ||
      nested.replacementParams;
    if (params && typeof params === "object" && !Array.isArray(params)) {
      return { ...params };
    }
  }
  return undefined;
}

function buildReplacementText(decision, fallback = DEFAULT_SANITIZED_MESSAGE) {
  return (
    asNonEmptyString(
      pickMetadata(decision, [
        "sanitizedText",
        "sanitized_text",
        "rewriteText",
        "rewrite_text",
        "replacementText",
        "replacement_text",
        "outputText",
        "output_text",
        "messageText",
        "message_text",
        "safeText",
        "safe_text",
      ]),
    ) || fallback
  );
}

function buildUserBlockMessage(decision) {
  return (
    asNonEmptyString(pickMetadata(decision, ["userMessage", "user_message", "blockMessage"])) ||
    DEFAULT_BLOCK_MESSAGE
  );
}

function buildBlockedPromptContext(decision) {
  const message = buildUserBlockMessage(decision);
  const reason = asNonEmptyString(decision && decision.reason);
  return [
    DEFAULT_BLOCK_PREPEND_CONTEXT,
    reason ? `Reason: ${reason}` : null,
    `Block message: ${message}`,
  ].filter(Boolean).join("\n");
}

function resolveCapabilities(toolCapabilities, event) {
  const configured = toolCapabilities[event.toolName];
  if (configured && configured.length) {
    return configured;
  }
  if (event.toolKind === "code_mode_exec" || event.toolName === "exec") {
    return ["exec"];
  }
  return [];
}

function buildLlmInputMessages(event = {}) {
  const prompt = asNonEmptyString(event.prompt);
  if (prompt) {
    const messages = [];
    const systemPrompt = asNonEmptyString(event.systemPrompt);
    if (systemPrompt) {
      messages.push({ role: "system", content: systemPrompt });
    }
    messages.push({ role: "user", content: prompt });
    return messages;
  }
  if (Array.isArray(event.messages) && event.messages.length) {
    return event.messages.map(normalizeOpenClawMessage);
  }
  const messages = [];
  if (asNonEmptyString(event.systemPrompt)) {
    messages.push({ role: "system", content: event.systemPrompt });
  }
  if (asNonEmptyString(event.prompt)) {
    messages.push({ role: "user", content: event.prompt });
  }
  return messages;
}

function buildLlmOutputText(event = {}) {
  if (Array.isArray(event.assistantTexts)) {
    const joined = event.assistantTexts
      .map((item) => normalizeOpenClawContent(item))
      .filter(Boolean)
      .join("\n");
    if (joined) {
      return joined;
    }
  }
  return normalizeOpenClawContent(
    event.content ?? event.output ?? event.text ?? event.message ?? event.final_output,
  );
}

function formatToolCallBlock(block = {}) {
  const name =
    asNonEmptyString(block.name) ||
    asNonEmptyString(block.toolName) ||
    asNonEmptyString(block.functionName) ||
    "unknown_tool";
  const args = block.arguments ?? block.args ?? block.input ?? block.params;
  if (args === undefined) {
    return `[toolCall ${name}]`;
  }
  return `[toolCall ${name}] ${safeJSONStringify(args)}`;
}

function formatToolResultContent(message = {}) {
  const toolName = asNonEmptyString(message.toolName) || asNonEmptyString(message.name) || "tool";
  const text =
    normalizeOpenClawContent(message.content) ||
    normalizeOpenClawContent(message.details?.text) ||
    normalizeOpenClawContent(message.details) ||
    normalizeOpenClawContent(message.result);
  return text ? `[toolResult ${toolName}] ${text}` : `[toolResult ${toolName}]`;
}

function normalizeOpenClawContent(value) {
  if (value == null) {
    return "";
  }
  if (typeof value === "string") {
    return value;
  }
  if (typeof value === "number" || typeof value === "boolean" || typeof value === "bigint") {
    return String(value);
  }
  if (Array.isArray(value)) {
    return value
      .map((item) => normalizeOpenClawContent(item))
      .filter((item) => typeof item === "string" && item.trim())
      .join("\n");
  }
  if (typeof value !== "object") {
    return String(value);
  }

  const blockType = asNonEmptyString(value.type);
  if (blockType === "text" || blockType === "input_text" || blockType === "output_text") {
    return asNonEmptyString(value.text) || "";
  }
  if (blockType === "toolCall" || blockType === "toolUse" || blockType === "functionCall") {
    return formatToolCallBlock(value);
  }
  if (Array.isArray(value.content)) {
    const nested = normalizeOpenClawContent(value.content);
    if (nested) {
      return nested;
    }
  }
  if (typeof value.text === "string") {
    return value.text;
  }
  if (typeof value.output === "string") {
    return value.output;
  }
  if (typeof value.message === "string") {
    return value.message;
  }
  if (value.details && typeof value.details === "object" && typeof value.details.text === "string") {
    return value.details.text;
  }
  return safeJSONStringify(value);
}

function normalizeOpenClawMessage(message) {
  const raw =
    message &&
    typeof message === "object" &&
    !Array.isArray(message) &&
    message.message &&
    typeof message.message === "object" &&
    !Array.isArray(message.message)
      ? message.message
      : message;
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) {
    return {
      role: "user",
      content: normalizeOpenClawContent(raw),
    };
  }

  const role = asNonEmptyString(raw.role) || "user";
  const normalized = {
    ...raw,
    role,
    content: role === "toolResult"
      ? formatToolResultContent(raw)
      : normalizeOpenClawContent(raw.content ?? raw.text ?? raw.output ?? raw.message),
  };
  return normalized;
}

function extractAssistantFinalText(messages) {
  if (!Array.isArray(messages) || messages.length === 0) {
    return "";
  }
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    const normalized = normalizeOpenClawMessage(messages[index]);
    if (normalized.role !== "assistant") {
      continue;
    }
    const raw =
      messages[index] &&
      typeof messages[index] === "object" &&
      !Array.isArray(messages[index]) &&
      messages[index].message &&
      typeof messages[index].message === "object" &&
      !Array.isArray(messages[index].message)
        ? messages[index].message
        : messages[index];
    if (raw && typeof raw === "object" && Array.isArray(raw.content)) {
      const textBlocks = raw.content
        .filter(
          (block) =>
            block &&
            typeof block === "object" &&
            !Array.isArray(block) &&
            (block.type === "text" ||
              block.type === "input_text" ||
              block.type === "output_text") &&
            typeof block.text === "string",
        )
        .map((block) => block.text.trim())
        .filter(Boolean);
      if (textBlocks.length) {
        return textBlocks.join("\n");
      }
    }
    if (normalized.content && !normalized.content.startsWith("[toolCall ")) {
      return normalized.content;
    }
  }
  return "";
}

function buildPluginConfigPayload(config) {
  return { phases: normalizePhaseConfig(config && config.phases) };
}

function emptySkillScanResult(config, diagnostics = []) {
  return {
    enabled: Boolean(config && config.enabled),
    skills: [],
    diagnostics,
    summary: {
      roots: config && Array.isArray(config.roots) ? config.roots : [],
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
      roots: config && Array.isArray(config.roots) ? config.roots : [],
      config_paths: config && Array.isArray(config.configPaths) ? config.configPaths : [],
      mcp_count: 0,
      diagnostic_count: diagnostics.length,
    },
  };
}

function scanConfiguredSkills(config, logger = console) {
  if (!config || !config.enabled) {
    return emptySkillScanResult(config);
  }
  if (!Array.isArray(config.roots) || config.roots.length === 0) {
    return emptySkillScanResult(config, [
      {
        level: "warning",
        reason: "no_skill_scan_roots",
        message: "skillScan.enabled is true but skillScan.roots is empty.",
      },
    ]);
  }
  try {
    const result = scanSkillRoots({
      roots: config.roots,
      baseDir: config.baseDir,
      maxFileBytes: config.maxFileBytes,
      maxTotalBytesPerSkill: config.maxTotalBytesPerSkill,
      maxFilesPerSkill: config.maxFilesPerSkill,
      excludeDirs: config.excludeDirs,
      excludeFiles: config.excludeFiles,
      textExtensions: config.textExtensions,
      followSymlinks: config.followSymlinks,
    });
    return {
      enabled: true,
      ...result,
    };
  } catch (error) {
    const message = String(error && error.message ? error.message : error);
    logger.warn?.("AgentGuard OpenClaw plugin failed to scan configured skills.", error);
    return emptySkillScanResult(config, [
      {
        level: "error",
        reason: "skill_scan_failed",
        message,
      },
    ]);
  }
}

function scanConfiguredMcps(config, logger = console) {
  if (!config || !config.enabled) {
    return emptyMcpScanResult(config);
  }
  const hasRoots = Array.isArray(config.roots) && config.roots.length > 0;
  const hasConfigPaths = Array.isArray(config.configPaths) && config.configPaths.length > 0;
  if (!hasRoots && !hasConfigPaths) {
    return emptyMcpScanResult(config, [
      {
        level: "warning",
        reason: "no_mcp_scan_sources",
        message: "mcpScan.enabled is true but mcpScan.roots and mcpScan.configPaths are empty.",
      },
    ]);
  }
  try {
    const result = scanMcpConfigs({
      roots: config.roots,
      configPaths: config.configPaths,
      configNames: config.configNames,
      baseDir: config.baseDir,
      maxFileBytes: config.maxFileBytes,
      maxTotalBytesPerServer: config.maxTotalBytesPerServer,
      maxFilesPerServer: config.maxFilesPerServer,
      excludeDirs: config.excludeDirs,
      excludeFiles: config.excludeFiles,
      textExtensions: config.textExtensions,
      followSymlinks: config.followSymlinks,
    });
    return {
      enabled: true,
      ...result,
    };
  } catch (error) {
    const message = String(error && error.message ? error.message : error);
    logger.warn?.("AgentGuard OpenClaw plugin failed to scan configured MCP servers.", error);
    return emptyMcpScanResult(config, [
      {
        level: "error",
        reason: "mcp_scan_failed",
        message,
      },
    ]);
  }
}

function buildSkillScanMetadata(skillScan) {
  const summary = skillScan && skillScan.summary ? skillScan.summary : {};
  return {
    enabled: Boolean(skillScan && skillScan.enabled),
    roots: Array.isArray(summary.roots) ? summary.roots : [],
    skill_count: Number.isFinite(summary.skill_count) ? summary.skill_count : 0,
    diagnostic_count: Number.isFinite(summary.diagnostic_count) ? summary.diagnostic_count : 0,
    skills: Array.isArray(skillScan && skillScan.skills)
      ? skillScan.skills.map((skill) => ({
        name: asNonEmptyString(skill.name) || "",
        description: asNonEmptyString(skill.description) || "",
        root_path: asNonEmptyString(skill.root_path) || "",
        sha256: asNonEmptyString(skill.sha256) || "",
        file_count: Number.isFinite(skill.file_count) ? skill.file_count : 0,
        total_size: Number.isFinite(skill.total_size) ? skill.total_size : 0,
        extraction: skill.extraction || null,
      }))
      : [],
  };
}

function buildMcpScanMetadata(mcpScan) {
  const summary = mcpScan && mcpScan.summary ? mcpScan.summary : {};
  return {
    enabled: Boolean(mcpScan && mcpScan.enabled),
    roots: Array.isArray(summary.roots) ? summary.roots : [],
    config_paths: Array.isArray(summary.config_paths) ? summary.config_paths : [],
    mcp_count: Number.isFinite(summary.mcp_count) ? summary.mcp_count : 0,
    diagnostic_count: Number.isFinite(summary.diagnostic_count) ? summary.diagnostic_count : 0,
    mcps: Array.isArray(mcpScan && mcpScan.mcps)
      ? mcpScan.mcps.map((mcp) => ({
        name: asNonEmptyString(mcp.name) || "",
        description: asNonEmptyString(mcp.description) || "",
        transport: asNonEmptyString(mcp.transport) || "",
        remote: Boolean(mcp.remote),
        root_path: asNonEmptyString(mcp.root_path) || "",
        entry_file: asNonEmptyString(mcp.entry_file) || "",
        url: asNonEmptyString(mcp.url) || "",
        source_status: asNonEmptyString(mcp.source_status) || "",
        sha256: asNonEmptyString(mcp.sha256) || "",
        tool_count: Number.isFinite(mcp.tool_count) ? mcp.tool_count : 0,
        file_count: Number.isFinite(mcp.file_count) ? mcp.file_count : 0,
        total_size: Number.isFinite(mcp.total_size) ? mcp.total_size : 0,
        extraction: mcp.extraction || null,
      }))
      : [],
  };
}

function normalizeMcpToolFragment(value) {
  return asNonEmptyString(value) || "";
}

function providerSafeMcpPrefix(name) {
  const raw = normalizeMcpToolFragment(name).toLowerCase();
  const normalized = raw.replace(/[^a-z0-9_-]+/g, "-").replace(/^-+|-+$/g, "");
  const prefixed = /^[a-z]/.test(normalized) ? normalized : `mcp-${normalized || "server"}`;
  return prefixed;
}

function candidateMcpPrefixes(mcp) {
  const prefixes = new Set();
  for (const value of [mcp.name, mcp.config_key]) {
    const raw = normalizeMcpToolFragment(value);
    if (!raw) {
      continue;
    }
    prefixes.add(raw);
    prefixes.add(raw.toLowerCase());
    prefixes.add(providerSafeMcpPrefix(raw));
  }
  return [...prefixes].filter(Boolean);
}

function candidateMcpToolNames(mcp, tool) {
  const toolName = normalizeMcpToolFragment(tool && tool.name);
  if (!toolName) {
    return [];
  }
  const names = new Set([toolName]);
  for (const prefix of candidateMcpPrefixes(mcp)) {
    names.add(`${prefix}__${toolName}`);
    names.add(`mcp:${prefix}:${toolName}`);
  }
  return [...names];
}

function extractMcpEventHints(event = {}) {
  const mcp = event.mcp && typeof event.mcp === "object" && !Array.isArray(event.mcp)
    ? event.mcp
    : {};
  return {
    server: normalizeMcpToolFragment(
      event.mcpServerName
        || event.mcp_server_name
        || event.mcpServer
        || event.mcp_server
        || event.serverName
        || event.server
        || mcp.serverName
        || mcp.server_name
        || mcp.server
        || mcp.name,
    ),
    tool: normalizeMcpToolFragment(
      event.mcpToolName
        || event.mcp_tool_name
        || event.originalToolName
        || event.original_tool_name
        || mcp.toolName
        || mcp.tool_name,
    ),
    id: normalizeMcpToolFragment(event.mcpUniqueId || event.mcp_unique_id || mcp.mcp_unique_id || mcp.id),
  };
}

function mcpUniqueIdForContext(context, mcp) {
  const explicit = normalizeMcpToolFragment(mcp.mcp_unique_id || mcp.id);
  if (explicit) {
    return explicit;
  }
  const agentId = normalizeMcpToolFragment(context && context.agent_id);
  const sha256 = normalizeMcpToolFragment(mcp.sha256);
  return agentId && sha256 ? `${agentId}:${sha256}` : sha256;
}

function serverHintMatchesMcp(serverHint, idHint, context, mcp) {
  const server = normalizeMcpToolFragment(serverHint).toLowerCase();
  const id = normalizeMcpToolFragment(idHint);
  if (id && id === mcpUniqueIdForContext(context, mcp)) {
    return true;
  }
  if (!server) {
    return false;
  }
  return candidateMcpPrefixes(mcp).some((prefix) => prefix.toLowerCase() === server)
    || normalizeMcpToolFragment(mcp.sha256) === server;
}

function toolNameMatchesMcpTool(runtimeToolName, toolName, mcp, tool) {
  const runtimeName = normalizeMcpToolFragment(runtimeToolName);
  const hintedTool = normalizeMcpToolFragment(toolName);
  return candidateMcpToolNames(mcp, tool).some((candidate) => candidate === runtimeName)
    || (hintedTool && normalizeMcpToolFragment(tool && tool.name) === hintedTool);
}

function matchMcpRuntimeTool(state, event = {}) {
  const mcps = Array.isArray(state && state.mcpScan && state.mcpScan.mcps)
    ? state.mcpScan.mcps
    : [];
  if (!mcps.length) {
    return null;
  }
  const runtimeToolName = normalizeMcpToolFragment(event.toolName);
  const hints = extractMcpEventHints(event);
  const matches = [];

  for (const mcp of mcps) {
    const tools = Array.isArray(mcp.tools) ? mcp.tools : [];
    const serverMatches = serverHintMatchesMcp(hints.server, hints.id, state.context, mcp);
    if (serverMatches && (!tools.length || !hints.tool)) {
      matches.push({ mcp, tool: null, confidence: "server_hint" });
      continue;
    }
    for (const tool of tools) {
      if (toolNameMatchesMcpTool(runtimeToolName, hints.tool, mcp, tool)) {
        matches.push({
          mcp,
          tool,
          confidence: serverMatches || runtimeToolName.includes("__") || runtimeToolName.startsWith("mcp:")
            ? "qualified_tool"
            : "tool_name",
        });
      }
    }
  }

  if (matches.length !== 1) {
    return null;
  }
  return matches[0];
}

function buildMcpRuntimeMetadata(state, event = {}) {
  const match = matchMcpRuntimeTool(state, event);
  if (!match) {
    return {};
  }
  const { mcp, tool, confidence } = match;
  const mcpToolName = normalizeMcpToolFragment(tool && tool.name)
    || normalizeMcpToolFragment(event.mcpToolName || event.mcp_tool_name)
    || normalizeMcpToolFragment(event.toolName);
  return {
    toolSource: "mcp",
    sourceFramework: "mcp_native",
    mcp_unique_id: mcpUniqueIdForContext(state.context, mcp),
    mcp_name: normalizeMcpToolFragment(mcp.name),
    mcp_tool_name: mcpToolName,
    mcp_match_confidence: confidence,
    mcp_transport: normalizeMcpToolFragment(mcp.transport),
    mcp_remote: Boolean(mcp.remote),
    mcp_config_path: normalizeMcpToolFragment(mcp.config_path),
    mcp_config_key: normalizeMcpToolFragment(mcp.config_key),
    mcp_root_path: normalizeMcpToolFragment(mcp.root_path),
    mcp_entry_file: normalizeMcpToolFragment(mcp.entry_file),
    mcp_url: normalizeMcpToolFragment(mcp.url),
    mcp_sha256: normalizeMcpToolFragment(mcp.sha256),
    mcp_source_status: normalizeMcpToolFragment(mcp.source_status),
  };
}

function buildToolReportPayload(tool) {
  const metadata =
    tool && typeof tool.metadata === "object" && tool.metadata && !Array.isArray(tool.metadata)
      ? tool.metadata
      : {};
  const capabilities = Array.isArray(tool.capabilities)
    ? tool.capabilities.filter((item) => typeof item === "string" && item.trim())
    : [];
  return {
    name: asNonEmptyString(tool.name) || "tool",
    description: asNonEmptyString(tool.description) || "",
    input_params: Array.isArray(tool.input_params)
      ? tool.input_params.filter((item) => typeof item === "string" && item.trim())
      : [],
    capabilities,
    labels: {
      boundary: asNonEmptyString(metadata.boundary) || "internal",
      sensitivity: asNonEmptyString(metadata.sensitivity) || "low",
      integrity: asNonEmptyString(metadata.integrity) || "trusted",
      tags: [
        ...new Set(
          [metadata.tags, capabilities]
            .flat()
            .filter((item) => typeof item === "string" && item.trim())
            .map((item) => item.trim()),
        ),
      ],
    },
  };
}

function buildRuntimeAuthState(config) {
  const runtimeAuth = config && config.runtimeAuth;
  if (!runtimeAuth || !runtimeAuth.configured) {
    return null;
  }
  const dpopKey = new DPoPKey();
  const state = {
    provider: "openclaw",
    user_ticket: runtimeAuth.userTicket,
    dpop_key: dpopKey,
    dpop_key_id: null,
    session_id: null,
    agent_id: null,
    external_agent_id: null,
    external_session_id: null,
    user_id: null,
    session_token: null,
    expires_at: 0,
  };
  state.proof = (method, url, accessToken = null) =>
    (state.dpop_key || dpopKey).proof(method, url, accessToken);
  return state;
}

function runtimeAuthFresh(runtimeAuth) {
  return Boolean(
    runtimeAuth &&
      runtimeAuth.session_token &&
      runtimeAuth.expires_at - Math.floor(Date.now() / 1000) > 60,
  );
}

function clearRuntimeAuthSession(runtimeAuth) {
  if (!runtimeAuth) {
    return;
  }
  runtimeAuth.session_id = null;
  runtimeAuth.agent_id = null;
  runtimeAuth.user_id = null;
  runtimeAuth.external_session_id = null;
  runtimeAuth.session_token = null;
  runtimeAuth.expires_at = 0;
}

function openClawExternalSessionId(context) {
  const metadata = context && context.metadata && typeof context.metadata === "object"
    ? context.metadata
    : {};
  const openclaw = metadata.openclaw && typeof metadata.openclaw === "object"
    ? metadata.openclaw
    : {};
  const sessionKey = asNonEmptyString(openclaw.sessionKey) || asNonEmptyString(metadata.client_session_key);
  const openclawSessionId = asNonEmptyString(openclaw.sessionId);
  const contextSessionId = asNonEmptyString(context && context.session_id);
  return (
    (openclawSessionId && openclawSessionId !== sessionKey ? openclawSessionId : undefined) ||
    (contextSessionId && contextSessionId !== sessionKey ? contextSessionId : undefined) ||
    sessionKey
  );
}

function buildOpenClawRuntimeAuthMetadata(context) {
  const metadata = context && context.metadata && typeof context.metadata === "object"
    ? context.metadata
    : {};
  const openclaw = metadata.openclaw && typeof metadata.openclaw === "object"
    ? metadata.openclaw
    : {};
  return {
    adapter: "openclaw",
    runtime_auth_provider: "openclaw",
    agentguard_agent_id: context.agent_id || null,
    openclaw_agent_id: openclaw.agentId || metadata.external_agent_id || context.agent_id || null,
    openclaw_session_id: openclaw.sessionId || context.session_id || null,
    openclaw_session_key: metadata.client_session_key || null,
    client_session_key: metadata.client_session_key || null,
    client_config_url: metadata.client_config_url || null,
    client_plugin_list_url: metadata.client_plugin_list_url || null,
    client_health_url: metadata.client_health_url || null,
    openclaw: {
      ...openclaw,
    },
  };
}

function openClawAgentIdFromSessionKey(sessionKey) {
  const raw = asNonEmptyString(sessionKey);
  if (!raw) {
    return undefined;
  }
  const match = raw.match(/^agent:([^:]+)(?::|$)/);
  return asNonEmptyString(match && match[1]) || undefined;
}

function resolveConfiguredOpenClawStorePath(store, agentId) {
  const configured = asNonEmptyString(store);
  if (!configured) {
    return undefined;
  }
  const expanded = expandHome(configured.replaceAll("{agentId}", agentId || "main"));
  return path.resolve(expanded);
}

function readOpenClawConfiguredSessionStore(config, agentId) {
  const configPath = asNonEmptyString(config && config.openclawConfigPath);
  if (!configPath || !fs.existsSync(configPath)) {
    return undefined;
  }
  try {
    const parsed = JSON.parse(fs.readFileSync(configPath, "utf8"));
    return parsed && parsed.session && parsed.session.store;
  } catch (_) {
    return undefined;
  }
}

function resolveOpenClawStorePath(openclawRuntime, sessionKey, config = null) {
  const agentId = openClawAgentIdFromSessionKey(sessionKey);
  const sessionRuntime = openclawRuntime && openclawRuntime.channel && openclawRuntime.channel.session;
  const resolveStorePath = sessionRuntime && sessionRuntime.resolveStorePath;
  if (typeof resolveStorePath === "function") {
    const cfg = openclawRuntime.config && typeof openclawRuntime.config.loadConfig === "function"
      ? openclawRuntime.config.loadConfig()
      : {};
    return resolveStorePath(cfg && cfg.session && cfg.session.store, { agentId });
  }
  const configuredStore = readOpenClawConfiguredSessionStore(config, agentId);
  const configuredStorePath = resolveConfiguredOpenClawStorePath(configuredStore, agentId);
  if (configuredStorePath) {
    return configuredStorePath;
  }
  const home = process.env.HOME || process.env.USERPROFILE || "";
  if (!home) {
    throw new Error("cannot resolve OpenClaw session store without HOME");
  }
  return path.join(home, ".openclaw", "agents", agentId || "main", "sessions", "sessions.json");
}

function readOpenClawSessionStore(storePath) {
  if (!fs.existsSync(storePath)) {
    return {};
  }
  const source = fs.readFileSync(storePath, "utf8");
  if (!source.trim()) {
    return {};
  }
  const parsed = JSON.parse(source);
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
    throw new TypeError(`OpenClaw session store at ${storePath} must be a JSON object.`);
  }
  return parsed;
}

function resolveOpenClawSessionEntry(openclawRuntime, sessionKey, config = null, logger = console) {
  const key = asNonEmptyString(sessionKey);
  if (!key) {
    return null;
  }
  try {
    const storePath = resolveOpenClawStorePath(openclawRuntime, key, config);
    const store = readOpenClawSessionStore(storePath);
    const entry = store[key];
    return {
      storePath,
      store,
      entry: entry && typeof entry === "object" && !Array.isArray(entry) ? entry : null,
    };
  } catch (error) {
    logger.warn?.("AgentGuard OpenClaw plugin failed to read OpenClaw session store.", error);
    return null;
  }
}

function resolveOpenClawSessionId(openclawRuntime, sessionKey, config = null, logger = console) {
  const resolved = resolveOpenClawSessionEntry(openclawRuntime, sessionKey, config, logger);
  return asNonEmptyString(resolved && resolved.entry && (resolved.entry.sessionId || resolved.entry.session_id));
}

function writeOpenClawSessionStore(storePath, store) {
  fs.mkdirSync(path.dirname(storePath), { recursive: true });
  fs.writeFileSync(storePath, `${JSON.stringify(store, null, 2)}\n`);
}

function isAgentGuardClosedOpenClawEntry(entry, sessionId) {
  if (!entry || typeof entry !== "object" || Array.isArray(entry)) {
    return false;
  }
  const closedSessionId = asNonEmptyString(entry.agentguardClosedExternalSessionId);
  const currentSessionId = asNonEmptyString(sessionId || entry.sessionId || entry.session_id);
  if (closedSessionId && currentSessionId && closedSessionId !== currentSessionId) {
    return false;
  }
  return (
    Boolean(closedSessionId || asNonEmptyString(entry.agentguardRuntimeSessionId)) &&
    Boolean(asNonEmptyString(entry.agentguardClosedAt) || asNonEmptyString(entry.agentguardRuntimeSessionId))
  );
}

function hasAgentGuardClosedOpenClawMarker(entry) {
  return Boolean(
    entry &&
      typeof entry === "object" &&
      !Array.isArray(entry) &&
      (asNonEmptyString(entry.agentguardClosedExternalSessionId) ||
        asNonEmptyString(entry.agentguardClosedAt) ||
        asNonEmptyString(entry.agentguardRuntimeSessionId))
  );
}

function reconcileOpenClawSessionClosure(openclawRuntime, sessionKey, sessionId, config = null, logger = console) {
  const resolved = resolveOpenClawSessionEntry(openclawRuntime, sessionKey, config, logger);
  const entry = resolved && resolved.entry;
  if (!entry) {
    return { closed: false, cleared: false };
  }
  if (isAgentGuardClosedOpenClawEntry(entry, sessionId)) {
    return { closed: true, cleared: false };
  }

  const closedSessionId = asNonEmptyString(entry.agentguardClosedExternalSessionId);
  const currentSessionId = asNonEmptyString(sessionId || entry.sessionId || entry.session_id);
  const isStaleAgentGuardClose =
    closedSessionId &&
    currentSessionId &&
    closedSessionId !== currentSessionId &&
    hasAgentGuardClosedOpenClawMarker(entry);
  if (!isStaleAgentGuardClose) {
    return { closed: false, cleared: false };
  }

  const nextEntry = { ...entry };
  delete nextEntry.agentguardClosedAt;
  delete nextEntry.agentguardClosedReason;
  delete nextEntry.agentguardClosedExternalSessionId;
  delete nextEntry.agentguardRuntimeSessionId;
  nextEntry.updatedAt = Date.now();
  resolved.store[sessionKey] = nextEntry;
  try {
    writeOpenClawSessionStore(resolved.storePath, resolved.store);
  } catch (error) {
    logger.warn?.("AgentGuard OpenClaw plugin failed to clear stale OpenClaw close marker.", error);
    return { closed: false, cleared: false };
  }
  return { closed: false, cleared: true };
}

function isOpenClawExternalSessionClosedError(error) {
  const message = String(error && error.message ? error.message : error || "");
  return (
    /openclaw external session is closed/i.test(message) ||
    /external session is closed/i.test(message) ||
    /remote guard call failed:\s*HTTP 401/i.test(message)
  );
}

function resolveOpenClawCloseSessionKey(state, body = {}) {
  return (
    asNonEmptyString(body.openclaw_session_key) ||
    asNonEmptyString(body.session_key) ||
    asNonEmptyString(body.sessionKey) ||
    asNonEmptyString(state && state.context && state.context.metadata && state.context.metadata.client_session_key)
  );
}

function discoverOpenClawAgents(config, openclawRuntime = null, logger = console) {
  const runtimeAgents = discoverOpenClawAgentsFromRuntime(openclawRuntime, logger);
  if (runtimeAgents.length) {
    return runtimeAgents;
  }
  return discoverOpenClawAgentsFromConfig(config && config.openclawConfigPath, logger);
}

function discoverOpenClawAgentsFromRuntime(openclawRuntime, logger = console) {
  const candidates = [
    openclawRuntime && openclawRuntime.agents && openclawRuntime.agents.list,
    openclawRuntime && openclawRuntime.agentRegistry && openclawRuntime.agentRegistry.list,
    openclawRuntime && openclawRuntime.listAgents,
  ];
  for (const candidate of candidates) {
    if (typeof candidate !== "function") {
      continue;
    }
    try {
      const value = candidate.call(openclawRuntime);
      if (Array.isArray(value)) {
        return normalizeOpenClawAgentCatalog(value);
      }
    } catch (error) {
      logger.warn?.("AgentGuard OpenClaw plugin failed to read runtime agent catalog.", error);
    }
  }
  return [];
}

function discoverOpenClawAgentsFromConfig(configPath, logger = console) {
  const resolvedPath = asNonEmptyString(configPath);
  if (!resolvedPath || !fs.existsSync(resolvedPath)) {
    return [];
  }
  try {
    const parsed = JSON.parse(fs.readFileSync(resolvedPath, "utf8"));
    const list = parsed && parsed.agents && Array.isArray(parsed.agents.list)
      ? parsed.agents.list
      : [];
    return normalizeOpenClawAgentCatalog(list);
  } catch (error) {
    logger.warn?.("AgentGuard OpenClaw plugin failed to read OpenClaw agent catalog.", error);
    return [];
  }
}

function normalizeOpenClawAgentCatalog(items) {
  const byId = new Map();
  for (const item of Array.isArray(items) ? items : []) {
    const agent = normalizeOpenClawCatalogAgent(item);
    if (agent && !byId.has(agent.id)) {
      byId.set(agent.id, agent);
    }
  }
  return [...byId.values()];
}

function normalizeOpenClawCatalogAgent(item) {
  const source = item && typeof item === "object" && !Array.isArray(item)
    ? item
    : { id: item };
  const id = asNonEmptyString(source.id || source.agentId || source.agent_id || source.name);
  if (!id) {
    return null;
  }
  return {
    id,
    name: asNonEmptyString(source.name) || id,
    description: asNonEmptyString(source.description) || `OpenClaw agent: ${id}`,
    workspace: asNonEmptyString(source.workspace || source.workspaceDir),
    agentDir: asNonEmptyString(source.agentDir || source.agent_dir),
    model: asNonEmptyString(source.model),
    isDefault: source.isDefault === true,
  };
}

function ensureCatalogContainsAgent(catalog, agentId) {
  const id = asNonEmptyString(agentId);
  const existing = normalizeOpenClawAgentCatalog(catalog);
  if (!id || existing.some((agent) => agent.id === id)) {
    return existing;
  }
  return [
    ...existing,
    {
      id,
      name: id,
      description: `OpenClaw agent: ${id}`,
    },
  ];
}

function buildOpenClawAgentRegistration(config, agent, extraMetadata = {}) {
  const providerInstanceId = asNonEmptyString(config && config.providerInstanceId) || "openclaw-local";
  const agentType = "agent";
  const keyId = agentIdentityKeyId({
    provider: "openclaw",
    provider_instance_id: providerInstanceId,
    tenant_id: null,
    external_agent_id: agent.id,
    agent_type: agentType,
  });
  const payload = buildAgentRegistrationPayload({
    provider: "openclaw",
    provider_instance_id: providerInstanceId,
    tenant_id: null,
    external_agent_id: agent.id,
    agent_type: agentType,
    name: agent.name || agent.id,
    description: agent.description || `OpenClaw agent: ${agent.id}`,
    metadata: {
      adapter: "openclaw",
      external_provider: "openclaw",
      external_agent_id: agent.id,
      display_agent_id: `openclaw:${agent.id}`,
      provider_instance_id: providerInstanceId,
      openclaw_agent_id: agent.id,
      openclaw_agent_name: agent.name || agent.id,
      openclaw_workspace: agent.workspace || null,
      openclaw_agent_dir: agent.agentDir || null,
      openclaw_model: agent.model || null,
      openclaw_is_default: Boolean(agent.isDefault),
      ...(extraMetadata && typeof extraMetadata === "object" && !Array.isArray(extraMetadata)
        ? extraMetadata
        : {}),
    },
  });
  return { payload, keyId, externalAgentId: agent.id };
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

function mergeDefaultToolCapabilities(tool, capabilityMap) {
  const configuredCapabilities = capabilityMap?.[tool.name];
  if (Array.isArray(tool.capabilities) && tool.capabilities.length) {
    return tool;
  }
  if (!Array.isArray(configuredCapabilities) || configuredCapabilities.length === 0) {
    return tool;
  }
  return {
    ...tool,
    capabilities: configuredCapabilities,
  };
}

class AgentGuardOpenClawBridge {
  constructor(options = {}) {
    this.pluginId = options.pluginId || "agentguard";
    this.config = normalizePluginConfig(options.pluginConfig || {});
    this.logger = options.logger || console;
    this.openclawRuntime = options.openclawRuntime || null;
    this.openclawAgents = discoverOpenClawAgents(this.config, this.openclawRuntime, this.logger);
    this.agentBootstrap = null;
    this.agentRegistrations = new Map();
    this.agentRegistrationPayloads = new Map();
    this.skillScan = scanConfiguredSkills(this.config.skillScan, this.logger);
    this.mcpScan = scanConfiguredMcps(this.config.mcpScan, this.logger);
    this.sessions = new Map();
  }

  getSkillScanResult() {
    return this.skillScan;
  }

  getMcpScanResult() {
    return this.mcpScan;
  }

  resolveIdentityContext(identityContext = {}) {
    const input = identityContext && typeof identityContext === "object" ? { ...identityContext } : {};
    const resolvedSessionId = resolveOpenClawSessionId(
      this.openclawRuntime,
      input.sessionKey,
      this.config,
      this.logger,
    );
    if (resolvedSessionId) {
      input.sessionId = resolvedSessionId;
    }
    return input;
  }

  getState(identityContext) {
    const resolvedIdentityContext = this.resolveIdentityContext(identityContext);
    const context = buildRuntimeContext(this.config, resolvedIdentityContext);
    const sessionKey = context.metadata.client_session_key || context.session_id;
    const openclawSessionId = context.metadata.openclaw && context.metadata.openclaw.sessionId;
    const openclawSessionClosure = this.reconcileOpenClawSessionClosure(sessionKey, openclawSessionId);
    const openclawSessionClosed = openclawSessionClosure.closed;
    let state = this.sessions.get(sessionKey);
    if (state) {
      const previousOpenClaw = state.context &&
        state.context.metadata &&
        state.context.metadata.openclaw &&
        typeof state.context.metadata.openclaw === "object"
        ? state.context.metadata.openclaw
        : {};
      const nextOpenClaw = context.metadata &&
        context.metadata.openclaw &&
        typeof context.metadata.openclaw === "object"
        ? context.metadata.openclaw
        : {};
      const previousSessionId = asNonEmptyString(previousOpenClaw.sessionId);
      const nextSessionId = asNonEmptyString(nextOpenClaw.sessionId);
      if (previousSessionId && nextSessionId && previousSessionId !== nextSessionId) {
        this.resetSessionStateForOpenClawSessionChange(state, context);
      }
      if (
        !nextSessionId &&
        !asNonEmptyString(resolvedIdentityContext && resolvedIdentityContext.conversationId) &&
        previousSessionId
      ) {
        context.session_id = state.context.session_id;
        context.metadata.openclaw = {
          ...nextOpenClaw,
          sessionId: previousSessionId,
        };
      }
      state.context = context;
      state.openclawSessionClosed = openclawSessionClosed;
      if (state.openclawSessionClosed) {
        this.resetRuntimeStateAfterOpenClawSessionClosed(state);
      }
      this.syncContextMetadata(state);
      this.applyRuntimeAuthContext(state);
      state.enforcer.remote.session_id = state.context.session_id;
      state.enforcer.remote.agent_id = state.context.agent_id;
      state.enforcer.remote.user_id = state.context.user_id;
      state.enforcer.remote.session_key = sessionKey;
      return state;
    }

    const runtimeAuth = buildRuntimeAuthState(this.config);
    const remote = new RemoteGuardClient(this.config.serverUrl || null, {
      api_key: this.config.apiKey || null,
      session_id: context.session_id,
      agent_id: context.agent_id,
      user_id: context.user_id,
      session_key: sessionKey,
      session_token: runtimeAuth && runtimeAuth.session_token,
      dpop_proof_factory: runtimeAuth && runtimeAuth.proof,
      use_dpop_auth: Boolean(runtimeAuth),
      legacy_identity_headers: !runtimeAuth,
    });
    const pluginManager = new PluginManager({
      config: { phases: this.config.phases },
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
      context,
      enforcer,
      audit,
      skillScan: this.skillScan,
      mcpScan: this.mcpScan,
      clientPluginConfig: buildPluginConfigPayload(this.config),
      remotePluginConfig: buildPluginConfigPayload(this.config),
      clientConfigApi: null,
      clientConfigApiStartup: null,
      runtimeAuth,
      runtimeAuthStartup: null,
      runtimeAuthError: null,
      remoteSessionRegistration: null,
      defaultToolReporting: null,
      recentLlmOutputs: new Map(),
      skillReporting: null,
      mcpReporting: null,
      openclawSessionClosed,
    };
    if (state.openclawSessionClosed) {
      this.resetRuntimeStateAfterOpenClawSessionClosed(state);
    }
    this.syncContextMetadata(state);
    this.sessions.set(sessionKey, state);
    if (!state.openclawSessionClosed) {
      this.ensureDefaultToolReports(state);
      this.ensureSkillReports(state);
      this.ensureMcpReports(state);
    }
    return state;
  }

  isOpenClawSessionClosed(sessionKey, sessionId) {
    return this.reconcileOpenClawSessionClosure(sessionKey, sessionId).closed;
  }

  reconcileOpenClawSessionClosure(sessionKey, sessionId) {
    return reconcileOpenClawSessionClosure(
      this.openclawRuntime,
      sessionKey,
      sessionId,
      this.config,
      this.logger,
    );
  }

  resetRuntimeStateAfterOpenClawSessionClosed(state) {
    if (state.runtimeAuth) {
      clearRuntimeAuthSession(state.runtimeAuth);
      state.runtimeAuthStartup = null;
    }
    state.remoteSessionRegistration = null;
    state.defaultToolReporting = null;
    state.skillReporting = null;
    state.mcpReporting = null;
    const remote = state.enforcer && state.enforcer.remote;
    if (remote) {
      remote.session_token = null;
    }
  }

  markOpenClawSessionClosed(state, reason = "agentguard_closed") {
    const metadata = state && state.context && state.context.metadata && typeof state.context.metadata === "object"
      ? state.context.metadata
      : {};
    const openclaw = metadata.openclaw && typeof metadata.openclaw === "object" ? metadata.openclaw : {};
    const sessionKey = asNonEmptyString(metadata.client_session_key || openclaw.sessionKey);
    if (!sessionKey) {
      return false;
    }
    const resolved = resolveOpenClawSessionEntry(this.openclawRuntime, sessionKey, this.config, this.logger);
    if (!resolved) {
      return false;
    }
    const current = resolved.entry || {};
    const externalSessionId = asNonEmptyString(openclaw.sessionId || current.sessionId || current.session_id);
    resolved.store[sessionKey] = {
      ...current,
      sessionId: externalSessionId || current.sessionId,
      updatedAt: Date.now(),
      agentguardClosedAt: asNonEmptyString(current.agentguardClosedAt) || new Date().toISOString(),
      agentguardClosedReason: reason,
      agentguardClosedExternalSessionId: externalSessionId || null,
      agentguardRuntimeSessionId:
        asNonEmptyString(current.agentguardRuntimeSessionId) ||
        asNonEmptyString(state && state.runtimeAuth && state.runtimeAuth.session_id) ||
        asNonEmptyString(metadata.agentguard_session_id),
    };
    writeOpenClawSessionStore(resolved.storePath, resolved.store);
    if (state) {
      state.openclawSessionClosed = true;
      this.resetRuntimeStateAfterOpenClawSessionClosed(state);
    }
    return true;
  }

  resetSessionStateForOpenClawSessionChange(state, context) {
    if (state.runtimeAuth) {
      clearRuntimeAuthSession(state.runtimeAuth);
      state.runtimeAuthStartup = null;
    }
    state.remoteSessionRegistration = null;
    state.defaultToolReporting = null;
    state.skillReporting = null;
    state.mcpReporting = null;
    state.recentLlmOutputs = new Map();
    state.audit = new AuditRecorder(
      context.session_id,
      new AuditLogger(this.config.auditPath || null),
    );
    if (state.enforcer) {
      state.enforcer.trace_window_provider = () => state.audit.trace.window(this.config.windowSize);
      if (state.enforcer.remote) {
        state.enforcer.remote.session_token = null;
      }
    }
  }

  clearSession(sessionKey) {
    if (sessionKey) {
      const state = this.sessions.get(sessionKey);
      this.sessions.delete(sessionKey);
      this.stopClientConfigApi(state);
    }
  }

  clearAll() {
    for (const state of this.sessions.values()) {
      this.stopClientConfigApi(state);
    }
    this.sessions.clear();
  }

  syncContextMetadata(state) {
    state.context.metadata = {
      ...(state.context.metadata || {}),
      client_plugin_config: state.clientPluginConfig,
      remote_plugin_config: state.remotePluginConfig,
      skill_scan: buildSkillScanMetadata(state.skillScan),
      mcp_scan: buildMcpScanMetadata(state.mcpScan),
    };
    if (state.clientConfigApi) {
      state.context.metadata.client_config_url = state.clientConfigApi.plugin_config_url;
      state.context.metadata.client_plugin_list_url = state.clientConfigApi.plugin_list_url;
      state.context.metadata.client_health_url = state.clientConfigApi.health_url;
    }
  }

  async ensureAgentBootstrap(state) {
    if (!state || !state.runtimeAuth) {
      return null;
    }
    const externalAgentId = asNonEmptyString(
      state.context.metadata &&
        state.context.metadata.openclaw &&
        state.context.metadata.openclaw.agentId,
    ) || asNonEmptyString(state.context.agent_id);
    const existing = externalAgentId ? this.agentRegistrations.get(externalAgentId) : null;
    if (existing) {
      this.applyCanonicalAgentRegistration(state, existing);
      return existing;
    }
    if (this.agentBootstrap) {
      await this.agentBootstrap;
      const registered = externalAgentId ? this.agentRegistrations.get(externalAgentId) : null;
      if (registered) {
        this.applyCanonicalAgentRegistration(state, registered);
      }
      return registered;
    }
    const remote = state.enforcer && state.enforcer.remote;
    if (!remote || !remote.enabled) {
      return null;
    }
    const discoveredCatalog = normalizeOpenClawAgentCatalog(this.openclawAgents);
    const catalog = discoveredCatalog.length
      ? discoveredCatalog
      : ensureCatalogContainsAgent(discoveredCatalog, externalAgentId);
    this.openclawAgents = catalog;
    const registrations = catalog.map((agent) =>
      buildOpenClawAgentRegistration(this.config, agent, {
        registration_reason: "bootstrap",
      }),
    );
    for (const registration of registrations) {
      this.agentRegistrationPayloads.set(registration.externalAgentId, registration);
    }
    this.agentBootstrap = remote.bootstrap_agents({
      provider: "openclaw",
      user_ticket: state.runtimeAuth.user_ticket,
      provider_instance_id: this.config.providerInstanceId,
      agents: registrations.map((item) => item.payload),
      metadata: {
        adapter: "openclaw",
        runtime_auth_provider: "openclaw",
        provider_instance_id: this.config.providerInstanceId,
      },
    })
      .then((response) => {
        const mapped = registrationMapFromBootstrap(response, registrations);
        for (const [key, value] of mapped.entries()) {
          this.agentRegistrations.set(key, value);
        }
        return mapped;
      })
      .catch((error) => {
        this.logger.warn?.("AgentGuard OpenClaw plugin failed to bootstrap OpenClaw agents.", error);
        throw error;
      })
      .finally(() => {
        this.agentBootstrap = null;
      });
    await this.agentBootstrap;
    const registered = externalAgentId ? this.agentRegistrations.get(externalAgentId) : null;
    if (registered) {
      this.applyCanonicalAgentRegistration(state, registered);
    }
    return registered;
  }

  applyCanonicalAgentRegistration(state, registration) {
    if (!state || !registration || !registration.agent_id) {
      return;
    }
    const originalAgentId = asNonEmptyString(
      state.context.metadata &&
        state.context.metadata.openclaw &&
        state.context.metadata.openclaw.agentId,
    ) || asNonEmptyString(state.context.agent_id);
    state.context.agent_id = registration.agent_id;
    state.context.metadata = {
      ...(state.context.metadata || {}),
      agentguard_agent_id: registration.agent_id,
      agent_identity_code: registration.agent_identity_code || null,
      agent_public_key_thumbprint: registration.public_key_thumbprint || null,
      agent_identity_key_id: registration.agent_identity_key_id || null,
      external_provider: "openclaw",
      external_agent_id: registration.external_agent_id || originalAgentId || null,
      display_agent_id: registration.external_agent_id
        ? `openclaw:${registration.external_agent_id}`
        : null,
      agentguard_user_bound: Boolean(registration.user_bound),
    };
    if (state.runtimeAuth) {
      state.runtimeAuth.agent_id = registration.agent_id;
      state.runtimeAuth.external_agent_id = registration.external_agent_id || originalAgentId || null;
      state.runtimeAuth.agent_identity_key_id = registration.agent_identity_key_id || null;
    }
    const remote = state.enforcer && state.enforcer.remote;
    if (remote) {
      remote.agent_id = registration.agent_id;
    }
  }

  applyRuntimeAuthContext(state) {
    const runtimeAuth = state && state.runtimeAuth;
    if (!runtimeAuth || !runtimeAuth.session_token) {
      return;
    }
    state.context.session_id = runtimeAuth.session_id || state.context.session_id;
    state.context.agent_id = runtimeAuth.agent_id || state.context.agent_id;
    state.context.user_id = runtimeAuth.user_id || state.context.user_id;
    state.context.metadata = {
      ...(state.context.metadata || {}),
      agentguard_session_id: runtimeAuth.session_id || null,
      agentguard_agent_id: runtimeAuth.agent_id || null,
      agentguard_user_id: runtimeAuth.user_id || null,
      runtime_auth_provider: "openclaw",
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
    }
  }

  async ensureRuntimeAuth(state) {
    const runtimeAuth = state && state.runtimeAuth;
    if (!runtimeAuth) {
      return true;
    }
    if (state.openclawSessionClosed) {
      throw new Error("OpenClaw external session is closed");
    }
    const desiredExternalSessionId = openClawExternalSessionId(state.context);
    if (runtimeAuthFresh(runtimeAuth) && runtimeAuth.external_session_id === desiredExternalSessionId) {
      this.applyRuntimeAuthContext(state);
      return true;
    }
    if (runtimeAuthFresh(runtimeAuth) && runtimeAuth.external_session_id !== desiredExternalSessionId) {
      clearRuntimeAuthSession(runtimeAuth);
      state.remoteSessionRegistration = null;
      state.defaultToolReporting = null;
      state.skillReporting = null;
      state.mcpReporting = null;
      const remote = state.enforcer && state.enforcer.remote;
      if (remote) {
        remote.session_token = null;
      }
    }
    if (state.runtimeAuthStartup) {
      await state.runtimeAuthStartup;
      return Boolean(runtimeAuth.session_token);
    }
    const remote = state.enforcer && state.enforcer.remote;
    if (!remote || !remote.enabled) {
      return false;
    }
    state.runtimeAuthStartup = (async () => {
      await this.ensureClientConfigApi(state);
      this.syncContextMetadata(state);
      if (runtimeAuth.session_token) {
        remote.session_token = runtimeAuth.session_token;
        try {
          const refreshed = await remote.refresh_runtime_session();
          this.applyRuntimeAuthIssue(state, refreshed);
          return true;
        } catch (error) {
          clearRuntimeAuthSession(runtimeAuth);
          remote.session_token = null;
          this.logger.warn?.(
            "AgentGuard OpenClaw plugin discarded stale runtime auth session after refresh failure.",
            error,
          );
        }
      }
      const registration = await this.ensureAgentBootstrap(state);
      if (!registration || !registration.agent_id) {
        throw new Error("OpenClaw agent bootstrap did not return a canonical AgentGuard agent");
      }
      const externalSessionId = openClawExternalSessionId(state.context);
      const dpopKeyId = ["openclaw", registration.agent_id, externalSessionId].join("\x1f");
      if (runtimeAuth.dpop_key_id !== dpopKeyId) {
        runtimeAuth.dpop_key = loadOrCreateDPoPKey(dpopKeyId);
        runtimeAuth.dpop_key_id = dpopKeyId;
      }
      remote.dpop_proof_factory = runtimeAuth.proof;
      remote.use_dpop_auth = true;
      remote.legacy_identity_headers = false;
      const body = {
        provider: "openclaw",
        agent_id: registration.agent_id,
        external_session_id: externalSessionId,
        external_user_id: asNonEmptyString(state.context.user_id) || null,
        metadata: buildOpenClawRuntimeAuthMetadata(state.context),
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
        if (isOpenClawExternalSessionClosedError(error)) {
          this.markOpenClawSessionClosed(state, "server_closed_external_session");
        }
        state.runtimeAuthError = error;
        throw error;
      })
      .finally(() => {
        state.runtimeAuthStartup = null;
      });
    await state.runtimeAuthStartup;
    return Boolean(runtimeAuth.session_token);
  }

  applyRuntimeAuthIssue(state, result, { externalSessionId = null } = {}) {
    const runtimeAuth = state.runtimeAuth;
    runtimeAuth.session_id = asNonEmptyString(result && result.session_id) || runtimeAuth.session_id;
    runtimeAuth.agent_id = asNonEmptyString(result && result.agent_id) || runtimeAuth.agent_id;
    runtimeAuth.user_id = asNonEmptyString(result && result.user_id) || runtimeAuth.user_id;
    runtimeAuth.external_session_id = asNonEmptyString(externalSessionId) || runtimeAuth.external_session_id;
    runtimeAuth.session_token = asNonEmptyString(result && result.session_token) || runtimeAuth.session_token;
    runtimeAuth.expires_at = Number(result && result.expires_at || 0);
    state.runtimeAuthError = null;
    this.applyRuntimeAuthContext(state);
    this.syncContextMetadata(state);
  }

  async updatePluginConfig(state, pluginConfig, { syncRemote = true, syncRemoteSession = syncRemote } = {}) {
    const nextConfig = pluginConfig && typeof pluginConfig === "object" ? { ...pluginConfig } : {};
    state.clientPluginConfig = buildPluginConfigPayload({ phases: nextConfig.phases });
    state.enforcer.update_plugin_config(state.clientPluginConfig);
    this.syncContextMetadata(state);
    if (syncRemoteSession) {
      state.remoteSessionRegistration = null;
      return this.ensureRemoteSessionRegistered(state);
    }
    return Promise.resolve(false);
  }

  async ensureClientConfigApi(state) {
    const remote = state.enforcer.remote;
    if (!remote || !remote.enabled) {
      return null;
    }
    if (state.clientConfigApiStartup) {
      await state.clientConfigApiStartup;
      return state.clientConfigApi;
    }
    if (!state.clientConfigApi) {
      const bridge = this;
      state.clientConfigApi = new ClientConfigAPIServer(
        {
          get context() {
            return state.context;
          },
          get session_key() {
            return state.context.metadata.client_session_key;
          },
          update_plugin_config(pluginConfig, options) {
            return bridge.updatePluginConfig(state, pluginConfig, options);
          },
          close_runtime_session(body) {
            return bridge.closeRuntimeSession(state, body);
          },
        },
        { host: "127.0.0.1", port: 0 },
      );
    }
    this.syncContextMetadata(state);
    state.clientConfigApiStartup = state.clientConfigApi.start()
      .then(() => {
        this.syncContextMetadata(state);
        return state.clientConfigApi;
      })
      .catch((error) => {
        this.logger.warn?.("AgentGuard OpenClaw plugin failed to start client config API.", error);
        this.syncContextMetadata(state);
        return state.clientConfigApi;
      })
      .finally(() => {
        state.clientConfigApiStartup = null;
      });
    await state.clientConfigApiStartup;
    return state.clientConfigApi;
  }

  stopClientConfigApi(state) {
    if (!state || !state.clientConfigApi) {
      return;
    }
    const server = state.clientConfigApi;
    state.clientConfigApi = null;
    state.clientConfigApiStartup = null;
    Promise.resolve(server.stop()).catch(() => {});
  }

  async closeRuntimeSession(state, body = {}) {
    const sessionKey = resolveOpenClawCloseSessionKey(state, body);
    if (!sessionKey) {
      throw new Error("missing OpenClaw session key");
    }
    let agentguardClose = null;
    const remote = state && state.enforcer && state.enforcer.remote;
    if (state && state.runtimeAuth && state.runtimeAuth.session_token && remote && remote.enabled) {
      this.applyRuntimeAuthContext(state);
      try {
        agentguardClose = await remote.close_runtime_session();
      } catch (error) {
        this.logger.warn?.("AgentGuard OpenClaw plugin failed to close runtime session.", error);
      }
    }
    const storePath = resolveOpenClawStorePath(this.openclawRuntime, sessionKey, this.config);
    const store = readOpenClawSessionStore(storePath);
    const current = store[sessionKey] && typeof store[sessionKey] === "object" && !Array.isArray(store[sessionKey])
      ? store[sessionKey]
      : {};
    const closedExternalSessionId =
      asNonEmptyString(current.sessionId) ||
      asNonEmptyString(body.openclaw_session_id) ||
      asNonEmptyString(body.session_id);
    store[sessionKey] = {
      ...current,
      sessionId: closedExternalSessionId,
      updatedAt: Date.now(),
      agentguardClosedAt: new Date().toISOString(),
      agentguardClosedReason: "agentguard_runtime_session_closed",
      agentguardClosedExternalSessionId: closedExternalSessionId || null,
      agentguardRuntimeSessionId:
        asNonEmptyString(body.agentguard_session_id) ||
        asNonEmptyString(state && state.runtimeAuth && state.runtimeAuth.session_id),
    };
    writeOpenClawSessionStore(storePath, store);
    if (state && state.runtimeAuth) {
      clearRuntimeAuthSession(state.runtimeAuth);
      state.runtimeAuthStartup = null;
      state.remoteSessionRegistration = null;
      state.defaultToolReporting = null;
      state.skillReporting = null;
      state.mcpReporting = null;
    }
    if (remote) {
      remote.session_token = null;
    }
    return {
      sessionKey,
      storePath,
      agentguardClosed: Boolean(agentguardClose && agentguardClose.closed),
      agentguardSessionId: asNonEmptyString(agentguardClose && agentguardClose.session_id) || null,
    };
  }

  ensureRemoteSessionRegistered(state) {
    const remote = state.enforcer.remote;
    if (!remote || !remote.enabled) {
      return Promise.resolve(false);
    }
    if (state.remoteSessionRegistration) {
      return state.remoteSessionRegistration;
    }
    state.remoteSessionRegistration = Promise.resolve()
      .then(() => this.ensureClientConfigApi(state))
      .then(() => {
        this.syncContextMetadata(state);
        if (state.runtimeAuth) {
          return this.ensureRuntimeAuth(state);
        }
        return remote.register_session(state.context);
      })
      .then(() => true)
      .catch((error) => {
        this.logger.warn?.("AgentGuard OpenClaw plugin failed to register remote session.", error);
        return false;
      });
    return state.remoteSessionRegistration;
  }

  ensureDefaultToolReports(state) {
    const remote = state.enforcer.remote;
    if (!remote || !remote.enabled) {
      return Promise.resolve(false);
    }
    if (state.defaultToolReporting) {
      return state.defaultToolReporting;
    }
    state.defaultToolReporting = this.ensureRemoteSessionRegistered(state)
      .then((registered) => {
        if (!registered) {
          return false;
        }
        return Promise.all(
          this.config.defaultTools.map((tool) =>
            remote.report_tool(
              state.context,
              buildToolReportPayload(
                mergeDefaultToolCapabilities(tool, this.config.toolCapabilities),
              ),
            ),
          ),
        ).then(() => true);
      })
      .catch((error) => {
        this.logger.warn?.("AgentGuard OpenClaw plugin failed to report default tools.", error);
        return false;
      });
    return state.defaultToolReporting;
  }

  ensureSkillReports(state) {
    const remote = state.enforcer.remote;
    if (!remote || !remote.enabled) {
      return Promise.resolve(false);
    }
    const skills = Array.isArray(state.skillScan && state.skillScan.skills)
      ? state.skillScan.skills
      : [];
    if (!state.skillScan || !state.skillScan.enabled || skills.length === 0) {
      return Promise.resolve(false);
    }
    if (state.skillReporting) {
      return state.skillReporting;
    }
    state.skillReporting = this.ensureRemoteSessionRegistered(state)
      .then((registered) => {
        if (!registered) {
          return false;
        }
        return remote.report_skills(
          state.context,
          skills,
          {
            source_framework: "openclaw_compatible",
            summary: state.skillScan.summary || {},
            diagnostics: state.skillScan.diagnostics || [],
          },
        ).then(() => true);
      })
      .catch((error) => {
        this.logger.warn?.("AgentGuard OpenClaw plugin failed to report skills.", error);
        return false;
      });
    return state.skillReporting;
  }

  ensureMcpReports(state) {
    const remote = state.enforcer.remote;
    if (!remote || !remote.enabled) {
      return Promise.resolve(false);
    }
    const mcps = Array.isArray(state.mcpScan && state.mcpScan.mcps)
      ? state.mcpScan.mcps
      : [];
    if (!state.mcpScan || !state.mcpScan.enabled || mcps.length === 0) {
      return Promise.resolve(false);
    }
    if (state.mcpReporting) {
      return state.mcpReporting;
    }
    state.mcpReporting = this.ensureRemoteSessionRegistered(state)
      .then((registered) => {
        if (!registered) {
          return false;
        }
        return remote.report_mcps(
          state.context,
          mcps,
          {
            source_framework: "mcp_native",
            summary: state.mcpScan.summary || {},
            diagnostics: state.mcpScan.diagnostics || [],
          },
        ).then(() => true);
      })
      .catch((error) => {
        this.logger.warn?.("AgentGuard OpenClaw plugin failed to report MCPs.", error);
        return false;
      });
    return state.mcpReporting;
  }

  async flushAsync(state, reason = "round_complete") {
    const remote = state.enforcer.remote;
    const buffer = state.enforcer.sync_buffer;
    if (!remote || !remote.enabled || !buffer || !buffer.has_entries()) {
      return false;
    }
    if (state.runtimeAuth) {
      try {
        await this.ensureRuntimeAuth(state);
      } catch (error) {
        this.logger.warn?.("AgentGuard OpenClaw plugin skipped async trace upload after runtime auth failure.", error);
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

  async flushNow(state, reason = "client_error") {
    const remote = state.enforcer.remote;
    const buffer = state.enforcer.sync_buffer;
    if (!remote || !remote.enabled || !buffer || !buffer.has_entries()) {
      return false;
    }
    if (state.runtimeAuth) {
      try {
        await this.ensureRuntimeAuth(state);
      } catch (error) {
        this.logger.warn?.("AgentGuard OpenClaw plugin skipped trace upload after runtime auth failure.", error);
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
    } catch (error) {
      buffer.restore_front(entries);
      return false;
    }
  }

  pruneRecentLlmOutputs(state) {
    if (!state || !state.recentLlmOutputs) {
      return;
    }
    const cutoff = Date.now() - LLM_OUTPUT_DEDUP_WINDOW_MS;
    for (const [fingerprint, seenAt] of state.recentLlmOutputs.entries()) {
      if (seenAt < cutoff) {
        state.recentLlmOutputs.delete(fingerprint);
      }
    }
  }

  rememberLlmOutput(state, outputText) {
    const fingerprint = asNonEmptyString(outputText);
    if (!fingerprint) {
      return;
    }
    this.pruneRecentLlmOutputs(state);
    state.recentLlmOutputs.set(fingerprint, Date.now());
  }

  hasRecentLlmOutput(state, outputText) {
    const fingerprint = asNonEmptyString(outputText);
    if (!fingerprint) {
      return false;
    }
    this.pruneRecentLlmOutputs(state);
    return state.recentLlmOutputs.has(fingerprint);
  }

  async enforce(state, runtimeEvent, options = {}) {
    if (state.openclawSessionClosed) {
      const decision = GuardDecision.deny(CLOSED_SESSION_BLOCK_MESSAGE, {
        metadata: {
          fail_closed: true,
          route: "openclaw_session_closed",
          runtime_auth_provider: "openclaw",
        },
      });
      state.audit.record(runtimeEvent, decision);
      return { event: runtimeEvent, decision };
    }
    if (state.runtimeAuth) {
      try {
        await this.ensureRuntimeAuth(state);
      } catch (error) {
        if (isOpenClawExternalSessionClosedError(error)) {
          this.markOpenClawSessionClosed(state, "server_closed_external_session");
          const decision = GuardDecision.deny(CLOSED_SESSION_BLOCK_MESSAGE, {
            metadata: {
              fail_closed: true,
              route: "openclaw_session_closed",
              runtime_auth_provider: "openclaw",
              error: String(error && error.message ? error.message : error),
            },
          });
          state.audit.record(runtimeEvent, decision);
          return { event: runtimeEvent, decision };
        }
        if (shouldFailClosed(this.config, options.phase)) {
          this.markOpenClawSessionClosed(state, "runtime_auth_failed");
          const decision = GuardDecision.deny(CLOSED_SESSION_BLOCK_MESSAGE, {
            metadata: {
              fail_closed: true,
              route: "openclaw_session_closed",
              runtime_auth_provider: "openclaw",
              error: String(error && error.message ? error.message : error),
            },
          });
          state.audit.record(runtimeEvent, decision);
          return { event: runtimeEvent, decision };
        }
        this.logger.warn?.("AgentGuard OpenClaw plugin runtime authentication failed.", error);
      }
    }
    let result;
    try {
      result = await state.enforcer.enforce(runtimeEvent, state.context, {
        extensions: options.extensions || {},
      });
      if (state.runtimeAuth && isRemoteUnavailableDecision(result.decision)) {
        const retry = await this.retryAfterRuntimeAuthUnavailable(state, runtimeEvent, options);
        if (retry) {
          result = retry;
        }
      }
    } catch (error) {
      await this.flushNow(state, "client_error");
      throw error;
    }

    let decision = result.decision;
    if (shouldFailClosed(this.config, options.phase) && isRemoteUnavailableDecision(decision)) {
      decision = {
        ...decision,
        decision_type: DecisionType.DENY,
        reason: decision.reason || "Remote AgentGuard review unavailable.",
        metadata: {
          ...(decision.metadata || {}),
          fail_closed: true,
          original_decision_type: DecisionType.REQUIRE_REMOTE_REVIEW,
        },
      };
    }

    state.audit.record(result.event, decision);
    return { ...result, decision };
  }

  async retryAfterRuntimeAuthUnavailable(state, runtimeEvent, options = {}) {
    if (!state || !state.runtimeAuth || !state.runtimeAuth.session_token) {
      return null;
    }
    clearRuntimeAuthSession(state.runtimeAuth);
    state.runtimeAuthStartup = null;
    state.remoteSessionRegistration = null;
    const remote = state.enforcer && state.enforcer.remote;
    if (remote) {
      remote.session_token = null;
    }
    try {
      await this.ensureRuntimeAuth(state);
      return await state.enforcer.enforce(runtimeEvent, state.context, {
        extensions: {
          ...(options.extensions || {}),
          runtime_auth_retry: true,
        },
      });
    } catch (error) {
      this.logger.warn?.("AgentGuard OpenClaw plugin failed to recover runtime auth session.", error);
      return null;
    }
  }

  async runBeforeToolCall({ ctx, event }) {
    const state = this.getState({
      agentId: ctx.agentId,
      sessionId: ctx.sessionId,
      sessionKey: ctx.sessionKey,
      runId: ctx.runId || event.runId,
      channelId: ctx.channelId,
    });
    const mcpRuntimeMetadata = buildMcpRuntimeMetadata(state, event);
    const runtimeEvent = createRuntimeEvent({
      eventType: EventType.TOOL_INVOKE,
      context: state.context,
      payload: {
        tool_name: event.toolName,
        arguments: event.params || {},
        capabilities: resolveCapabilities(this.config.toolCapabilities, event),
      },
      metadata: {
        phase: "tool_before",
        toolKind: event.toolKind,
        toolInputKind: event.toolInputKind,
        derivedPaths: event.derivedPaths || [],
        toolCallId: event.toolCallId || ctx.toolCallId,
        runId: event.runId || ctx.runId,
        ...mcpRuntimeMetadata,
      },
    });
    const result = await this.enforce(state, runtimeEvent, { phase: "tool_before" });
    const decision = result.decision;

    if (
      decision.decision_type === DecisionType.ALLOW ||
      decision.decision_type === DecisionType.LOG_ONLY
    ) {
      return undefined;
    }
    if (
      decision.decision_type === DecisionType.HUMAN_CHECK ||
      decision.decision_type === DecisionType.REQUIRE_APPROVAL
    ) {
      await this.flushNow(state, "guard_decide");
      return { requireApproval: buildApproval(decision) };
    }

    const rewrittenParams = buildRewrittenParams(decision);
    if (
      rewrittenParams &&
      (decision.decision_type === DecisionType.REWRITE ||
        decision.decision_type === DecisionType.REPAIR ||
        decision.decision_type === DecisionType.SANITIZE)
    ) {
      return { params: rewrittenParams };
    }

    await this.flushNow(state, "guard_decide");
    return {
      block: true,
      blockReason: decision.reason || "AgentGuard blocked tool call.",
    };
  }

  async runAfterToolCall({ ctx, event }) {
    const state = this.getState({
      agentId: ctx.agentId,
      sessionId: ctx.sessionId,
      sessionKey: ctx.sessionKey,
      runId: ctx.runId || event.runId,
      channelId: ctx.channelId,
    });
    const mcpRuntimeMetadata = buildMcpRuntimeMetadata(state, event);
    const runtimeEvent = createRuntimeEvent({
      eventType: EventType.TOOL_RESULT,
      context: state.context,
      payload: {
        tool_name: event.toolName,
        result: event.result,
      },
      metadata: {
        phase: "tool_after",
        toolCallId: event.toolCallId || ctx.toolCallId,
        runId: event.runId || ctx.runId,
        durationMs: event.durationMs,
        ...(event.error ? { error: event.error } : {}),
        ...mcpRuntimeMetadata,
      },
    });
    await this.enforce(state, runtimeEvent, { phase: "tool_after" });
    await this.flushAsync(state);
  }

  async runBeforeAgentRun({ ctx, event }) {
    const state = this.getState({
      agentId: ctx.agentId,
      sessionId: ctx.sessionId,
      sessionKey: ctx.sessionKey,
      runId: ctx.runId,
      channelId: ctx.channelId,
    });
    const runtimeEvent = createRuntimeEvent({
      eventType: EventType.LLM_INPUT,
      context: state.context,
      payload: {
        messages: buildLlmInputMessages(event),
      },
      metadata: {
        phase: "llm_before",
        runId: ctx.runId,
        ...(event.prompt ? { prompt: event.prompt } : {}),
        ...(event.systemPrompt ? { systemPrompt: event.systemPrompt } : {}),
      },
    });
    const result = await this.enforce(state, runtimeEvent, { phase: "llm_before" });
    const decision = result.decision;

    if (
      decision.decision_type === DecisionType.ALLOW ||
      decision.decision_type === DecisionType.LOG_ONLY
    ) {
      return undefined;
    }

    await this.flushNow(state, "guard_decide");
    return {
      prependContext: buildBlockedPromptContext(decision),
    };
  }

  async runMessageSending({ ctx, event }) {
    const state = this.getState({
      agentId: undefined,
      sessionId: undefined,
      sessionKey: ctx.sessionKey,
      runId: ctx.runId,
      channelId: ctx.channelId,
      accountId: ctx.accountId,
      conversationId: ctx.conversationId,
      senderId: ctx.senderId,
    });
    const outputText = buildLlmOutputText(event);
    const runtimeEvent = createRuntimeEvent({
      eventType: EventType.LLM_OUTPUT,
      context: state.context,
      payload: {
        output: outputText,
        final_output: outputText,
      },
      metadata: {
        phase: "llm_after",
        sourceHook: "message_sending",
        runId: ctx.runId,
        channelId: ctx.channelId,
        to: event.to,
        replyToId: event.replyToId,
        threadId: event.threadId,
        messageMetadata: event.metadata || {},
      },
    });
    const result = await this.enforce(state, runtimeEvent, { phase: "llm_after" });
    const decision = result.decision;

    if (
      decision.decision_type === DecisionType.ALLOW ||
      decision.decision_type === DecisionType.LOG_ONLY ||
      isRemoteUnavailableDecision(decision)
    ) {
      this.rememberLlmOutput(state, outputText);
      return undefined;
    }
    if (
      decision.decision_type === DecisionType.SANITIZE ||
      decision.decision_type === DecisionType.REWRITE ||
      decision.decision_type === DecisionType.REPAIR
    ) {
      this.rememberLlmOutput(state, outputText);
      return {
        content: buildReplacementText(decision),
        metadata: {
          agentguard: {
            decisionType: decision.decision_type,
            reason: decision.reason,
          },
        },
      };
    }

    return {
      cancel: true,
      cancelReason: decision.reason || "AgentGuard cancelled outbound message.",
    };
  }

  async runAgentEnd({ ctx, event }) {
    const state = this.getState({
      agentId: ctx.agentId,
      sessionId: ctx.sessionId,
      sessionKey: ctx.sessionKey,
      channelId: ctx.messageProvider || "agent",
    });
    const outputText = extractAssistantFinalText(event.messages);
    if (!outputText || this.hasRecentLlmOutput(state, outputText)) {
      return;
    }
    const runtimeEvent = createRuntimeEvent({
      eventType: EventType.LLM_OUTPUT,
      context: state.context,
      payload: {
        output: outputText,
        final_output: outputText,
      },
      metadata: {
        phase: "llm_after",
        sourceHook: "agent_end",
        posthoc_only: true,
        success: event.success,
        durationMs: event.durationMs,
        messageProvider: ctx.messageProvider,
        messageCount: Array.isArray(event.messages) ? event.messages.length : 0,
        ...(event.error ? { error: event.error } : {}),
      },
    });
    await this.enforce(state, runtimeEvent, { phase: "llm_after" });
    this.rememberLlmOutput(state, outputText);
    await this.flushAsync(state);
  }
}

module.exports = {
  AgentGuardOpenClawBridge,
  __testing: {
    buildApproval,
    buildReplacementText,
    buildRewrittenParams,
    buildRuntimeContext,
    buildLlmInputMessages,
    buildLlmOutputText,
    buildMcpRuntimeMetadata,
    buildUserBlockMessage,
    extractAssistantFinalText,
    formatToolResultContent,
    normalizeOpenClawContent,
    normalizeOpenClawMessage,
    matchMcpRuntimeTool,
    isRemoteUnavailableDecision,
    loadConfigFile,
    loadPluginConfigSource,
    normalizeMcpScanConfig,
    normalizePluginConfig,
    normalizeSkillScanConfig,
    buildMcpScanMetadata,
    buildSkillScanMetadata,
    scanConfiguredMcps,
    scanConfiguredSkills,
    shouldFailClosed,
  },
};

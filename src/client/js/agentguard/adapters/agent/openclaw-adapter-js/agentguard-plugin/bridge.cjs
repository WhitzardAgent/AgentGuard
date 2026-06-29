"use strict";

const fs = require("node:fs");
const path = require("node:path");

const {
  DEFAULT_OPTIONS: DEFAULT_SKILL_SCAN_OPTIONS,
  resolveScanPath,
  scanSkillRoots,
} = require("./skill_scanner.cjs");
const {
  AuditLogger,
  AuditRecorder,
  ClientConfigAPIServer,
  ClientSyncBuffer,
  DecisionType,
  EventType,
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
    phases: resolvePhaseConfig(config),
    toolCapabilities: normalizeToolCapabilities(config.toolCapabilities),
    identity: normalizeIdentity(config.identity),
    defaultTools: resolveDefaultTools(config, configDir),
    skillScan: normalizeSkillScanConfig(config.skillScan, configDir),
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

function resolveApiKey(config) {
  const direct = asNonEmptyString(config.apiKey);
  if (direct) {
    return direct;
  }
  const envVar = asNonEmptyString(config.apiKeyEnvVar);
  return envVar ? asNonEmptyString(process.env[envVar]) : undefined;
}

function asNonEmptyString(value) {
  return typeof value === "string" && value.trim() ? value.trim() : undefined;
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
  const sessionId =
    asNonEmptyString(identityContext.sessionId) ||
    asNonEmptyString(identityContext.sessionKey) ||
    "unknown";
  const sessionKey = asNonEmptyString(identityContext.sessionKey) || sessionId;
  const derivedUserId =
    config.identity.userId ||
    deriveIdentityValue(config.identity.userIdFrom, identityContext) ||
    null;
  const derivedAgentId =
    config.identity.agentId ||
    deriveIdentityValue(config.identity.agentIdFrom, identityContext) ||
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
    this.skillScan = scanConfiguredSkills(this.config.skillScan, this.logger);
    this.sessions = new Map();
  }

  getSkillScanResult() {
    return this.skillScan;
  }

  getState(identityContext) {
    const context = buildRuntimeContext(this.config, identityContext);
    const sessionKey = context.metadata.client_session_key || context.session_id;
    let state = this.sessions.get(sessionKey);
    if (state) {
      state.context = context;
      this.syncContextMetadata(state);
      state.enforcer.remote.session_id = context.session_id;
      state.enforcer.remote.agent_id = context.agent_id;
      state.enforcer.remote.user_id = context.user_id;
      state.enforcer.remote.session_key = sessionKey;
      return state;
    }

    const remote = new RemoteGuardClient(this.config.serverUrl || null, {
      api_key: this.config.apiKey || null,
      session_id: context.session_id,
      agent_id: context.agent_id,
      user_id: context.user_id,
      session_key: sessionKey,
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
      clientPluginConfig: buildPluginConfigPayload(this.config),
      remotePluginConfig: buildPluginConfigPayload(this.config),
      clientConfigApi: null,
      clientConfigApiStartup: null,
      remoteSessionRegistration: null,
      defaultToolReporting: null,
      recentLlmOutputs: new Map(),
      skillReporting: null,
    };
    this.syncContextMetadata(state);
    this.sessions.set(sessionKey, state);
    this.ensureDefaultToolReports(state);
    this.ensureSkillReports(state);
    return state;
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
    };
    if (state.clientConfigApi) {
      state.context.metadata.client_config_url = state.clientConfigApi.plugin_config_url;
      state.context.metadata.client_plugin_list_url = state.clientConfigApi.plugin_list_url;
      state.context.metadata.client_health_url = state.clientConfigApi.health_url;
    }
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

  ensureRemoteSessionRegistered(state) {
    const remote = state.enforcer.remote;
    if (!remote || !remote.enabled) {
      return Promise.resolve(false);
    }
    if (state.remoteSessionRegistration) {
      return state.remoteSessionRegistration;
    }
    state.remoteSessionRegistration = this.ensureClientConfigApi(state)
      .then(() => {
        this.syncContextMetadata(state);
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

  async flushAsync(state, reason = "round_complete") {
    const remote = state.enforcer.remote;
    const buffer = state.enforcer.sync_buffer;
    if (!remote || !remote.enabled || !buffer || !buffer.has_entries()) {
      return false;
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

  async runBeforeToolCall({ ctx, event }) {
    const state = this.getState({
      agentId: ctx.agentId,
      sessionId: ctx.sessionId,
      sessionKey: ctx.sessionKey,
      runId: ctx.runId || event.runId,
      channelId: ctx.channelId,
    });
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
      outcome: "block",
      reason: decision.reason || "AgentGuard blocked model call.",
      message: buildUserBlockMessage(decision),
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
      sessionId: ctx.sessionKey,
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
    buildUserBlockMessage,
    extractAssistantFinalText,
    formatToolResultContent,
    normalizeOpenClawContent,
    normalizeOpenClawMessage,
    isRemoteUnavailableDecision,
    loadConfigFile,
    loadPluginConfigSource,
    normalizePluginConfig,
    normalizeSkillScanConfig,
    buildSkillScanMetadata,
    scanConfiguredSkills,
    shouldFailClosed,
  },
};

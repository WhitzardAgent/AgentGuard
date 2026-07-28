"use strict";

const Module = require("module");
const { AsyncLocalStorage } = require("async_hooks");
const crypto = require("crypto");
const path = require("path");
const { createRequire } = require("module");

const { AgentGuard } = require("../../../guard");
const { buildClientPluginCatalog } = require("../../../config_api");
const ev = require("../../../schemas/events");
const { RuntimeContext } = require("../../../schemas/context");
const { DecisionType } = require("../../../schemas/decisions");
const { ToolMetadata } = require("../../../tools/metadata");
const { RemoteGuardClient } = require("../../../u_guard/remote_client");
const { DPoPKey, loadOrCreateDPoPKey } = require("../../../u_guard/dpop");
const { stableStringify } = require("../../../utils/hash");
const {
  agentIdentityKeyId,
  buildAgentRegistrationPayload,
  loadOrCreateAgentKey,
} = require("../../../u_guard/agent_keys");

const PATCHED = Symbol.for("agentguard.n8n.patched");
const LOADER_PATCHED = Symbol.for("agentguard.n8n.loader_patched");
const TOOL_INVOKE_PATCHED = Symbol.for("agentguard.n8n.tool_invoke_patched");
const TOOL_SOURCE_CONTEXT = Symbol.for("agentguard.n8n.tool_source_context");
const OPENAI_TEXT_MESSAGE_PATCHED = Symbol.for("agentguard.n8n.openai_text_message_patched");
const ALS = new AsyncLocalStorage();
const GUARDS = new Map();
const REPORTED_TOOLS = new Set();
const CATALOG_FINGERPRINTS = new Map();
const N8N_AGENT_REGISTRATIONS = new Map();
const N8N_RUNTIME_AUTH = new Map();
const WORKFLOW_IDENTITY_CACHE = new Map();
const WORKFLOW_NODE_CACHE = new Map();
const MAX_GUARDS = 128;
let catalogSyncStarted = false;
const AI_TOOL_CONNECTION_TYPE = "ai_tool";
const MAX_LLM_LOOPBACK_ATTEMPTS = 3;
const THOUGHT_ALIGNMENT_PROTOCOL = "thought_alignment_v1";

const PROVIDER_SIDE_BUILT_IN_TOOLS = new Set([
  "web_search",
  "web_search_preview",
  "file_search",
  "code_interpreter",
  "computer_use_preview",
  "image_generation",
]);

const N8N_ENGINE_METADATA_KEYS = new Set([
  "action",
  "sessionId",
  "chatInput",
  "toolCallId",
]);

const THOUGHT_TAG_RE = /<(?<tag>think|thought|reason|reasoning)\b[^>]*>(?<body>[\s\S]*?)<\/\k<tag>>/gi;
const FINAL_TAG_RE = /<(?<tag>answer|final|final_output)\b[^>]*>(?<body>[\s\S]*?)<\/\k<tag>>/gi;

const LOGIC_NODE_TYPES = new Set([
  "n8n-nodes-base.if",
  "n8n-nodes-base.switch",
  "n8n-nodes-base.merge",
  "n8n-nodes-base.filter",
  "n8n-nodes-base.wait",
  "n8n-nodes-base.noOp",
  "n8n-nodes-base.stickyNote",
  "n8n-nodes-base.manualTrigger",
  "n8n-nodes-base.start",
  "n8n-nodes-base.stopAndError",
]);

const AI_TYPE_PATTERNS = [
  /^@n8n\/n8n-nodes-langchain\.chatTrigger$/,
  /^@n8n\/n8n-nodes-langchain\.agent$/,
  /^@n8n\/n8n-nodes-langchain\.agentTool$/,
  /^@n8n\/n8n-nodes-langchain\.openAi$/i,
  /^@n8n\/n8n-nodes-langchain\.ollama$/i,
  /^@n8n\/n8n-nodes-langchain\.lm/i,
  /^@n8n\/n8n-nodes-langchain\.memory/i,
  /^@n8n\/n8n-nodes-langchain\.outputParser/i,
  /^@n8n\/n8n-nodes-langchain\.vectorStore/i,
  /^@n8n\/n8n-nodes-langchain\.embeddings/i,
  /^@n8n\/n8n-nodes-langchain\.retriever/i,
  /^@n8n\/n8n-nodes-langchain\.reranker/i,
  /^@n8n\/n8n-nodes-langchain\.textSplitter/i,
  /^@n8n\/n8n-nodes-langchain\.document/i,
  /^@n8n\/n8n-nodes-langchain\.ToolExecutor$/,
];

const ROOT_LLM_NODE_PATTERNS = [
  /^@n8n\/n8n-nodes-langchain\.openAi$/i,
  /^@n8n\/n8n-nodes-langchain\.ollama$/i,
];

function envFlag(name, defaultValue = false) {
  const raw = process.env[name];
  if (raw == null || raw === "") {
    return defaultValue;
  }
  return !["0", "false", "no", "off"].includes(String(raw).toLowerCase());
}

function csvEnv(name) {
  return new Set(
    String(process.env[name] || "")
      .split(",")
      .map((item) => item.trim())
      .filter(Boolean)
  );
}

function numberEnv(name, defaultValue) {
  const raw = process.env[name];
  if (raw == null || raw === "") {
    return defaultValue;
  }
  const parsed = Number(raw);
  return Number.isFinite(parsed) ? parsed : defaultValue;
}

function pluginConfigFromEnv() {
  const raw = process.env.AGENTGUARD_PLUGIN_CONFIG;
  if (raw == null || raw === "") {
    return null;
  }
  try {
    const parsed = JSON.parse(raw);
    return parsed && typeof parsed === "object" && !Array.isArray(parsed) ? parsed : raw;
  } catch (_) {
    return raw;
  }
}

function clonePluginConfig(config) {
  if (config == null || typeof config !== "object") {
    return config;
  }
  return JSON.parse(JSON.stringify(config));
}

function pluginConfigSignature(config) {
  if (config == null) {
    return "null";
  }
  if (typeof config === "string") {
    return `path:${config}`;
  }
  return `json:${stableStringify(config)}`;
}

function log(level, message, extra = null) {
  const prefix = `[agentguard:n8n] ${message}`;
  if (extra == null) {
    console[level](prefix);
    return;
  }
  console[level](prefix, extra);
}

function safeString(value) {
  if (value == null) {
    return "";
  }
  if (typeof value === "string") {
    return value;
  }
  try {
    return JSON.stringify(value);
  } catch (_) {
    return String(value);
  }
}

function optionalString(value) {
  if (value == null || value === "") {
    return null;
  }
  return String(value);
}

function displayName(firstName, lastName, fallback = null) {
  const name = [firstName, lastName].map((value) => optionalString(value)).filter(Boolean).join(" ").trim();
  return name || optionalString(fallback);
}

function normalizeValue(value, depth = 0) {
  if (depth > 5) {
    return "[MaxDepth]";
  }
  if (value == null || ["boolean", "number", "string"].includes(typeof value)) {
    return value;
  }
  if (typeof Buffer !== "undefined" && Buffer.isBuffer(value)) {
    return value.toString("utf-8");
  }
  if (Array.isArray(value)) {
    return value.map((item) => normalizeValue(item, depth + 1));
  }
  if (value instanceof Map) {
    return Object.fromEntries([...value.entries()].map(([key, item]) => [String(key), normalizeValue(item, depth + 1)]));
  }
  if (value instanceof Set) {
    return [...value].map((item) => normalizeValue(item, depth + 1));
  }
  if (typeof value === "object") {
    if (typeof value.toDict === "function") {
      try {
        return normalizeValue(value.toDict(), depth + 1);
      } catch (_) {
        // Fall through to selected object fields.
      }
    }
    const out = {};
    for (const [key, item] of Object.entries(value)) {
      if (typeof item === "function") {
        continue;
      }
      out[key] = normalizeValue(item, depth + 1);
    }
    return out;
  }
  return String(value);
}

function isPlainObject(value) {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function stableContextId(context) {
  const workflow = context.workflow_id || context.workflow_name || "unknown_workflow";
  const execution = context.n8n_session_id || context.external_session_id || context.execution_id || `pid_${process.pid}`;
  return `n8n:${workflow}:execution:${execution}`;
}

function agentId(context) {
  if (context.agentguard_agent_id) {
    return context.agentguard_agent_id;
  }
  const workflow = context.workflow_id || context.workflow_name || "unknown_workflow";
  return `n8n:${workflow}`;
}

function workflowAgentId(workflowId) {
  return `n8n:${workflowId || "unknown_workflow"}`;
}

function n8nProviderInstanceId() {
  return optionalString(
    process.env.AGENTGUARD_N8N_INSTANCE_ID ||
    process.env.N8N_HOST ||
    process.env.N8N_EDITOR_BASE_URL ||
    process.env.WEBHOOK_URL
  ) || "";
}

function normalizeWorkflowIdentity(workflow = {}) {
  const userId = optionalString(workflow.user_id || workflow.ownerUserId || workflow.owner_user_id || workflow.creatorId);
  const userEmail = optionalString(
    workflow.user_email ||
    workflow.ownerUserEmail ||
    workflow.owner_user_email ||
    workflow.email
  );
  const firstName = optionalString(workflow.ownerUserFirstName || workflow.owner_user_first_name || workflow.firstName);
  const lastName = optionalString(workflow.ownerUserLastName || workflow.owner_user_last_name || workflow.lastName);
  return {
    user_id: userId,
    user_email: userEmail,
    user_name: displayName(firstName, lastName, userEmail),
    user_source: userId ? "workflow_owner" : null,
    project_id: optionalString(workflow.project_id || workflow.projectId),
    project_name: optionalString(workflow.project_name || workflow.projectName),
  };
}

function cacheWorkflowIdentity(workflow = {}) {
  const workflowId = optionalString(workflow.id || workflow.workflowId || workflow.workflow_id);
  if (!workflowId) {
    return {};
  }
  const identity = normalizeWorkflowIdentity(workflow);
  WORKFLOW_IDENTITY_CACHE.set(workflowId, identity);
  return identity;
}

function workflowIdentityForWorkflowId(workflowId) {
  const key = optionalString(workflowId);
  if (!key) {
    return {};
  }
  return WORKFLOW_IDENTITY_CACHE.get(key) || {};
}

function mergeWorkflowIdentity(base = {}, override = {}) {
  const next = { ...(base || {}) };
  for (const [key, value] of Object.entries(override || {})) {
    if (value != null && value !== "") {
      next[key] = value;
    }
  }
  return next;
}

function cacheWorkflowNodes(workflow = {}) {
  const workflowId = optionalString(workflow.id || workflow.workflowId || workflow.workflow_id);
  if (!workflowId || !Array.isArray(workflow.nodes)) {
    return null;
  }
  const byKey = new Map();
  for (const node of workflow.nodes) {
    if (!node || typeof node !== "object") {
      continue;
    }
    for (const key of [
      node.id,
      node.name,
      nodeNameToToolName(node),
    ]) {
      const normalized = optionalString(key);
      if (normalized) {
        byKey.set(normalized, node);
      }
    }
  }
  WORKFLOW_NODE_CACHE.set(workflowId, byKey);
  return byKey;
}

function workflowNodeForTool(workflowId, candidates = []) {
  const workflowKey = optionalString(workflowId);
  if (!workflowKey) {
    return null;
  }
  const byKey = WORKFLOW_NODE_CACHE.get(workflowKey);
  if (!byKey) {
    return null;
  }
  for (const candidate of candidates) {
    const key = optionalString(candidate);
    if (key && byKey.has(key)) {
      return byKey.get(key);
    }
  }
  return null;
}

function n8nSessionIdFromValue(value, depth = 0, seen = null) {
  if (value == null || depth > 6) {
    return null;
  }
  if (typeof value !== "object") {
    return null;
  }
  const visited = seen || new Set();
  if (visited.has(value)) {
    return null;
  }
  visited.add(value);
  if (Array.isArray(value)) {
    for (const item of value) {
      const found = n8nSessionIdFromValue(item, depth + 1, visited);
      if (found) {
        return found;
      }
    }
    return null;
  }
  for (const key of [
    "n8n_session_id",
    "sessionId",
    "session_id",
    "chatSessionId",
    "chat_session_id",
    "conversationId",
    "conversation_id",
  ]) {
    const raw = value[key];
    if (raw != null && raw !== "") {
      return String(raw);
    }
  }
  for (const key of ["json", "metadata", "response_metadata", "additional_kwargs", "input", "data", "body"]) {
    if (Object.prototype.hasOwnProperty.call(value, key)) {
      const found = n8nSessionIdFromValue(value[key], depth + 1, visited);
      if (found) {
        return found;
      }
    }
  }
  for (const item of Object.values(value)) {
    const found = n8nSessionIdFromValue(item, depth + 1, visited);
    if (found) {
      return found;
    }
  }
  return null;
}

function n8nSessionIdFromSources(...sources) {
  for (const source of sources) {
    const found = n8nSessionIdFromValue(source);
    if (found) {
      return found;
    }
  }
  return null;
}

function enrichContextWithN8nSession(context = {}, ...sources) {
  const sessionId = optionalString(
    context.n8n_session_id ||
    context.external_session_id ||
    n8nSessionIdFromSources(...sources)
  );
  if (!sessionId) {
    return context;
  }
  return {
    ...(context || {}),
    n8n_session_id: sessionId,
    external_session_id: sessionId,
  };
}

function guardOptions(context, runtimeAuth = null) {
  return buildGuardOptions(context, runtimeAuth, pluginConfigFromEnv());
}

function buildGuardOptions(context, runtimeAuth = null, pluginConfig = null) {
  return {
    server_url: process.env.AGENTGUARD_SERVER_URL || null,
    api_key: process.env.AGENTGUARD_API_KEY || null,
    policy: process.env.AGENTGUARD_POLICY || null,
    plugin_config: pluginConfig,
    user_id: (runtimeAuth && runtimeAuth.canonical_user_id) || context.user_id || process.env.AGENTGUARD_USER_ID || null,
    agent_id: (runtimeAuth && runtimeAuth.agent_id) || context.agentguard_agent_id || agentId(context),
    environment: "n8n",
    session_token: runtimeAuth && runtimeAuth.session_token,
    dpop_proof_factory: runtimeAuth && runtimeAuth.proof,
    use_dpop_auth: Boolean(runtimeAuth && runtimeAuth.session_token),
    legacy_identity_headers: !(runtimeAuth && runtimeAuth.session_token),
    auto_register_session: !(runtimeAuth && runtimeAuth.session_token),
    auto_close_runtime_session: false,
  };
}

function getGuard(context = {}, runtimeAuth = null, pluginConfig = pluginConfigFromEnv()) {
  const key = stableContextId(context);
  const pluginSignature = pluginConfigSignature(pluginConfig);
  const runtimeSessionId = optionalString(runtimeAuth && runtimeAuth.session_id);
  if (GUARDS.has(key)) {
    const guard = GUARDS.get(key);
    const usesRuntimeAuth = Boolean(guard && guard.remote && guard.remote.use_dpop_auth);
    const guardSessionId = optionalString(guard && guard.__agentguard_n8n_runtime_session_id);
    if (
      (runtimeAuth && runtimeAuth.session_token && (!usesRuntimeAuth || guardSessionId !== runtimeSessionId))
      || (!runtimeAuth && usesRuntimeAuth)
    ) {
      if (guard && typeof guard.close === "function") {
        guard.close().catch(() => {});
      }
      GUARDS.delete(key);
    } else {
      if (guard.__agentguard_n8n_plugin_signature !== pluginSignature) {
        guard.update_plugin_config(pluginConfig, { syncRemote: false });
        guard.__agentguard_n8n_plugin_signature = pluginSignature;
        guard.__agentguard_n8n_plugin_config = clonePluginConfig(pluginConfig);
      }
      guard.__agentguard_n8n_runtime_session_id = runtimeSessionId;
      refreshGuardMetadata(guard, context, runtimeAuth);
      return guard;
    }
  }
  if (GUARDS.size >= MAX_GUARDS) {
    const [oldestKey, oldestGuard] = GUARDS.entries().next().value;
    GUARDS.delete(oldestKey);
    if (oldestGuard && typeof oldestGuard.close === "function") {
      oldestGuard.close().catch(() => {});
    }
  }
  const guard = new AgentGuard((runtimeAuth && runtimeAuth.session_id) || key, buildGuardOptions(context, runtimeAuth, pluginConfig));
  guard.__agentguard_n8n_plugin_signature = pluginSignature;
  guard.__agentguard_n8n_plugin_config = clonePluginConfig(pluginConfig);
  guard.__agentguard_n8n_runtime_session_id = runtimeSessionId;
  refreshGuardMetadata(guard, context, runtimeAuth);
  GUARDS.set(key, guard);
  return guard;
}

async function fetchRuntimePluginConfig(context = {}, runtimeAuth = null) {
  const fallback = pluginConfigFromEnv();
  if (!runtimeAuth || !runtimeAuth.session_token || typeof runtimeAuth.proof !== "function") {
    return clonePluginConfig(fallback);
  }
  if (
    runtimeAuth.runtime_plugin_config_loaded
    && runtimeAuth.runtime_plugin_config_session_id === runtimeAuth.session_id
  ) {
    return clonePluginConfig(runtimeAuth.runtime_plugin_config_effective);
  }
  const remote = new RemoteGuardClient(process.env.AGENTGUARD_SERVER_URL || null, {
    api_key: process.env.AGENTGUARD_API_KEY || null,
    session_id: runtimeAuth.session_id || null,
    agent_id: runtimeAuth.agent_id || null,
    user_id: runtimeAuth.canonical_user_id || null,
    session_token: runtimeAuth.session_token,
    dpop_proof_factory: runtimeAuth.proof,
    use_dpop_auth: true,
    legacy_identity_headers: false,
    timeout_s: numberEnv("AGENTGUARD_N8N_RUNTIME_AUTH_TIMEOUT_S", 5.0),
    retries: numberEnv("AGENTGUARD_N8N_RUNTIME_AUTH_RETRIES", 1),
  });
  try {
    const payload = await remote.fetch_runtime_plugin_config();
    const effective = isPlainObject(payload && payload.plugin_config)
      ? payload.plugin_config
      : fallback;
    runtimeAuth.runtime_plugin_config_loaded = true;
    runtimeAuth.runtime_plugin_config_session_id = runtimeAuth.session_id || null;
    runtimeAuth.runtime_plugin_config_effective = clonePluginConfig(effective);
    return clonePluginConfig(effective);
  } catch (error) {
    log(
      "warn",
      "runtime plugin config fetch failed",
      {
        workflow_id: optionalString(context.workflow_id),
        agent_id: optionalString(runtimeAuth.agent_id),
        session_id: optionalString(runtimeAuth.session_id),
        error: error && error.message ? error.message : String(error),
      },
    );
    return clonePluginConfig(fallback);
  }
}

async function getGuardForRuntime(context = {}) {
  const effectiveContext = enrichContextWithN8nSession(context);
  if (!process.env.AGENTGUARD_SERVER_URL) {
    return getGuard(effectiveContext);
  }
  const runtimeAuth = await ensureN8nRuntimeAuth(effectiveContext);
  if (!runtimeAuth || !runtimeAuth.session_token) {
    const workflowId = optionalString(effectiveContext.workflow_id) || "unknown_workflow";
    throw new Error(
      `AgentGuard n8n runtime auth did not produce a session token for workflow ${workflowId}.`
    );
  }
  const contextWithAuth = enrichContextWithAuth(effectiveContext, runtimeAuth);
  const runtimePluginConfig = await fetchRuntimePluginConfig(contextWithAuth, runtimeAuth);
  return getGuard(contextWithAuth, runtimeAuth, runtimePluginConfig);
}

function refreshGuardMetadata(guard, context = {}, runtimeAuth = null) {
  if (!guard || !guard.context) {
    return;
  }
  guard.context.environment = "n8n";
  const userId = (runtimeAuth && runtimeAuth.canonical_user_id) || context.user_id || process.env.AGENTGUARD_USER_ID || null;
  if (userId && guard.context.user_id !== userId) {
    guard.context.user_id = userId;
    if (guard.remote) {
      guard.remote.user_id = userId;
    }
    if (typeof guard.syncRemoteSession === "function") {
      guard.syncRemoteSession().catch(() => {});
    }
  }
  guard.context.metadata = {
    ...(guard.context.metadata || {}),
    environment: "n8n",
    workflow_id: context.workflow_id || null,
    workflow_name: context.workflow_name || null,
    execution_id: context.execution_id || null,
    n8n_session_id: context.n8n_session_id || null,
    external_session_id: context.external_session_id || null,
    n8n_user_id: context.n8n_user_id || null,
    n8n_user_email: context.n8n_user_email || null,
    n8n_user_name: context.n8n_user_name || null,
    n8n_user_source: context.n8n_user_source || null,
    n8n_project_id: context.n8n_project_id || null,
    n8n_project_name: context.n8n_project_name || null,
    external_provider: context.external_provider || null,
    external_account_email: context.external_account_email || null,
    display_agent_id: context.display_agent_id || null,
    external_agent_id: context.external_agent_id || null,
    agentguard_agent_id: context.agentguard_agent_id || null,
    agent_identity_code: context.agent_identity_code || null,
    agent_public_key_thumbprint: context.agent_public_key_thumbprint || null,
    runtime_auth_fallback: context.runtime_auth_fallback || null,
    runtime_auth_fallback_reason: context.runtime_auth_fallback_reason || null,
  };
}

function currentContext(overrides = {}) {
  return {
    ...(ALS.getStore() || {}),
    ...(overrides || {}),
  };
}

function buildRunNodeContext(workflow, executionData, runExecutionData, runIndex, additionalData, mode) {
  const node = executionData && executionData.node ? executionData.node : {};
  const workflowId = workflow && (workflow.id || workflow.workflowId || workflow.workflow_id);
  const workflowIdentity = mergeWorkflowIdentity(
    workflowIdentityForWorkflowId(workflowId),
    normalizeWorkflowIdentity(workflow)
  );
  const executionId = (
    additionalData && (additionalData.executionId || additionalData.execution_id)
  ) || (
    runExecutionData && runExecutionData.executionData &&
    (runExecutionData.executionData.id || runExecutionData.executionData.executionId)
  ) || (
    runExecutionData && runExecutionData.resultData && runExecutionData.resultData.runId
  );
  const n8nSessionId = n8nSessionIdFromSources(executionData, runExecutionData, additionalData);
  return {
    workflow_id: workflowId == null ? null : String(workflowId),
    workflow_name: workflow && workflow.name ? String(workflow.name) : null,
    execution_id: executionId == null ? null : String(executionId),
    n8n_session_id: n8nSessionId,
    external_session_id: n8nSessionId,
    user_id: workflowIdentity.user_id || process.env.AGENTGUARD_USER_ID || null,
    n8n_user_id: workflowIdentity.user_id || null,
    n8n_user_email: workflowIdentity.user_email || null,
    n8n_user_name: workflowIdentity.user_name || null,
    n8n_user_source: workflowIdentity.user_source || null,
    n8n_project_id: workflowIdentity.project_id || null,
    n8n_project_name: workflowIdentity.project_name || null,
    node_id: node.id || null,
    node_name: node.name || null,
    node_type: node.type || null,
    node_version: node.typeVersion || null,
    run_index: runIndex,
    mode: mode || null,
  };
}

function eventMetadata(context = {}, extra = {}) {
  return {
    adapter: "n8n",
    environment: "n8n",
    workflow_id: context.workflow_id || null,
    workflow_name: context.workflow_name || null,
    execution_id: context.execution_id || null,
    n8n_session_id: context.n8n_session_id || null,
    external_session_id: context.external_session_id || null,
    user_id: context.user_id || null,
    n8n_user_id: context.n8n_user_id || null,
    n8n_user_email: context.n8n_user_email || null,
    n8n_user_name: context.n8n_user_name || null,
    n8n_user_source: context.n8n_user_source || null,
    n8n_project_id: context.n8n_project_id || null,
    n8n_project_name: context.n8n_project_name || null,
    node_id: context.node_id || null,
    node_name: context.node_name || null,
    node_type: context.node_type || null,
    node_version: context.node_version || null,
    caller_node_id: context.caller_node_id || null,
    caller_node_name: context.caller_node_name || null,
    caller_node_type: context.caller_node_type || null,
    source_node_id: context.source_node_id || null,
    source_node_name: context.source_node_name || null,
    source_node_type: context.source_node_type || null,
    run_index: context.run_index ?? null,
    mode: context.mode || null,
    ...(extra || {}),
  };
}

function registrationKeyForWorkflow({ workflow_id = null, tenant_id = null, account_email = null } = {}) {
  return ["n8n", n8nProviderInstanceId(), tenant_id || "", workflow_id || "", account_email || ""].join("\x1f");
}

function n8nAccountEmail(context = {}) {
  const email = optionalString(
    context.external_account_email ||
    context.n8n_user_email ||
    context.user_email
  );
  return email && email.includes("@") ? email.toLowerCase() : null;
}

function n8nRegistrationMetadata(context = {}, extra = {}) {
  const workflowId = optionalString(context.workflow_id);
  const email = n8nAccountEmail(context);
  const metadata = {
    adapter: "n8n",
    environment: "n8n",
    external_provider: "n8n",
    external_agent_id: workflowId,
    display_agent_id: workflowAgentId(workflowId),
    workflow_id: workflowId,
    workflow_name: context.workflow_name || null,
    workflow_version: context.workflow_version || null,
    workflow_active: context.workflow_active ?? null,
    n8n_user_id: context.n8n_user_id || null,
    n8n_user_email: email,
    n8n_user_name: context.n8n_user_name || null,
    n8n_user_source: context.n8n_user_source || null,
    n8n_project_id: context.n8n_project_id || null,
    n8n_project_name: context.n8n_project_name || null,
    provider_instance_id: n8nProviderInstanceId(),
    ...(extra || {}),
  };
  if (email) {
    metadata.external_account_email = email;
  }
  return metadata;
}

function registrationContextFromWorkflow(workflow = {}, tools = []) {
  const version = workflow.activeVersionId || workflow.versionId || workflow.publishedVersionId || workflow.versionCounter || "published";
  const identity = normalizeWorkflowIdentity(workflow);
  return {
    workflow_id: optionalString(workflow.id || workflow.workflowId || workflow.workflow_id),
    workflow_name: optionalString(workflow.name),
    workflow_version: optionalString(version),
    workflow_active: Boolean(workflow.active),
    tool_count: Array.isArray(tools) ? tools.length : 0,
    n8n_user_id: identity.user_id || null,
    n8n_user_email: identity.user_email || null,
    n8n_user_name: identity.user_name || null,
    n8n_user_source: identity.user_source || null,
    n8n_project_id: identity.project_id || null,
    n8n_project_name: identity.project_name || null,
  };
}

async function registerN8nWorkflowAgent(context = {}, { remote = null, tools = null, reason = "runtime" } = {}) {
  const workflowId = optionalString(context.workflow_id);
  if (!workflowId || !process.env.AGENTGUARD_SERVER_URL) {
    return null;
  }
  const accountEmail = n8nAccountEmail(context);
  const key = registrationKeyForWorkflow({ workflow_id: workflowId, account_email: accountEmail });
  if (N8N_AGENT_REGISTRATIONS.has(key)) {
    return N8N_AGENT_REGISTRATIONS.get(key);
  }
  const providerInstanceId = n8nProviderInstanceId();
  const metadata = n8nRegistrationMetadata(context, {
    registration_reason: reason,
    tool_count: Array.isArray(tools) ? tools.length : context.tool_count || null,
  });
  const agentType = "workflow";
  const keyId = agentIdentityKeyId({
    provider: "n8n",
    provider_instance_id: providerInstanceId,
    tenant_id: null,
    external_agent_id: workflowId,
    agent_type: agentType,
  });
  const payload = buildAgentRegistrationPayload({
    provider: "n8n",
    provider_instance_id: providerInstanceId,
    tenant_id: null,
    external_agent_id: workflowId,
    agent_type: agentType,
    name: context.workflow_name || workflowAgentId(workflowId),
    description: context.workflow_name ? `n8n workflow: ${context.workflow_name}` : "n8n workflow",
    account_email: accountEmail,
    metadata,
  });
  payload.client_plugins = buildClientPluginCatalog();
  const client = remote || new RemoteGuardClient(process.env.AGENTGUARD_SERVER_URL || null, {
    api_key: process.env.AGENTGUARD_API_KEY || null,
    timeout_s: numberEnv("AGENTGUARD_N8N_AGENT_REGISTER_TIMEOUT_S", 5.0),
    retries: numberEnv("AGENTGUARD_N8N_AGENT_REGISTER_RETRIES", 1),
  });
  if (!client.enabled) {
    return null;
  }
  const registration = await client.register_agent(payload);
  const result = {
    ...(registration || {}),
    agent_identity_key_id: keyId,
    provider_instance_id: providerInstanceId,
    external_agent_id: workflowId,
    agent_type: agentType,
  };
  N8N_AGENT_REGISTRATIONS.set(key, result);
  return result;
}

function enrichContextWithRegistration(context = {}, registration = null) {
  if (!registration || !registration.agent) {
    return context;
  }
  const agent = registration.agent || {};
  const agentIdValue = optionalString(agent.agent_id);
  if (!agentIdValue) {
    return context;
  }
  const email = n8nAccountEmail(context);
  return {
    ...(context || {}),
    agentguard_agent_id: agentIdValue,
    agent_identity_code: agent.agent_identity_code || null,
    agent_public_key_thumbprint: agent.public_key_thumbprint || null,
    agent_identity_key_id: registration.agent_identity_key_id || null,
    external_provider: "n8n",
    external_account_email: email,
    external_agent_id: optionalString(context.workflow_id),
    display_agent_id: workflowAgentId(context.workflow_id),
  };
}

function enrichContextWithAuth(context = {}, runtimeAuth = null) {
  if (!runtimeAuth) {
    return context;
  }
  if (runtimeAuth.fallback) {
    return {
      ...(context || {}),
      runtime_auth_fallback: true,
      runtime_auth_fallback_reason: runtimeAuth.fallback_reason || "runtime_auth_unavailable",
    };
  }
  return {
    ...(context || {}),
    agentguard_agent_id: runtimeAuth.agent_id || context.agentguard_agent_id,
    user_id: runtimeAuth.canonical_user_id || context.user_id,
    external_provider: "n8n",
    external_account_email: n8nAccountEmail(context),
    n8n_session_id: context.n8n_session_id || runtimeAuth.external_session_id || null,
    external_session_id: context.external_session_id || runtimeAuth.external_session_id || null,
  };
}

async function ensureN8nRuntimeAuth(context = {}) {
  if (!process.env.AGENTGUARD_SERVER_URL) {
    return null;
  }
  await hydrateWorkflowIdentityContext(context);
  const accountEmail = n8nAccountEmail(context);
  if (!accountEmail) {
    const workflowId = optionalString(context.workflow_id) || "unknown_workflow";
    throw new Error(`n8n workflow ${workflowId} is missing owner email for runtime auth`);
  }
  const registration = await registerN8nWorkflowAgent(context, { reason: "runtime" });
  const registeredAgent = registration && registration.agent;
  const canonicalAgentId = registeredAgent && optionalString(registeredAgent.agent_id);
  if (!canonicalAgentId) {
    const workflowId = optionalString(context.workflow_id) || "unknown_workflow";
    throw new Error(`n8n workflow ${workflowId} did not return a registered AgentGuard agent_id`);
  }
  const externalSessionId = optionalString(context.n8n_session_id || context.external_session_id || context.execution_id);
  const cacheKey = [canonicalAgentId, externalSessionId || context.workflow_id || `pid_${process.pid}`].join("\x1f");
  const cached = N8N_RUNTIME_AUTH.get(cacheKey);
  if (cached && cached.session_token && cached.expires_at - Math.floor(Date.now() / 1000) > 60) {
    return cached;
  }
  const dpopKey = cached && cached.dpop_key
    ? cached.dpop_key
    : externalSessionId
      ? loadOrCreateDPoPKey(["n8n", canonicalAgentId, externalSessionId].join("\x1f"))
      : new DPoPKey();
  const state = {
    agent_id: canonicalAgentId,
    dpop_key: dpopKey,
    session_id: cached && cached.session_id,
    session_token: cached && cached.session_token,
    expires_at: cached && cached.expires_at || 0,
    canonical_user_id: cached && cached.canonical_user_id,
    proof: (method, url, accessToken = null) => dpopKey.proof(method, url, accessToken),
  };
  const client = new RemoteGuardClient(process.env.AGENTGUARD_SERVER_URL || null, {
    api_key: process.env.AGENTGUARD_API_KEY || null,
    session_token: state.session_token || null,
    dpop_proof_factory: state.proof,
    use_dpop_auth: true,
    legacy_identity_headers: false,
    timeout_s: numberEnv("AGENTGUARD_N8N_RUNTIME_AUTH_TIMEOUT_S", 5.0),
    retries: numberEnv("AGENTGUARD_N8N_RUNTIME_AUTH_RETRIES", 1),
  });
  const metadata = n8nRegistrationMetadata(context, {
    runtime_auth_provider: "n8n",
    external_session_id: externalSessionId,
    n8n_session_id: context.n8n_session_id || null,
    execution_id: context.execution_id || null,
  });
  const body = {
    provider: "n8n",
    agent_id: canonicalAgentId,
    account_email: accountEmail,
    external_user_id: optionalString(context.n8n_user_id || context.user_id),
    metadata,
  };
  if (externalSessionId) {
    body.external_session_id = externalSessionId;
  }
  const agentKey = loadOrCreateAgentKey(registration.agent_identity_key_id);
  const result = await client.create_runtime_session(body, {
    extra_headers_factory: (method, url, requestBody) => ({
      "X-AgentGuard-Agent-Proof": agentKey.signSessionCreateProof({
        agent_id: canonicalAgentId,
        method,
        url,
        body: requestBody,
        dpop_jkt: dpopKey.thumbprint,
      }),
    }),
  });
  state.session_id = optionalString(result.session_id);
  state.session_token = optionalString(result.session_token);
  state.canonical_user_id = optionalString(result.user_id);
  state.external_session_id = externalSessionId || null;
  state.expires_at = Number(result.expires_at || 0);
  N8N_RUNTIME_AUTH.set(cacheKey, state);
  return state.session_token ? state : null;
}

function shouldUseAdapter() {
  return envFlag("AGENTGUARD_ENABLED", true);
}

function matchesConfiguredFilters(context = {}) {
  const workflowIds = csvEnv("AGENTGUARD_N8N_WORKFLOW_IDS");
  const nodeIds = csvEnv("AGENTGUARD_N8N_NODE_IDS");
  if (workflowIds.size && !workflowIds.has(String(context.workflow_id || ""))) {
    return false;
  }
  if (nodeIds.size && !nodeIds.has(String(context.node_id || "")) && !nodeIds.has(String(context.node_name || ""))) {
    return false;
  }
  return true;
}

function workflowAllowed(workflowId) {
  const workflowIds = csvEnv("AGENTGUARD_N8N_WORKFLOW_IDS");
  return !workflowIds.size || workflowIds.has(String(workflowId || ""));
}

function blockedToolValue(decision, toolName) {
  if (!decision) {
    return null;
  }
  if (decision.decision_type === DecisionType.DENY) {
    return { agentguard: "blocked", tool: toolName, reason: decision.reason };
  }
  if (decision.requires_user || decision.requires_remote) {
    return { agentguard: "pending", tool: toolName, reason: decision.reason, decision: decision.decision_type };
  }
  if (decision.decision_type === DecisionType.DEGRADE) {
    return { agentguard: "degraded", tool: toolName, reason: decision.reason, decision: decision.decision_type };
  }
  return null;
}

function blockedResultValue(decision, toolName) {
  if (!decision) {
    return null;
  }
  if (decision.decision_type === DecisionType.DENY) {
    return { agentguard: "blocked", tool: toolName, reason: decision.reason };
  }
  if (decision.decision_type === DecisionType.SANITIZE) {
    return { agentguard: "sanitized", tool: toolName, reason: decision.reason };
  }
  if (decision.requires_user || decision.requires_remote) {
    return { agentguard: "pending", tool: toolName, reason: decision.reason, decision: decision.decision_type };
  }
  return null;
}

function blockedLLMResponse(decision, request = {}) {
  const reason = decision && decision.reason ? decision.reason : "blocked by AgentGuard";
  if (request && request.stream) {
    return syntheticBlockedStream(reason, request && request.model);
  }
  return syntheticResponsesPayload(reason, request && request.model);
}

function coerceLoopbackPayload(payload) {
  if (typeof payload !== "string") {
    return payload;
  }
  const text = payload.trim();
  if (!text || !["{", "["].includes(text[0])) {
    return payload;
  }
  try {
    return JSON.parse(text);
  } catch (_) {
    return payload;
  }
}

function protocolFromDecision(decision) {
  const protocol = decision && decision.metadata && decision.metadata.protocol;
  return protocol == null ? "" : String(protocol).trim().toLowerCase();
}

function isThoughtAlignmentLoopbackDecision(decision) {
  return Boolean(
    decision &&
    decision.decision_type === DecisionType.LOOP_BACK_TO_LLM &&
    protocolFromDecision(decision) === THOUGHT_ALIGNMENT_PROTOCOL
  );
}

function buildThoughtAlignmentMetadata({ supported = false, retryAttempt = 0 } = {}) {
  const metadata = {};
  if (supported) {
    metadata.thought_regeneration_supported = true;
  }
  if (retryAttempt > 0) {
    metadata.thought_alignment_attempt = retryAttempt;
  }
  return metadata;
}

function supportsThoughtAlignmentForResponsesRequest(_request = {}) {
  return true;
}

function supportsThoughtAlignmentForRunNode(node = null) {
  return providerFromLLMNode(node) === "openai";
}

function denormalizeResponsesInput(payload) {
  if (Array.isArray(payload)) {
    return payload.map((item) => denormalizeResponsesMessage(item));
  }
  if (payload && typeof payload === "object") {
    if (Array.isArray(payload.input)) {
      return denormalizeResponsesInput(payload.input);
    }
    if (Array.isArray(payload.messages)) {
      return denormalizeResponsesInput(payload.messages);
    }
    return [denormalizeResponsesMessage(payload)];
  }
  return [denormalizeResponsesMessage(payload)];
}

function denormalizeResponsesMessage(message) {
  if (message && typeof message === "object" && !Array.isArray(message)) {
    return {
      role: message.role || message.type || "user",
      content: normalizeValue(
        Object.prototype.hasOwnProperty.call(message, "content")
          ? message.content
          : Object.prototype.hasOwnProperty.call(message, "text")
            ? message.text
            : message
      ),
    };
  }
  return {
    role: "user",
    content: typeof message === "string" ? message : safeString(message),
  };
}

function responseMessageTemplateFromOutput(rawOutput = null) {
  const normalized = normalizeValue(rawOutput);
  if (normalized && Array.isArray(normalized.data)) {
    for (let branchIndex = normalized.data.length - 1; branchIndex >= 0; branchIndex -= 1) {
      const branch = normalized.data[branchIndex];
      if (!Array.isArray(branch)) {
        continue;
      }
      for (let itemIndex = branch.length - 1; itemIndex >= 0; itemIndex -= 1) {
        const item = branch[itemIndex];
        if (!item || typeof item !== "object" || !isPlainObject(item.json)) {
          continue;
        }
        if (item.json.response) {
          const nested = responseMessageTemplateFromOutput(item.json.response);
          if (nested) {
            return nested;
          }
        }
        if (item.json.output) {
          const nested = responseMessageTemplateFromOutput({ output: item.json.output });
          if (nested) {
            return nested;
          }
        }
      }
    }
  }
  const outputItems = Array.isArray(normalized && normalized.output)
    ? normalized.output
    : Array.isArray(normalized && normalized.output_items)
      ? normalized.output_items
      : Array.isArray(normalized)
        ? normalized
        : [];
  for (let index = outputItems.length - 1; index >= 0; index -= 1) {
    const item = outputItems[index];
    if (item && typeof item === "object" && item.type === "response.completed" && item.response) {
      const nested = responseMessageTemplateFromOutput(item.response);
      if (nested) {
        return nested;
      }
    }
    if (item && typeof item === "object" && item.type === "response.output_item.done" && item.item) {
      const nested = responseMessageTemplateFromOutput(item.item);
      if (nested) {
        return nested;
      }
    }
    if (item && typeof item === "object" && item.delta && item.delta.message) {
      return normalizeValue(item.delta.message);
    }
    if (!item || typeof item !== "object" || item.type !== "message") {
      continue;
    }
    return normalizeValue(item);
  }
  return null;
}

function replaceResponseMessageContent(message = {}, text = "") {
  const updated = isPlainObject(message) ? normalizeValue(message) : {};
  const nextText = String(text || "").trim();
  if (!nextText) {
    return updated;
  }
  if (Array.isArray(updated.content)) {
    const template = updated.content.find((part) => part && typeof part === "object" && (
      part.type === "output_text" ||
      part.type === "text" ||
      typeof part.text === "string" ||
      typeof part.content === "string"
    ));
    const nextPart = isPlainObject(template) ? { ...template } : { type: "output_text", annotations: [] };
    if (!nextPart.type) {
      nextPart.type = "output_text";
    }
    if (Object.prototype.hasOwnProperty.call(nextPart, "content") && !Object.prototype.hasOwnProperty.call(nextPart, "text")) {
      nextPart.content = nextText;
    } else {
      nextPart.text = nextText;
      if (Object.prototype.hasOwnProperty.call(nextPart, "content")) {
        delete nextPart.content;
      }
    }
    if (nextPart.type === "output_text" && !Array.isArray(nextPart.annotations)) {
      nextPart.annotations = [];
    }
    updated.content = [nextPart];
    return updated;
  }
  if (typeof updated.content === "string" || updated.content == null) {
    updated.content = nextText;
    return updated;
  }
  if (typeof updated.text === "string" || updated.text == null) {
    updated.text = nextText;
    return updated;
  }
  updated.content = [{ type: "output_text", text: nextText, annotations: [] }];
  return updated;
}

function thoughtAlignmentRetryMessageForResponses(rawOutput = null, processedContent = "") {
  const thought = String(processedContent || "").trim();
  if (!thought) {
    return null;
  }
  const template = responseMessageTemplateFromOutput(rawOutput);
  if (template) {
    return replaceResponseMessageContent(template, thought);
  }
  return {
    type: "message",
    role: "assistant",
    status: "completed",
    content: [{ type: "output_text", text: thought, annotations: [] }],
  };
}

function thoughtAlignmentRetryMessageForNodeParameters(rawOutput = null, processedContent = "") {
  const thought = String(processedContent || "").trim();
  if (!thought) {
    return null;
  }
  const template = thoughtAlignmentRetryMessageForResponses(rawOutput, thought);
  if (!template) {
    return null;
  }
  return {
    role: template.role || "assistant",
    content: firstNonEmptyText(template.content, template.text, template.output_text, thought) || thought,
  };
}

function applyLoopbackToResponsesRequest(request = {}, processedContent = "") {
  const payload = coerceLoopbackPayload(processedContent);
  return {
    ...(request || {}),
    input: denormalizeResponsesInput(payload),
  };
}

function applyThoughtAlignmentToResponsesRequest(request = {}, rawOutput = null, processedContent = "") {
  if (typeof rawOutput === "string" && processedContent === "") {
    processedContent = rawOutput;
    rawOutput = null;
  }
  const retryMessage = thoughtAlignmentRetryMessageForResponses(rawOutput, processedContent);
  if (!retryMessage) {
    return { ...(request || {}) };
  }
  const currentInput = request && Object.prototype.hasOwnProperty.call(request, "input") ? request.input : [];
  const messages = Array.isArray(currentInput)
    ? [...currentInput]
    : currentInput == null
      ? []
      : denormalizeResponsesInput(currentInput);
  return {
    ...(request || {}),
    input: [...messages, retryMessage],
  };
}

function applyModifiedResponsesRequest(request = {}, processedContent = "") {
  const payload = coerceLoopbackPayload(processedContent);
  if (payload && typeof payload === "object" && !Array.isArray(payload)) {
    const nextRequest = { ...(request || {}) };
    for (const [key, value] of Object.entries(payload)) {
      if (["input", "messages"].includes(key)) {
        continue;
      }
      nextRequest[key] = normalizeValue(value);
    }
    if (Object.prototype.hasOwnProperty.call(payload, "input") || Object.prototype.hasOwnProperty.call(payload, "messages")) {
      nextRequest.input = denormalizeResponsesInput(
        Object.prototype.hasOwnProperty.call(payload, "input") ? payload.input : payload.messages
      );
      return nextRequest;
    }
  }
  return {
    ...(request || {}),
    input: denormalizeResponsesInput(payload),
  };
}

function applyLoopbackToNodeParameters(parameters = {}, processedContent = "") {
  const payload = coerceLoopbackPayload(processedContent);
  const nextMessages = denormalizeResponsesInput(payload);
  const sourceKey = (
    parameters.responses && Array.isArray(parameters.responses.values)
      ? "responses"
      : parameters.messages && Array.isArray(parameters.messages.values)
        ? "messages"
        : "responses"
  );
  const currentSource = isPlainObject(parameters[sourceKey]) ? parameters[sourceKey] : {};
  const currentValues = Array.isArray(currentSource.values) ? currentSource.values : [];
  return {
    ...parameters,
    [sourceKey]: {
      ...currentSource,
      values: nextMessages.map((message, index) => ({
        ...(isPlainObject(currentValues[index]) ? currentValues[index] : {}),
        role: message.role || "user",
        content: message.content,
      })),
    },
  };
}

function applyThoughtAlignmentToNodeParameters(parameters = {}, rawOutput = null, processedContent = "", fallbackMessages = []) {
  if (typeof rawOutput === "string" && processedContent === "") {
    processedContent = rawOutput;
    rawOutput = null;
  }
  const retryMessage = thoughtAlignmentRetryMessageForNodeParameters(rawOutput, processedContent);
  if (!retryMessage) {
    return { ...(parameters || {}) };
  }
  const sourceKey = (
    parameters.responses && Array.isArray(parameters.responses.values)
      ? "responses"
      : parameters.messages && Array.isArray(parameters.messages.values)
        ? "messages"
        : "responses"
  );
  const currentSource = isPlainObject(parameters[sourceKey]) ? parameters[sourceKey] : {};
  const currentValues = Array.isArray(currentSource.values)
    ? currentSource.values.map((message) => normalizeValue(message))
    : (Array.isArray(fallbackMessages) ? fallbackMessages.map((message) => normalizeValue(message)) : []);
  return {
    ...parameters,
    [sourceKey]: {
      ...currentSource,
      values: [...currentValues, retryMessage],
    },
  };
}

function applyLoopbackToRunNodeArgs(args = {}, processedContent = "") {
  const node = args && args.node;
  const parameters = isPlainObject(node && node.parameters) ? node.parameters : {};
  const nextParameters = applyLoopbackToNodeParameters(parameters, processedContent);
  return {
    ...(args || {}),
    node: {
      ...(node || {}),
      parameters: nextParameters,
    },
    executionData: {
      ...((args && args.executionData) || {}),
      node: {
        ...(((args && args.executionData) || {}).node || {}),
        parameters: nextParameters,
      },
    },
  };
}

function applyThoughtAlignmentToRunNodeArgs(args = {}, rawOutput = null, processedContent = "") {
  if (typeof rawOutput === "string" && processedContent === "") {
    processedContent = rawOutput;
    rawOutput = null;
  }
  const node = args && args.node;
  const parameters = isPlainObject(node && node.parameters) ? node.parameters : {};
  const fallbackMessages = llmRequestFromRunNodeExecution(
    (args && args.executionData) || {},
    node,
    args && args.runExecutionData
  ).input;
  const nextParameters = applyThoughtAlignmentToNodeParameters(parameters, rawOutput, processedContent, fallbackMessages);
  return {
    ...(args || {}),
    node: {
      ...(node || {}),
      parameters: nextParameters,
    },
    executionData: {
      ...((args && args.executionData) || {}),
      node: {
        ...(((args && args.executionData) || {}).node || {}),
        parameters: nextParameters,
      },
    },
  };
}

function applyModifiedRunNodeArgs(args = {}, processedContent = "") {
  const node = args && args.node;
  const parameters = isPlainObject(node && node.parameters) ? node.parameters : {};
  const nextParameters = applyLoopbackToNodeParameters(parameters, processedContent);
  return {
    ...(args || {}),
    node: {
      ...(node || {}),
      parameters: nextParameters,
    },
    executionData: {
      ...((args && args.executionData) || {}),
      node: {
        ...(((args && args.executionData) || {}).node || {}),
        parameters: nextParameters,
      },
    },
  };
}

function isOpenAiTextMessageOperationNode(node = null) {
  if (!node || node.disabled === true) {
    return false;
  }
  const type = String(node.type || "").toLowerCase();
  if (type !== "@n8n/n8n-nodes-langchain.openai") {
    return false;
  }
  const parameters = isPlainObject(node.parameters) ? node.parameters : {};
  return String(parameters.resource || "").toLowerCase() === "text"
    && String(parameters.operation || "").toLowerCase() === "message";
}

function applyLoopbackToOpenAiTextMessages(_messages = [], processedContent = "") {
  return denormalizeResponsesInput(coerceLoopbackPayload(processedContent));
}

function applyModifyToOpenAiTextMessages(messages = [], processedContent = "") {
  const payload = coerceLoopbackPayload(processedContent);
  if (payload && typeof payload === "object" && !Array.isArray(payload)) {
    if (Object.prototype.hasOwnProperty.call(payload, "messages")) {
      return denormalizeResponsesInput(payload.messages);
    }
    if (Object.prototype.hasOwnProperty.call(payload, "input")) {
      return denormalizeResponsesInput(payload.input);
    }
  }
  return applyLoopbackToOpenAiTextMessages(messages, payload);
}

function applyThoughtAlignmentToOpenAiTextMessages(messages = [], rawOutput = null, processedContent = "") {
  if (typeof rawOutput === "string" && processedContent === "") {
    processedContent = rawOutput;
    rawOutput = null;
  }
  const retryMessage = thoughtAlignmentRetryMessageForNodeParameters(rawOutput, processedContent);
  if (!retryMessage) {
    return Array.isArray(messages) ? [...messages] : [];
  }
  const currentMessages = Array.isArray(messages) ? messages.map((message) => normalizeValue(message)) : [];
  return [...currentMessages, retryMessage];
}

function parseOpenAiToolArguments(argumentsText) {
  if (typeof argumentsText !== "string" || !argumentsText.trim()) {
    return argumentsText;
  }
  try {
    return JSON.parse(argumentsText);
  } catch (_) {
    return argumentsText;
  }
}

function openAiTextMessageToolActions(toolCalls = []) {
  if (!Array.isArray(toolCalls) || !toolCalls.length) {
    return [];
  }
  return toolCalls.map((toolCall) => {
    const functionCall = toolCall && typeof toolCall === "object" ? toolCall.function || {} : {};
    const parsedArgs = parseOpenAiToolArguments(functionCall.arguments);
    const input = isPlainObject(parsedArgs) && Object.prototype.hasOwnProperty.call(parsedArgs, "input")
      ? parsedArgs.input
      : parsedArgs;
    return {
      metadata: {
        toolName: firstNonEmptyText(functionCall.name, toolCall && toolCall.name, "tool"),
      },
      input: normalizeValue(input),
    };
  });
}

function openAiTextMessageOutput(response = null) {
  const choice = response && Array.isArray(response.choices) ? response.choices[0] : null;
  const message = choice && choice.message ? normalizeValue(choice.message) : {};
  const toolActions = openAiTextMessageToolActions(message && message.tool_calls);
  const content = firstNonEmptyText(message && message.content, choice && choice.text);
  return {
    output: content || semanticToolActionsOutput(toolActions) || safeString(message),
    thought: extractThoughtText(message),
    final_output: content || null,
    tool_calls: message && message.tool_calls ? normalizeValue(message.tool_calls) : [],
  };
}

function syntheticOpenAiTextMessageResponse(text = "", model = null) {
  const content = String(text || "");
  return {
    id: `agentguard_chatcmpl_${Date.now()}`,
    object: "chat.completion",
    created: Math.floor(Date.now() / 1000),
    model: model || "unknown",
    choices: [
      {
        index: 0,
        finish_reason: "stop",
        message: {
          role: "assistant",
          content,
          tool_calls: [],
        },
      },
    ],
  };
}

function applyModifyToOpenAiTextMessageResponse(response, processedContent = "") {
  const value = textFromValue(extractModifiedPrimaryValue(processedContent));
  if (value == null) {
    return response;
  }
  const baseResponse = response && typeof response === "object" ? { ...response } : syntheticOpenAiTextMessageResponse("", null);
  const choices = Array.isArray(baseResponse.choices) ? [...baseResponse.choices] : [];
  const firstChoice = choices[0] && typeof choices[0] === "object" ? { ...choices[0] } : { index: 0 };
  const currentMessage = firstChoice.message && typeof firstChoice.message === "object" ? { ...firstChoice.message } : {};
  currentMessage.role = currentMessage.role || "assistant";
  currentMessage.content = value;
  currentMessage.tool_calls = [];
  firstChoice.message = currentMessage;
  firstChoice.finish_reason = firstChoice.finish_reason || "stop";
  choices[0] = firstChoice;
  baseResponse.choices = choices;
  return baseResponse;
}

function openAiTextMessageReturnData(response, simplify, itemIndex) {
  const finalResponse = normalizeValue(response || syntheticOpenAiTextMessageResponse("", null));
  if (simplify) {
    const returnData = [];
    const choices = Array.isArray(finalResponse.choices) ? finalResponse.choices : [];
    for (const entry of choices) {
      const nextEntry = normalizeValue(entry);
      if (nextEntry && nextEntry.message && typeof nextEntry.message.content === "string") {
        try {
          nextEntry.message.content = JSON.parse(nextEntry.message.content);
        } catch (_) {
          // Keep non-JSON content as-is.
        }
      }
      returnData.push({
        json: nextEntry,
        pairedItem: { item: itemIndex },
      });
    }
    return returnData;
  }
  return [
    {
      json: finalResponse,
      pairedItem: { item: itemIndex },
    },
  ];
}

function responseOutputItemsFromText(text) {
  return [
    {
      id: `agentguard_modified_message_${Date.now()}`,
      type: "message",
      status: "completed",
      role: "assistant",
      content: [
        {
          type: "output_text",
          text,
          annotations: [],
        },
      ],
    },
  ];
}

function denormalizeResponsesOutput(payload, original = null) {
  const normalizedPayload = coerceLoopbackPayload(payload);
  const updated = isPlainObject(original) ? { ...original } : {};
  if (normalizedPayload && typeof normalizedPayload === "object" && !Array.isArray(normalizedPayload)) {
    const outputItems = Array.isArray(normalizedPayload.output) ? normalizeValue(normalizedPayload.output) : null;
    Object.assign(updated, normalizeValue(normalizedPayload));
    if (outputItems) {
      updated.output = outputItems;
      updated.output_text = firstNonEmptyText(
        normalizedPayload.output_text,
        normalizedPayload.final_output,
        extractVisibleOutputText({ output: outputItems })
      ) || "";
    } else {
      const text = firstNonEmptyText(
        normalizedPayload.output_text,
        normalizedPayload.final_output,
        normalizedPayload.output,
        normalizedPayload.text,
        normalizedPayload.content,
        normalizedPayload.message
      ) || safeString(normalizedPayload);
      updated.output_text = text;
      updated.output = responseOutputItemsFromText(text);
    }
  } else {
    const text = normalizedPayload == null ? "" : String(normalizedPayload);
    updated.output_text = text;
    updated.output = responseOutputItemsFromText(text);
  }
  if (!updated.id) {
    updated.id = `agentguard_modified_${Date.now()}`;
  }
  if (!updated.object) {
    updated.object = "response";
  }
  if (updated.created_at == null) {
    updated.created_at = Math.floor(Date.now() / 1000);
  }
  if (!updated.status) {
    updated.status = "completed";
  }
  return updated;
}

function denormalizeToolPayload(payload) {
  const value = coerceLoopbackPayload(payload);
  if (value == null) {
    return {};
  }
  if (isPlainObject(value)) {
    return normalizeValue(value);
  }
  if (Array.isArray(value)) {
    return normalizeValue(value);
  }
  return normalizeValue(value);
}

function applyModifiedConnectedToolInput(input = {}, processedContent = "") {
  const payload = denormalizeToolPayload(processedContent);
  if (!isPlainObject(input)) {
    return payload;
  }
  if (isPlainObject(input.args)) {
    return {
      ...(input || {}),
      args: isPlainObject(payload) ? payload : { value: payload },
    };
  }
  return isPlainObject(payload)
    ? {
      ...(input || {}),
      ...payload,
    }
    : payload;
}

function setConnectionDataJson(data = {}, jsonValue, connectionType = "main") {
  const nextData = isPlainObject(data) ? { ...data } : {};
  const existingBranches = Array.isArray(nextData[connectionType]) ? nextData[connectionType].map((branch) => (
    Array.isArray(branch) ? [...branch] : []
  )) : [[]];
  const branches = existingBranches.length ? existingBranches : [[]];
  const firstBranch = Array.isArray(branches[0]) ? [...branches[0]] : [];
  const firstItem = isPlainObject(firstBranch[0]) ? { ...firstBranch[0] } : {};
  firstItem.json = normalizeValue(jsonValue);
  firstBranch[0] = firstItem;
  branches[0] = firstBranch;
  nextData[connectionType] = branches;
  return nextData;
}

function applyModifiedActionInput(action = {}, processedContent = "") {
  const originalInput = isPlainObject(action && action.input) ? action.input : {};
  const payload = denormalizeToolPayload(processedContent);
  return {
    ...(action || {}),
    input: isPlainObject(payload)
      ? {
        ...originalInput,
        ...payload,
      }
      : payload,
  };
}

function applyModifiedAiToolExecutionData(executionData = {}, processedContent = "") {
  const data = isPlainObject(executionData && executionData.data) ? executionData.data : {};
  const currentInput = firstJsonFromConnectionData(data, [AI_TOOL_CONNECTION_TYPE, "main"]) || {};
  const updatedInput = applyModifiedConnectedToolInput(currentInput, processedContent);
  const connectionType = Array.isArray(data[AI_TOOL_CONNECTION_TYPE]) ? AI_TOOL_CONNECTION_TYPE : "main";
  return {
    ...(executionData || {}),
    data: setConnectionDataJson(data, updatedInput, connectionType),
  };
}

function applyModifiedOrdinaryExecutionData(executionData = {}, processedContent = "") {
  const payload = coerceLoopbackPayload(processedContent);
  return {
    ...(executionData || {}),
    data: isPlainObject(payload) ? normalizeValue(payload) : setConnectionDataJson(executionData && executionData.data, payload),
  };
}

function applyModifiedToolResult(processedContent = "") {
  return coerceLoopbackPayload(processedContent);
}

function applyModifiedExecuteEngineActionResult(result, action, processedContent = "") {
  const payload = coerceLoopbackPayload(processedContent);
  if (result && result.data && result.data.data) {
    return {
      ...result,
      data: {
        ...result.data,
        data: normalizeValue(payload),
      },
    };
  }
  return aiToolResult(action, normalizeValue(payload));
}

function applyModifiedRunNodeResult(result, processedContent = "") {
  const payload = coerceLoopbackPayload(processedContent);
  if (result && typeof result === "object" && Object.prototype.hasOwnProperty.call(result, "data")) {
    return {
      ...result,
      data: normalizeValue(payload),
    };
  }
  return ordinaryNodeResult(normalizeValue(payload));
}

function applyModifiedLLMRunNodeResult(result, processedContent = "") {
  const originalResponse = firstJsonFromConnectionData(result && result.data, ["main"]);
  const response = denormalizeResponsesOutput(
    processedContent,
    originalResponse && originalResponse.response ? originalResponse.response : null
  );
  const rebuilt = n8nLLMRunNodeResult(response);
  return result && typeof result === "object"
    ? {
      ...result,
      data: rebuilt.data,
    }
    : rebuilt;
}

function applyModifyToResponsesRequest(request = {}, processedContent = "") {
  const payload = coerceLoopbackPayload(processedContent);
  const next = {
    ...(request || {}),
  };
  if (isPlainObject(payload)) {
    for (const [key, value] of Object.entries(payload)) {
      if (key === "input" || key === "messages") {
        continue;
      }
      next[key] = normalizeValue(value);
    }
  }
  next.input = denormalizeResponsesInput(payload);
  return next;
}

function applyModifyToNodeParameters(parameters = {}, processedContent = "") {
  const payload = coerceLoopbackPayload(processedContent);
  const next = {
    ...(parameters || {}),
  };
  if (isPlainObject(payload)) {
    for (const [key, value] of Object.entries(payload)) {
      if (key === "input" || key === "messages") {
        continue;
      }
      next[key] = normalizeValue(value);
    }
  }
  return applyLoopbackToNodeParameters(next, payload);
}

function applyModifyToRunNodeArgs(args = {}, processedContent = "") {
  const node = args && args.node;
  const parameters = isPlainObject(node && node.parameters) ? node.parameters : {};
  return {
    ...(args || {}),
    node: {
      ...(node || {}),
      parameters: applyModifyToNodeParameters(parameters, processedContent),
    },
  };
}

function applyModifyToToolInput(input = {}, processedContent = "") {
  const payload = coerceLoopbackPayload(processedContent);
  if (isPlainObject(payload)) {
    return isPlainObject(input)
      ? normalizeValue({
        ...(input || {}),
        ...payload,
      })
      : normalizeValue(payload);
  }
  return isPlainObject(input)
    ? {
      ...(input || {}),
      input: normalizeValue(payload),
    }
    : { input: normalizeValue(payload) };
}

function extractModifiedPrimaryValue(payload) {
  const data = coerceLoopbackPayload(payload);
  if (isPlainObject(data)) {
    for (const key of ["result", "output", "final_output", "content", "text", "message", "value"]) {
      if (Object.prototype.hasOwnProperty.call(data, key)) {
        return normalizeValue(data[key]);
      }
    }
  }
  return normalizeValue(data);
}

function replaceResponseOutputItems(output = [], text) {
  if (!Array.isArray(output)) {
    return output;
  }
  let replaced = false;
  const next = output.map((item) => {
    if (replaced || !item || typeof item !== "object" || item.type !== "message") {
      return item;
    }
    replaced = true;
    const currentContent = Array.isArray(item.content) ? item.content : [];
    const content = currentContent.length
      ? currentContent.map((part, index) => {
        if (index > 0 || !part || typeof part !== "object") {
          return part;
        }
        if (part.type === "output_text" || part.type === "text") {
          return { ...part, text };
        }
        return { ...part, content: text };
      })
      : [{ type: "output_text", text, annotations: [] }];
    return { ...item, content };
  });
  if (replaced) {
    return next;
  }
  return [
    ...next,
    {
      id: "agentguard_modified_message",
      type: "message",
      status: "completed",
      role: "assistant",
      content: [{ type: "output_text", text, annotations: [] }],
    },
  ];
}

function applyModifyToResponsesResult(result, processedContent = "") {
  const payload = coerceLoopbackPayload(processedContent);
  if (!result || typeof result !== "object" || Array.isArray(result)) {
    return extractModifiedPrimaryValue(payload);
  }
  const next = {
    ...result,
  };
  if (isPlainObject(payload)) {
    Object.assign(next, normalizeValue(payload));
  }
  const text = textFromValue(extractModifiedPrimaryValue(payload));
  if (text != null) {
    next.output_text = text;
    next.output = replaceResponseOutputItems(Array.isArray(result.output) ? result.output : [], text);
  }
  return next;
}

function modifiedResponsesStreamText(processedContent = "", fallbackOutput = null) {
  const primary = textFromValue(extractModifiedPrimaryValue(processedContent));
  if (primary != null && primary !== "") {
    return primary;
  }
  return extractVisibleOutputText(fallbackOutput) || "";
}

function replayResponsesStream(chunks = []) {
  return (async function* replay() {
    for (const chunk of chunks) {
      yield chunk;
    }
  })();
}

function llmStreamOutputPayload(chunks = [], status = "stream_completed") {
  return {
    output_text: "",
    output: chunks,
    status,
  };
}

async function finalizeResponsesStream(iterable, request, context, thoughtAlignmentAttempt = 0) {
  const replayChunks = [];
  const normalizedChunks = [];
  try {
    for await (const item of iterable) {
      replayChunks.push(item);
      normalizedChunks.push(normalizeValue(item));
    }
  } catch (error) {
    const message = safeString(error && error.message ? error.message : error);
    await guardLLMAfter(
      llmStreamOutputPayload(normalizedChunks, "stream_error"),
      context,
      llmGuardMetadata(
        {
          stream: true,
          error: message,
        },
        {
          supported: supportsThoughtAlignmentForResponsesRequest(request),
          retryAttempt: thoughtAlignmentAttempt,
        }
      )
    );
    throw error;
  }
  const rawOutput = llmStreamOutputPayload(normalizedChunks, "stream_completed");
  const decision = await guardLLMAfter(
    rawOutput,
    context,
    llmGuardMetadata(
      {
        stream: true,
      },
      {
        supported: supportsThoughtAlignmentForResponsesRequest(request),
        retryAttempt: thoughtAlignmentAttempt,
      }
    )
  );
  if (decision.decision_type === DecisionType.MODIFY_LLM_OUTPUT) {
    return {
      decision,
      rawOutput,
      stream: syntheticResponsesStream(
        modifiedResponsesStreamText(decision.processed_content, rawOutput),
        request && request.model
      ),
    };
  }
  const blocked = blockedResultValue(decision, "llm");
  if (blocked) {
    return {
      decision,
      rawOutput,
      stream: syntheticBlockedStream(blocked.reason || decision.reason, request && request.model),
    };
  }
  return {
    decision,
    rawOutput,
    stream: replayResponsesStream(replayChunks),
  };
}

function replaceFirstJsonValue(data = {}, value, connectionTypes = ["main"]) {
  const next = isPlainObject(data) ? { ...data } : {};
  let replaced = false;
  for (const connectionType of connectionTypes) {
    const branches = Array.isArray(next[connectionType]) ? next[connectionType] : [];
    next[connectionType] = branches.map((branch) => {
      if (replaced || !Array.isArray(branch)) {
        return branch;
      }
      return branch.map((item) => {
        if (replaced || !item || typeof item !== "object") {
          return item;
        }
        replaced = true;
        return { ...item, json: normalizeValue(value) };
      });
    });
    if (replaced) {
      break;
    }
  }
  if (!replaced) {
    const connectionType = connectionTypes[0] || "main";
    next[connectionType] = [[{ json: normalizeValue(value) }]];
  }
  return next;
}

function applyModifyToRunNodeResult(result, processedContent = "") {
  const value = extractModifiedPrimaryValue(processedContent);
  if (!result || typeof result !== "object" || !Array.isArray(result.data)) {
    return value;
  }
  let replaced = false;
  const data = result.data.map((branch) => {
    if (!Array.isArray(branch)) {
      return branch;
    }
    return branch.map((item) => {
      if (replaced || !item || typeof item !== "object") {
        return item;
      }
      replaced = true;
      if (item.json && typeof item.json === "object" && !Array.isArray(item.json) && Object.prototype.hasOwnProperty.call(item.json, "output")) {
        return { ...item, json: { ...item.json, output: value } };
      }
      return { ...item, json: normalizeValue(value) };
    });
  });
  return {
    ...(result || {}),
    data,
  };
}

function applyModifyToEngineActionResult(result, processedContent = "") {
  const value = extractModifiedPrimaryValue(processedContent);
  if (!result || typeof result !== "object") {
    return value;
  }
  if (result.data && result.data.data && typeof result.data.data === "object") {
    return {
      ...result,
      data: {
        ...result.data,
        data: replaceFirstJsonValue(result.data.data, { output: value }, ["ai_tool", "main"]),
      },
    };
  }
  return result;
}

function applyModifyToExecutionData(executionData = {}, processedContent = "", connectionTypes = ["main"]) {
  const payload = coerceLoopbackPayload(processedContent);
  return {
    ...(executionData || {}),
    data: replaceFirstJsonValue(
      executionData && executionData.data ? executionData.data : {},
      applyModifyToToolInput(payload),
      connectionTypes,
    ),
  };
}

function syntheticResponsesPayloadFromText(text, model = null) {
  const content = String(text || "");
  return {
    id: `agentguard_response_${Date.now()}`,
    object: "response",
    created_at: Math.floor(Date.now() / 1000),
    model: model || "agentguard-blocked",
    status: "completed",
    output_text: content,
    output: [
      {
        id: "agentguard_response_message",
        type: "message",
        status: "completed",
        role: "assistant",
        content: [
          {
            type: "output_text",
            text: content,
            annotations: [],
          },
        ],
      },
    ],
    usage: {
      input_tokens: 0,
      output_tokens: 0,
      total_tokens: 0,
    },
  };
}

async function* syntheticResponsesStream(text, model = null) {
  const content = String(text || "");
  yield {
    type: "response.output_text.delta",
    delta: content,
  };
  yield {
    type: "response.completed",
    response: syntheticResponsesPayloadFromText(content, model),
  };
}

function syntheticResponsesPayload(reason, model = null) {
  const text = `[AgentGuard blocked] ${reason}`;
  return syntheticResponsesPayloadFromText(text, model);
}

async function* syntheticBlockedStream(reason, model = null) {
  yield* syntheticResponsesStream(`[AgentGuard blocked] ${reason}`, model);
}

function extractProviderBuiltInTools(tools = []) {
  if (!Array.isArray(tools)) {
    return [];
  }
  return tools
    .map((tool) => normalizeProviderBuiltInToolType(tool && tool.type))
    .filter((type) => type && PROVIDER_SIDE_BUILT_IN_TOOLS.has(String(type)));
}

function normalizeProviderBuiltInToolType(type) {
  if (!type) {
    return null;
  }
  const raw = String(type);
  const aliases = {
    webSearch: "web_search",
    webSearchPreview: "web_search_preview",
    fileSearch: "file_search",
    codeInterpreter: "code_interpreter",
    computerUsePreview: "computer_use_preview",
    imageGeneration: "image_generation",
  };
  return aliases[raw] || raw;
}

function normalizeResponsesInput(input) {
  if (Array.isArray(input)) {
    return input.map((item) => {
      if (item && typeof item === "object") {
        return {
          role: item.role || item.type || "user",
          content: normalizeValue(item.content ?? item.text ?? item),
        };
      }
      return { role: "user", content: safeString(item) };
    });
  }
  if (input && typeof input === "object") {
    return [{ role: input.role || input.type || "user", content: normalizeValue(input.content ?? input.text ?? input) }];
  }
  return [{ role: "user", content: safeString(input) }];
}

function normalizeLLMOutput(data) {
  if (data == null) {
    return null;
  }
  const normalized = normalizeValue(data);
  const output = firstNonEmptyText(
    extractVisibleOutputText(normalized),
    normalized && normalized.output,
    normalized && normalized.text,
    normalized && normalized.content,
    normalized && normalized.message
  ) || "";
  let thought = extractThoughtText(normalized);
  let finalOutput = output;
  if (thought == null) {
    const parsed = parseTaggedLLMOutput(output);
    thought = parsed.thought;
    finalOutput = parsed.final_output;
  }
  return {
    output,
    thought,
    final_output: finalOutput,
  };
}

function firstNonEmptyText(...values) {
  for (const value of values) {
    const text = textFromValue(value);
    if (text != null && text !== "") {
      return text;
    }
  }
  return null;
}

function textFromValue(value) {
  if (value == null) {
    return null;
  }
  if (typeof value === "string") {
    return value;
  }
  if (typeof value === "number" || typeof value === "boolean") {
    return String(value);
  }
  if (Array.isArray(value)) {
    const parts = value.map((item) => textFromValue(item)).filter((item) => item != null && item !== "");
    return parts.length ? parts.join("") : null;
  }
  if (typeof value === "object") {
    return firstNonEmptyText(
      value.text,
      value.output_text,
      value.content,
      value.message,
      value.refusal,
      value.delta
    );
  }
  return String(value);
}

function extractVisibleOutputText(value) {
  if (!value || typeof value !== "object") {
    return textFromValue(value);
  }
  if (typeof value.output_text === "string" && value.output_text) {
    return value.output_text;
  }
  if (Array.isArray(value.output)) {
    const parts = [];
    for (const item of value.output) {
      if (!item || typeof item !== "object") {
        continue;
      }
      if (item.type === "message" && Array.isArray(item.content)) {
        for (const part of item.content) {
          const text = textFromMessagePart(part);
          if (text) {
            parts.push(text);
          }
        }
      } else if (item.type === "message") {
        const text = firstNonEmptyText(item.text, item.content, item.output_text);
        if (text) {
          parts.push(text);
        }
      }
    }
    if (parts.length) {
      return parts.join("");
    }
    const streamText = extractVisibleTextFromStreamEvents(value.output);
    if (streamText) {
      return streamText;
    }
  }
  if (Array.isArray(value.output_items)) {
    return extractVisibleOutputText({ output: value.output_items });
  }
  if (Array.isArray(value.output) && !value.output.length) {
    return null;
  }
  if (Array.isArray(value)) {
    return extractVisibleTextFromStreamEvents(value);
  }
  return firstNonEmptyText(value.text, value.content, value.message);
}

function textFromMessagePart(part) {
  if (!part || typeof part !== "object") {
    return textFromValue(part);
  }
  if (part.type === "output_text" || part.type === "text") {
    return firstNonEmptyText(part.text, part.output_text, part.content);
  }
  if (part.type === "refusal") {
    return firstNonEmptyText(part.refusal, part.text);
  }
  return null;
}

function extractVisibleTextFromStreamEvents(events) {
  const parts = [];
  for (const event of events) {
    if (!event || typeof event !== "object") {
      continue;
    }
    if (event.type === "response.output_text.delta" && event.delta) {
      parts.push(String(event.delta));
    }
    if (event.type === "response.completed" && event.response) {
      const text = extractVisibleOutputText(event.response);
      if (text) {
        return text;
      }
    }
  }
  return parts.length ? parts.join("") : null;
}

function extractThoughtText(value) {
  if (!value || typeof value !== "object") {
    return null;
  }
  const direct = firstNonEmptyText(
    value.thought,
    value.reasoning_content,
    value.reasoningContent,
    value.thinking,
    value.reasoning && reasoningText(value.reasoning)
  );
  if (direct) {
    return direct;
  }
  for (const key of ["additional_kwargs", "response_metadata", "metadata"]) {
    if (value[key] && typeof value[key] === "object") {
      const nested = extractThoughtText(value[key]);
      if (nested) {
        return nested;
      }
    }
  }
  if (Array.isArray(value.output)) {
    const parts = [];
    for (const item of value.output) {
      const text = thoughtTextFromOutputItem(item);
      if (text) {
        parts.push(text);
      }
    }
    if (parts.length) {
      return parts.join("\n\n");
    }
    const streamThought = extractThoughtTextFromStreamEvents(value.output);
    if (streamThought) {
      return streamThought;
    }
  }
  if (Array.isArray(value)) {
    return extractThoughtTextFromStreamEvents(value);
  }
  return null;
}

function thoughtTextFromOutputItem(item) {
  if (!item || typeof item !== "object") {
    return null;
  }
  if (item.type === "reasoning" || item.type === "thinking") {
    return reasoningText(item);
  }
  if (Array.isArray(item.content)) {
    const parts = [];
    for (const block of item.content) {
      if (!block || typeof block !== "object") {
        continue;
      }
      const blockType = String(block.type || "").toLowerCase();
      if (["reasoning", "thinking", "thinkingblock", "reasoningblock"].includes(blockType)) {
        const text = firstNonEmptyText(block.text, block.content, block.thinking, block.reasoning);
        if (text) {
          parts.push(text);
        }
      }
    }
    return parts.length ? parts.join("\n\n") : null;
  }
  return null;
}

function reasoningText(reasoning) {
  if (!reasoning) {
    return null;
  }
  if (typeof reasoning === "string") {
    return reasoning;
  }
  if (Array.isArray(reasoning)) {
    const parts = reasoning.map((item) => reasoningText(item)).filter(Boolean);
    return parts.length ? parts.join("\n\n") : null;
  }
  if (typeof reasoning === "object") {
    if (Array.isArray(reasoning.summary)) {
      const parts = reasoning.summary.map((item) => firstNonEmptyText(item && item.text, item && item.content, item)).filter(Boolean);
      if (parts.length) {
        return parts.join("\n\n");
      }
    }
    return firstNonEmptyText(reasoning.text, reasoning.content, reasoning.reasoning, reasoning.thinking, reasoning.summary_text);
  }
  return String(reasoning);
}

function extractThoughtTextFromStreamEvents(events) {
  const parts = [];
  for (const event of events) {
    if (!event || typeof event !== "object") {
      continue;
    }
    if (
      ["response.reasoning_summary_text.delta", "response.reasoning_text.delta"].includes(event.type) &&
      event.delta
    ) {
      parts.push(String(event.delta));
    }
    if (event.type === "response.output_item.done") {
      const text = thoughtTextFromOutputItem(event.item);
      if (text) {
        parts.push(text);
      }
    }
    if (event.type === "response.completed" && event.response) {
      const text = extractThoughtText(event.response);
      if (text) {
        return text;
      }
    }
  }
  return parts.length ? parts.join("") : null;
}

function parseTaggedLLMOutput(output) {
  const text = output == null ? "" : String(output);
  const thoughtParts = [];
  for (const match of text.matchAll(THOUGHT_TAG_RE)) {
    const body = match.groups && match.groups.body ? match.groups.body.trim() : "";
    if (body) {
      thoughtParts.push(body);
    }
  }
  if (!thoughtParts.length) {
    return { thought: null, final_output: text };
  }
  const thought = thoughtParts.join("\n\n") || null;
  const remainder = text.replace(THOUGHT_TAG_RE, "").trim();
  const finalParts = [];
  for (const match of remainder.matchAll(FINAL_TAG_RE)) {
    const body = match.groups && match.groups.body ? match.groups.body.trim() : "";
    if (body) {
      finalParts.push(body);
    }
  }
  return {
    thought,
    final_output: finalParts.length ? finalParts.join("\n\n") : remainder,
  };
}

async function guardLLMBefore(request, context, extra = {}) {
  const effectiveContext = enrichContextWithN8nSession(context, request, extra);
  const guard = await getGuardForRuntime(effectiveContext);
  const builtIns = extractProviderBuiltInTools(request && request.tools);
  const metadata = eventMetadata(effectiveContext, {
    event_source: "langchain_openai_responses",
    model: request && request.model ? String(request.model) : null,
    stream: Boolean(request && request.stream),
    model_builtin_tools: builtIns,
    model_builtin_tools_hooked: false,
    model_builtin_tools_reason: builtIns.length ? "provider_side_execution" : null,
    ...(extra || {}),
  });
  const result = await guard.runtime.guard(ev.llm_input(guard.context, normalizeResponsesInput(request && request.input), metadata));
  return result.decision;
}

async function guardLLMAfter(output, context, extra = {}) {
  const effectiveContext = enrichContextWithN8nSession(context, output, extra);
  const guard = await getGuardForRuntime(effectiveContext);
  const metadata = eventMetadata(effectiveContext, {
    event_source: "langchain_openai_responses",
    ...(extra || {}),
  });
  const result = await guard.runtime.guard(ev.llm_output(guard.context, normalizeLLMOutput(output), metadata), {
    phase: "after",
  });
  return result.decision;
}

function llmGuardMetadata(extra = {}, thoughtAlignment = {}) {
  return {
    ...(extra || {}),
    ...buildThoughtAlignmentMetadata(thoughtAlignment),
  };
}

function semanticEventMetadata(context = {}, extra = {}) {
  return eventMetadata(context, {
    event_source: "n8n_tools_agent_v3",
    ...(extra || {}),
  });
}

async function emitSemanticLLMInput(messages, context, extra = {}) {
  const normalizedMessages = Array.isArray(messages) ? normalizeValue(messages) : [];
  if (!normalizedMessages.length) {
    return null;
  }
  const effectiveContext = enrichContextWithN8nSession(context, normalizedMessages, extra);
  const guard = await getGuardForRuntime(effectiveContext);
  const metadata = semanticEventMetadata(effectiveContext, extra);
  return guard.runtime.guard(ev.llm_input(guard.context, normalizedMessages, metadata));
}

async function emitSemanticLLMOutput(output, context, extra = {}) {
  const effectiveContext = enrichContextWithN8nSession(context, output, extra);
  const guard = await getGuardForRuntime(effectiveContext);
  const metadata = semanticEventMetadata(effectiveContext, extra);
  return guard.runtime.guard(ev.llm_output(guard.context, normalizeValue(output), metadata), {
    phase: "after",
  });
}

async function guardToolBefore(toolName, args, context, capabilities = [], extraMetadata = {}) {
  const effectiveContext = enrichContextWithN8nSession(context, args, extraMetadata);
  const guard = await getGuardForRuntime(effectiveContext);
  reportToolOnce(guard, toolName, {
    description: context.tool_description || "",
    capabilities,
    metadata: {
      boundary: "external",
      tags: capabilities,
    },
  });
  const result = await guard.runtime.guard(
    ev.tool_invoke(guard.context, toolName, normalizeValue(args), {
      capabilities,
      metadata: eventMetadata(effectiveContext, extraMetadata),
    })
  );
  return result.decision;
}

async function guardToolAfter(toolName, output, context, { error = null, metadata = {} } = {}) {
  const effectiveContext = enrichContextWithN8nSession(context, output, metadata);
  const guard = await getGuardForRuntime(effectiveContext);
  const result = await guard.runtime.guard(
    ev.tool_result(guard.context, toolName, normalizeValue(output), {
      error: error ? safeString(error) : null,
      metadata: eventMetadata(effectiveContext, metadata),
    }),
    { phase: "after" }
  );
  return result.decision;
}

function reportToolOnce(guard, name, { description = "", capabilities = [], metadata = {} } = {}) {
  if (!guard || REPORTED_TOOLS.has(`${guard.context.session_id}:${name}`)) {
    return;
  }
  REPORTED_TOOLS.add(`${guard.context.session_id}:${name}`);
  try {
    guard.register_tool(() => {}, new ToolMetadata({
      name,
      description,
      capabilities,
      metadata,
    }));
  } catch (_) {
    // Tool reporting is best-effort; runtime events still carry full metadata.
  }
}

function semanticRole(value) {
  const role = String(value || "assistant").toLowerCase();
  if (role === "human") {
    return "user";
  }
  if (role === "ai") {
    return "assistant";
  }
  return role;
}

function semanticToolCallText(toolName, input) {
  return `Calling ${toolName || "tool"} with input: ${JSON.stringify(normalizeValue(input ?? {}))}`;
}

function semanticToolCallFromValue(value) {
  if (!value || typeof value !== "object") {
    return null;
  }
  const toolName = firstNonEmptyText(value.name, value.tool_name, value.toolName);
  const args = (
    (value.args && typeof value.args === "object") ? value.args :
      (value.arguments && typeof value.arguments === "object") ? value.arguments :
        {}
  );
  if (!toolName) {
    return null;
  }
  return semanticToolCallText(toolName, args);
}

function semanticContentFromMessage(message, fallback = "") {
  const normalized = normalizeValue(message);
  if (!normalized || typeof normalized !== "object") {
    return firstNonEmptyText(normalized, fallback) || "";
  }
  const toolCalls = Array.isArray(normalized.tool_calls)
    ? normalized.tool_calls.map((toolCall) => semanticToolCallFromValue(toolCall)).filter(Boolean)
    : Array.isArray(normalized.toolCalls)
      ? normalized.toolCalls.map((toolCall) => semanticToolCallFromValue(toolCall)).filter(Boolean)
      : [];
  if (toolCalls.length) {
    return toolCalls.join("\n");
  }
  return firstNonEmptyText(
    normalized.content,
    normalized.text,
    normalized.message,
    normalized.output,
    fallback,
  ) || "";
}

function semanticMessagesFromHistory(history = []) {
  if (!Array.isArray(history)) {
    return [];
  }
  return history.map((message) => {
    const normalized = normalizeValue(message);
    const role = semanticRole(
      normalized && typeof normalized === "object"
        ? (
          normalized.role ||
          normalized.type ||
          (typeof normalized.getType === "function" ? normalized.getType() : null) ||
          (typeof normalized._getType === "function" ? normalized._getType() : null)
        )
        : null
    );
    return {
      role,
      content: semanticContentFromMessage(normalized),
    };
  }).filter((message) => message.content !== "");
}

function semanticMessagesFromSteps(steps = []) {
  if (!Array.isArray(steps)) {
    return [];
  }
  const messages = [];
  for (const step of steps) {
    const action = step && step.action ? step.action : {};
    const messageLog = Array.isArray(action.messageLog) ? action.messageLog : [];
    if (messageLog.length) {
      messages.push(...semanticMessagesFromHistory(messageLog));
    } else {
      const fallbackText = firstNonEmptyText(
        action.log,
        action.tool && semanticToolCallText(action.tool, action.toolInput || {}),
      );
      if (fallbackText) {
        messages.push({ role: "assistant", content: fallbackText });
      }
    }
    if (step && step.observation != null) {
      messages.push({
        role: "tool",
        content: textFromValue(step.observation) || safeString(step.observation),
      });
    }
  }
  return messages;
}

function buildSemanticAgentInputMessages(invokeParams = {}) {
  const messages = [];
  const systemParts = [];
  if (invokeParams.system_message) {
    systemParts.push(String(invokeParams.system_message));
  }
  if (invokeParams.formatting_instructions) {
    systemParts.push(String(invokeParams.formatting_instructions));
  }
  if (systemParts.length) {
    messages.push({ role: "system", content: systemParts.join("\n\n") });
  }
  messages.push(...semanticMessagesFromHistory(invokeParams.chat_history));
  if (invokeParams.input != null) {
    messages.push({ role: "user", content: textFromValue(invokeParams.input) || safeString(invokeParams.input) });
  }
  messages.push(...semanticMessagesFromSteps(invokeParams.steps));
  return messages;
}

function semanticToolActionsOutput(actions = []) {
  if (!Array.isArray(actions)) {
    return "";
  }
  return actions.map((action) => {
    if (!action || typeof action !== "object") {
      return "";
    }
    const toolName = firstNonEmptyText(
      action.metadata && action.metadata.toolName,
      action.tool_name,
      action.nodeName,
      "tool",
    );
    const input = isPlainObject(action.input) ? { ...action.input } : normalizeValue(action.input);
    if (input && typeof input === "object" && !Array.isArray(input) && Object.prototype.hasOwnProperty.call(input, "tool")) {
      delete input.tool;
    }
    return semanticToolCallText(toolName, input);
  }).filter(Boolean).join("\n");
}

function toolsAgentModelName(model = null) {
  if (!model || typeof model !== "object") {
    return null;
  }
  return firstNonEmptyText(
    model.modelName,
    model.model,
    model.modelId,
    model.model_name,
    model.model_id,
    model.modelKwargs && model.modelKwargs.model,
  );
}

function toolsAgentProviderName(model = null) {
  const lowerModel = String(toolsAgentModelName(model) || "").toLowerCase();
  if (lowerModel.includes("gpt") || lowerModel.includes("openai")) {
    return "openai";
  }
  if (lowerModel.includes("ollama")) {
    return "ollama";
  }
  return "unknown";
}

function semanticMessagesFromPayload(payload) {
  const normalizedPayload = coerceLoopbackPayload(payload);
  if (Array.isArray(normalizedPayload)) {
    return normalizeResponsesInput(normalizedPayload);
  }
  if (isPlainObject(normalizedPayload)) {
    if (Array.isArray(normalizedPayload.messages)) {
      return normalizeResponsesInput(normalizedPayload.messages);
    }
    if (Array.isArray(normalizedPayload.input)) {
      return normalizeResponsesInput(normalizedPayload.input);
    }
    if (Object.prototype.hasOwnProperty.call(normalizedPayload, "content") || Object.prototype.hasOwnProperty.call(normalizedPayload, "role")) {
      return normalizeResponsesInput(normalizedPayload);
    }
  }
  return null;
}

function applySemanticMessagesToInvokeParams(invokeParams = {}, messages = []) {
  const normalizedMessages = normalizeResponsesInput(messages);
  if (!normalizedMessages.length) {
    return { ...(invokeParams || {}) };
  }
  const systemMessages = normalizedMessages
    .filter((message) => String(message && message.role || "").toLowerCase() === "system")
    .map((message) => textFromValue(message.content) || "")
    .filter(Boolean);
  const nonSystemMessages = normalizedMessages
    .filter((message) => String(message && message.role || "").toLowerCase() !== "system")
    .map((message) => ({
      role: message.role || "user",
      content: normalizeValue(message.content),
    }));
  const next = {
    ...(invokeParams || {}),
  };
  if (systemMessages.length) {
    next.system_message = systemMessages.join("\n\n");
  }
  if (!nonSystemMessages.length) {
    return next;
  }
  const lastMessage = nonSystemMessages[nonSystemMessages.length - 1];
  next.input = normalizeValue(lastMessage.content);
  next.chat_history = nonSystemMessages.slice(0, -1).map((message) => ({
    role: message.role,
    content: normalizeValue(message.content),
  }));
  return next;
}

function applyModifyToSemanticInvokeParams(invokeParams = {}, processedContent = "") {
  const payload = coerceLoopbackPayload(processedContent);
  const messages = semanticMessagesFromPayload(payload);
  if (messages && messages.length) {
    return applySemanticMessagesToInvokeParams(invokeParams, messages);
  }
  if (isPlainObject(payload) && Object.prototype.hasOwnProperty.call(payload, "input")) {
    return {
      ...(invokeParams || {}),
      input: normalizeValue(payload.input),
    };
  }
  if (typeof payload === "string") {
    return {
      ...(invokeParams || {}),
      input: payload,
    };
  }
  return { ...(invokeParams || {}) };
}

function applyLoopbackToSemanticInvokeParams(invokeParams = {}, processedContent = "") {
  return applyModifyToSemanticInvokeParams(invokeParams, processedContent);
}

function applyThoughtAlignmentToSemanticInvokeParams(invokeParams = {}, _rawOutput = null, processedContent = "") {
  const thought = String(processedContent || "").trim();
  if (!thought) {
    return { ...(invokeParams || {}) };
  }
  const chatHistory = Array.isArray(invokeParams && invokeParams.chat_history)
    ? [...invokeParams.chat_history]
    : [];
  return {
    ...(invokeParams || {}),
    chat_history: [
      ...chatHistory,
      { role: "assistant", content: thought },
    ],
  };
}
function buildToolsAgentRuntimeContext(ctx, itemContext = {}, response = null, model = null, extra = {}) {
  const runtimeContext = currentContext();
  const workflow = ctx && typeof ctx.getWorkflow === "function" ? ctx.getWorkflow() : null;
  const workflowId = optionalString(
    runtimeContext.workflow_id ||
    (workflow && (workflow.id || workflow.workflowId || workflow.workflow_id))
  );
  const workflowIdentity = mergeWorkflowIdentity(
    workflowIdentityForWorkflowId(workflowId),
    normalizeWorkflowIdentity(workflow)
  );
  const node = ctx && typeof ctx.getNode === "function" ? ctx.getNode() : null;
  const inputItems = ctx && typeof ctx.getInputData === "function" ? ctx.getInputData() : [];
  const itemIndex = itemContext && itemContext.itemIndex != null ? itemContext.itemIndex : 0;
  const inputItem = Array.isArray(inputItems) ? inputItems[itemIndex] : null;
  const inputJson = inputItem && inputItem.json && typeof inputItem.json === "object" ? inputItem.json : inputItem;
  const n8nSessionId = n8nSessionIdFromSources(runtimeContext, inputJson, response, itemContext);
  return {
    ...runtimeContext,
    workflow_id: workflowId,
    workflow_name: runtimeContext.workflow_name || (workflow && workflow.name ? String(workflow.name) : null),
    n8n_session_id: n8nSessionId,
    external_session_id: n8nSessionId || runtimeContext.external_session_id || runtimeContext.execution_id || null,
    user_id: runtimeContext.user_id || workflowIdentity.user_id || process.env.AGENTGUARD_USER_ID || null,
    n8n_user_id: runtimeContext.n8n_user_id || workflowIdentity.user_id || null,
    n8n_user_email: runtimeContext.n8n_user_email || workflowIdentity.user_email || null,
    n8n_user_name: runtimeContext.n8n_user_name || workflowIdentity.user_name || null,
    n8n_user_source: runtimeContext.n8n_user_source || workflowIdentity.user_source || null,
    n8n_project_id: runtimeContext.n8n_project_id || workflowIdentity.project_id || null,
    n8n_project_name: runtimeContext.n8n_project_name || workflowIdentity.project_name || null,
    node_id: runtimeContext.node_id || (node && node.id ? node.id : null),
    node_name: runtimeContext.node_name || (node && node.name ? node.name : null),
    node_type: runtimeContext.node_type || (node && node.type ? node.type : null),
    node_version: runtimeContext.node_version || (node && node.typeVersion ? node.typeVersion : null),
    llm_provider: runtimeContext.llm_provider || toolsAgentProviderName(model),
    llm_model: runtimeContext.llm_model || toolsAgentModelName(model),
    ...extra,
  };
}

function aiToolResult(action, value, executionStatus = "success", error = null) {
  const data = {
    executionTime: 0,
    startTime: 0,
    executionIndex: 0,
    source: [],
    executionStatus,
  };
  if (error) {
    data.error = error;
  } else {
    data.data = { ai_tool: [[{ json: { output: value } }]] };
  }
  return { action, data };
}

function aiToolRunNodeResult(value) {
  return {
    data: [[{ json: value }]],
    hints: [],
  };
}

function ordinaryNodeResult(value) {
  return {
    data: [[{ json: value }]],
    hints: [],
  };
}

function toolNameForAction(action) {
  return String(
    (action && action.metadata && action.metadata.toolName) ||
    (action && action.nodeName) ||
    "n8n_ai_tool"
  );
}

function actionContext(node, action) {
  return currentContext({
    node_id: node && node.id ? node.id : null,
    node_name: action && action.nodeName ? action.nodeName : (node && node.name ? node.name : null),
    node_type: node && node.type ? node.type : null,
    connection_type: action && action.type ? action.type : "ai_tool",
    tool_call_id: action && action.id ? action.id : null,
    tool_name: action && action.metadata && action.metadata.toolName ? action.metadata.toolName : null,
  });
}

function connectedToolParentNodes(ctx) {
  try {
    if (!ctx || typeof ctx.getParentNodes !== "function" || typeof ctx.getNode !== "function") {
      return [];
    }
    return (ctx.getParentNodes(ctx.getNode().name, {
      connectionType: AI_TOOL_CONNECTION_TYPE,
      depth: 1,
    }) || []).filter((node) => node && !node.disabled);
  } catch (_) {
    return [];
  }
}

function nodeFromToolContext(tool) {
  try {
    if (tool && tool.context && typeof tool.context.getNode === "function") {
      return tool.context.getNode();
    }
  } catch (_) {
    return null;
  }
  return null;
}

function mergeDefinedContext(base = {}, extra = {}) {
  const out = { ...(base || {}) };
  for (const [key, value] of Object.entries(extra || {})) {
    if (value !== null && value !== undefined) {
      out[key] = value;
    } else if (!Object.prototype.hasOwnProperty.call(out, key)) {
      out[key] = value;
    }
  }
  return out;
}

function buildConnectedToolSourceContext(ctx, tool, index = 0, parentNodes = null) {
  const callerNode = ctx && typeof ctx.getNode === "function" ? ctx.getNode() : null;
  const metadata = tool && tool.metadata && typeof tool.metadata === "object" ? tool.metadata : {};
  const parents = Array.isArray(parentNodes) ? parentNodes : connectedToolParentNodes(ctx);
  const metadataSourceName = metadata.sourceNodeName || metadata.source_node_name || metadata.nodeName || metadata.node_name;
  const toolContextNode = nodeFromToolContext(tool);
  const runtimeContext = currentContext();
  const cachedSourceNode = workflowNodeForTool(runtimeContext.workflow_id, [
    metadata.sourceNodeId,
    metadata.source_node_id,
    metadataSourceName,
    tool && tool.name,
  ]);
  const sourceNode = (
    parents[index] ||
    parents.find((node) => node && metadataSourceName && node.name === metadataSourceName) ||
    toolContextNode ||
    cachedSourceNode ||
    null
  );
  return currentContext({
    node_id: callerNode && callerNode.id ? callerNode.id : null,
    node_name: callerNode && callerNode.name ? callerNode.name : null,
    node_type: callerNode && callerNode.type ? callerNode.type : null,
    node_version: callerNode && callerNode.typeVersion ? callerNode.typeVersion : null,
    caller_node_id: callerNode && callerNode.id ? callerNode.id : null,
    caller_node_name: callerNode && callerNode.name ? callerNode.name : null,
    caller_node_type: callerNode && callerNode.type ? callerNode.type : null,
    source_node_id: sourceNode && sourceNode.id ? sourceNode.id : null,
    source_node_name: sourceNode && sourceNode.name ? sourceNode.name : metadataSourceName || null,
    source_node_type: sourceNode && sourceNode.type ? sourceNode.type : null,
    source_node_version: sourceNode && sourceNode.typeVersion ? sourceNode.typeVersion : null,
    source_node_parameters: sourceNode && isPlainObject(sourceNode.parameters) ? normalizeValue(sourceNode.parameters) : null,
    connection_type: AI_TOOL_CONNECTION_TYPE,
    tool_name: tool && tool.name ? tool.name : null,
    tool_description: tool && (tool.description || metadata.description || metadata.toolDescription) || "",
  });
}

function toolContext(tool, overrides = {}) {
  const metadata = tool && tool.metadata && typeof tool.metadata === "object" ? tool.metadata : {};
  const sourceNodeName = (
    overrides.source_node_name ||
    metadata.sourceNodeName ||
    metadata.source_node_name ||
    metadata.nodeName ||
    metadata.node_name
  );
  const sourceNodeId = overrides.source_node_id || metadata.sourceNodeId || metadata.source_node_id || null;
  const sourceNodeType = overrides.source_node_type || metadata.sourceNodeType || metadata.source_node_type || null;
  const sourceNodeVersion = overrides.source_node_version || metadata.sourceNodeVersion || metadata.source_node_version || null;
  const description = tool && (tool.description || (metadata.description || metadata.toolDescription));
  const context = currentContext(overrides);
  return {
    ...context,
    agent_node_id: context.agent_node_id || context.caller_node_id || context.node_id || null,
    agent_node_name: context.agent_node_name || context.caller_node_name || context.node_name || null,
    agent_node_type: context.agent_node_type || context.caller_node_type || context.node_type || null,
    node_id: sourceNodeId || context.node_id || null,
    node_name: sourceNodeName || context.node_name || (tool && tool.name ? tool.name : null),
    node_type: sourceNodeType || context.node_type || null,
    node_version: sourceNodeVersion || context.node_version || null,
    connection_type: "ai_tool",
    tool_name: tool && tool.name ? tool.name : null,
    tool_description: description || "",
  };
}

function sourceNodeForToolInvocation(sourceContext = {}) {
  if (sourceContext.source_node_parameters && isPlainObject(sourceContext.source_node_parameters)) {
    return { parameters: sourceContext.source_node_parameters };
  }
  const sourceNode = workflowNodeForTool(sourceContext.workflow_id, [
    sourceContext.source_node_id,
    sourceContext.source_node_name,
    sourceContext.node_name,
    sourceContext.tool_name,
  ]);
  if (sourceNode && isPlainObject(sourceNode.parameters)) {
    return { parameters: normalizeValue(sourceNode.parameters) };
  }
  return null;
}

function wrapConnectedTool(tool, sourceContext = {}) {
  if (!tool || typeof tool.invoke !== "function") {
    return tool;
  }
  tool[TOOL_SOURCE_CONTEXT] = mergeDefinedContext(tool[TOOL_SOURCE_CONTEXT] || {}, sourceContext || {});
  if (tool.invoke[TOOL_INVOKE_PATCHED]) {
    return tool;
  }
  const original = tool.invoke;
  const toolName = String((tool && tool.name) || (sourceContext && sourceContext.tool_name) || "n8n_ai_tool");
  tool.invoke = async function agentguardN8nToolInvoke(input, ...rest) {
    const activeSourceContext = tool[TOOL_SOURCE_CONTEXT] || sourceContext;
    let currentInput = input;
    let invocation = buildAiToolInvocation(currentInput || {}, sourceNodeForToolInvocation(activeSourceContext));
    const context = enrichContextWithN8nSession(
      toolContext(tool, activeSourceContext),
      currentInput,
      invocation.metadata
    );
    if (!matchesConfiguredFilters(context)) {
      return original.call(this, input, ...rest);
    }
    const capabilities = inferCapabilitiesFromNode(
      { type: context.node_type || (tool.metadata && tool.metadata.sourceNodeType), name: context.node_name || toolName },
      ["ai_tool"]
    );
    const beforeDecision = await guardToolBefore(toolName, invocation.arguments, context, capabilities, invocation.metadata);
    if (beforeDecision.decision_type === DecisionType.MODIFY_TOOL_INVOKE) {
      currentInput = applyModifyToToolInput(currentInput || {}, beforeDecision.processed_content);
      invocation = buildAiToolInvocation(currentInput, sourceNodeForToolInvocation(activeSourceContext));
    }
    const blocked = blockedToolValue(beforeDecision, toolName);
    if (blocked) {
      flushGuardAsync(context);
      return blocked;
    }
    try {
      let output = await original.call(this, currentInput, ...rest);
      const afterDecision = await guardToolAfter(toolName, output, context, { metadata: invocation.metadata });
      if (afterDecision.decision_type === DecisionType.MODIFY_TOOL_RESULT) {
        output = extractModifiedPrimaryValue(afterDecision.processed_content);
      }
      const resultBlocked = blockedResultValue(afterDecision, toolName);
      return resultBlocked || output;
    } catch (error) {
      await guardToolAfter(toolName, null, context, { error, metadata: invocation.metadata });
      throw error;
    } finally {
      flushGuardAsync(context);
    }
  };
  tool.invoke[TOOL_INVOKE_PATCHED] = true;
  tool.invoke.__agentguard_original_invoke__ = original;
  return tool;
}

function patchOpenAI(moduleExports) {
  for (const key of ["ChatOpenAIResponses", "AzureChatOpenAIResponses"]) {
    const Klass = moduleExports && moduleExports[key];
    if (!Klass || !Klass.prototype || typeof Klass.prototype.completionWithRetry !== "function") {
      continue;
    }
    if (Klass.prototype.completionWithRetry[PATCHED]) {
      continue;
    }
    const original = Klass.prototype.completionWithRetry;
    Klass.prototype.completionWithRetry = async function agentguardCompletionWithRetry(request, requestOptions) {
      const context = enrichContextWithN8nSession(currentContext({
        llm_provider: "openai",
        llm_class: key,
      }), request, requestOptions);
      if (!matchesConfiguredFilters(context)) {
        return original.call(this, request, requestOptions);
      }
      let currentRequest = request;
      let attempts = 0;
      let thoughtAlignmentAttempt = 0;
      while (true) {
        const beforeDecision = await guardLLMBefore(
          currentRequest,
          context,
          llmGuardMetadata(
            {},
            {
              supported: supportsThoughtAlignmentForResponsesRequest(currentRequest),
              retryAttempt: thoughtAlignmentAttempt,
            }
          )
        );
        if (beforeDecision.decision_type === DecisionType.LOOP_BACK_TO_LLM) {
          if (attempts >= MAX_LLM_LOOPBACK_ATTEMPTS) {
            return blockedLLMResponse(beforeDecision, currentRequest);
          }
          currentRequest = isThoughtAlignmentLoopbackDecision(beforeDecision)
            ? applyThoughtAlignmentToResponsesRequest(currentRequest, beforeDecision.processed_content)
            : applyLoopbackToResponsesRequest(currentRequest, beforeDecision.processed_content);
          thoughtAlignmentAttempt = isThoughtAlignmentLoopbackDecision(beforeDecision) ? 1 : thoughtAlignmentAttempt;
          attempts += 1;
          continue;
        }
        if (beforeDecision.decision_type === DecisionType.MODIFY_LLM_INPUT) {
          currentRequest = applyModifyToResponsesRequest(currentRequest, beforeDecision.processed_content);
        }
        const beforeBlocked = blockedToolValue(beforeDecision, "llm");
        if (beforeBlocked) {
          return blockedLLMResponse(beforeDecision, currentRequest);
        }
        const raw = await original.call(this, currentRequest, requestOptions);
        if (currentRequest && currentRequest.stream && raw && typeof raw[Symbol.asyncIterator] === "function") {
          const outcome = await finalizeResponsesStream(raw, currentRequest, context, thoughtAlignmentAttempt);
          const afterDecision = outcome.decision;
          if (afterDecision.decision_type === DecisionType.LOOP_BACK_TO_LLM) {
            if (attempts >= MAX_LLM_LOOPBACK_ATTEMPTS) {
              return blockedLLMResponse(afterDecision, currentRequest);
            }
            currentRequest = isThoughtAlignmentLoopbackDecision(afterDecision)
              ? applyThoughtAlignmentToResponsesRequest(currentRequest, outcome.rawOutput, afterDecision.processed_content)
              : applyLoopbackToResponsesRequest(currentRequest, afterDecision.processed_content);
            thoughtAlignmentAttempt = isThoughtAlignmentLoopbackDecision(afterDecision) ? 1 : thoughtAlignmentAttempt;
            attempts += 1;
            continue;
          }
          return outcome.stream;
        }
        const afterDecision = await guardLLMAfter(
          raw,
          context,
          llmGuardMetadata(
            {},
            {
              supported: supportsThoughtAlignmentForResponsesRequest(currentRequest),
              retryAttempt: thoughtAlignmentAttempt,
            }
          )
        );
        if (afterDecision.decision_type === DecisionType.LOOP_BACK_TO_LLM) {
          if (attempts >= MAX_LLM_LOOPBACK_ATTEMPTS) {
            return blockedLLMResponse(afterDecision, currentRequest);
          }
          currentRequest = isThoughtAlignmentLoopbackDecision(afterDecision)
            ? applyThoughtAlignmentToResponsesRequest(currentRequest, raw, afterDecision.processed_content)
            : applyLoopbackToResponsesRequest(currentRequest, afterDecision.processed_content);
          thoughtAlignmentAttempt = isThoughtAlignmentLoopbackDecision(afterDecision) ? 1 : thoughtAlignmentAttempt;
          attempts += 1;
          continue;
        }
        let finalRaw = raw;
        if (afterDecision.decision_type === DecisionType.MODIFY_LLM_OUTPUT) {
          finalRaw = applyModifyToResponsesResult(raw, afterDecision.processed_content);
        }
        const afterBlocked = blockedResultValue(afterDecision, "llm");
        return afterBlocked
          ? syntheticResponsesPayload(afterBlocked.reason || afterDecision.reason, currentRequest && currentRequest.model)
          : finalRaw;
      }
    };
    Klass.prototype.completionWithRetry[PATCHED] = true;
  }
}

function patchAgentExecutionModule(moduleExports, resolved) {
  if (resolved && resolved.endsWith("/createEngineRequests.js")) {
    patchCreateEngineRequests(moduleExports);
  }
  if (resolved && resolved.endsWith("/executeEngineAction.js")) {
    patchExecuteEngineAction(moduleExports);
  }
}

function patchAgentToolsCommon(moduleExports) {
  if (!moduleExports || typeof moduleExports.getTools !== "function" || moduleExports.getTools[PATCHED]) {
    return;
  }
  const original = moduleExports.getTools;
  moduleExports.getTools = async function agentguardGetTools(ctx, outputParser) {
    const tools = await original.call(this, ctx, outputParser);
    const parentNodes = connectedToolParentNodes(ctx);
    if (Array.isArray(tools)) {
      for (const [index, tool] of tools.entries()) {
        if (tool && tool.name === "format_final_json_response") {
          continue;
        }
        wrapConnectedTool(tool, buildConnectedToolSourceContext(ctx, tool, index, parentNodes));
      }
    }
    return tools;
  };
  moduleExports.getTools[PATCHED] = true;
}

function patchConnectedToolsHelpers(moduleExports) {
  if (!moduleExports || typeof moduleExports.getConnectedTools !== "function" || moduleExports.getConnectedTools[PATCHED]) {
    return;
  }
  const original = moduleExports.getConnectedTools;
  moduleExports.getConnectedTools = async function agentguardGetConnectedTools(ctx, ...args) {
    const tools = await original.call(this, ctx, ...args);
    if (!Array.isArray(tools)) {
      return tools;
    }
    const parentNodes = connectedToolParentNodes(ctx);
    return tools.map((tool, index) => wrapConnectedTool(
      tool,
      buildConnectedToolSourceContext(ctx, tool, index, parentNodes)
    ));
  };
  moduleExports.getConnectedTools[PATCHED] = true;
}

function patchOpenAiTextMessageOperation(moduleExports, resolved, deps = {}) {
  if (!moduleExports || typeof moduleExports.execute !== "function" || moduleExports.execute[OPENAI_TEXT_MESSAGE_PATCHED]) {
    return;
  }
  const localRequire = deps.require || createRequire(resolved);
  const n8nWorkflow = deps.n8nWorkflow || localRequire("n8n-workflow");
  const helpers = deps.helpers || localRequire("../../../../../../utils/helpers");
  const utils = deps.utils || localRequire("../../../helpers/utils");
  const transport = deps.transport || localRequire("../../../transport");
  const original = moduleExports.execute;
  moduleExports.execute = async function agentguardOpenAiTextMessageExecute(i) {
    const node = typeof this.getNode === "function" ? this.getNode() : null;
    if (!isOpenAiTextMessageOperationNode(node)) {
      return original.call(this, i);
    }
    const context = enrichContextWithN8nSession(currentContext({
      llm_node: true,
      llm_provider: "openai",
      llm_class: "n8n_openai_text_message_operation",
    }), { item_index: i });
    if (!matchesConfiguredFilters(context)) {
      return original.call(this, i);
    }
    const { accumulateTokenUsage, jsonParse, NodeOperationError } = n8nWorkflow;
    const { getConnectedTools } = helpers;
    const { formatToOpenAIAssistantTool } = utils;
    const { apiRequest } = transport;
    const nodeVersion = node.typeVersion;
    const model = this.getNodeParameter("modelId", i, "", { extractValue: true });
    let currentMessages = this.getNodeParameter("messages.values", i, []);
    if (!currentMessages.some((message) => typeof message.content === "string" && message.content.trim() !== "")) {
      throw new NodeOperationError(this.getNode(), "A non-empty prompt is required.", {
        itemIndex: i,
      });
    }
    const options = normalizeValue(this.getNodeParameter("options", i, {})) || {};
    const jsonOutput = this.getNodeParameter("jsonOutput", i, false);
    const maxToolsIterations = nodeVersion >= 1.5 ? this.getNodeParameter("options.maxToolsIterations", i, 15) : 0;
    const abortSignal = this.getExecutionCancelSignal();
    if (options.maxTokens !== undefined) {
      options.max_completion_tokens = options.maxTokens;
      delete options.maxTokens;
    }
    if (options.topP !== undefined) {
      options.top_p = options.topP;
      delete options.topP;
    }
    let responseFormat;
    currentMessages = denormalizeResponsesInput(currentMessages);
    if (jsonOutput) {
      responseFormat = { type: "json_object" };
      currentMessages = [
        {
          role: "system",
          content: "You are a helpful assistant designed to output JSON.",
        },
        ...currentMessages,
      ];
    }
    const hideTools = this.getNodeParameter("hideTools", i, "");
    let tools;
    let externalTools = [];
    if (hideTools !== "hide") {
      externalTools = await getConnectedTools(this, nodeVersion > 1, false);
    }
    if (externalTools.length) {
      tools = externalTools.map(formatToOpenAIAssistantTool);
    }
    const baseBody = {
      model,
      tools,
      response_format: responseFormat,
      ...normalizeValue(options),
    };
    delete baseBody.maxToolsIterations;

    const guardMetadata = (retryAttempt) => llmGuardMetadata(
      {
        event_source: "n8n_openai_text_message_operation",
        provider: "openai",
        node_parameters: normalizeValue((node && node.parameters) || {}),
      },
      {
        supported: true,
        retryAttempt,
      }
    );

    let attempts = 0;
    let thoughtAlignmentAttempt = 0;
    let currentIteration = 1;
    let finalResponse = null;
    try {
      while (true) {
        const beforeDecision = await guardLLMBefore(
          {
            model,
            input: currentMessages,
            tools,
            node_parameters: normalizeValue((node && node.parameters) || {}),
          },
          context,
          guardMetadata(thoughtAlignmentAttempt)
        );
        if (beforeDecision.decision_type === DecisionType.LOOP_BACK_TO_LLM) {
          if (attempts >= MAX_LLM_LOOPBACK_ATTEMPTS) {
            return openAiTextMessageReturnData(
              syntheticOpenAiTextMessageResponse(beforeDecision.reason || "blocked by AgentGuard", model),
              this.getNodeParameter("simplify", i),
              i
            );
          }
          currentMessages = isThoughtAlignmentLoopbackDecision(beforeDecision)
            ? applyThoughtAlignmentToOpenAiTextMessages(currentMessages, null, beforeDecision.processed_content)
            : applyLoopbackToOpenAiTextMessages(currentMessages, beforeDecision.processed_content);
          thoughtAlignmentAttempt = isThoughtAlignmentLoopbackDecision(beforeDecision) ? 1 : thoughtAlignmentAttempt;
          attempts += 1;
          continue;
        }
        if (beforeDecision.decision_type === DecisionType.MODIFY_LLM_INPUT) {
          currentMessages = applyModifyToOpenAiTextMessages(currentMessages, beforeDecision.processed_content);
        }
        const blockedBefore = blockedToolValue(beforeDecision, "llm");
        if (blockedBefore) {
          return openAiTextMessageReturnData(
            syntheticOpenAiTextMessageResponse(beforeDecision.reason || "blocked by AgentGuard", model),
            this.getNodeParameter("simplify", i),
            i
          );
        }

        let response = await apiRequest.call(this, "POST", "/chat/completions", {
          body: {
            ...baseBody,
            messages: currentMessages,
          },
        });
        if (!response) {
          return [];
        }
        if (response.usage) {
          accumulateTokenUsage(this, response.usage.prompt_tokens, response.usage.completion_tokens);
        }
        const afterDecision = await guardLLMAfter(
          openAiTextMessageOutput(response),
          context,
          guardMetadata(thoughtAlignmentAttempt)
        );
        if (afterDecision.decision_type === DecisionType.LOOP_BACK_TO_LLM) {
          if (attempts >= MAX_LLM_LOOPBACK_ATTEMPTS) {
            return openAiTextMessageReturnData(
              syntheticOpenAiTextMessageResponse(afterDecision.reason || "blocked by AgentGuard", model),
              this.getNodeParameter("simplify", i),
              i
            );
          }
          currentMessages = isThoughtAlignmentLoopbackDecision(afterDecision)
            ? applyThoughtAlignmentToOpenAiTextMessages(currentMessages, response, afterDecision.processed_content)
            : applyLoopbackToOpenAiTextMessages(currentMessages, afterDecision.processed_content);
          thoughtAlignmentAttempt = isThoughtAlignmentLoopbackDecision(afterDecision) ? 1 : thoughtAlignmentAttempt;
          attempts += 1;
          continue;
        }
        if (afterDecision.decision_type === DecisionType.MODIFY_LLM_OUTPUT) {
          response = applyModifyToOpenAiTextMessageResponse(response, afterDecision.processed_content);
        }
        const blockedAfter = blockedResultValue(afterDecision, "llm");
        if (blockedAfter) {
          return openAiTextMessageReturnData(
            syntheticOpenAiTextMessageResponse(blockedAfter.reason || afterDecision.reason || "blocked by AgentGuard", model),
            this.getNodeParameter("simplify", i),
            i
          );
        }

        finalResponse = response;
        const toolCalls = response && response.choices && response.choices[0] && response.choices[0].message
          ? response.choices[0].message.tool_calls
          : null;
        if (!Array.isArray(toolCalls) || !toolCalls.length) {
          break;
        }
        if (abortSignal?.aborted || (maxToolsIterations > 0 && currentIteration >= maxToolsIterations)) {
          break;
        }
        currentMessages = [
          ...currentMessages,
          normalizeValue(response.choices[0].message),
        ];
        for (const toolCall of toolCalls) {
          const functionName = toolCall && toolCall.function ? toolCall.function.name : null;
          const functionArgs = toolCall && toolCall.function ? toolCall.function.arguments : null;
          let functionResponse;
          for (const tool of externalTools || []) {
            if (tool.name === functionName) {
              const parsedArgs = jsonParse(functionArgs);
              const functionInput = parsedArgs && typeof parsedArgs === "object" && Object.prototype.hasOwnProperty.call(parsedArgs, "input")
                ? parsedArgs.input
                : parsedArgs ?? functionArgs;
              functionResponse = await tool.invoke(functionInput);
            }
          }
          if (typeof functionResponse === "object") {
            functionResponse = JSON.stringify(functionResponse);
          }
          currentMessages.push({
            tool_call_id: toolCall.id,
            role: "tool",
            content: functionResponse,
          });
        }
        currentIteration += 1;
      }
      return openAiTextMessageReturnData(finalResponse, this.getNodeParameter("simplify", i), i);
    } catch (error) {
      await guardLLMAfter(
        { output_text: "", output: [], status: "error", error: safeString(error) },
        context,
        {
          ...guardMetadata(thoughtAlignmentAttempt),
          error: safeString(error && error.message ? error.message : error),
        }
      );
      throw error;
    } finally {
      flushGuardAsync(context);
    }
  };
  moduleExports.execute[OPENAI_TEXT_MESSAGE_PATCHED] = true;
}

function patchCreateEngineRequests(moduleExports) {
  if (!moduleExports || typeof moduleExports.createEngineRequests !== "function" || moduleExports.createEngineRequests[PATCHED]) {
    return;
  }
  const original = moduleExports.createEngineRequests;
  moduleExports.createEngineRequests = function agentguardCreateEngineRequests(toolCalls, itemIndex, tools) {
    const actions = original.call(this, toolCalls, itemIndex, tools);
    for (const action of actions || []) {
      action.metadata = {
        ...(action.metadata || {}),
        agentguard: {
          local_tool: true,
          source: "n8n_createEngineRequests",
        },
      };
    }
    return actions;
  };
  moduleExports.createEngineRequests[PATCHED] = true;
}

function patchExecuteEngineAction(moduleExports) {
  if (!moduleExports || typeof moduleExports.executeEngineAction !== "function" || moduleExports.executeEngineAction[PATCHED]) {
    return;
  }
  const original = moduleExports.executeEngineAction;
  moduleExports.executeEngineAction = async function agentguardExecuteEngineAction(node, action, tools) {
    const toolName = toolNameForAction(action);
    const tool = Array.isArray(tools) ? tools.find((candidate) => candidate.name === toolName) : null;
    const context = actionContext(node, action);
    if (!matchesConfiguredFilters(context)) {
      return original.call(this, node, action, tools);
    }
    if (tool && tool.invoke && tool.invoke[TOOL_INVOKE_PATCHED]) {
      return ALS.run(context, () => original.call(this, node, action, tools));
    }
    const invocation = buildAiToolInvocation(action && action.input ? action.input : {}, null);
    const args = invocation.arguments;
    const invocationMetadata = {
      ...invocation.metadata,
      tool_call_id: invocation.metadata.tool_call_id || (action && action.id ? String(action.id) : null),
    };
    const capabilities = inferCapabilitiesFromNode({ type: context.node_type, name: context.node_name }, ["ai_tool"]);
    let currentAction = action;
    let currentArgs = args;
    const beforeDecision = await guardToolBefore(toolName, currentArgs, context, capabilities, invocationMetadata);
    if (beforeDecision.decision_type === DecisionType.MODIFY_TOOL_INVOKE) {
      currentArgs = applyModifyToToolInput(currentArgs, beforeDecision.processed_content);
      currentAction = { ...(action || {}), input: currentArgs };
    }
    const blocked = blockedToolValue(beforeDecision, toolName);
    if (blocked) {
      return aiToolResult(currentAction, blocked);
    }
    try {
      let result;
      if (tool && typeof tool.invoke === "function") {
        const input = { ...(currentArgs || {}) };
        delete input.tool;
        result = await tool.invoke(input);
        result = {
          action: currentAction,
          data: {
            executionTime: 0,
            startTime: 0,
            executionIndex: 0,
            source: [],
            executionStatus: "success",
            data: { ai_tool: [[{ json: { output: result } }]] },
          },
        };
      } else {
        result = await original.call(this, node, currentAction, tools);
      }
      const output = result && result.data && result.data.data ? result.data.data : result;
      const afterDecision = await guardToolAfter(toolName, output, context, { metadata: invocationMetadata });
      if (afterDecision.decision_type === DecisionType.MODIFY_TOOL_RESULT) {
        result = applyModifyToEngineActionResult(result, afterDecision.processed_content);
      }
      const resultBlocked = blockedResultValue(afterDecision, toolName);
      return resultBlocked ? aiToolResult(currentAction, resultBlocked) : result;
    } catch (error) {
      await guardToolAfter(toolName, null, context, { error, metadata: invocationMetadata });
      return aiToolResult(currentAction, null, "error", error);
    } finally {
      flushGuardAsync(context);
    }
  };
  moduleExports.executeEngineAction[PATCHED] = true;
}

function patchN8nCore(moduleExports) {
  const WorkflowExecute = moduleExports && moduleExports.WorkflowExecute;
  if (!WorkflowExecute || !WorkflowExecute.prototype || typeof WorkflowExecute.prototype.runNode !== "function") {
    return;
  }
  if (WorkflowExecute.prototype.runNode[PATCHED]) {
    return;
  }
  const original = WorkflowExecute.prototype.runNode;
  WorkflowExecute.prototype.runNode = async function agentguardRunNode(
    workflow,
    executionData,
    runExecutionData,
    runIndex,
    additionalData,
    mode,
    abortSignal,
    subNodeExecutionResults
  ) {
    const context = buildRunNodeContext(workflow, executionData, runExecutionData, runIndex, additionalData, mode);
    return ALS.run(context, async () => {
      if (!matchesConfiguredFilters(context)) {
        return original.call(this, workflow, executionData, runExecutionData, runIndex, additionalData, mode, abortSignal, subNodeExecutionResults);
      }
      const node = executionData && executionData.node ? executionData.node : {};
      const nodeType = resolveNodeType(workflow, node);
      if (isRunNodeLLMExecution(node)) {
        if (isOpenAiTextMessageOperationNode(node)) {
          return original.call(this, workflow, executionData, runExecutionData, runIndex, additionalData, mode, abortSignal, subNodeExecutionResults);
        }
        return guardedRunNodeLLM(original, this, {
          workflow,
          executionData,
          runExecutionData,
          runIndex,
          additionalData,
          mode,
          abortSignal,
          subNodeExecutionResults,
          context,
          node,
          nodeType,
        });
      }
      if (isAiToolRunNodeExecution(node, executionData)) {
        return guardedRunNodeAiTool(original, this, {
          workflow,
          executionData,
          runExecutionData,
          runIndex,
          additionalData,
          mode,
          abortSignal,
          subNodeExecutionResults,
          context,
          node,
          nodeType,
        });
      }
      if (shouldTreatRunNodeAsTool(node, nodeType, executionData)) {
        return guardedRunNodeTool(original, this, {
          workflow,
          executionData,
          runExecutionData,
          runIndex,
          additionalData,
          mode,
          abortSignal,
          subNodeExecutionResults,
          context,
          node,
          nodeType,
        });
      }
      return original.call(this, workflow, executionData, runExecutionData, runIndex, additionalData, mode, abortSignal, subNodeExecutionResults);
    });
  };
  WorkflowExecute.prototype.runNode[PATCHED] = true;
}

async function guardedRunNodeLLM(original, target, args) {
  const { workflow, runIndex, additionalData, mode, abortSignal, subNodeExecutionResults, context, node } = args;
  const thoughtAlignmentSupported = supportsThoughtAlignmentForRunNode(node);
  const baseLLMContext = {
    ...context,
    llm_node: true,
    llm_provider: providerFromLLMNode(node),
  };
  let currentArgs = { ...(args || {}) };
  let llmContext = baseLLMContext;
  let attempts = 0;
  let thoughtAlignmentAttempt = 0;
  try {
    while (true) {
      const request = llmRequestFromRunNodeExecution(
        currentArgs.executionData,
        currentArgs.node,
        currentArgs.runExecutionData
      );
      llmContext = enrichContextWithN8nSession(
        baseLLMContext,
        request,
        currentArgs.executionData,
        currentArgs.runExecutionData,
        additionalData
      );
      const beforeDecision = await guardLLMBefore(request, llmContext, {
        ...llmGuardMetadata(
          {
            event_source: "n8n_run_node_llm",
            provider: providerFromLLMNode(currentArgs.node),
            node_parameters: normalizeValue((currentArgs.node && currentArgs.node.parameters) || {}),
          },
          {
            supported: thoughtAlignmentSupported,
            retryAttempt: thoughtAlignmentAttempt,
          }
        ),
      });
      if (beforeDecision.decision_type === DecisionType.LOOP_BACK_TO_LLM) {
        if (attempts >= MAX_LLM_LOOPBACK_ATTEMPTS) {
          return n8nLLMRunNodeResult(blockedLLMResponse(beforeDecision, request));
        }
        currentArgs = isThoughtAlignmentLoopbackDecision(beforeDecision)
          ? applyThoughtAlignmentToRunNodeArgs(currentArgs, beforeDecision.processed_content)
          : applyLoopbackToRunNodeArgs(currentArgs, beforeDecision.processed_content);
        thoughtAlignmentAttempt = isThoughtAlignmentLoopbackDecision(beforeDecision) ? 1 : thoughtAlignmentAttempt;
        attempts += 1;
        continue;
      }
      if (beforeDecision.decision_type === DecisionType.MODIFY_LLM_INPUT) {
        currentArgs = applyModifyToRunNodeArgs(currentArgs, beforeDecision.processed_content);
      }
      const beforeBlocked = blockedToolValue(beforeDecision, "llm");
      if (beforeBlocked) {
        return n8nLLMRunNodeResult(blockedLLMResponse(beforeDecision, request));
      }

      const result = await original.call(
        target,
        workflow,
        currentArgs.executionData,
        currentArgs.runExecutionData,
        runIndex,
        additionalData,
        mode,
        abortSignal,
        subNodeExecutionResults
      );
      const output = llmOutputFromRunNodeResult(result);
      const afterDecision = await guardLLMAfter(output, llmContext, {
        ...llmGuardMetadata(
          {
            event_source: "n8n_run_node_llm",
            provider: providerFromLLMNode(currentArgs.node),
            node_parameters: normalizeValue((currentArgs.node && currentArgs.node.parameters) || {}),
          },
          {
            supported: thoughtAlignmentSupported,
            retryAttempt: thoughtAlignmentAttempt,
          }
        ),
      });
      if (afterDecision.decision_type === DecisionType.LOOP_BACK_TO_LLM) {
        if (attempts >= MAX_LLM_LOOPBACK_ATTEMPTS) {
          return n8nLLMRunNodeResult(blockedLLMResponse(afterDecision, request));
        }
        currentArgs = isThoughtAlignmentLoopbackDecision(afterDecision)
          ? applyThoughtAlignmentToRunNodeArgs(currentArgs, result, afterDecision.processed_content)
          : applyLoopbackToRunNodeArgs(currentArgs, afterDecision.processed_content);
        thoughtAlignmentAttempt = isThoughtAlignmentLoopbackDecision(afterDecision) ? 1 : thoughtAlignmentAttempt;
        attempts += 1;
        continue;
      }
      let finalResult = result;
      if (afterDecision.decision_type === DecisionType.MODIFY_LLM_OUTPUT) {
        finalResult = applyModifyToRunNodeResult(result, afterDecision.processed_content);
      }
      const afterBlocked = blockedResultValue(afterDecision, "llm");
      return afterBlocked
        ? n8nLLMRunNodeResult(syntheticResponsesPayload(afterBlocked.reason || afterDecision.reason, request.model))
        : finalResult;
    }
  } catch (error) {
    await guardLLMAfter({ output_text: "", output: [], status: "error", error: safeString(error) }, llmContext, {
      ...llmGuardMetadata(
        {
          event_source: "n8n_run_node_llm",
          provider: providerFromLLMNode(currentArgs.node),
          error: safeString(error && error.message ? error.message : error),
        },
        {
          supported: thoughtAlignmentSupported,
          retryAttempt: thoughtAlignmentAttempt,
        }
      ),
    });
    throw error;
  } finally {
    flushGuardAsync(llmContext);
  }
}

async function guardedRunNodeAiTool(original, target, args) {
  const { workflow, executionData, runExecutionData, runIndex, additionalData, mode, abortSignal, subNodeExecutionResults, context, node, nodeType } = args;
  const toolName = nodeNameToToolName(node);
  const invocation = aiToolInvocationFromExecutionData(executionData, node);
  const input = invocation.arguments;
  const invocationMetadata = invocation.metadata;
  const capabilities = inferCapabilitiesFromNode(node, ["ai_tool"]);
  const toolContext = enrichContextWithN8nSession({
    ...context,
    connection_type: AI_TOOL_CONNECTION_TYPE,
    tool_name: toolName,
    tool_call_id: invocationMetadata.tool_call_id || null,
    tool_description: nodeType && nodeType.description ? nodeType.description.description : "",
  }, invocationMetadata, executionData, runExecutionData, additionalData);
  let currentExecutionData = executionData;
  const beforeDecision = await guardToolBefore(toolName, input, toolContext, capabilities, invocationMetadata);
  if (beforeDecision.decision_type === DecisionType.MODIFY_TOOL_INVOKE) {
    currentExecutionData = applyModifyToExecutionData(executionData, beforeDecision.processed_content, [AI_TOOL_CONNECTION_TYPE, "main"]);
  }
  const blocked = blockedToolValue(beforeDecision, toolName);
  if (blocked) {
    flushGuardAsync(toolContext);
    return aiToolRunNodeResult(blocked);
  }
  try {
    let result = await original.call(target, workflow, currentExecutionData, runExecutionData, runIndex, additionalData, mode, abortSignal, subNodeExecutionResults);
    const afterDecision = await guardToolAfter(toolName, result && result.data !== undefined ? result.data : result, toolContext, {
      metadata: invocationMetadata,
    });
    if (afterDecision.decision_type === DecisionType.MODIFY_TOOL_RESULT) {
      result = applyModifyToRunNodeResult(result, afterDecision.processed_content);
    }
    const resultBlocked = blockedResultValue(afterDecision, toolName);
    return resultBlocked ? aiToolRunNodeResult(resultBlocked) : result;
  } catch (error) {
    await guardToolAfter(toolName, null, toolContext, { error, metadata: invocationMetadata });
    throw error;
  } finally {
    flushGuardAsync(toolContext);
  }
}

async function guardedRunNodeTool(original, target, args) {
  const { workflow, executionData, runExecutionData, runIndex, additionalData, mode, abortSignal, subNodeExecutionResults, context, node, nodeType } = args;
  const toolName = nodeNameToToolName(node);
  const input = executionData && executionData.data ? executionData.data : {};
  const capabilities = inferCapabilitiesFromNode(node, ["n8n_node"]);
  const toolContext = {
    ...context,
    tool_name: toolName,
    tool_kind: "n8n_node",
    tool_description: nodeType && nodeType.description ? nodeType.description.description : "",
  };
  let currentExecutionData = executionData;
  const beforeDecision = await guardToolBefore(toolName, input, toolContext, capabilities);
  if (beforeDecision.decision_type === DecisionType.MODIFY_TOOL_INVOKE) {
    currentExecutionData = applyModifyToExecutionData(executionData, beforeDecision.processed_content, ["main"]);
  }
  const blocked = blockedToolValue(beforeDecision, toolName);
  if (blocked) {
    return ordinaryNodeResult(blocked);
  }
  try {
    let result = await original.call(target, workflow, currentExecutionData, runExecutionData, runIndex, additionalData, mode, abortSignal, subNodeExecutionResults);
    const afterDecision = await guardToolAfter(toolName, result && result.data !== undefined ? result.data : result, toolContext);
    if (afterDecision.decision_type === DecisionType.MODIFY_TOOL_RESULT) {
      result = applyModifyToRunNodeResult(result, afterDecision.processed_content);
    }
    const resultBlocked = blockedResultValue(afterDecision, toolName);
    return resultBlocked ? ordinaryNodeResult(resultBlocked) : result;
  } catch (error) {
    await guardToolAfter(toolName, null, toolContext, { error });
    throw error;
  } finally {
    flushGuardAsync(toolContext);
  }
}

function isAiToolRunNodeExecution(node, executionData = {}) {
  return Boolean(
    (node && node.rewireOutputLogTo === AI_TOOL_CONNECTION_TYPE) ||
    (executionData && executionData.node && executionData.node.rewireOutputLogTo === AI_TOOL_CONNECTION_TYPE)
  );
}

function isRunNodeLLMExecution(node) {
  if (!node || node.disabled === true) {
    return false;
  }
  return ROOT_LLM_NODE_PATTERNS.some((pattern) => pattern.test(String(node.type || "")));
}

function providerFromLLMNode(node = {}) {
  const type = String(node.type || "").toLowerCase();
  if (type.includes("openai")) {
    return "openai";
  }
  if (type.includes("ollama")) {
    return "ollama";
  }
  return "unknown";
}

function llmRequestFromRunNodeExecution(executionData = {}, node = null, runExecutionData = null) {
  const parameters = node && isPlainObject(node.parameters) ? normalizeValue(node.parameters) : {};
  const inputJson = firstJsonFromConnectionData(executionData.data, ["main"]) || {};
  const messages = llmMessagesFromNodeParameters(parameters, inputJson, runExecutionData);
  const model = modelNameFromNodeParameters(parameters);
  const builtIns = extractN8nBuiltInTools(parameters.builtInTools);
  return {
    model,
    input: messages,
    tools: builtIns.map((type) => ({ type })),
    node_parameters: parameters,
  };
}

function llmMessagesFromNodeParameters(parameters = {}, inputJson = {}, runExecutionData = null) {
  const rawMessages = (
    parameters.responses && Array.isArray(parameters.responses.values)
      ? parameters.responses.values
      : parameters.messages && Array.isArray(parameters.messages.values)
        ? parameters.messages.values
        : []
  );
  return rawMessages.map((message) => ({
    role: message && message.role ? String(message.role) : "user",
    content: resolveN8nExpressionText(message && message.content, inputJson, runExecutionData),
  }));
}

function modelNameFromNodeParameters(parameters = {}) {
  const modelId = parameters.modelId;
  if (modelId && typeof modelId === "object" && modelId.value != null) {
    return String(modelId.value);
  }
  if (modelId != null) {
    return String(modelId);
  }
  if (parameters.model != null) {
    return String(parameters.model);
  }
  return null;
}

function extractN8nBuiltInTools(builtInTools = {}) {
  if (!builtInTools || typeof builtInTools !== "object") {
    return [];
  }
  const tools = [];
  for (const [name, value] of Object.entries(builtInTools)) {
    if (value === true || (value && typeof value === "object" && Object.keys(value).length > 0)) {
      tools.push(normalizeProviderBuiltInToolType(name));
    }
  }
  return tools;
}

function resolveN8nExpressionText(value, inputJson = {}, runExecutionData = null) {
  if (typeof value !== "string") {
    return normalizeValue(value);
  }
  const trimmed = value.trim();
  const jsonPathMatch = trimmed.match(/^={{\s*\$json\.([A-Za-z0-9_.$[\]-]+)\s*}}$/);
  if (jsonPathMatch) {
    return valueAtPath(inputJson, jsonPathMatch[1]) ?? value;
  }
  const nodeJsonPathMatch = trimmed.match(/^={{\s*\$\(['"](.+?)['"]\)\.item\.json\.([A-Za-z0-9_.$[\]-]+)\s*}}$/);
  if (nodeJsonPathMatch) {
    const sourceJson = latestJsonForRunNode(runExecutionData, nodeJsonPathMatch[1]);
    return valueAtPath(sourceJson, nodeJsonPathMatch[2]) ?? value;
  }
  return value;
}

function valueAtPath(source, pathExpression) {
  if (!source || !pathExpression) {
    return undefined;
  }
  const parts = String(pathExpression)
    .replace(/\[(\d+)\]/g, ".$1")
    .split(".")
    .filter(Boolean);
  let current = source;
  for (const part of parts) {
    if (current == null || !Object.prototype.hasOwnProperty.call(Object(current), part)) {
      return undefined;
    }
    current = current[part];
  }
  return normalizeValue(current);
}

function latestJsonForRunNode(runExecutionData, nodeName) {
  const runData = runExecutionData && runExecutionData.resultData && runExecutionData.resultData.runData;
  const nodeRuns = runData && runData[nodeName];
  if (!Array.isArray(nodeRuns) || !nodeRuns.length) {
    return null;
  }
  for (let i = nodeRuns.length - 1; i >= 0; i -= 1) {
    const data = nodeRuns[i] && nodeRuns[i].data;
    const json = firstJsonFromConnectionData(data, ["main"]);
    if (json) {
      return json;
    }
  }
  return null;
}

function llmOutputFromRunNodeResult(result) {
  const items = result && result.data;
  if (!Array.isArray(items)) {
    return result;
  }
  for (const branch of items) {
    if (!Array.isArray(branch)) {
      continue;
    }
    for (const item of branch) {
      if (item && item.json && typeof item.json === "object") {
        if (item.json.output != null) {
          return { output: normalizeValue(item.json.output) };
        }
        return normalizeValue(item.json);
      }
    }
  }
  return result;
}

function n8nLLMRunNodeResult(response) {
  return {
    data: [[{ json: { output: (response && response.output) || [], response: normalizeValue(response) } }]],
    hints: [],
  };
}

function aiToolArgumentsFromExecutionData(executionData = {}, node = null) {
  return aiToolInvocationFromExecutionData(executionData, node).arguments;
}

function aiToolInvocationFromExecutionData(executionData = {}, node = null) {
  const runtimeInput = firstJsonFromConnectionData(executionData.data, [AI_TOOL_CONNECTION_TYPE, "main"]) || {};
  return buildAiToolInvocation(runtimeInput, node);
}

function firstJsonFromConnectionData(data, connectionTypes = ["main"]) {
  if (!data || typeof data !== "object") {
    return null;
  }
  for (const connectionType of connectionTypes) {
    const branches = data[connectionType];
    if (!Array.isArray(branches)) {
      continue;
    }
    for (const branch of branches) {
      if (!Array.isArray(branch)) {
        continue;
      }
      for (const item of branch) {
        if (item && typeof item === "object" && item.json && typeof item.json === "object") {
          return normalizeValue(item.json);
        }
      }
    }
  }
  return null;
}

function buildAiToolInvocation(runtimeInput = {}, node = null) {
  const input = isPlainObject(runtimeInput) ? normalizeValue(runtimeInput) : { input: normalizeValue(runtimeInput) };
  const parameters = node && isPlainObject(node.parameters) ? normalizeValue(node.parameters) : null;
  const metadata = buildAiToolInvocationMetadata(input, parameters);
  if (parameters && Object.keys(parameters).length) {
    return { arguments: parameters, metadata };
  }
  const args = {};
  for (const [key, value] of Object.entries(input)) {
    if (!N8N_ENGINE_METADATA_KEYS.has(key)) {
      args[key] = value;
    }
  }
  return { arguments: args, metadata };
}

function buildAiToolInvocationMetadata(runtimeInput = {}, nodeParameters = null) {
  const metadata = {
    n8n_runtime_input: normalizeValue(runtimeInput || {}),
  };
  if (nodeParameters && Object.keys(nodeParameters).length) {
    metadata.n8n_node_parameters = normalizeValue(nodeParameters);
  }
  if (runtimeInput && runtimeInput.toolCallId != null) {
    metadata.tool_call_id = String(runtimeInput.toolCallId);
  }
  if (runtimeInput && runtimeInput.sessionId != null) {
    metadata.n8n_session_id = String(runtimeInput.sessionId);
  }
  if (runtimeInput && runtimeInput.action != null) {
    metadata.n8n_action = String(runtimeInput.action);
  }
  if (runtimeInput && runtimeInput.chatInput != null) {
    metadata.n8n_chat_input = normalizeValue(runtimeInput.chatInput);
  }
  if (nodeParameters && nodeParameters.toolDescription != null) {
    metadata.n8n_tool_description = String(nodeParameters.toolDescription);
  }
  return metadata;
}

function resolveNodeType(workflow, node) {
  try {
    if (workflow && workflow.nodeTypes && typeof workflow.nodeTypes.getByNameAndVersion === "function") {
      return workflow.nodeTypes.getByNameAndVersion(node.type, node.typeVersion);
    }
  } catch (_) {
    return null;
  }
  return null;
}

function shouldTreatRunNodeAsTool(node, nodeType, executionData) {
  if (!node || node.disabled === true) {
    return false;
  }
  if (isAiToolRunNodeExecution(node, executionData)) {
    return false;
  }
  if (LOGIC_NODE_TYPES.has(node.type)) {
    return false;
  }
  if (AI_TYPE_PATTERNS.some((pattern) => pattern.test(String(node.type || "")))) {
    return false;
  }
  const configuredSkips = csvEnv("AGENTGUARD_N8N_SKIP_NODE_TYPES");
  if (configuredSkips.has(String(node.type || ""))) {
    return false;
  }
  const description = nodeType && nodeType.description ? nodeType.description : {};
  if (Array.isArray(description.group) && description.group.includes("trigger")) {
    return false;
  }
  if (hasNonMainConnection(description.outputs) || hasNonMainConnection(description.inputs)) {
    return false;
  }
  return true;
}

function hasNonMainConnection(value) {
  if (!value || typeof value === "string") {
    return false;
  }
  const items = Array.isArray(value) ? value : [value];
  return items.some((item) => {
    if (typeof item === "string") {
      return item !== "main";
    }
    if (item && typeof item === "object" && item.type) {
      return item.type !== "main";
    }
    return false;
  });
}

function inferCapabilitiesFromNode(node, base = []) {
  const text = `${node && node.type ? node.type : ""} ${node && node.name ? node.name : ""}`.toLowerCase();
  const caps = new Set(base);
  if (text.includes("http") || text.includes("webhook")) {
    caps.add("network");
    caps.add("external_send");
  }
  if (text.includes("mail") || text.includes("email") || text.includes("slack") || text.includes("telegram")) {
    caps.add("external_send");
  }
  if (text.includes("code") || text.includes("execute")) {
    caps.add("code_execution");
  }
  if (text.includes("file") || text.includes("binary")) {
    caps.add("file_access");
  }
  if (text.includes("database") || text.includes("postgres") || text.includes("mysql") || text.includes("mongo")) {
    caps.add("database");
  }
  return [...caps];
}

function nodeNameToToolName(nodeOrName) {
  const name = typeof nodeOrName === "string" ? nodeOrName : nodeOrName && nodeOrName.name ? nodeOrName.name : "tool";
  let toolName = String(name).replace(/[^a-zA-Z0-9_-]+/g, "_");
  if (toolName.length > 64) {
    toolName = toolName.slice(0, 64).replace(/[_-]+$/, "");
  }
  return toolName || "tool";
}

function labelsForCapabilities(capabilities, extraTags = []) {
  const tags = [...new Set([...(capabilities || []), ...(extraTags || [])].map(String).filter(Boolean))];
  return {
    boundary: tags.includes("network") || tags.includes("external_send") ? "external" : "internal",
    sensitivity: "low",
    integrity: "trusted",
    tags,
  };
}

function inputParamsForNode(node) {
  const params = node && isPlainObject(node.parameters) ? node.parameters : {};
  const out = new Set();
  for (const item of (params.placeholderDefinitions && params.placeholderDefinitions.values) || []) {
    if (item && item.name) {
      out.add(String(item.name).replace(/^\{|\}$/g, ""));
    }
  }
  for (const key of ["parametersQuery", "parametersHeaders", "parametersBody"]) {
    for (const item of (params[key] && params[key].values) || []) {
      const provider = String(item && item.valueProvider ? item.valueProvider : "").toLowerCase();
      if (item && item.name && provider.includes("model")) {
        out.add(String(item.name));
      }
    }
  }
  if (params.inputSchema) {
    try {
      const schema = typeof params.inputSchema === "string" ? JSON.parse(params.inputSchema) : params.inputSchema;
      for (const name of Object.keys((schema && schema.properties) || {})) {
        out.add(name);
      }
    } catch (_) {
      // Ignore user-authored schema parse errors during catalog sync.
    }
  }
  return [...out];
}

function toolDescriptionForNode(node) {
  const params = node && isPlainObject(node.parameters) ? node.parameters : {};
  return String(params.toolDescription || params.description || params.resource || params.operation || node.name || node.type || "");
}

function catalogToolRecord(name, node, capabilities, { kind = "n8n_node", inputParams = null } = {}) {
  return {
    name,
    description: toolDescriptionForNode(node),
    input_params: inputParams || inputParamsForNode(node),
    capabilities,
    labels: labelsForCapabilities(capabilities, ["n8n", kind, node && node.type ? node.type : ""]),
    metadata: {
      adapter: "n8n",
      source: "n8n_workflow_catalog",
      node_id: node && node.id ? node.id : null,
      node_name: node && node.name ? node.name : null,
      node_type: node && node.type ? node.type : null,
      tool_kind: kind,
    },
  };
}

function connectionTargetsByType(connections, sourceName, type) {
  const source = connections && connections[sourceName] ? connections[sourceName] : {};
  const groups = source[type] || [];
  return groups.flatMap((group) => Array.isArray(group) ? group : []).map((item) => item && item.node).filter(Boolean);
}

function aiToolSourceNames(connections) {
  const names = new Set();
  for (const [sourceName, byType] of Object.entries(connections || {})) {
    if (byType && Array.isArray(byType.ai_tool) && byType.ai_tool.some((group) => Array.isArray(group) && group.length > 0)) {
      names.add(sourceName);
    }
  }
  return names;
}

function shouldCatalogOrdinaryNode(node) {
  if (!node || node.disabled === true) {
    return false;
  }
  const type = String(node.type || "");
  const lowerType = type.toLowerCase();
  if (lowerType.includes("trigger")) {
    return false;
  }
  if (LOGIC_NODE_TYPES.has(type)) {
    return false;
  }
  if (AI_TYPE_PATTERNS.some((pattern) => pattern.test(type))) {
    return false;
  }
  const configuredSkips = csvEnv("AGENTGUARD_N8N_SKIP_NODE_TYPES");
  if (configuredSkips.has(type)) {
    return false;
  }
  return true;
}

function extractWorkflowTools(workflow) {
  const nodes = Array.isArray(workflow.nodes) ? workflow.nodes : [];
  const connections = isPlainObject(workflow.connections) ? workflow.connections : {};
  const byName = new Map(nodes.map((node) => [node.name, node]));
  const aiSources = aiToolSourceNames(connections);
  const tools = [];
  for (const node of nodes) {
    if (!node || node.disabled === true) {
      continue;
    }
    if (aiSources.has(node.name)) {
      const capabilities = inferCapabilitiesFromNode(node, ["ai_tool"]);
      tools.push(catalogToolRecord(nodeNameToToolName(node), node, capabilities, {
        kind: "n8n_ai_tool",
      }));
      continue;
    }
    if (!shouldCatalogOrdinaryNode(node)) {
      continue;
    }
    const hasMainParent = [...byName.keys()].some((sourceName) => connectionTargetsByType(connections, sourceName, "main").includes(node.name));
    const hasMainOutput = connectionTargetsByType(connections, node.name, "main").length > 0;
    if (!hasMainParent && !hasMainOutput) {
      continue;
    }
    const capabilities = inferCapabilitiesFromNode(node, ["n8n_node"]);
    tools.push(catalogToolRecord(nodeNameToToolName(node), node, capabilities, {
      kind: "n8n_node",
    }));
  }
  return mergeToolsByName(tools);
}

function mergeToolsByName(tools) {
  const byName = new Map();
  for (const tool of tools) {
    if (!tool || !tool.name) {
      continue;
    }
    if (!byName.has(tool.name)) {
      byName.set(tool.name, tool);
      continue;
    }
    const existing = byName.get(tool.name);
    existing.input_params = [...new Set([...(existing.input_params || []), ...(tool.input_params || [])])];
    const tags = [...new Set([
      ...((existing.labels && existing.labels.tags) || []),
      ...((tool.labels && tool.labels.tags) || []),
    ].map(String).filter(Boolean))];
    existing.labels = {
      boundary: existing.labels && existing.labels.boundary === "external" || tool.labels && tool.labels.boundary === "external"
        ? "external"
        : "internal",
      sensitivity: existing.labels && existing.labels.sensitivity || tool.labels && tool.labels.sensitivity || "low",
      integrity: existing.labels && existing.labels.integrity || tool.labels && tool.labels.integrity || "trusted",
      tags,
    };
  }
  return [...byName.values()];
}

function stableFingerprint(payload) {
  return crypto.createHash("sha256").update(JSON.stringify(payload)).digest("hex");
}

function catalogSessionKey(workflowId, version) {
  const seed = process.env.AGENTGUARD_N8N_CATALOG_SESSION_KEY;
  if (seed) {
    return seed;
  }
  return `sk-n8n-catalog-${workflowId}-${version || "published"}`;
}

function catalogContextForWorkflow(workflow, tools) {
  const version = workflow.activeVersionId || workflow.versionId || workflow.publishedVersionId || workflow.versionCounter || "published";
  const sessionKey = catalogSessionKey(workflow.id, version);
  const identity = cacheWorkflowIdentity(workflow);
  cacheWorkflowNodes(workflow);
  const accountEmail = identity.user_email || null;
  return {
    sessionKey,
    context: new RuntimeContext({
      session_id: `n8n-workflow-catalog:${workflow.id}:${version}`,
      agent_id: workflowAgentId(workflow.id),
      user_id: identity.user_id || process.env.AGENTGUARD_USER_ID || null,
      policy: process.env.AGENTGUARD_POLICY || null,
      environment: "n8n",
      metadata: {
        adapter: "n8n",
        catalog_sync: true,
        workflow_id: workflow.id,
        workflow_name: workflow.name,
        workflow_version: version,
        workflow_active: Boolean(workflow.active),
        published_version_id: workflow.publishedVersionId || null,
        tool_count: tools.length,
        client_session_key: sessionKey,
        external_provider: "n8n",
        external_account_email: accountEmail,
        external_agent_id: workflow.id,
        display_agent_id: workflowAgentId(workflow.id),
        n8n_user_id: identity.user_id || null,
        n8n_user_email: accountEmail,
        n8n_user_name: identity.user_name || null,
        n8n_user_source: identity.user_source || null,
        n8n_project_id: identity.project_id || null,
        n8n_project_name: identity.project_name || null,
      },
    }),
  };
}

async function syncWorkflowCatalog(workflow) {
  if (!workflow || !workflow.id || !workflowAllowed(workflow.id)) {
    return null;
  }
  const tools = extractWorkflowTools(workflow);
  const version = workflow.activeVersionId || workflow.versionId || workflow.publishedVersionId || workflow.versionCounter || "published";
  const fingerprint = stableFingerprint({
    workflow_id: workflow.id,
    version,
    updated_at: workflow.updatedAt,
    identity: normalizeWorkflowIdentity(workflow),
    tools,
  });
  if (CATALOG_FINGERPRINTS.get(workflow.id) === fingerprint) {
    return { workflow_id: workflow.id, skipped: true, reason: "unchanged", tool_count: tools.length };
  }
  const { context, sessionKey } = catalogContextForWorkflow(workflow, tools);
  const remote = new RemoteGuardClient(process.env.AGENTGUARD_SERVER_URL || null, {
    api_key: process.env.AGENTGUARD_API_KEY || null,
    session_id: context.session_id,
    agent_id: context.agent_id,
    user_id: context.user_id,
    session_key: sessionKey,
    timeout_s: numberEnv("AGENTGUARD_N8N_CATALOG_SYNC_TIMEOUT_S", 5.0),
    retries: numberEnv("AGENTGUARD_N8N_CATALOG_SYNC_RETRIES", 1),
  });
  if (!remote.enabled) {
    return { workflow_id: workflow.id, skipped: true, reason: "server_url_missing", tool_count: tools.length };
  }
  const registration = await registerN8nWorkflowAgent(
    registrationContextFromWorkflow(workflow, tools),
    { remote, tools, reason: "catalog_sync" }
  ).catch((error) => {
    log("warn", "failed to register n8n workflow agent", error && error.message ? error.message : String(error));
    return null;
  });
  if (registration && registration.agent && registration.agent.agent_id) {
    const registeredAgent = registration.agent;
    context.agent_id = registeredAgent.agent_id;
    remote.agent_id = registeredAgent.agent_id;
    context.metadata.agentguard_agent_id = registeredAgent.agent_id;
    context.metadata.agent_identity_code = registeredAgent.agent_identity_code || null;
    context.metadata.agent_public_key_thumbprint = registeredAgent.public_key_thumbprint || null;
    context.metadata.agentguard_user_bound = Boolean(registration.user_agent && registration.user_agent.bound);
  }
  await remote.register_session(context);
  const result = await remote.sync_tools(context, tools);
  CATALOG_FINGERPRINTS.set(workflow.id, fingerprint);
  return {
    workflow_id: workflow.id,
    agent_id: context.agent_id,
    tool_count: result.tool_count || tools.length,
  };
}

function catalogSyncEnabled() {
  return envFlag("AGENTGUARD_N8N_CATALOG_SYNC_ENABLED", true);
}

function catalogSyncProcessAllowed() {
  const role = String(process.env.AGENTGUARD_N8N_CATALOG_SYNC_PROCESS || "main").toLowerCase();
  if (role === "all") {
    return true;
  }
  const argv = process.argv.join(" ");
  const isTaskRunner = argv.includes("@n8n/task-runner") || argv.includes("task-runner");
  return role === "task-runner" ? isTaskRunner : !isTaskRunner;
}

function n8nDatabasePath() {
  return process.env.AGENTGUARD_N8N_DB_PATH || path.join(process.env.HOME || "/home/node", ".n8n", "database.sqlite");
}

function configureN8nKeyDir() {
  if (process.env.AGENTGUARD_AGENT_KEY_DIR) {
    return;
  }
  process.env.AGENTGUARD_AGENT_KEY_DIR = path.join(path.dirname(n8nDatabasePath()), "agentguard_keys");
}

function openSqliteReadOnly() {
  if (typeof global.__agentguardN8nOpenSqliteReadOnlyForTests === "function") {
    return global.__agentguardN8nOpenSqliteReadOnlyForTests();
  }
  const sqlite3 = requireFromN8n("sqlite3").verbose();
  return new sqlite3.Database(n8nDatabasePath(), sqlite3.OPEN_READONLY);
}

function requireFromN8n(moduleName) {
  const errors = [];
  try {
    return require(moduleName);
  } catch (error) {
    errors.push(error);
  }
  for (const base of [
    process.cwd(),
    "/usr/local/lib/node_modules/n8n",
    process.env.N8N_PACKAGE_DIR,
  ].filter(Boolean)) {
    try {
      return createRequire(path.join(base, "package.json"))(moduleName);
    } catch (error) {
      errors.push(error);
    }
  }
  const message = errors.map((error) => error && error.message ? error.message : String(error)).join("; ");
  throw new Error(`${moduleName} module unavailable: ${message}`);
}

function dbAll(db, sql, params = []) {
  return new Promise((resolve, reject) => {
    db.all(sql, params, (error, rows) => {
      if (error) {
        reject(error);
      } else {
        resolve(rows || []);
      }
    });
  });
}

function closeDb(db) {
  return new Promise((resolve) => db.close(() => resolve()));
}

function parseJsonField(raw, fallback) {
  if (raw == null || raw === "") {
    return fallback;
  }
  if (typeof raw !== "string") {
    return raw;
  }
  try {
    return JSON.parse(raw);
  } catch (_) {
    return fallback;
  }
}

async function scanPublishedWorkflows() {
  const db = openSqliteReadOnly();
  try {
    const query = workflowCatalogQuery();
    const rows = await dbAll(db, query.sql, query.params);
    return rows.map((row) => ({
      ...row,
      active: row.active === true || row.active === 1 || row.active === "1",
      nodes: parseJsonField(row.nodes, []),
      connections: parseJsonField(row.connections, {}),
    })).map((workflow) => {
      cacheWorkflowIdentity(workflow);
      cacheWorkflowNodes(workflow);
      return workflow;
    });
  } finally {
    await closeDb(db);
  }
}

function workflowCatalogQuery(whereClause = "", params = []) {
  return {
    sql: `
      SELECT
        w.id,
        w.name,
        w.active,
        w.nodes,
        w.connections,
        w.versionId,
        w.activeVersionId,
        w.versionCounter,
        w.updatedAt,
        pv.publishedVersionId,
        sw.projectId AS projectId,
        p.name AS projectName,
        p.creatorId AS ownerUserId,
        u.email AS ownerUserEmail,
        u.firstName AS ownerUserFirstName,
        u.lastName AS ownerUserLastName
      FROM workflow_entity w
      LEFT JOIN workflow_published_version pv ON pv.workflowId = w.id
      LEFT JOIN shared_workflow sw ON sw.workflowId = w.id AND sw.role = 'workflow:owner'
      LEFT JOIN project p ON p.id = sw.projectId
      LEFT JOIN user u ON u.id = p.creatorId
      WHERE COALESCE(w.isArchived, 0) = 0
        AND (COALESCE(w.active, 0) = 1 OR pv.workflowId IS NOT NULL)
        ${whereClause}
    `,
    params,
  };
}

async function loadWorkflowIdentityById(workflowId) {
  const workflowKey = optionalString(workflowId);
  if (!workflowKey) {
    return {};
  }
  const cached = workflowIdentityForWorkflowId(workflowKey);
  if (cached.user_email || cached.user_id || cached.project_id) {
    return cached;
  }
  const db = openSqliteReadOnly();
  try {
    const query = workflowCatalogQuery("AND w.id = ?", [workflowKey]);
    const rows = await dbAll(db, query.sql, query.params);
    const workflow = rows && rows[0] ? rows[0] : null;
    if (!workflow) {
      return {};
    }
    return cacheWorkflowIdentity(workflow);
  } finally {
    await closeDb(db);
  }
}

async function hydrateWorkflowIdentityContext(context = {}) {
  if (!context || typeof context !== "object") {
    return context;
  }
  const existingEmail = n8nAccountEmail(context);
  if (existingEmail) {
    return context;
  }
  const workflowId = optionalString(context.workflow_id);
  if (!workflowId) {
    return context;
  }
  const identity = await loadWorkflowIdentityById(workflowId);
  if (!identity || typeof identity !== "object") {
    return context;
  }
  if (!context.user_id && identity.user_id) {
    context.user_id = identity.user_id;
  }
  if (!context.n8n_user_id && identity.user_id) {
    context.n8n_user_id = identity.user_id;
  }
  if (!context.n8n_user_email && identity.user_email) {
    context.n8n_user_email = identity.user_email;
  }
  if (!context.n8n_user_name && identity.user_name) {
    context.n8n_user_name = identity.user_name;
  }
  if (!context.n8n_user_source && identity.user_source) {
    context.n8n_user_source = identity.user_source;
  }
  if (!context.n8n_project_id && identity.project_id) {
    context.n8n_project_id = identity.project_id;
  }
  if (!context.n8n_project_name && identity.project_name) {
    context.n8n_project_name = identity.project_name;
  }
  return context;
}

async function syncPublishedWorkflowCatalogOnce() {
  const workflows = await scanPublishedWorkflows();
  const synced = [];
  for (const workflow of workflows) {
    try {
      const result = await syncWorkflowCatalog(workflow);
      if (result) {
        synced.push(result);
      }
    } catch (error) {
      log("warn", `catalog sync failed for workflow ${workflow && workflow.id ? workflow.id : "unknown"}`, error && error.message ? error.message : String(error));
    }
  }
  return { workflow_count: workflows.length, synced };
}

function startCatalogSync() {
  if (!catalogSyncEnabled()) {
    return { enabled: false, reason: "disabled" };
  }
  if (!process.env.AGENTGUARD_SERVER_URL) {
    return { enabled: false, reason: "server_url_missing" };
  }
  if (!catalogSyncProcessAllowed()) {
    return { enabled: false, reason: "process_not_allowed" };
  }
  if (catalogSyncStarted) {
    return { enabled: true, started: false, reason: "already_started" };
  }
  catalogSyncStarted = true;
  const initialDelayMs = Math.max(0, numberEnv("AGENTGUARD_N8N_CATALOG_INITIAL_DELAY_S", 2.0) * 1000);
  const intervalMs = Math.max(0, numberEnv("AGENTGUARD_N8N_CATALOG_SYNC_INTERVAL_S", 5.0) * 1000);
  setTimeout(() => {
    syncPublishedWorkflowCatalogOnce()
      .then((result) => log("warn", "catalog sync complete", result))
      .catch((error) => log("warn", "catalog sync failed", error && error.message ? error.message : String(error)));
    if (intervalMs > 0) {
      setInterval(() => {
        syncPublishedWorkflowCatalogOnce()
          .then((result) => {
            const changed = (result.synced || []).filter((item) => !item.skipped);
            if (changed.length) {
              log("warn", "catalog sync updated", { workflow_count: result.workflow_count, synced: changed });
            }
          })
          .catch((error) => log("warn", "catalog sync failed", error && error.message ? error.message : String(error)));
      }, intervalMs).unref();
    }
  }, initialDelayMs).unref();
  return { enabled: true, started: true, interval_s: intervalMs / 1000 };
}

function primeWorkflowIdentityCache() {
  const delayMs = Math.max(0, numberEnv("AGENTGUARD_N8N_IDENTITY_INITIAL_DELAY_S", 0.5) * 1000);
  setTimeout(() => {
    scanPublishedWorkflows()
      .catch((error) => log("warn", "workflow identity cache refresh failed", error && error.message ? error.message : String(error)));
  }, delayMs).unref();
}

function flushGuardAsync(context) {
  try {
    const guard = getGuard(enrichContextWithN8nSession(context));
    if (guard && guard.runtime && typeof guard.runtime.sync_local_cache_async === "function") {
      guard.runtime.sync_local_cache_async({ reason: "round_complete" });
    }
  } catch (_) {
    // Flush is best-effort.
  }
}

function patchLoadedModule(request, resolved, moduleExports) {
  try {
    if (request === "@langchain/openai" || (resolved && resolved.includes("/@langchain/openai/"))) {
      patchOpenAI(moduleExports);
    }
    if (
      request === "n8n-core" ||
      (resolved && (resolved.endsWith("/n8n-core/dist/index.js") || resolved.endsWith("/execution-engine/workflow-execute.js")))
    ) {
      patchN8nCore(moduleExports);
    }
    if (resolved && resolved.includes("/@n8n/n8n-nodes-langchain/") && resolved.includes("/utils/agent-execution/")) {
      patchAgentExecutionModule(moduleExports, resolved);
    }
    if (
      resolved &&
      resolved.includes("/@n8n/n8n-nodes-langchain/") &&
      resolved.endsWith("/utils/helpers.js")
    ) {
      patchConnectedToolsHelpers(moduleExports);
    }
    if (
      resolved &&
      resolved.includes("/@n8n/n8n-nodes-langchain/") &&
      resolved.endsWith("/nodes/agents/Agent/agents/ToolsAgent/common.js")
    ) {
      patchAgentToolsCommon(moduleExports);
    }
    if (
      resolved &&
      resolved.includes("/@n8n/n8n-nodes-langchain/") &&
      resolved.endsWith("/nodes/vendors/OpenAi/v1/actions/text/message.operation.js")
    ) {
      patchOpenAiTextMessageOperation(moduleExports, resolved);
    }
  } catch (error) {
    log("warn", `failed to patch ${request}`, error && error.stack ? error.stack : String(error));
  }
}

function installModuleLoadHook() {
  if (Module[LOADER_PATCHED]) {
    return;
  }
  const originalLoad = Module._load;
  Module._load = function agentguardN8nLoad(request, parent, isMain) {
    let resolved = null;
    try {
      resolved = Module._resolveFilename(request, parent, isMain);
    } catch (_) {
      // Keep Node's original resolution behavior.
    }
    const moduleExports = originalLoad.apply(this, arguments);
    patchLoadedModule(request, resolved, moduleExports);
    return moduleExports;
  };
  Module[LOADER_PATCHED] = true;
}

function installN8nAdapter() {
  if (!shouldUseAdapter()) {
    log("info", "adapter disabled by AGENTGUARD_ENABLED");
    return { installed: false, reason: "disabled" };
  }
  configureN8nKeyDir();
  installModuleLoadHook();
  for (const request of ["@langchain/openai", "n8n-core"]) {
    try {
      const moduleExports = require(request);
      patchLoadedModule(request, require.resolve(request), moduleExports);
    } catch (_) {
      // n8n package paths may not be resolvable from the bootstrap module; the load hook will catch them.
    }
  }
  primeWorkflowIdentityCache();
  const catalogSync = startCatalogSync();
  log("warn", "adapter installed");
  return { installed: true, catalog_sync: catalogSync };
}

process.once("beforeExit", () => {
  for (const guard of GUARDS.values()) {
    if (guard && guard.runtime && typeof guard.runtime.sync_local_cache_now === "function") {
      guard.runtime.sync_local_cache_now({ reason: "process_before_exit" }).catch(() => {});
    }
  }
});

module.exports = {
  installN8nAdapter,
  _private: {
    applyLoopbackToNodeParameters,
    applyLoopbackToResponsesRequest,
    applyLoopbackToRunNodeArgs,
    applyThoughtAlignmentToNodeParameters,
    applyThoughtAlignmentToResponsesRequest,
    applyThoughtAlignmentToRunNodeArgs,
    applyModifyToNodeParameters,
    applyModifyToResponsesRequest,
    applyModifyToRunNodeArgs,
    applyModifyToResponsesResult,
    applyModifyToRunNodeResult,
    applyModifyToToolInput,
    applyModifyToExecutionData,
    applyModifyToEngineActionResult,
    extractProviderBuiltInTools,
    extractWorkflowTools,
    fetchRuntimePluginConfig,
    guardOptions,
    hasNonMainConnection,
    cacheWorkflowIdentity,
    cacheWorkflowNodes,
    catalogContextForWorkflow,
    enrichContextWithN8nSession,
    enrichContextWithRegistration,
    ensureN8nRuntimeAuth,
    getGuardForRuntime,
    hydrateWorkflowIdentityContext,
    buildConnectedToolSourceContext,
    eventMetadata,
    inferCapabilitiesFromNode,
    loadWorkflowIdentityById,
    workflowIdentityForWorkflowId,
    workflowNodeForTool,
    n8nSessionIdFromSources,
    aiToolArgumentsFromExecutionData,
    aiToolInvocationFromExecutionData,
    buildRunNodeContext,
    firstJsonFromConnectionData,
    isAiToolRunNodeExecution,
    isRunNodeLLMExecution,
    llmOutputFromRunNodeResult,
    llmRequestFromRunNodeExecution,
    denormalizeResponsesInput,
    buildThoughtAlignmentMetadata,
    isThoughtAlignmentLoopbackDecision,
    normalizeLLMOutput,
    normalizeResponsesInput,
    nodeNameToToolName,
    isOpenAiTextMessageOperationNode,
    openAiTextMessageOutput,
    patchOpenAiTextMessageOperation,
    pluginConfigFromEnv,
    patchAgentToolsCommon,
    patchConnectedToolsHelpers,
    patchOpenAI,
    patchN8nCore,
    scanPublishedWorkflows,
    setOpenSqliteReadOnlyForTests(factory = null) {
      global.__agentguardN8nOpenSqliteReadOnlyForTests = typeof factory === "function" ? factory : null;
    },
    sourceNodeForToolInvocation,
    registerN8nWorkflowAgent,
    supportsThoughtAlignmentForResponsesRequest,
    supportsThoughtAlignmentForRunNode,
    syncWorkflowCatalog,
    syncPublishedWorkflowCatalogOnce,
    shouldTreatRunNodeAsTool,
    shouldCatalogOrdinaryNode,
    wrapConnectedTool,
  },
};

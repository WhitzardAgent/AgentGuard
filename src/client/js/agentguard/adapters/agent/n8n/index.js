"use strict";

const Module = require("module");
const { AsyncLocalStorage } = require("async_hooks");
const crypto = require("crypto");
const path = require("path");
const { createRequire } = require("module");

const { AgentGuard } = require("../../../guard");
const ev = require("../../../schemas/events");
const { RuntimeContext } = require("../../../schemas/context");
const { DecisionType } = require("../../../schemas/decisions");
const { ToolMetadata } = require("../../../tools/metadata");
const { RemoteGuardClient } = require("../../../u_guard/remote_client");

const PATCHED = Symbol.for("agentguard.n8n.patched");
const LOADER_PATCHED = Symbol.for("agentguard.n8n.loader_patched");
const TOOL_INVOKE_PATCHED = Symbol.for("agentguard.n8n.tool_invoke_patched");
const TOOL_SOURCE_CONTEXT = Symbol.for("agentguard.n8n.tool_source_context");
const ALS = new AsyncLocalStorage();
const GUARDS = new Map();
const REPORTED_TOOLS = new Set();
const CATALOG_FINGERPRINTS = new Map();
const WORKFLOW_IDENTITY_CACHE = new Map();
const WORKFLOW_NODE_CACHE = new Map();
const MAX_GUARDS = 128;
let catalogSyncStarted = false;
const AI_TOOL_CONNECTION_TYPE = "ai_tool";

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
  const execution = context.execution_id || `pid_${process.pid}`;
  return `n8n:${workflow}:execution:${execution}`;
}

function agentId(context) {
  const workflow = context.workflow_id || context.workflow_name || "unknown_workflow";
  return `n8n:${workflow}`;
}

function workflowAgentId(workflowId) {
  return `n8n:${workflowId || "unknown_workflow"}`;
}

function normalizeWorkflowIdentity(workflow = {}) {
  const userId = optionalString(workflow.user_id || workflow.ownerUserId || workflow.owner_user_id || workflow.creatorId);
  const userEmail = optionalString(workflow.user_email || workflow.ownerUserEmail || workflow.owner_user_email || workflow.email);
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

function guardOptions(context) {
  return {
    server_url: process.env.AGENTGUARD_SERVER_URL || null,
    api_key: process.env.AGENTGUARD_API_KEY || null,
    policy: process.env.AGENTGUARD_POLICY || null,
    user_id: context.user_id || process.env.AGENTGUARD_USER_ID || null,
    agent_id: agentId(context),
    environment: "n8n",
  };
}

function getGuard(context = {}) {
  const key = stableContextId(context);
  if (GUARDS.has(key)) {
    const guard = GUARDS.get(key);
    refreshGuardMetadata(guard, context);
    return guard;
  }
  if (GUARDS.size >= MAX_GUARDS) {
    const [oldestKey, oldestGuard] = GUARDS.entries().next().value;
    GUARDS.delete(oldestKey);
    if (oldestGuard && typeof oldestGuard.close === "function") {
      oldestGuard.close().catch(() => {});
    }
  }
  const guard = new AgentGuard(key, guardOptions(context));
  refreshGuardMetadata(guard, context);
  GUARDS.set(key, guard);
  return guard;
}

function refreshGuardMetadata(guard, context = {}) {
  if (!guard || !guard.context) {
    return;
  }
  guard.context.environment = "n8n";
  const userId = context.user_id || process.env.AGENTGUARD_USER_ID || null;
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
    n8n_user_id: context.n8n_user_id || null,
    n8n_user_email: context.n8n_user_email || null,
    n8n_user_name: context.n8n_user_name || null,
    n8n_user_source: context.n8n_user_source || null,
    n8n_project_id: context.n8n_project_id || null,
    n8n_project_name: context.n8n_project_name || null,
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
  const workflowIdentity = workflowIdentityForWorkflowId(workflowId);
  const executionId = (
    additionalData && (additionalData.executionId || additionalData.execution_id)
  ) || (
    runExecutionData && runExecutionData.executionData &&
    (runExecutionData.executionData.id || runExecutionData.executionData.executionId)
  ) || (
    runExecutionData && runExecutionData.resultData && runExecutionData.resultData.runId
  );
  return {
    workflow_id: workflowId == null ? null : String(workflowId),
    workflow_name: workflow && workflow.name ? String(workflow.name) : null,
    execution_id: executionId == null ? null : String(executionId),
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
    return syntheticBlockedStream(reason);
  }
  return syntheticResponsesPayload(reason, request && request.model);
}

async function* syntheticBlockedStream(reason) {
  yield {
    type: "response.output_text.delta",
    delta: `[AgentGuard blocked] ${reason}`,
  };
  yield {
    type: "response.completed",
    response: syntheticResponsesPayload(reason),
  };
}

function syntheticResponsesPayload(reason, model = null) {
  const text = `[AgentGuard blocked] ${reason}`;
  return {
    id: `agentguard_blocked_${Date.now()}`,
    object: "response",
    created_at: Math.floor(Date.now() / 1000),
    model: model || "agentguard-blocked",
    status: "completed",
    output_text: text,
    output: [
      {
        id: "agentguard_blocked_message",
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
    ],
    usage: {
      input_tokens: 0,
      output_tokens: 0,
      total_tokens: 0,
    },
  };
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
  const guard = getGuard(context);
  const builtIns = extractProviderBuiltInTools(request && request.tools);
  const metadata = eventMetadata(context, {
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
  const guard = getGuard(context);
  const metadata = eventMetadata(context, {
    event_source: "langchain_openai_responses",
    ...(extra || {}),
  });
  const result = await guard.runtime.guard(ev.llm_output(guard.context, normalizeLLMOutput(output), metadata), {
    phase: "after",
  });
  return result.decision;
}

async function guardToolBefore(toolName, args, context, capabilities = [], extraMetadata = {}) {
  const guard = getGuard(context);
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
      metadata: eventMetadata(context, extraMetadata),
    })
  );
  return result.decision;
}

async function guardToolAfter(toolName, output, context, { error = null, metadata = {} } = {}) {
  const guard = getGuard(context);
  const result = await guard.runtime.guard(
    ev.tool_result(guard.context, toolName, normalizeValue(output), {
      error: error ? safeString(error) : null,
      metadata: eventMetadata(context, metadata),
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
    const context = toolContext(tool, activeSourceContext);
    if (!matchesConfiguredFilters(context)) {
      return original.call(this, input, ...rest);
    }
    const invocation = buildAiToolInvocation(input || {}, sourceNodeForToolInvocation(activeSourceContext));
    const capabilities = inferCapabilitiesFromNode(
      { type: context.node_type || (tool.metadata && tool.metadata.sourceNodeType), name: context.node_name || toolName },
      ["ai_tool"]
    );
    const beforeDecision = await guardToolBefore(toolName, invocation.arguments, context, capabilities, invocation.metadata);
    const blocked = blockedToolValue(beforeDecision, toolName);
    if (blocked) {
      flushGuardAsync(context);
      return blocked;
    }
    try {
      const output = await original.call(this, input, ...rest);
      const afterDecision = await guardToolAfter(toolName, output, context, { metadata: invocation.metadata });
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
      const context = currentContext({
        llm_provider: "openai",
        llm_class: key,
      });
      if (!matchesConfiguredFilters(context)) {
        return original.call(this, request, requestOptions);
      }
      const beforeDecision = await guardLLMBefore(request, context);
      const beforeBlocked = blockedToolValue(beforeDecision, "llm");
      if (beforeBlocked) {
        return blockedLLMResponse(beforeDecision, request);
      }
      const raw = await original.call(this, request, requestOptions);
      if (request && request.stream && raw && typeof raw[Symbol.asyncIterator] === "function") {
        return wrapOpenAIStream(raw, context);
      }
      const afterDecision = await guardLLMAfter(raw, context);
      const afterBlocked = blockedResultValue(afterDecision, "llm");
      return afterBlocked ? syntheticResponsesPayload(afterBlocked.reason || afterDecision.reason, request && request.model) : raw;
    };
    Klass.prototype.completionWithRetry[PATCHED] = true;
  }
}

async function* wrapOpenAIStream(iterable, context) {
  const chunks = [];
  try {
    for await (const item of iterable) {
      chunks.push(normalizeValue(item));
      yield item;
    }
    await guardLLMAfter({ output_text: "", output: chunks, status: "stream_completed" }, context, {
      stream: true,
    });
  } catch (error) {
    await guardLLMAfter({ output_text: "", output: chunks, status: "stream_error" }, context, {
      stream: true,
      error: safeString(error && error.message ? error.message : error),
    });
    throw error;
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
    const beforeDecision = await guardToolBefore(toolName, args, context, capabilities, invocationMetadata);
    const blocked = blockedToolValue(beforeDecision, toolName);
    if (blocked) {
      return aiToolResult(action, blocked);
    }
    try {
      let result;
      if (tool && typeof tool.invoke === "function") {
        const input = { ...(args || {}) };
        delete input.tool;
        result = await tool.invoke(input);
        result = {
          action,
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
        result = await original.call(this, node, action, tools);
      }
      const output = result && result.data && result.data.data ? result.data.data : result;
      const afterDecision = await guardToolAfter(toolName, output, context, { metadata: invocationMetadata });
      const resultBlocked = blockedResultValue(afterDecision, toolName);
      return resultBlocked ? aiToolResult(action, resultBlocked) : result;
    } catch (error) {
      await guardToolAfter(toolName, null, context, { error, metadata: invocationMetadata });
      return aiToolResult(action, null, "error", error);
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
  const { workflow, executionData, runExecutionData, runIndex, additionalData, mode, abortSignal, subNodeExecutionResults, context, node } = args;
  const request = llmRequestFromRunNodeExecution(executionData, node, runExecutionData);
  const llmContext = {
    ...context,
    llm_node: true,
    llm_provider: providerFromLLMNode(node),
  };
  const beforeDecision = await guardLLMBefore(request, llmContext, {
    event_source: "n8n_run_node_llm",
    provider: providerFromLLMNode(node),
    node_parameters: normalizeValue((node && node.parameters) || {}),
  });
  const beforeBlocked = blockedToolValue(beforeDecision, "llm");
  if (beforeBlocked) {
    flushGuardAsync(llmContext);
    return n8nLLMRunNodeResult(blockedLLMResponse(beforeDecision, request));
  }
  try {
    const result = await original.call(target, workflow, executionData, runExecutionData, runIndex, additionalData, mode, abortSignal, subNodeExecutionResults);
    const output = llmOutputFromRunNodeResult(result);
    const afterDecision = await guardLLMAfter(output, llmContext, {
      event_source: "n8n_run_node_llm",
      provider: providerFromLLMNode(node),
      node_parameters: normalizeValue((node && node.parameters) || {}),
    });
    const afterBlocked = blockedResultValue(afterDecision, "llm");
    return afterBlocked ? n8nLLMRunNodeResult(syntheticResponsesPayload(afterBlocked.reason || afterDecision.reason, request.model)) : result;
  } catch (error) {
    await guardLLMAfter({ output_text: "", output: [], status: "error", error: safeString(error) }, llmContext, {
      event_source: "n8n_run_node_llm",
      provider: providerFromLLMNode(node),
      error: safeString(error && error.message ? error.message : error),
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
  const toolContext = {
    ...context,
    connection_type: AI_TOOL_CONNECTION_TYPE,
    tool_name: toolName,
    tool_call_id: invocationMetadata.tool_call_id || null,
    tool_description: nodeType && nodeType.description ? nodeType.description.description : "",
  };
  const beforeDecision = await guardToolBefore(toolName, input, toolContext, capabilities, invocationMetadata);
  const blocked = blockedToolValue(beforeDecision, toolName);
  if (blocked) {
    flushGuardAsync(toolContext);
    return aiToolRunNodeResult(blocked);
  }
  try {
    const result = await original.call(target, workflow, executionData, runExecutionData, runIndex, additionalData, mode, abortSignal, subNodeExecutionResults);
    const afterDecision = await guardToolAfter(toolName, result && result.data !== undefined ? result.data : result, toolContext, {
      metadata: invocationMetadata,
    });
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
  const beforeDecision = await guardToolBefore(toolName, input, toolContext, capabilities);
  const blocked = blockedToolValue(beforeDecision, toolName);
  if (blocked) {
    return ordinaryNodeResult(blocked);
  }
  try {
    const result = await original.call(target, workflow, executionData, runExecutionData, runIndex, additionalData, mode, abortSignal, subNodeExecutionResults);
    const afterDecision = await guardToolAfter(toolName, result && result.data !== undefined ? result.data : result, toolContext);
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
        n8n_user_id: identity.user_id || null,
        n8n_user_email: identity.user_email || null,
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

function openSqliteReadOnly() {
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
    const rows = await dbAll(db, `
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
    `);
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
    const guard = getGuard(context);
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
    extractProviderBuiltInTools,
    extractWorkflowTools,
    hasNonMainConnection,
    cacheWorkflowIdentity,
    cacheWorkflowNodes,
    catalogContextForWorkflow,
    buildConnectedToolSourceContext,
    eventMetadata,
    inferCapabilitiesFromNode,
    workflowIdentityForWorkflowId,
    workflowNodeForTool,
    aiToolArgumentsFromExecutionData,
    aiToolInvocationFromExecutionData,
    buildRunNodeContext,
    firstJsonFromConnectionData,
    isAiToolRunNodeExecution,
    isRunNodeLLMExecution,
    llmOutputFromRunNodeResult,
    llmRequestFromRunNodeExecution,
    normalizeLLMOutput,
    normalizeResponsesInput,
    nodeNameToToolName,
    patchAgentToolsCommon,
    patchConnectedToolsHelpers,
    scanPublishedWorkflows,
    sourceNodeForToolInvocation,
    syncPublishedWorkflowCatalogOnce,
    shouldTreatRunNodeAsTool,
    shouldCatalogOrdinaryNode,
    wrapConnectedTool,
  },
};

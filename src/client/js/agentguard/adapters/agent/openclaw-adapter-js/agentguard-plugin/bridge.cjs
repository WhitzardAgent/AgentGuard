"use strict";

const {
  AuditLogger,
  AuditRecorder,
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

const PRE_GUARD_PHASES = new Set(["tool_before", "llm_before"]);

function normalizePluginConfig(raw = {}) {
  const config = raw && typeof raw === "object" ? { ...raw } : {};
  const serverUrl = asNonEmptyString(config.serverUrl);
  return {
    serverUrl,
    apiKey: resolveApiKey(config),
    policy: asNonEmptyString(config.policy) || DEFAULT_POLICY,
    auditPath: asNonEmptyString(config.auditPath),
    phases: normalizePhaseConfig(config.phases),
    toolCapabilities: normalizeToolCapabilities(config.toolCapabilities),
    identity: normalizeIdentity(config.identity),
    remoteUnavailableMode:
      asNonEmptyString(config.remoteUnavailableMode) || DEFAULT_REMOTE_UNAVAILABLE_MODE,
    windowSize: asPositiveInteger(config.windowSize, DEFAULT_WINDOW_SIZE),
    hasRemoteConfigured: Boolean(serverUrl),
  };
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

function normalizeIdentity(value) {
  const identity = value && typeof value === "object" && !Array.isArray(value) ? { ...value } : {};
  return {
    userId: asNonEmptyString(identity.userId),
    userIdFrom: asNonEmptyString(identity.userIdFrom) || "accountId",
    agentId: asNonEmptyString(identity.agentId),
    agentIdFrom: asNonEmptyString(identity.agentIdFrom) || "agentId",
    environment: asNonEmptyString(identity.environment),
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
    return event.messages;
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
  if (typeof event.content === "string") {
    return event.content;
  }
  if (Array.isArray(event.assistantTexts)) {
    return event.assistantTexts.filter((item) => typeof item === "string").join("\n");
  }
  if (typeof event.text === "string") {
    return event.text;
  }
  if (event.content != null) {
    return String(event.content);
  }
  return "";
}

class AgentGuardOpenClawBridge {
  constructor(options = {}) {
    this.pluginId = options.pluginId || "agentguard";
    this.config = normalizePluginConfig(options.pluginConfig || {});
    this.logger = options.logger || console;
    this.sessions = new Map();
  }

  getState(identityContext) {
    const context = buildRuntimeContext(this.config, identityContext);
    const sessionKey = context.metadata.client_session_key || context.session_id;
    let state = this.sessions.get(sessionKey);
    if (state) {
      state.context = context;
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
    };
    this.sessions.set(sessionKey, state);
    return state;
  }

  clearSession(sessionKey) {
    if (sessionKey) {
      this.sessions.delete(sessionKey);
    }
  }

  clearAll() {
    this.sessions.clear();
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
      decision.decision_type === DecisionType.ASK_USER ||
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
    const runtimeEvent = createRuntimeEvent({
      eventType: EventType.LLM_OUTPUT,
      context: state.context,
      payload: {
        output: buildLlmOutputText(event),
      },
      metadata: {
        phase: "llm_after",
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
      return undefined;
    }
    if (
      decision.decision_type === DecisionType.SANITIZE ||
      decision.decision_type === DecisionType.REWRITE ||
      decision.decision_type === DecisionType.REPAIR
    ) {
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
    isRemoteUnavailableDecision,
    normalizePluginConfig,
    shouldFailClosed,
  },
};

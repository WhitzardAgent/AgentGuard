"use strict";

class GuardConfig {
  constructor(options = {}) {
    if (!options.session_id && !options.sessionId) {
      throw new Error("session_id is required");
    }
    this.session_id = options.session_id || options.sessionId;
    this.user_id = options.user_id ?? options.userId ?? null;
    this.agent_id = options.agent_id ?? options.agentId ?? null;
    this.policy = options.policy ?? null;
    this.server_url = options.server_url ?? options.serverUrl ?? null;
    this.api_key = options.api_key ?? options.apiKey ?? null;
    this.environment = options.environment ?? null;
    this.sandbox = options.sandbox ?? "local";
    this.sandbox_profile = options.sandbox_profile ?? options.sandboxProfile ?? null;
    this.enable_agentdog = options.enable_agentdog ?? options.enableAgentdog ?? false;
    this.max_steps = options.max_steps ?? options.maxSteps ?? 12;
    this.max_tool_calls = options.max_tool_calls ?? options.maxToolCalls ?? 24;
    this.window_size = options.window_size ?? options.windowSize ?? 8;
    this.audit_path = options.audit_path ?? options.auditPath ?? null;
    this.remote_timeout_s = options.remote_timeout_s ?? options.remoteTimeoutS ?? 5.0;
    this.remote_retries = options.remote_retries ?? options.remoteRetries ?? 2;
    this.metadata = { ...(options.metadata || {}) };
  }
}

module.exports = {
  GuardConfig,
};

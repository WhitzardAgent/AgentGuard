"use strict";

const { GuardDecision } = require("../schemas/decisions");
const { RemoteGuardError } = require("../utils/errors");

class CircuitBreaker {
  constructor({ threshold = 3, reset_after_s = 15.0 } = {}) {
    this.threshold = threshold;
    this.reset_after_s = reset_after_s;
    this.failures = 0;
    this.opened_at = 0;
  }

  get is_open() {
    if (this.failures < this.threshold) {
      return false;
    }
    if (Date.now() / 1000 - this.opened_at > this.reset_after_s) {
      this.failures = this.threshold - 1;
      return false;
    }
    return true;
  }

  record_success() {
    this.failures = 0;
    this.opened_at = 0;
  }

  record_failure() {
    this.failures += 1;
    if (this.failures >= this.threshold) {
      this.opened_at = Date.now() / 1000;
    }
  }
}

class RemoteGuardClient {
  constructor(server_url = null, options = {}) {
    this.server_url = (server_url || "").replace(/\/$/, "");
    this.api_key = options.api_key || options.apiKey || null;
    this.session_id = options.session_id || options.sessionId || null;
    this.agent_id = options.agent_id || options.agentId || null;
    this.user_id = options.user_id || options.userId || null;
    this.session_key = options.session_key || options.sessionKey || null;
    this.user_ticket = options.user_ticket || options.userTicket || null;
    this.timeout_s = options.timeout_s ?? options.timeoutS ?? 5.0;
    this.retries = options.retries ?? 2;
    this.decide_path = options.decide_path || "/v1/server/guard/decide";
    this.snapshot_path = options.snapshot_path || "/v1/server/policy/snapshot";
    this.trace_path = options.trace_path || "/v1/server/trace/upload";
    this.tool_report_path = options.tool_report_path || "/v1/server/tools/report";
    this.tool_sync_path = options.tool_sync_path || options.toolSyncPath || "/v1/server/tools/sync";
    this.skill_report_path = options.skill_report_path || options.skillReportPath || "/v1/server/skills/report";
    this.mcp_report_path = options.mcp_report_path || options.mcpReportPath || "/v1/server/mcps/report";
    this.agent_register_path = options.agent_register_path || options.agentRegisterPath || "/v1/server/agents/register";
    this.agent_bootstrap_path = options.agent_bootstrap_path || options.agentBootstrapPath || "/v1/server/agents/bootstrap";
    this.agent_sync_path = options.agent_sync_path || options.agentSyncPath || "/v1/server/agents/sync";
    this.runtime_session_create_path = options.runtime_session_create_path || options.runtimeSessionCreatePath || "/v1/server/session/create";
    this.runtime_session_refresh_path = options.runtime_session_refresh_path || options.runtimeSessionRefreshPath || "/v1/server/session/refresh";
    this.runtime_session_close_path = options.runtime_session_close_path || options.runtimeSessionClosePath || "/v1/server/session/close";
    this.runtime_plugin_config_path = options.runtime_plugin_config_path || options.runtimePluginConfigPath || "/v1/server/session/plugin-config";
    this.approval_path = options.approval_path || "/v1/server/approvals/{ticket_id}";
    this.register_path = options.register_path || "/v1/server/session/register";
    this.unregister_path = options.unregister_path || "/v1/server/session/unregister";
    this.session_token = options.session_token || options.sessionToken || null;
    this.dpop_proof_factory = options.dpop_proof_factory || options.dpopProofFactory || null;
    this.use_dpop_auth = Boolean(options.use_dpop_auth || options.useDpopAuth);
    this.legacy_identity_headers = options.legacy_identity_headers ?? options.legacyIdentityHeaders ?? true;
    this.approval_wait_timeout_s = options.approval_wait_timeout_s ?? options.approvalWaitTimeoutS ?? 600.0;
    this.approval_wait_chunk_s = Math.max(1.0, options.approval_wait_chunk_s ?? options.approvalWaitChunkS ?? 25.0);
    this.breaker = new CircuitBreaker();
  }

  get enabled() {
    return Boolean(this.server_url);
  }

  requireRuntimeAuth(purpose) {
    if (!this.use_dpop_auth || !this.session_token || typeof this.dpop_proof_factory !== "function") {
      throw new RemoteGuardError(
        `${purpose} requires a runtime-auth session_token and DPoP proof factory; create a runtime session first`,
      );
    }
  }

  async decide(event, context, options = {}) {
    if (!this.enabled) {
      throw new RemoteGuardError("no server_url configured");
    }
    this.requireRuntimeAuth("remote guard decisions");
    if (this.breaker.is_open) {
      throw new RemoteGuardError("circuit breaker open");
    }
    const payload = await this.post(this.decide_path, {
      request_id: `req_${event.event_id}`,
      current_event: event.toDict(),
      context: context.toDict(),
      trajectory_window: (options.trajectory_window || []).map((item) => item.toDict()),
      local_signals: options.local_signals || event.risk_signals || [],
      policy_version: context.policy_version,
      extensions: options.extensions || {},
      client_cached_entries: options.client_cached_entries || [],
    });
    const decision = GuardDecision.fromDict(payload.decision || {});
    for (const signal of payload.risk_signals || []) {
      if (!decision.risk_signals.includes(signal)) {
        decision.risk_signals.push(signal);
      }
    }
    decision.metadata.plugin_result = decision.metadata.plugin_result || payload.plugin_result || {};
    decision.metadata.source = decision.metadata.source || "remote";
    return this.awaitReviewResolution(decision);
  }

  fetch_snapshot() {
    this.requireRuntimeAuth("remote policy snapshot fetches");
    return this.get(this.snapshot_path);
  }

  upload_trace(trace) {
    this.requireRuntimeAuth("remote trace uploads");
    return this.post(this.trace_path, trace);
  }

  report_tool(context, tool) {
    this.requireRuntimeAuth("remote tool reports");
    return this.post(this.tool_report_path, {
      context: context.toDict(),
      tool,
    });
  }

  sync_tools(context, tools) {
    this.requireRuntimeAuth("remote tool sync");
    return this.post(this.tool_sync_path, {
      context: context.toDict(),
      tools: Array.isArray(tools) ? tools : [],
    });
  }

  report_skills(context, skills, scan = {}) {
    this.requireRuntimeAuth("remote skill reports");
    return this.post(this.skill_report_path, {
      context: context.toDict(),
      skills: Array.isArray(skills) ? skills : [],
      scan: scan && typeof scan === "object" && !Array.isArray(scan) ? scan : {},
    });
  }

  report_mcps(context, mcps, scan = {}) {
    this.requireRuntimeAuth("remote MCP reports");
    return this.post(this.mcp_report_path, {
      context: context.toDict(),
      mcps: Array.isArray(mcps) ? mcps : [],
      scan: scan && typeof scan === "object" && !Array.isArray(scan) ? scan : {},
    });
  }

  register_session(context) {
    this.requireRuntimeAuth("runtime session sync");
    return this.post(this.register_path, {
      context: context.toDict(),
    });
  }

  unregister_session() {
    this.requireRuntimeAuth("runtime session cleanup");
    return this.post(this.unregister_path, {});
  }

  register_agent(agent) {
    return this.post(this.agent_register_path, { ...(agent || {}) });
  }

  bootstrap_agents(catalog) {
    return this.post(this.agent_bootstrap_path, { ...(catalog || {}) });
  }

  sync_agents(catalog) {
    return this.post(this.agent_sync_path, { ...(catalog || {}) });
  }

  create_runtime_session(body, { extra_headers_factory = null, extraHeadersFactory = extra_headers_factory } = {}) {
    return this.post(this.runtime_session_create_path, { ...(body || {}) }, { extra_headers_factory: extraHeadersFactory });
  }

  refresh_runtime_session() {
    this.requireRuntimeAuth("runtime session refresh");
    return this.post(this.runtime_session_refresh_path, {});
  }

  fetch_runtime_plugin_config() {
    this.requireRuntimeAuth("runtime plugin-config fetches");
    return this.get(this.runtime_plugin_config_path);
  }

  close_runtime_session() {
    this.requireRuntimeAuth("runtime session close");
    return this.post(this.runtime_session_close_path, {});
  }

  upload_trace_async(trace, { on_success = null, on_error = null } = {}) {
    return this.upload_trace(trace).then(() => {
      if (typeof on_success === "function") {
        on_success();
      }
    }).catch((error) => {
      if (typeof on_error === "function") {
        on_error(error);
      }
    });
  }

  headers({ method = null, url = null } = {}) {
    const headers = {
      "Content-Type": "application/json",
      Accept: "application/json",
    };
    if (this.use_dpop_auth) {
      if (this.session_token) {
        headers.Authorization = `DPoP ${this.session_token}`;
      } else if (this.api_key) {
        headers.Authorization = `Bearer ${this.api_key}`;
      }
      if (typeof this.dpop_proof_factory === "function" && method && url) {
        headers.DPoP = String(this.dpop_proof_factory(method, url, this.session_token));
      }
    } else if (this.api_key) {
      headers.Authorization = `Bearer ${this.api_key}`;
    }
    if (this.session_id && this.legacy_identity_headers) {
      headers["X-AgentGuard-Session-Id"] = this.session_id;
    }
    if (this.agent_id && this.legacy_identity_headers) {
      headers["X-AgentGuard-Agent-Id"] = this.agent_id;
    }
    if (this.user_id && this.legacy_identity_headers) {
      headers["X-AgentGuard-User-Id"] = this.user_id;
    }
    if (this.session_key && this.legacy_identity_headers) {
      headers["X-AgentGuard-Session-Key"] = this.session_key;
    }
    if (this.user_ticket && !this.session_token) {
      headers["X-AgentGuard-User-Ticket"] = this.user_ticket;
    }
    return headers;
  }

  async request(method, path, body = null, { extra_headers_factory = null } = {}) {
    const url = `${this.server_url}${path}`;
    let lastError = null;
    for (let attempt = 0; attempt <= this.retries; attempt += 1) {
      const controller = new AbortController();
      const timeout = setTimeout(() => controller.abort(), this.timeout_s * 1000);
      const headers = this.headers({ method, url });
      if (typeof extra_headers_factory === "function") {
        Object.assign(headers, extra_headers_factory(method, url, body || {}) || {});
      }
      try {
        const response = await fetch(url, {
          method,
          headers,
          body: body == null ? undefined : JSON.stringify(body),
          signal: controller.signal,
        });
        clearTimeout(timeout);
        if (!response.ok) {
          let detail = "";
          try {
            const text = await response.text();
            if (text) {
              const parsed = JSON.parse(text);
              detail = parsed && parsed.detail ? String(parsed.detail) : text;
            }
          } catch (_) {
            detail = "";
          }
          throw new Error(`HTTP ${response.status}${detail ? `: ${detail}` : ""}`);
        }
        this.breaker.record_success();
        return await response.json();
      } catch (error) {
        clearTimeout(timeout);
        lastError = error;
      }
    }
    this.breaker.record_failure();
    throw new RemoteGuardError(`remote guard call failed: ${String(lastError && lastError.message ? lastError.message : lastError)}`);
  }

  post(path, body, options = {}) {
    return this.request("POST", path, body, options);
  }

  get(path) {
    return this.request("GET", path, null);
  }

  async awaitReviewResolution(decision) {
    if (!(decision.requires_user || decision.requires_remote)) {
      return decision;
    }
    const ticketId = String(
      decision.metadata.review_ticket_id || decision.metadata.ticket_id || "",
    ).trim();
    if (!ticketId) {
      return decision;
    }
    const timeoutS = Number(this.approval_wait_timeout_s || 0);
    const deadline = timeoutS > 0 ? Date.now() + timeoutS * 1000 : null;
    const maxWaitS = Math.max(Number(this.timeout_s || 0) - 0.5, 1.0);
    while (true) {
      const remainingMs = deadline == null ? null : deadline - Date.now();
      if (remainingMs != null && remainingMs <= 0) {
        return decision;
      }
      const waitS = remainingMs == null
        ? Math.min(this.approval_wait_chunk_s, maxWaitS)
        : Math.min(this.approval_wait_chunk_s, maxWaitS, remainingMs / 1000);
      const path = this.approval_path.replace(
        "{ticket_id}",
        encodeURIComponent(ticketId),
      );
      const payload = await this.get(`${path}?wait_ms=${Math.max(0, Math.floor(waitS * 1000))}`);
      const status = String(payload && payload.status ? payload.status : "").toLowerCase();
      if (status === "approved" || status === "denied") {
        const resolved = payload && payload.resolved_decision;
        if (resolved && typeof resolved === "object" && resolved.decision_type) {
          return GuardDecision.fromDict(resolved);
        }
        return decision;
      }
      if (status !== "pending") {
        return decision;
      }
    }
  }
}

module.exports = {
  CircuitBreaker,
  RemoteGuardClient,
};

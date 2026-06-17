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
    this.timeout_s = options.timeout_s ?? options.timeoutS ?? 5.0;
    this.retries = options.retries ?? 2;
    this.decide_path = options.decide_path || "/v1/server/guard/decide";
    this.snapshot_path = options.snapshot_path || "/v1/server/policy/snapshot";
    this.trace_path = options.trace_path || "/v1/server/trace/upload";
    this.tool_report_path = options.tool_report_path || "/v1/server/tools/report";
    this.register_path = options.register_path || "/v1/server/session/register";
    this.unregister_path = options.unregister_path || "/v1/server/session/unregister";
    this.breaker = new CircuitBreaker();
  }

  get enabled() {
    return Boolean(this.server_url);
  }

  async decide(event, context, options = {}) {
    if (!this.enabled) {
      throw new RemoteGuardError("no server_url configured");
    }
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
    decision.metadata.checker_result = decision.metadata.checker_result || payload.checker_result || {};
    decision.metadata.source = decision.metadata.source || "remote";
    return decision;
  }

  fetch_snapshot() {
    return this.get(this.snapshot_path);
  }

  upload_trace(trace) {
    return this.post(this.trace_path, trace);
  }

  report_tool(context, tool) {
    return this.post(this.tool_report_path, {
      context: context.toDict(),
      tool,
    });
  }

  register_session(context) {
    return this.post(this.register_path, {
      context: context.toDict(),
    });
  }

  unregister_session() {
    return this.post(this.unregister_path, {});
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

  headers() {
    const headers = {
      "Content-Type": "application/json",
      Accept: "application/json",
    };
    if (this.api_key) {
      headers.Authorization = `Bearer ${this.api_key}`;
    }
    if (this.session_id) {
      headers["X-AgentGuard-Session-Id"] = this.session_id;
    }
    if (this.agent_id) {
      headers["X-AgentGuard-Agent-Id"] = this.agent_id;
    }
    if (this.user_id) {
      headers["X-AgentGuard-User-Id"] = this.user_id;
    }
    if (this.session_key) {
      headers["X-AgentGuard-Session-Key"] = this.session_key;
    }
    return headers;
  }

  async request(method, path, body = null) {
    const url = `${this.server_url}${path}`;
    let lastError = null;
    for (let attempt = 0; attempt <= this.retries; attempt += 1) {
      const controller = new AbortController();
      const timeout = setTimeout(() => controller.abort(), this.timeout_s * 1000);
      try {
        const response = await fetch(url, {
          method,
          headers: this.headers(),
          body: body == null ? undefined : JSON.stringify(body),
          signal: controller.signal,
        });
        clearTimeout(timeout);
        if (!response.ok) {
          throw new Error(`HTTP ${response.status}`);
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

  post(path, body) {
    return this.request("POST", path, body);
  }

  get(path) {
    return this.request("GET", path, null);
  }
}

module.exports = {
  CircuitBreaker,
  RemoteGuardClient,
};

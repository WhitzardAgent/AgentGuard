"use strict";

const { SkillError } = require("../utils/errors");

class RemoteSkillRunner {
  constructor(server_url = null, options = {}) {
    this.server_url = (server_url || "").replace(/\/$/, "");
    this.options = options;
    this.timeout_s = options.timeout_s ?? options.timeoutS ?? 10.0;
  }

  get enabled() {
    return Boolean(this.server_url);
  }

  async run(skill_name, input_data = {}) {
    if (!this.enabled) {
      throw new SkillError("no server_url configured for remote skills");
    }
    const headers = {
      "Content-Type": "application/json",
      ...(this.options.api_key ? { Authorization: `Bearer ${this.options.api_key}` } : {}),
    };
    if (this.options.session_id) {
      headers["X-AgentGuard-Session-Id"] = this.options.session_id;
    }
    if (this.options.agent_id) {
      headers["X-AgentGuard-Agent-Id"] = this.options.agent_id;
    }
    if (this.options.user_id) {
      headers["X-AgentGuard-User-Id"] = this.options.user_id;
    }
    if (this.options.session_key) {
      headers["X-AgentGuard-Session-Key"] = this.options.session_key;
    }
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), this.timeout_s * 1000);
    try {
      const response = await fetch(`${this.server_url}/v1/server/skills/run`, {
        method: "POST",
        headers,
        body: JSON.stringify({
          skill_name,
          input: input_data,
        }),
        signal: controller.signal,
      });
      if (!response.ok) {
        throw new Error(`HTTP ${response.status}`);
      }
      return await response.json();
    } catch (error) {
      throw new SkillError(`remote skill call failed: ${String(error.message || error)}`);
    } finally {
      clearTimeout(timeout);
    }
  }
}

module.exports = {
  RemoteSkillRunner,
};

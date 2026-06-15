"use strict";

class RemoteSkillRunner {
  constructor(server_url = null, options = {}) {
    this.server_url = server_url;
    this.options = options;
  }

  async run(skill_name, input_data = {}) {
    if (!this.server_url) {
      throw new Error("no remote skill server configured");
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
    const response = await fetch(`${this.server_url.replace(/\/$/, "")}/v1/server/skills/run`, {
      method: "POST",
      headers,
      body: JSON.stringify({
        skill_name,
        input: input_data,
      }),
    });
    return response.json();
  }
}

module.exports = {
  RemoteSkillRunner,
};

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
    const response = await fetch(`${this.server_url.replace(/\/$/, "")}/v1/server/skills/run`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        ...(this.options.api_key ? { Authorization: `Bearer ${this.options.api_key}` } : {}),
      },
      body: JSON.stringify({
        skill_name,
        input_data,
        session_id: this.options.session_id || null,
        session_key: this.options.session_key || null,
      }),
    });
    return response.json();
  }
}

module.exports = {
  RemoteSkillRunner,
};

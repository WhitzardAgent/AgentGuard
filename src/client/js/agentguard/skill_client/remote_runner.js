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
    const sessionToken = this.options.session_token || this.options.sessionToken || null;
    const dpopProofFactory = this.options.dpop_proof_factory || this.options.dpopProofFactory || null;
    const useDpopAuth = Boolean(this.options.use_dpop_auth || this.options.useDpopAuth);
    if (!useDpopAuth || !sessionToken || typeof dpopProofFactory !== "function") {
      throw new SkillError("remote skills require a runtime-auth session_token and DPoP proof factory");
    }
    const url = `${this.server_url}/v1/server/skills/run`;
    const headers = {
      "Content-Type": "application/json",
      Accept: "application/json",
      Authorization: `DPoP ${sessionToken}`,
      DPoP: String(dpopProofFactory("POST", url, sessionToken)),
    };
    if (this.options.user_ticket || this.options.userTicket) {
      headers["X-AgentGuard-User-Ticket"] = this.options.user_ticket || this.options.userTicket;
    }
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), this.timeout_s * 1000);
    try {
      const response = await fetch(url, {
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

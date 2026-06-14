"use strict";

class RuntimeContext {
  constructor(data = {}) {
    this.session_id = data.session_id || data.sessionId || "unknown";
    this.user_id = data.user_id ?? data.userId ?? null;
    this.agent_id = data.agent_id ?? data.agentId ?? null;
    this.task_id = data.task_id ?? data.taskId ?? null;
    this.policy = data.policy ?? null;
    this.policy_version = data.policy_version ?? data.policyVersion ?? null;
    this.environment = data.environment ?? null;
    this.metadata = { ...(data.metadata || {}) };
  }

  toDict() {
    return {
      session_id: this.session_id,
      user_id: this.user_id,
      agent_id: this.agent_id,
      task_id: this.task_id,
      policy: this.policy,
      policy_version: this.policy_version,
      environment: this.environment,
      metadata: { ...this.metadata },
    };
  }

  child(overrides = {}) {
    return new RuntimeContext({ ...this.toDict(), ...overrides });
  }

  static fromDict(data = {}) {
    return new RuntimeContext(data);
  }
}

module.exports = {
  RuntimeContext,
};

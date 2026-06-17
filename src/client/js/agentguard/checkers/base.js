"use strict";

function resolveEnvValue(value) {
  if (Array.isArray(value)) {
    return value.map(resolveEnvValue);
  }
  if (value && typeof value === "object") {
    return Object.fromEntries(Object.entries(value).map(([key, item]) => [key, resolveEnvValue(item)]));
  }
  if (typeof value !== "string") {
    return value;
  }
  const match = value.match(/^\$(?:\{([A-Za-z_][A-Za-z0-9_]*)\}|([A-Za-z_][A-Za-z0-9_]*))$/);
  if (match) {
    return process.env[match[1] || match[2]];
  }
  if (/^[A-Z_][A-Z0-9_]*$/.test(value) && Object.prototype.hasOwnProperty.call(process.env, value)) {
    return process.env[value];
  }
  return value;
}

class CheckResult {
  constructor(data = {}) {
    this.decision_candidate = data.decision_candidate || null;
    this.risk_signals = [...(data.risk_signals || [])];
    this.is_final = Boolean(data.is_final);
    this.metadata = { ...(data.metadata || {}) };
  }

  static empty() {
    return new CheckResult();
  }
}

class BaseChecker {
  constructor({ env = null, ...kwargs } = {}) {
    this.name = this.constructor.name || "base";
    this.description = "";
    this.event_types = [];
    this.bind_config({ env, ...kwargs });
  }

  bind_config({ env = null, ...kwargs } = {}) {
    this.config = { ...kwargs };
    this.env_spec = { ...(env || {}) };
    this.env = resolveEnvValue(this.env_spec);
    Object.assign(this, this.config, this.env);
  }

  applies(event) {
    return !this.event_types.length || this.event_types.includes(event.event_type);
  }

  check() {
    throw new Error("check() must be implemented");
  }
}

module.exports = {
  CheckResult,
  BaseChecker,
};

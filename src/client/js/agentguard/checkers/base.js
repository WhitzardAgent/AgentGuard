"use strict";

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
  constructor() {
    this.name = this.constructor.name || "base";
    this.description = "";
    this.event_types = [];
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

"use strict";

const { builtinRules } = require("../rules/builtin");
const { matchRules } = require("../rules/matcher");
const { PolicyRule } = require("../schemas/policy");
const { stableHash } = require("../utils/hash");

class PolicySnapshot {
  constructor(data = {}) {
    this.version = data.version || "v0";
    this.rules = (data.rules || []).map((rule) => (rule instanceof PolicyRule ? rule : PolicyRule.fromDict(rule)));
    this.metadata = { ...(data.metadata || {}) };
    this.buildIndexes();
  }

  buildIndexes() {
    this.byCapability = {};
    this.byRisk = {};
    this.byEvent = {};
    for (const rule of this.rules) {
      for (const capability of rule.capabilities) {
        this.byCapability[capability] = this.byCapability[capability] || [];
        this.byCapability[capability].push(rule);
      }
      for (const signal of rule.risk_signals) {
        this.byRisk[signal] = this.byRisk[signal] || [];
        this.byRisk[signal].push(rule);
      }
      for (const eventType of rule.event_types) {
        this.byEvent[eventType] = this.byEvent[eventType] || [];
        this.byEvent[eventType].push(rule);
      }
    }
  }

  evaluate(event, traceWindow = null) {
    return matchRules(this.rules, event, traceWindow);
  }

  toDict() {
    return {
      version: this.version,
      rules: this.rules.map((rule) => rule.toDict()),
      metadata: { ...this.metadata },
      stable_hash: this.stableHash(),
    };
  }

  stableHash() {
    return stableHash({
      version: this.version,
      rules: this.rules.map((rule) => rule.toDict()),
    });
  }

  static fromDict(data = {}) {
    return new PolicySnapshot(data);
  }

  static default() {
    return new PolicySnapshot({
      version: "builtin",
      rules: builtinRules(),
    });
  }
}

module.exports = {
  PolicySnapshot,
};

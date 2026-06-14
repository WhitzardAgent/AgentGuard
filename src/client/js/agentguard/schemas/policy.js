"use strict";

const { DecisionType } = require("./decisions");

const PolicyEffect = Object.freeze({
  ALLOW: "allow",
  DENY: "deny",
  SANITIZE: "sanitize",
  DEGRADE: "degrade",
  REQUIRE_APPROVAL: "require_approval",
  REQUIRE_REMOTE_REVIEW: "require_remote_review",
  LOG_ONLY: "log_only",
});

const EFFECT_TO_DECISION = {
  [PolicyEffect.ALLOW]: DecisionType.ALLOW,
  [PolicyEffect.DENY]: DecisionType.DENY,
  [PolicyEffect.SANITIZE]: DecisionType.SANITIZE,
  [PolicyEffect.DEGRADE]: DecisionType.DEGRADE,
  [PolicyEffect.REQUIRE_APPROVAL]: DecisionType.REQUIRE_APPROVAL,
  [PolicyEffect.REQUIRE_REMOTE_REVIEW]: DecisionType.REQUIRE_REMOTE_REVIEW,
  [PolicyEffect.LOG_ONLY]: DecisionType.LOG_ONLY,
};

function effectToDecision(effect) {
  return EFFECT_TO_DECISION[effect];
}

class RuleCondition {
  constructor(data = {}) {
    this.field = data.field || "";
    this.op = data.op || "eq";
    this.value = data.value;
  }

  toDict() {
    return { field: this.field, op: this.op, value: this.value };
  }

  static fromDict(data = {}) {
    return new RuleCondition(data);
  }
}

function resolve(path, root) {
  return path.split(".").reduce((current, part) => {
    if (current && typeof current === "object" && part in current) {
      return current[part];
    }
    return undefined;
  }, root);
}

function applyOp(op, actual, expected) {
  switch (op) {
    case "eq":
      return actual === expected;
    case "ne":
      return actual !== expected;
    case "in":
      return Array.isArray(expected) ? expected.includes(actual) : false;
    case "not_in":
      return Array.isArray(expected) ? !expected.includes(actual) : true;
    case "contains":
      return actual != null && String(actual).includes(String(expected));
    case "icontains":
      return String(actual || "").toLowerCase().includes(String(expected || "").toLowerCase());
    case "any_in": {
      const actualSet = new Set(Array.isArray(actual) ? actual : [actual]);
      return (expected || []).some((item) => actualSet.has(item));
    }
    case "regex":
      return new RegExp(String(expected)).test(String(actual || ""));
    case "exists":
      return (actual !== undefined && actual !== null) === Boolean(expected);
    case "gt":
      return Number(actual) > Number(expected);
    case "lt":
      return Number(actual) < Number(expected);
    default:
      return false;
  }
}

class PolicyRule {
  constructor(data = {}) {
    this.rule_id = data.rule_id;
    this.effect = data.effect;
    this.reason = data.reason || "";
    this.priority = Number(data.priority || 0);
    this.event_types = [...(data.event_types || [])];
    this.tool_names = [...(data.tool_names || [])];
    this.capabilities = [...(data.capabilities || [])];
    this.risk_signals = [...(data.risk_signals || [])];
    this.conditions = (data.conditions || []).map((condition) =>
      condition instanceof RuleCondition ? condition : RuleCondition.fromDict(condition)
    );
    this.metadata = { ...(data.metadata || {}) };
  }

  toDict() {
    return {
      rule_id: this.rule_id,
      effect: this.effect,
      reason: this.reason,
      priority: this.priority,
      event_types: [...this.event_types],
      tool_names: [...this.tool_names],
      capabilities: [...this.capabilities],
      risk_signals: [...this.risk_signals],
      conditions: this.conditions.map((condition) => condition.toDict()),
      metadata: { ...this.metadata },
    };
  }

  matches(event, traceWindow = []) {
    if (this.event_types.length && !this.event_types.includes(event.event_type)) {
      return false;
    }
    const payload = event.payload || {};
    if (this.tool_names.length && !wildcardMatch(payload.tool_name, this.tool_names)) {
      return false;
    }
    if (this.capabilities.length) {
      const caps = new Set(payload.capabilities || []);
      if (!this.capabilities.some((cap) => caps.has(cap))) {
        return false;
      }
    }
    if (this.risk_signals.length) {
      const signals = new Set(event.risk_signals || []);
      if (!this.risk_signals.some((signal) => signals.has(signal))) {
        return false;
      }
    }
    const eventDict = event.toDict();
    for (const condition of this.conditions) {
      if (condition.field.startsWith("trace.")) {
        if (!matchTrace(condition, traceWindow)) {
          return false;
        }
        continue;
      }
      if (!applyOp(condition.op, resolve(condition.field, eventDict), condition.value)) {
        return false;
      }
    }
    return true;
  }

  static fromDict(data = {}) {
    return new PolicyRule(data);
  }
}

function wildcardMatch(value, patterns) {
  if (value == null) {
    return false;
  }
  return patterns.some((pattern) => pattern === "*" || pattern === value || (pattern.endsWith("*") && String(value).startsWith(pattern.slice(0, -1))));
}

function matchTrace(condition, window) {
  const key = condition.field.split(".", 2)[1];
  if (key === "contains_event_type") {
    return window.some((event) => event.event_type === condition.value);
  }
  if (key === "contains_signal") {
    return window.some((event) => (event.risk_signals || []).includes(condition.value));
  }
  if (key === "sequence") {
    const wanted = [...(condition.value || [])];
    let index = 0;
    for (const event of window) {
      if (index < wanted.length && event.event_type === wanted[index]) {
        index += 1;
      }
    }
    return index >= wanted.length;
  }
  return false;
}

module.exports = {
  PolicyEffect,
  RuleCondition,
  PolicyRule,
  effectToDecision,
};

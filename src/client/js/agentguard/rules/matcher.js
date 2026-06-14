"use strict";

const { PolicyEffect } = require("../schemas/policy");

const EFFECT_RANK = {
  [PolicyEffect.DENY]: 7,
  [PolicyEffect.REQUIRE_REMOTE_REVIEW]: 6,
  [PolicyEffect.REQUIRE_APPROVAL]: 5,
  [PolicyEffect.DEGRADE]: 4,
  [PolicyEffect.SANITIZE]: 3,
  [PolicyEffect.LOG_ONLY]: 2,
  [PolicyEffect.ALLOW]: 1,
};

class MatchResult {
  constructor(data = {}) {
    this.matched = Boolean(data.matched);
    this.rule = data.rule || null;
    this.effect = data.effect || null;
    this.reason = data.reason || "";
    this.all_matched = [...(data.all_matched || [])];
  }

  toDict() {
    return {
      matched: this.matched,
      rule_id: this.rule ? this.rule.rule_id : null,
      effect: this.effect,
      reason: this.reason,
      matched_rule_ids: this.all_matched.map((rule) => rule.rule_id),
    };
  }
}

function matchRules(rules, event, traceWindow = null) {
  const matched = rules.filter((rule) => rule.matches(event, traceWindow || []));
  if (!matched.length) {
    return new MatchResult({ matched: false, all_matched: [] });
  }
  const winner = matched.reduce((best, current) => {
    if (!best) {
      return current;
    }
    const bestKey = [best.priority, EFFECT_RANK[best.effect] || 0];
    const currentKey = [current.priority, EFFECT_RANK[current.effect] || 0];
    return currentKey[0] > bestKey[0] || (currentKey[0] === bestKey[0] && currentKey[1] > bestKey[1]) ? current : best;
  }, null);
  return new MatchResult({
    matched: true,
    rule: winner,
    effect: winner.effect,
    reason: winner.reason,
    all_matched: matched,
  });
}

module.exports = {
  MatchResult,
  matchRules,
};

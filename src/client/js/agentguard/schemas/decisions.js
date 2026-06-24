"use strict";

const DecisionType = Object.freeze({
  ALLOW: "allow",
  DENY: "deny",
  SANITIZE: "sanitize",
  REWRITE: "rewrite",
  REPAIR: "repair",
  DEGRADE: "degrade",
  HUMAN_CHECK: "human_check",
  REQUIRE_APPROVAL: "require_approval",
  REQUIRE_REMOTE_REVIEW: "require_remote_review",
  LOOP_BACK_TO_LLM: "loop_back_to_llm",
  DROP_THOUGHT: "drop_thought",
  ALIGN_THOUGHT: "align_thought",
  LOG_ONLY: "log_only",
});

const BLOCKING = new Set([
  DecisionType.DENY,
  DecisionType.DEGRADE,
  DecisionType.HUMAN_CHECK,
  DecisionType.REQUIRE_APPROVAL,
  DecisionType.DROP_THOUGHT,
]);
const REQUIRES_USER = new Set([DecisionType.HUMAN_CHECK, DecisionType.REQUIRE_APPROVAL]);
const REQUIRES_REMOTE = new Set([DecisionType.REQUIRE_REMOTE_REVIEW]);

class GuardDecision {
  constructor(data = {}) {
    const incomingDecisionType = data.decision_type || data.decisionType || DecisionType.ALLOW;
    this.decision_type = incomingDecisionType === "ask_user" ? DecisionType.HUMAN_CHECK : incomingDecisionType;
    this.reason = data.reason || "";
    this.policy_id = data.policy_id ?? data.policyId ?? null;
    this.confidence = data.confidence ?? null;
    this.risk_signals = [...(data.risk_signals || data.riskSignals || [])];
    this.metadata = { ...(data.metadata || {}) };
  }

  get is_allow() {
    return this.decision_type === DecisionType.ALLOW;
  }

  get is_blocking() {
    return BLOCKING.has(this.decision_type);
  }

  get requires_remote() {
    return REQUIRES_REMOTE.has(this.decision_type);
  }

  get requires_user() {
    return REQUIRES_USER.has(this.decision_type);
  }

  toDict() {
    return {
      decision_type: this.decision_type,
      reason: this.reason,
      policy_id: this.policy_id,
      confidence: this.confidence,
      risk_signals: [...this.risk_signals],
      metadata: { ...this.metadata },
    };
  }

  static fromDict(data = {}) {
    return new GuardDecision(data);
  }
}

function makeDecision(decisionType, reason, extra = {}) {
  return new GuardDecision({ decision_type: decisionType, reason, ...extra });
}

GuardDecision.allow = (reason = "allowed", extra = {}) => makeDecision(DecisionType.ALLOW, reason, extra);
GuardDecision.deny = (reason, extra = {}) => makeDecision(DecisionType.DENY, reason, extra);
GuardDecision.sanitize = (reason, extra = {}) => makeDecision(DecisionType.SANITIZE, reason, extra);
GuardDecision.rewrite = (reason, extra = {}) => makeDecision(DecisionType.REWRITE, reason, extra);
GuardDecision.repair = (reason, extra = {}) => makeDecision(DecisionType.REPAIR, reason, extra);
GuardDecision.degrade = (reason, extra = {}) => makeDecision(DecisionType.DEGRADE, reason, extra);
GuardDecision.human_check = (reason, extra = {}) => makeDecision(DecisionType.HUMAN_CHECK, reason, extra);
GuardDecision.require_approval = (reason, extra = {}) => makeDecision(DecisionType.REQUIRE_APPROVAL, reason, extra);
GuardDecision.require_remote_review = (reason, extra = {}) =>
  makeDecision(DecisionType.REQUIRE_REMOTE_REVIEW, reason, extra);
GuardDecision.log_only = (reason = "log only", extra = {}) => makeDecision(DecisionType.LOG_ONLY, reason, extra);

module.exports = {
  DecisionType,
  GuardDecision,
};

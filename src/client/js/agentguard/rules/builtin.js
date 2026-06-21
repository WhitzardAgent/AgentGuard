"use strict";

const { PolicyEffect, PolicyRule, RuleCondition } = require("../schemas/policy");
const {
  CAP_DATABASE_WRITE,
  CAP_EXTERNAL_SEND,
  CAP_PAYMENT,
  CAP_SHELL,
} = require("../tools/capability");

function builtinRules() {
  return [
    // new PolicyRule({
    //   rule_id: "deny_secret_exfiltration",
    //   effect: PolicyEffect.DENY,
    //   reason: "Secret-like content combined with external send.",
    //   priority: 100,
    //   event_types: ["tool_invoke"],
    //   capabilities: [CAP_EXTERNAL_SEND],
    //   risk_signals: ["secret_detected", "api_key_detected", "system_prompt_leak"],
    // }),
    // new PolicyRule({
    //   rule_id: "review_external_send",
    //   effect: PolicyEffect.REQUIRE_REMOTE_REVIEW,
    //   reason: "External send is high-risk and needs remote review.",
    //   priority: 60,
    //   event_types: ["tool_invoke"],
    //   capabilities: [CAP_EXTERNAL_SEND],
    // }),
    // new PolicyRule({
    //   rule_id: "approve_payment",
    //   effect: PolicyEffect.REQUIRE_APPROVAL,
    //   reason: "Payment actions require explicit approval.",
    //   priority: 80,
    //   event_types: ["tool_invoke"],
    //   capabilities: [CAP_PAYMENT],
    // }),
    // new PolicyRule({
    //   rule_id: "review_shell",
    //   effect: PolicyEffect.REQUIRE_REMOTE_REVIEW,
    //   reason: "Shell execution requires remote review.",
    //   priority: 70,
    //   event_types: ["tool_invoke"],
    //   capabilities: [CAP_SHELL],
    // }),
    // new PolicyRule({
    //   rule_id: "deny_dangerous_shell",
    //   effect: PolicyEffect.DENY,
    //   reason: "Destructive shell command detected.",
    //   priority: 110,
    //   event_types: ["tool_invoke"],
    //   capabilities: [CAP_SHELL],
    //   conditions: [new RuleCondition({ field: "payload.arguments.command", op: "regex", value: "rm\\s+-rf\\s+/|mkfs|:\\(\\)\\{|dd\\s+if=" })],
    // }),
    // new PolicyRule({
    //   rule_id: "approve_database_write",
    //   effect: PolicyEffect.REQUIRE_APPROVAL,
    //   reason: "Database writes require approval.",
    //   priority: 55,
    //   event_types: ["tool_invoke"],
    //   capabilities: [CAP_DATABASE_WRITE],
    // }),
    // new PolicyRule({
    //   rule_id: "sanitize_pii_output",
    //   effect: PolicyEffect.SANITIZE,
    //   reason: "PII detected in model output.",
    //   priority: 40,
    //   event_types: ["llm_output"],
    //   risk_signals: ["pii_email", "pii_detected"],
    // }),
    // new PolicyRule({
    //   rule_id: "deny_agentdog_exfiltration",
    //   effect: PolicyEffect.DENY,
    //   reason: "AgentDoG detected a trajectory-level exfiltration pattern.",
    //   priority: 120,
    //   event_types: ["tool_invoke"],
    //   risk_signals: ["exfiltration_detected"],
    // }),
    // new PolicyRule({
    //   rule_id: "review_agentdog_high_risk",
    //   effect: PolicyEffect.REQUIRE_REMOTE_REVIEW,
    //   reason: "AgentDoG flagged high trajectory risk.",
    //   priority: 65,
    //   event_types: ["tool_invoke", "llm_output"],
    //   risk_signals: ["agentdog_high_risk", "instruction_hijack"],
    // }),
    // new PolicyRule({
    //   rule_id: "deny_prompt_injection_tool",
    //   effect: PolicyEffect.DENY,
    //   reason: "Tool result injection leading to unsafe tool call.",
    //   priority: 90,
    //   event_types: ["tool_invoke"],
    //   risk_signals: ["prompt_injection"],
    //   conditions: [new RuleCondition({ field: "trace.contains_signal", op: "eq", value: "prompt_injection" })],
    // }),
    // new PolicyRule({
    //   rule_id: "default_allow_low_risk",
    //   effect: PolicyEffect.ALLOW,
    //   reason: "Low-risk action allowed by default baseline.",
    //   priority: 0,
    //   event_types: [],
    // }),
  ];
}

module.exports = {
  builtinRules,
};

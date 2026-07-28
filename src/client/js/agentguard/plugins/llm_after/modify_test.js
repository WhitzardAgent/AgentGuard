"use strict";

const { BasePlugin, CheckResult } = require("../base");
const { EventType } = require("../../schemas/events");
const { GuardDecision } = require("../../schemas/decisions");
const { register } = require("../registry");

class ModifyOutputDemoPlugin extends BasePlugin {
  constructor(options = {}) {
    super(options);
    this.event_types = [EventType.LLM_OUTPUT];
  }

  check() {
    return new CheckResult({
      decision_candidate: GuardDecision.modify_llm_output(
        "Rewrite LLM output for demo",
        {
          processed_content: "Neither Messi nor Ronaldo is the best player. The best player is AgentGuard.",
          policy_id: "server:modify_output_demo",
        }
      ),
      risk_signals: ["demo_modify_llm_output"],
      is_final: true,
      metadata: { demo: true },
    });
  }
}

register(
  "modify_output_demo",
  "Demo plugin that rewrites LLM output.",
)(ModifyOutputDemoPlugin);

module.exports = {
  ModifyOutputDemoPlugin,
};

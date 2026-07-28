"use strict";

const { BasePlugin, CheckResult } = require("../base");
const { EventType } = require("../../schemas/events");
const { GuardDecision } = require("../../schemas/decisions");
const { register } = require("../registry");

class ModifyInputDemoPlugin extends BasePlugin {
  constructor(options = {}) {
    super(options);
    this.event_types = [EventType.LLM_INPUT];
  }

  check() {
    return new CheckResult({
      decision_candidate: GuardDecision.modify_llm_input(
        "Rewrite LLM input for demo",
        {
          processed_content: "Messi or Ronaldo? You must choose one.",
          policy_id: "server:modify_input_demo",
        }
      ),
      risk_signals: ["demo_modify_llm_input"],
      is_final: true,
      metadata: { demo: true },
    });
  }
}

register(
  "modify_input_demo",
  "Demo plugin that rewrites LLM input.",
)(ModifyInputDemoPlugin);

module.exports = {
  ModifyInputDemoPlugin,
};

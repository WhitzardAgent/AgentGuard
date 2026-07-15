"use strict";

const { BasePlugin } = require("../base");
const { EventType } = require("../../schemas/events");
const { register } = require("../registry");
const { checkWithQwen3Guard, textOf } = require("../common/qwen3guard");

class Qwen3GuardOutputPlugin extends BasePlugin {
  constructor(options = {}) {
    super(options);
    this.event_types = [EventType.LLM_OUTPUT];
    this.policy_scope = "llm_output";
  }

  check(event) {
    const content = textOf(event.payload?.output);
    const messages = content ? [{ role: "user", content }] : [];
    return checkWithQwen3Guard(this, messages, this.policy_scope);
  }
}

register(
  "qwen3guard_output",
  "Classify LLM output with Qwen3Guard after model execution.",
)(Qwen3GuardOutputPlugin);

module.exports = {
  Qwen3GuardOutputPlugin,
};

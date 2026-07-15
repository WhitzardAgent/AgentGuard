"use strict";

const { BasePlugin } = require("../base");
const { EventType } = require("../../schemas/events");
const { register } = require("../registry");
const { checkWithQwen3Guard, textOf } = require("../common/qwen3guard");

class Qwen3GuardInputPlugin extends BasePlugin {
  constructor(options = {}) {
    super(options);
    this.event_types = [EventType.LLM_INPUT];
    this.policy_scope = "llm_input";
  }

  check(event) {
    const messages = [];
    for (const item of event.payload?.messages || []) {
      const role = String(item?.role || "user").trim() || "user";
      const content = textOf(item?.content);
      if (content) {
        messages.push({ role, content });
      }
    }
    return checkWithQwen3Guard(this, messages, this.policy_scope);
  }
}

register(
  "qwen3guard_input",
  "Classify LLM input with Qwen3Guard before model execution.",
)(Qwen3GuardInputPlugin);

module.exports = {
  Qwen3GuardInputPlugin,
};

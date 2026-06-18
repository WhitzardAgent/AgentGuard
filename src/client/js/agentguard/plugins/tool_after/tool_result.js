"use strict";

const { BasePlugin, CheckResult } = require("../base");
const { EventType } = require("../../schemas/events");

class ToolResultPlugin extends BasePlugin {
  constructor() {
    super();
    this.event_types = [EventType.TOOL_RESULT];
  }

  check(event) {
    const text = JSON.stringify((event.payload || {}).result || "");
    const signals = [];
    if (/ignore previous instructions|system prompt/i.test(text)) {
      signals.push("prompt_injection");
    }
    return new CheckResult({ risk_signals: signals });
  }
}

module.exports = {
  ToolResultPlugin,
};

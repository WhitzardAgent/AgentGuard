"use strict";

const { BaseChecker, CheckResult } = require("../base");
const { EventType } = require("../../schemas/events");

class ToolResultChecker extends BaseChecker {
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
  ToolResultChecker,
};

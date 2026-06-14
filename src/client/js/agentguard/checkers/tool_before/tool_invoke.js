"use strict";

const { BaseChecker, CheckResult } = require("../base");
const { EventType } = require("../../schemas/events");
const { matchSignals } = require("../common/patterns");

class ToolInvokeChecker extends BaseChecker {
  constructor() {
    super();
    this.event_types = [EventType.TOOL_INVOKE];
  }

  check(event) {
    const signals = matchSignals(JSON.stringify((event.payload || {}).arguments || {}));
    const command = (((event.payload || {}).arguments || {}).command || "").toLowerCase();
    if (/rm\s+-rf|mkfs|dd\s+if=/.test(command)) {
      signals.push("dangerous_shell");
    }
    return new CheckResult({ risk_signals: [...new Set(signals)] });
  }
}

module.exports = {
  ToolInvokeChecker,
};

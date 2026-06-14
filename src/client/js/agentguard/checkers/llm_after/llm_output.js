"use strict";

const { BaseChecker, CheckResult } = require("../base");
const { EventType } = require("../../schemas/events");
const { matchSignals } = require("../common/patterns");

class LLMOutputChecker extends BaseChecker {
  constructor() {
    super();
    this.event_types = [EventType.LLM_OUTPUT];
  }

  check(event) {
    const text = JSON.stringify(event.payload || {});
    return new CheckResult({ risk_signals: matchSignals(text) });
  }
}

module.exports = {
  LLMOutputChecker,
};

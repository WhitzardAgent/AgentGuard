"use strict";

const { CheckResult, BaseChecker } = require("./base");
const { LLMInputChecker } = require("./llm_before/llm_input");
const { LLMOutputChecker } = require("./llm_after/llm_output");
const { ToolInvokeChecker } = require("./tool_before/tool_invoke");
const { ToolResultChecker } = require("./tool_after/tool_result");

const PHASE_ORDER = ["llm_before", "llm_after", "tool_before", "tool_after", "global"];
const EVENT_PHASE = {
  llm_input: "llm_before",
  llm_output: "llm_after",
  tool_invoke: "tool_before",
  tool_result: "tool_after",
};

function defaultCheckers() {
  return [new LLMInputChecker(), new LLMOutputChecker(), new ToolInvokeChecker(), new ToolResultChecker()];
}

function buildCheckersByPhase(config = null) {
  if (!config) {
    return { global: defaultCheckers() };
  }
  const result = {};
  for (const [phase, specs] of Object.entries(config)) {
    result[phase] = specs.map(instantiateChecker);
  }
  return result;
}

function instantiateChecker(spec) {
  if (spec instanceof BaseChecker) {
    return spec;
  }
  if (typeof spec === "function") {
    return new spec();
  }
  throw new Error(`invalid checker config entry: ${String(spec)}`);
}

class CheckerManager {
  constructor({ checkers = null, config = null } = {}) {
    this.checkers_by_phase = checkers ? { global: [...checkers] } : buildCheckersByPhase(config);
    this.refresh();
  }

  update_config(config = null) {
    this.checkers_by_phase = buildCheckersByPhase(config);
    this.refresh();
  }

  add(checker, phase = null) {
    const target = phase || "global";
    this.checkers_by_phase[target] = this.checkers_by_phase[target] || [];
    this.checkers_by_phase[target].push(checker);
    this.checkers.push(checker);
  }

  refresh() {
    this.checkers = PHASE_ORDER.flatMap((phase) => this.checkers_by_phase[phase] || []);
  }

  run(event, context) {
    const phase = EVENT_PHASE[event.event_type] || "global";
    const phaseCheckers = [...(this.checkers_by_phase[phase] || []), ...(this.checkers_by_phase.global || [])];
    const mergedSignals = [];
    let candidate = null;
    let isFinal = false;
    const metadata = {};
    for (const checker of phaseCheckers) {
      if (!checker.applies(event)) {
        continue;
      }
      try {
        const result = checker.check(event, context);
        for (const signal of result.risk_signals) {
          if (!mergedSignals.includes(signal)) {
            mergedSignals.push(signal);
          }
        }
        Object.assign(metadata, result.metadata || {});
        if (result.decision_candidate && (candidate === null || result.is_final)) {
          candidate = result.decision_candidate;
          isFinal = isFinal || result.is_final;
        }
      } catch (error) {
        metadata[`${checker.name}_error`] = String(error.message || error);
      }
    }
    for (const signal of mergedSignals) {
      event.addSignal(signal);
    }
    return new CheckResult({
      decision_candidate: candidate,
      risk_signals: mergedSignals,
      is_final: isFinal,
      metadata,
    });
  }
}

module.exports = {
  PHASE_ORDER,
  CheckerManager,
  defaultCheckers,
};

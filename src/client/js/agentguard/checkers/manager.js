"use strict";

const fs = require("fs");
const { CheckResult, BaseChecker } = require("./base");
const { getCheckerClass, discoverCheckers } = require("./registry");
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
const BUILTIN_CHECKERS = {
  llm_input: LLMInputChecker,
  llm_output: LLMOutputChecker,
  tool_invoke: ToolInvokeChecker,
  tool_result: ToolResultChecker,
};

function defaultCheckers() {
  return [];
}

function loadCheckerConfig(source = null) {
  if (source == null) {
    return null;
  }
  let data;
  if (typeof source === "string") {
    data = JSON.parse(fs.readFileSync(source, "utf-8"));
  } else {
    data = { ...source };
  }
  const phases = data.phases;
  if (!phases || typeof phases !== "object" || Array.isArray(phases)) {
    throw new Error("checker config must contain a 'phases' object");
  }
  const config = {};
  for (const phase of PHASE_ORDER) {
    if (phase in phases) {
      config[phase] = checkerSpecsForScope(phases[phase], "local");
    }
  }
  return config;
}

function checkerSpecsForScope(value, scope) {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new Error("checker phase config must be an object with 'local' and 'remote'");
  }
  if (!("local" in value) || !("remote" in value)) {
    throw new Error("checker phase config must include both 'local' and 'remote'");
  }
  const specs = value[scope];
  if (specs == null) {
    return [];
  }
  if (!Array.isArray(specs)) {
    throw new Error(`checker phase '${scope}' config must be a list`);
  }
  return [...specs];
}

function buildCheckersByPhase(config = null) {
  if (!config) {
    return {};
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
    return buildChecker(spec);
  }
  if (typeof spec === "string") {
    discoverCheckers();
    const CheckerClass = BUILTIN_CHECKERS[spec] || getCheckerClass(spec);
    if (!CheckerClass) {
      throw new Error(`invalid checker config entry: ${String(spec)}`);
    }
    return buildChecker(CheckerClass);
  }
  if (spec && typeof spec === "object") {
    const target = spec.class || spec.checker || spec.name;
    const kwargs = checkerKwargs(spec);
    const env = checkerEnv(spec);
    const CheckerClass = typeof target === "function" ? target : BUILTIN_CHECKERS[target] || getCheckerClass(target);
    if (!CheckerClass) {
      throw new Error(`invalid checker config entry: ${JSON.stringify(spec)}`);
    }
    return buildChecker(CheckerClass, { kwargs, env });
  }
  throw new Error(`invalid checker config entry: ${String(spec)}`);
}

function checkerKwargs(spec) {
  const reserved = new Set(["class", "checker", "name", "kwargs", "env"]);
  const kwargs = Object.fromEntries(Object.entries(spec).filter(([key]) => !reserved.has(key)));
  if (spec.kwargs != null && (typeof spec.kwargs !== "object" || Array.isArray(spec.kwargs))) {
    throw new Error(`checker kwargs config must be an object: ${JSON.stringify(spec)}`);
  }
  return { ...kwargs, ...(spec.kwargs || {}) };
}

function checkerEnv(spec) {
  if (spec.env != null && (typeof spec.env !== "object" || Array.isArray(spec.env))) {
    throw new Error(`checker env config must be an object: ${JSON.stringify(spec)}`);
  }
  return { ...(spec.env || {}) };
}

function buildChecker(CheckerClass, { kwargs = null, env = null } = {}) {
  const checkerKwargs = { ...(kwargs || {}) };
  const checkerEnv = { ...(env || {}) };
  try {
    return new CheckerClass({ env: checkerEnv, ...checkerKwargs });
  } catch (_) {
    const checker = new CheckerClass();
    if (typeof checker.bind_config === "function") {
      checker.bind_config({ env: checkerEnv, ...checkerKwargs });
    }
    return checker;
  }
}

class CheckerManager {
  constructor({ checkers = null, config = null } = {}) {
    this.checkers_by_phase = checkers ? { global: [...checkers] } : buildCheckersByPhase(loadCheckerConfig(config));
    this.refresh();
  }

  update_config(config = null) {
    this.checkers_by_phase = buildCheckersByPhase(loadCheckerConfig(config));
    this.refresh();
  }

  updateConfig(config = null) {
    this.update_config(config);
  }

  add(checker, phase = null) {
    const target = phase || inferPhase(checker);
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

function inferPhase(checker) {
  for (const eventType of checker.event_types || []) {
    const phase = EVENT_PHASE[eventType];
    if (phase) {
      return phase;
    }
  }
  return "global";
}

module.exports = {
  PHASE_ORDER,
  CheckerManager,
  defaultCheckers,
  loadCheckerConfig,
  load_checker_config: loadCheckerConfig,
};

"use strict";

const fs = require("fs");
const { CheckResult, BasePlugin } = require("./base");
const { getPluginClass, discoverPlugins } = require("./registry");
const { LLMInputPlugin } = require("./llm_before/llm_input");
const { LLMOutputPlugin } = require("./llm_after/llm_output");
const { ToolInvokePlugin } = require("./tool_before/tool_invoke");
const { ToolResultPlugin } = require("./tool_after/tool_result");

const PHASE_ORDER = ["llm_before", "llm_after", "tool_before", "tool_after", "global"];
const EVENT_PHASE = {
  llm_input: "llm_before",
  llm_output: "llm_after",
  tool_invoke: "tool_before",
  tool_result: "tool_after",
};
const BUILTIN_PLUGINS = {
  llm_input: LLMInputPlugin,
  llm_output: LLMOutputPlugin,
  tool_invoke: ToolInvokePlugin,
  tool_result: ToolResultPlugin,
};

function defaultPlugins() {
  return [];
}

function loadPluginConfig(source = null) {
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
    throw new Error("plugin config must contain a 'phases' object");
  }
  const config = {};
  for (const phase of PHASE_ORDER) {
    if (phase in phases) {
      config[phase] = pluginSpecsForScope(phases[phase], "client");
    }
  }
  return config;
}

function pluginSpecsForScope(value, scope) {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new Error("plugin phase config must be an object with 'client' and 'server'");
  }
  if (!hasScope(value, "client") || !hasScope(value, "server")) {
    throw new Error("plugin phase config must include both 'client' and 'server'");
  }
  const specs = scopeValue(value, scope);
  if (specs == null) {
    return [];
  }
  if (!Array.isArray(specs)) {
    throw new Error(`plugin phase '${scope}' config must be a list`);
  }
  return [...specs];
}

function hasScope(value, scope) {
  return Object.prototype.hasOwnProperty.call(value, scope)
    || Object.prototype.hasOwnProperty.call(value, legacyScope(scope));
}

function scopeValue(value, scope) {
  if (Object.prototype.hasOwnProperty.call(value, scope)) {
    return value[scope];
  }
  return value[legacyScope(scope)];
}

function legacyScope(scope) {
  return scope === "client" ? "local" : "remote";
}

function buildPluginsByPhase(config = null) {
  if (!config) {
    return {};
  }
  const result = {};
  for (const [phase, specs] of Object.entries(config)) {
    result[phase] = specs.map(instantiatePlugin);
  }
  return result;
}

function instantiatePlugin(spec) {
  if (spec instanceof BasePlugin) {
    return spec;
  }
  if (typeof spec === "function") {
    return buildPlugin(spec);
  }
  if (typeof spec === "string") {
    discoverPlugins();
    const PluginClass = BUILTIN_PLUGINS[spec] || getPluginClass(spec);
    if (!PluginClass) {
      throw new Error(`invalid plugin config entry: ${String(spec)}`);
    }
    return buildPlugin(PluginClass);
  }
  if (spec && typeof spec === "object") {
    const target = spec.class || spec.plugin || spec.name;
    const kwargs = pluginKwargs(spec);
    const env = pluginEnv(spec);
    const PluginClass = typeof target === "function" ? target : BUILTIN_PLUGINS[target] || getPluginClass(target);
    if (!PluginClass) {
      throw new Error(`invalid plugin config entry: ${JSON.stringify(spec)}`);
    }
    return buildPlugin(PluginClass, { kwargs, env });
  }
  throw new Error(`invalid plugin config entry: ${String(spec)}`);
}

function pluginKwargs(spec) {
  const reserved = new Set(["class", "plugin", "name", "kwargs", "env"]);
  const kwargs = Object.fromEntries(Object.entries(spec).filter(([key]) => !reserved.has(key)));
  if (spec.kwargs != null && (typeof spec.kwargs !== "object" || Array.isArray(spec.kwargs))) {
    throw new Error(`plugin kwargs config must be an object: ${JSON.stringify(spec)}`);
  }
  return { ...kwargs, ...(spec.kwargs || {}) };
}

function pluginEnv(spec) {
  if (spec.env != null && (typeof spec.env !== "object" || Array.isArray(spec.env))) {
    throw new Error(`plugin env config must be an object: ${JSON.stringify(spec)}`);
  }
  return { ...(spec.env || {}) };
}

function buildPlugin(PluginClass, { kwargs = null, env = null } = {}) {
  const pluginKwargs = { ...(kwargs || {}) };
  const pluginEnv = { ...(env || {}) };
  try {
    return new PluginClass({ env: pluginEnv, ...pluginKwargs });
  } catch (_) {
    const plugin = new PluginClass();
    if (typeof plugin.bind_config === "function") {
      plugin.bind_config({ env: pluginEnv, ...pluginKwargs });
    }
    return plugin;
  }
}

class PluginManager {
  constructor({ plugins = null, config = null } = {}) {
    this.plugins_by_phase = plugins ? { global: [...plugins] } : buildPluginsByPhase(loadPluginConfig(config));
    this.refresh();
  }

  update_config(config = null) {
    this.plugins_by_phase = buildPluginsByPhase(loadPluginConfig(config));
    this.refresh();
  }

  updateConfig(config = null) {
    this.update_config(config);
  }

  add(plugin, phase = null) {
    const target = phase || inferPhase(plugin);
    this.plugins_by_phase[target] = this.plugins_by_phase[target] || [];
    this.plugins_by_phase[target].push(plugin);
    this.plugins.push(plugin);
  }

  refresh() {
    this.plugins = PHASE_ORDER.flatMap((phase) => this.plugins_by_phase[phase] || []);
  }

  run(event, context) {
    const phase = EVENT_PHASE[event.event_type] || "global";
    const phasePlugins = [...(this.plugins_by_phase[phase] || []), ...(this.plugins_by_phase.global || [])];
    const mergedSignals = [];
    let candidate = null;
    let isFinal = false;
    const metadata = {};
    for (const plugin of phasePlugins) {
      if (!plugin.applies(event)) {
        continue;
      }
      try {
        const result = plugin.check(event, context);
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
        metadata[`${plugin.name}_error`] = String(error.message || error);
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

function inferPhase(plugin) {
  for (const eventType of plugin.event_types || []) {
    const phase = EVENT_PHASE[eventType];
    if (phase) {
      return phase;
    }
  }
  return "global";
}

module.exports = {
  PHASE_ORDER,
  PluginManager,
  defaultPlugins,
  loadPluginConfig,
  load_plugin_config: loadPluginConfig,
};

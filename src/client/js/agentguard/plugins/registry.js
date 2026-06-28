"use strict";

const PLUGINS = new Map();
const DESCRIPTIONS = new Map();

let DISCOVERED = false;

function register(name, description) {
  if (!name) {
    throw new Error("plugin registration name must not be empty");
  }
  return (PluginClass) => {
    PluginClass.prototype.name = name;
    PluginClass.prototype.description = description;
    PLUGINS.set(name, PluginClass);
    DESCRIPTIONS.set(name, description);
    return PluginClass;
  };
}

function getPluginClass(name) {
  discoverPlugins();
  return PLUGINS.get(name) || null;
}

function pluginDescriptions() {
  discoverPlugins();
  return Object.fromEntries(DESCRIPTIONS.entries());
}

function registeredPlugins() {
  discoverPlugins();
  return Object.fromEntries(PLUGINS.entries());
}

function discoverPlugins() {
  if (DISCOVERED) {
    return;
  }
  DISCOVERED = true;
  require("./llm_before/jailbreak_check");
  require("./llm_after/llm_output");
  require("./llm_after/llm_thought");
  require("./llm_after/final_response");
  require("./tool_before/tool_invoke");
  require("./tool_after/tool_result");
}

module.exports = {
  register,
  getPluginClass,
  pluginDescriptions,
  registeredPlugins,
  discoverPlugins,
};

"use strict";

const { PluginRegistry } = require("./registry");
const { TRANSFORM_HOOKS, NOTIFY_HOOKS } = require("./protocol");

class PluginManager {
  constructor(lifecycle) {
    this.lifecycle = lifecycle;
    this.registry = new PluginRegistry();
  }

  register(plugin) {
    this.registry.add(plugin);
    for (const hook of TRANSFORM_HOOKS) {
      if (typeof plugin[hook] === "function") {
        this.lifecycle.register(hook, plugin[hook].bind(plugin));
      }
    }
    for (const hook of NOTIFY_HOOKS) {
      if (typeof plugin[hook] === "function") {
        this.lifecycle.register(hook, plugin[hook].bind(plugin));
      }
    }
    return plugin;
  }

  start_session(context) {
    this.lifecycle.notify("on_session_start", context);
  }

  end_session(trace, context) {
    this.lifecycle.notify("on_session_end", trace, context);
  }

  plugins() {
    return this.registry.all();
  }
}

module.exports = {
  PluginManager,
};

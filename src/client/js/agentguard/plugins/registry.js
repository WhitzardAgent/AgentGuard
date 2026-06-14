"use strict";

class PluginRegistry {
  constructor() {
    this.items = [];
  }

  add(plugin) {
    this.items.push(plugin);
    return plugin;
  }

  all() {
    return [...this.items];
  }
}

module.exports = {
  PluginRegistry,
};

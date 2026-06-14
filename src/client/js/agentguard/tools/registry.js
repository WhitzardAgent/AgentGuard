"use strict";

const { ToolMetadata } = require("./metadata");

class RegisteredTool {
  constructor(fn, metadata) {
    this.fn = fn;
    this.metadata = metadata;
  }
}

class ToolRegistry {
  constructor() {
    this.tools = new Map();
  }

  register(fn, metadata = null, overrides = {}) {
    const meta = metadata || ToolMetadata.infer(fn, overrides);
    this.tools.set(meta.name, new RegisteredTool(fn, meta));
    return meta;
  }

  get(name) {
    return this.tools.get(name) || null;
  }

  names() {
    return [...this.tools.keys()];
  }

  metadata(name) {
    const item = this.tools.get(name);
    return item ? item.metadata : null;
  }

  has(name) {
    return this.tools.has(name);
  }
}

module.exports = {
  RegisteredTool,
  ToolRegistry,
};

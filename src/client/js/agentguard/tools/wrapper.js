"use strict";

class ToolWrapper {
  constructor(fn, metadata, runtime) {
    this._fn = fn;
    this.metadata = metadata;
    this._runtime = runtime;
  }

  get name() {
    return this.metadata.name;
  }

  call(...args) {
    return this.invoke(...args);
  }

  invoke(...args) {
    let kwargs = {};
    if (args.length === 1 && args[0] && typeof args[0] === "object" && !Array.isArray(args[0])) {
      kwargs = args[0];
    } else if (args.length) {
      kwargs = { _args: args };
    }
    return this._runtime.invoke_tool({
      tool_name: this.metadata.name,
      arguments: kwargs,
      fn: this._fn,
      metadata: this.metadata,
    });
  }
}

module.exports = {
  ToolWrapper,
};

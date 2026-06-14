"use strict";

function inferRequiredArgs(fn) {
  if (typeof fn !== "function") {
    return [];
  }
  return [...fn.toString().matchAll(/(?:function[^(]*\(|=>\s*|^[^(]*\()([^)]*)\)/g)]
    .slice(0, 1)
    .flatMap((match) => (match[1] || "").split(","))
    .map((part) => part.trim().replace(/=.*$/, ""))
    .filter(Boolean);
}

class ToolMetadata {
  constructor(data = {}) {
    this.name = data.name || "tool";
    this.description = data.description || "";
    this.capabilities = [...(data.capabilities || [])];
    this.required_args = [...(data.required_args || [])];
    this.degraded_to = data.degraded_to ?? null;
    this.is_async = Boolean(data.is_async);
    this.schema = { ...(data.schema || {}) };
    this.metadata = { ...(data.metadata || {}) };
  }

  toDict() {
    return {
      name: this.name,
      description: this.description,
      capabilities: [...this.capabilities],
      required_args: [...this.required_args],
      degraded_to: this.degraded_to,
      is_async: this.is_async,
      schema: { ...this.schema },
      metadata: { ...this.metadata },
    };
  }

  static infer(fn, overrides = {}) {
    const name = overrides.name || fn.name || "tool";
    const description = overrides.description || "";
    return new ToolMetadata({
      name,
      description: description.split("\n")[0],
      required_args: overrides.required_args || inferRequiredArgs(fn),
      is_async: fn && fn.constructor && fn.constructor.name === "AsyncFunction",
      capabilities: overrides.capabilities || [],
      degraded_to: overrides.degraded_to || null,
      schema: overrides.schema || {},
      metadata: overrides.metadata || {},
    });
  }
}

module.exports = {
  ToolMetadata,
};

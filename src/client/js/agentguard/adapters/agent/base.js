"use strict";

class BaseAgentAdapter {
  constructor() {
    this.name = "base";
  }

  can_wrap() {
    return false;
  }

  generate() {
    throw new Error("generate() must be implemented");
  }

  attach(agent, guard, { wrap_tools = true, wrap_llm = true } = {}) {
    const patched = { tools: 0, llm: 0 };
    if (wrap_tools) {
      patched.tools += this.patchtool(agent, guard);
    }
    if (wrap_llm) {
      patched.llm += this.patchLLM(agent, guard);
    }
    return patched;
  }

  patchtool() {
    return 0;
  }

  patchLLM() {
    return 0;
  }
}

module.exports = {
  BaseAgentAdapter,
};

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

  attach() {
    return { tools: 0, llm: 0 };
  }
}

module.exports = {
  BaseAgentAdapter,
};

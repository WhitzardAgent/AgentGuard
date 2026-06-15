"use strict";

const { BaseAgentAdapter } = require("./base");
const { patchLLMMethods } = require("./patching");

class AutogenAgentAdapter extends BaseAgentAdapter {
  constructor() {
    super();
    this.name = "autogen";
  }

  can_wrap(agent) {
    return Boolean(agent && typeof agent === "object");
  }

  attach(agent, guard, { wrap_llm = true } = {}) {
    return {
      tools: 0,
      llm: wrap_llm ? patchLLMMethods(guard, agent) : 0,
    };
  }
}

module.exports = {
  AutogenAgentAdapter,
};

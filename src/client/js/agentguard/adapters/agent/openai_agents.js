"use strict";

const { BaseAgentAdapter } = require("./base");
const { patchLLMMethods } = require("./patching");

class OpenAIAgentsAdapter extends BaseAgentAdapter {
  constructor() {
    super();
    this.name = "openai_agents";
  }

  can_wrap(agent) {
    return Boolean(agent && (agent.run || agent.invoke));
  }

  attach(agent, guard, { wrap_llm = true } = {}) {
    return {
      tools: 0,
      llm: wrap_llm ? patchLLMMethods(guard, agent) : 0,
    };
  }
}

module.exports = {
  OpenAIAgentsAdapter,
};

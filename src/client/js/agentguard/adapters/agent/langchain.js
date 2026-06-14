"use strict";

const { BaseAgentAdapter } = require("./base");
const {
  isGuarded,
  makeGuardedTool,
  patchLLMMethods,
  setAttr,
  toolName,
} = require("./patching");
const { AdapterError } = require("../../utils/errors");

function moduleName(obj) {
  return (obj && obj.constructor && obj.constructor.name ? obj.constructor.name : "").toLowerCase();
}

class LangChainAgentAdapter extends BaseAgentAdapter {
  constructor() {
    super();
    this.name = "langchain";
  }

  can_wrap(agent) {
    const name = moduleName(agent);
    return name.includes("langchain") || name.includes("langgraph");
  }

  async generate(agent, messages) {
    const prompt = messages.length ? messages[messages.length - 1].content || "" : "";
    for (const method of ["invoke", "run", "predict"]) {
      if (typeof agent[method] === "function") {
        try {
          return await agent[method](prompt);
        } catch (error) {
          throw new AdapterError(`langchain agent invoke failed: ${String(error.message || error)}`);
        }
      }
    }
    throw new AdapterError("langchain agent exposes no invoke/run/predict");
  }

  attach(agent, guard, { wrap_tools = true, wrap_llm = true } = {}) {
    const patched = { tools: 0, llm: 0 };
    if (wrap_tools) {
      patched.tools += patchContainerTools(agent, guard);
    }
    if (wrap_llm) {
      patched.llm += patchLLMMethods(guard, agent);
      for (const candidate of [agent.model, agent.llm, agent.runnable]) {
        if (candidate) {
          patched.llm += patchLLMMethods(guard, candidate);
        }
      }
    }
    return patched;
  }
}

function patchContainerTools(container, guard) {
  if (!container) {
    return 0;
  }
  let patched = 0;
  for (const attr of ["tools_by_name", "_tools_by_name", "tools", "_tools"]) {
    const tools = container[attr];
    if (Array.isArray(tools)) {
      tools.forEach((tool, index) => {
        patched += patchToolObject(tool, guard, toolName(tool, null, `tool_${index}`));
      });
      continue;
    }
    if (tools && typeof tools === "object") {
      for (const [name, tool] of Object.entries(tools)) {
        if (typeof tool === "function" && typeof tool.invoke !== "function") {
          tools[name] = makeGuardedTool(guard, tool, { name, tool });
          patched += 1;
        } else {
          patched += patchToolObject(tool, guard, String(name));
        }
      }
    }
  }
  return patched;
}

function patchToolObject(tool, guard, name) {
  if (!tool || isGuarded(tool)) {
    return 0;
  }
  for (const attr of ["invoke", "ainvoke", "_run", "_arun", "func", "coroutine"]) {
    const fn = tool[attr];
    if (typeof fn !== "function" || isGuarded(fn)) {
      continue;
    }
    if (setAttr(tool, attr, makeGuardedTool(guard, fn.bind(tool), { name, tool }))) {
      return 1;
    }
  }
  return 0;
}

module.exports = {
  LangChainAgentAdapter,
};

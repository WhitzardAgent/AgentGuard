"use strict";

const { BaseAgentAdapter } = require("./base");
const {
  isGuarded,
  makeGuardedTool,
  markPatched,
  patchLLMMethods,
  setAttr,
  toolName,
} = require("./patching");
const { AdapterError } = require("../../utils/errors");

const FUNC_ATTRS = ["func", "_func"];

class AutogenAgentAdapter extends BaseAgentAdapter {
  constructor() {
    super();
    this.name = "autogen";
  }

  can_wrap(agent) {
    return Boolean(agent && typeof agent === "object" && String(agent.constructor?.module || agent.constructor?.name || "").toLowerCase().includes("autogen"));
  }

  generate(agent, messages) {
    const fn = agent && agent.generate_reply;
    if (typeof fn === "function") {
      try {
        return fn.call(agent, { messages });
      } catch (error) {
        throw new AdapterError(`autogen generate_reply failed: ${String(error.message || error)}`);
      }
    }
    throw new AdapterError("autogen agent exposes no generate_reply");
  }

  patchLLM(agent, guard) {
    const modelClient = agent && agent._model_client;
    if (!modelClient) {
      return 0;
    }
    const typeName = String(modelClient.constructor?.name || "");
    let methods = [];
    if (typeName === "BaseOpenAIChatCompletionClient") {
      methods = ["_client.beta.chat.completions.parse", "_client.chat.completions.create", "_client.beta.chat.completions.stream"];
    } else if (typeName === "BaseOllamaChatCompletionClient") {
      methods = ["_client.chat"];
    } else if (typeName === "BaseAnthropicChatCompletionClient") {
      methods = ["_client.messages.create"];
    } else if (typeName === "AzureAIChatCompletionClient") {
      methods = ["_client.complete"];
    } else if (typeName === "LlamaCppChatCompletionClient") {
      methods = ["llm.create_chat_completion"];
    } else {
      methods = ["create", "create_stream", "complete", "completion", "generate", "invoke", "predict", "chat"];
    }
    return patchLLMMethods(guard, modelClient, { methods });
  }

  patchtool(agent, guard) {
    let patched = 0;
    const toolsList = agent && agent._tools;
    if (Array.isArray(toolsList)) {
      patched += this.patchToolsList(toolsList, guard);
    }
    const handoffs = agent && agent._handoffs;
    if (Array.isArray(handoffs)) {
      patched += this.patchToolsList(handoffs, guard);
    }
    const registry = agent && agent.function_map;
    if (registry && typeof registry === "object") {
      patched += this.patchFunctionMap(registry, guard);
    }
    if (agent && typeof agent.register_function === "function") {
      patched += this.patchRegisterFunction(agent, guard);
    }
    return patched;
  }

  patchToolsList(toolsList, guard) {
    let patched = 0;
    toolsList.forEach((tool, index) => {
      if (isGuarded(tool)) {
        return;
      }
      const [fn, attr] = extractToolFn(tool);
      if (fn && attr) {
        const name = toolName(tool, fn, `tool_${index}`);
        const wrapped = makeGuardedTool(guard, fn, { name, tool });
        if (setAttr(tool, attr, wrapped)) {
          markPatched(tool);
        } else {
          toolsList[index] = wrapped;
        }
        patched += 1;
        return;
      }
      const runJson = tool && tool.run_json;
      if (typeof runJson === "function" && !isGuarded(runJson)) {
        const name = toolName(tool, runJson, `tool_${index}`);
        const wrapped = makeGuardedTool(guard, runJson, { name, tool });
        if (setAttr(tool, "run_json", wrapped)) {
          markPatched(tool);
          patched += 1;
        }
        return;
      }
      if (typeof tool === "function") {
        const name = toolName(tool, null, `tool_${index}`);
        toolsList[index] = makeGuardedTool(guard, tool, { name, tool });
        patched += 1;
      }
    });
    return patched;
  }

  patchFunctionMap(registry, guard) {
    let patched = 0;
    for (const [name, fn] of Object.entries(registry)) {
      if (typeof fn !== "function" || isGuarded(fn)) {
        continue;
      }
      registry[name] = makeGuardedTool(guard, fn, { name, tool: fn });
      patched += 1;
    }
    return patched;
  }

  patchRegisterFunction(agent, guard) {
    const original = agent.register_function;
    if (typeof original !== "function" || isGuarded(original)) {
      return 0;
    }
    function patchedRegister(func = null, ...rest) {
      let wrapped = func;
      if (typeof func === "function" && !isGuarded(func)) {
        wrapped = makeGuardedTool(guard, func, { name: toolName(func), tool: func });
      }
      return original.call(agent, wrapped, ...rest);
    }
    setAttr(agent, "register_function", patchedRegister);
    return 1;
  }
}

function extractToolFn(tool) {
  for (const attr of FUNC_ATTRS) {
    const fn = tool && tool[attr];
    if (typeof fn === "function" && !isGuarded(fn)) {
      return [fn, attr];
    }
  }
  return [null, null];
}

module.exports = {
  AutogenAgentAdapter,
};

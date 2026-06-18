"use strict";

const ev = require("../../schemas/events");
const { BaseAgentAdapter } = require("./base");
const {
  isGuarded,
  makeGuardedTool,
  markGuarded,
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
    return name.includes("langchain") || name.includes("langgraph") || name.includes("reactagent");
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

  patchtool(agent, guard) {
    let patched = 0;
    patched += patchContainerTools(agent, guard);
    for (const [, toolNode] of iterToolNodes(agent)) {
      patched += patchToolNode(toolNode, guard);
    }
    const nodes = agent?.nodes || agent?._nodes;
    const iterable = nodes && typeof nodes === "object"
      ? (Array.isArray(nodes) ? nodes : Object.values(nodes))
      : [];
    for (const node of iterable) {
      patched += patchContainerTools(node, guard);
      if (node && node.runnable) {
        patched += patchContainerTools(node.runnable, guard);
      }
    }
    return patched;
  }

  patchLLM(agent, guard) {
    return patchLangchainLLM(agent, guard);
  }
}

function iterToolNodes(agent) {
  const toolNodes = [];
  const seen = new Set();
  const compiledNodes = agent && agent.nodes;
  if (compiledNodes && typeof compiledNodes === "object" && !Array.isArray(compiledNodes)) {
    for (const [name, node] of Object.entries(compiledNodes)) {
      const toolNode = node && node.bound;
      if (!toolNode || typeof toolNode.tools_by_name !== "object") {
        continue;
      }
      if (seen.has(toolNode)) {
        continue;
      }
      seen.add(toolNode);
      toolNodes.push([String(name), toolNode]);
    }
  }
  const builderNodes = agent && agent.builder && agent.builder.nodes;
  if (builderNodes && typeof builderNodes === "object" && !Array.isArray(builderNodes)) {
    for (const [name, node] of Object.entries(builderNodes)) {
      const toolNode = node && node.data;
      if (!toolNode || typeof toolNode.tools_by_name !== "object") {
        continue;
      }
      if (seen.has(toolNode)) {
        continue;
      }
      seen.add(toolNode);
      toolNodes.push([String(name), toolNode]);
    }
  }
  return toolNodes;
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
        if (typeof tool === "function" && typeof tool.invoke !== "function") {
          tools[index] = makeGuardedTool(guard, tool, { name: toolName(tool, null, `tool_${index}`), tool });
          patched += 1;
        } else {
          patched += patchToolObject(tool, guard, toolName(tool, null, `tool_${index}`));
        }
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
  if (container.options && container.options !== container) {
    patched += patchContainerTools(container.options, guard);
  }
  return patched;
}

function patchToolNode(toolNode, guard) {
  const toolsByName = toolNode && toolNode.tools_by_name;
  if (!toolsByName || typeof toolsByName !== "object") {
    return 0;
  }
  let patched = 0;
  for (const [name, tool] of Object.entries(toolsByName)) {
    patched += patchToolObject(tool, guard, String(name));
  }
  return patched;
}

function patchToolObject(tool, guard, name) {
  if (!tool || isGuarded(tool)) {
    return 0;
  }
  for (const spec of [
    { attrs: ["invoke", "ainvoke"], bind: true },
    { attrs: ["_run", "_arun"], bind: true },
    { attrs: ["func", "coroutine"], bind: false },
  ]) {
    let patched = false;
    for (const attr of spec.attrs) {
      const fn = tool[attr];
      if (typeof fn !== "function" || isGuarded(fn)) {
        continue;
      }
      const target = spec.bind ? fn.bind(tool) : fn;
      if (setAttr(tool, attr, makeGuardedTool(guard, target, { name, tool }))) {
        patched = true;
      }
    }
    if (patched) {
      return 1;
    }
  }
  return 0;
}

function patchLangchainLLM(agent, guard) {
  const baseModel = getLangchainBaseModel(agent);
  if (!baseModel) {
    return 0;
  }
  let patched = patchLangchainBindTools(baseModel, guard);
  if (patched > 0) {
    return patched;
  }
  const target = unwrapLangchainLLMTarget(baseModel);
  if (!target) {
    return 0;
  }
  return patchLangchainConcreteLLM(target, guard);
}

function getLangchainModelRunnable(agent) {
  const directRunnable = agent?.builder?.nodes?.model_request?.runnable
    || agent?.nodes?.model_request?.runnable
    || agent?.builder?.nodes?.model?.runnable
    || agent?.nodes?.model?.runnable;
  if (directRunnable) {
    return directRunnable;
  }
  for (const owner of [agent, agent && agent.builder]) {
    const nodes = owner && owner.nodes;
    if (!nodes || typeof nodes !== "object" || Array.isArray(nodes)) {
      continue;
    }
    const modelNode = nodes.model || nodes.model_request;
    if (modelNode && modelNode.runnable) {
      return modelNode.runnable;
    }
  }
  return null;
}

function getLangchainBaseModel(agent) {
  const directAgentModel = agent?.model;
  if (directAgentModel && typeof directAgentModel === "object") {
    return directAgentModel;
  }

  const directOptionsModel = agent?.options?.model;
  if (directOptionsModel && typeof directOptionsModel === "object") {
    return directOptionsModel;
  }

  const chainModel = agent?.agent?.llm_chain?.llm;
  if (chainModel && typeof chainModel === "object") {
    return chainModel;
  }

  const runnable = getLangchainModelRunnable(agent);
  if (!runnable) {
    return null;
  }
  for (const attr of ["func", "afunc"]) {
    const fn = runnable[attr];
    const model = extractLangchainClosureModel(fn);
    if (model) {
      return model;
    }
  }
  return null;
}

function extractLangchainClosureModel(fn) {
  if (typeof fn !== "function") {
    return null;
  }
  try {
    return fn();
  } catch (_) {
    return null;
  }
}

function unwrapLangchainLLMTarget(model) {
  const seen = new Set();
  let current = model;
  while (current && !seen.has(current)) {
    seen.add(current);
    const inner = current.bound;
    if (!inner || inner === current) {
      return current;
    }
    current = inner;
  }
  return current;
}

function patchLangchainBindTools(model, guard) {
  const seen = new Set();
  let patched = 0;
  let current = model;
  while (current && !seen.has(current)) {
    seen.add(current);
    patched += patchBindToolsMethod(current, guard);
    const inner = current.bound;
    if (!inner || inner === current) {
      break;
    }
    current = inner;
  }
  return patched;
}

function patchBindToolsMethod(model, guard) {
  const bindTools = model && model.bindTools;
  if (typeof bindTools !== "function" || isGuarded(bindTools)) {
    return 0;
  }
  const wrapped = async (...args) => {
    const bound = await bindTools.apply(model, args);
    patchLangchainConcreteLLM(bound, guard);
    return bound;
  };
  return setAttr(model, "bindTools", markGuarded(wrapped)) ? 1 : 0;
}

function patchLangchainConcreteLLM(model, guard) {
  const target = unwrapLangchainLLMTarget(model);
  if (!target) {
    return 0;
  }
  let patched = 0;
  for (const attr of ["invoke", "ainvoke"]) {
    const fn = target && target[attr];
    if (typeof fn !== "function" || isGuarded(fn)) {
      continue;
    }
    if (setAttr(target, attr, makeGuardedLangchainLLMMethod(guard, fn.bind(target), { owner: target, label: attr }))) {
      patched += 1;
    }
  }
  return patched;
}

function makeGuardedLangchainLLMMethod(guard, fn, { owner, label }) {
  const wrapper = async (...args) => {
    try {
      const payload = normalizeLangchainRequest(args, {});
      await guard.runtime.guard(ev.llm_input(guard.context, payload, {
        adapter: "langchain",
        label,
        owner_type: owner?.constructor?.name,
      }));
      const raw = await fn(...args);
      const decision = (await guard.runtime.guard(
        ev.llm_output(guard.context, normalizeLangchainValue(raw), {
          adapter: "langchain",
          label,
          owner_type: owner?.constructor?.name,
        }),
        { phase: "after" }
      )).decision;
      if (decision.decision_type === "deny") {
        return { agentguard: "blocked", reason: decision.reason };
      }
      if (decision.decision_type === "sanitize") {
        return { agentguard: "sanitized", reason: decision.reason };
      }
      return raw;
    } catch (error) {
      await guard.runtime.sync_local_cache_now({ reason: "client_error" });
      throw error;
    } finally {
      guard.runtime.sync_local_cache_async({ reason: "round_complete" });
    }
  };
  return markGuarded(wrapper);
}

function normalizeLangchainRequest(args, kwargs) {
  const modelInput = Object.prototype.hasOwnProperty.call(kwargs, "input") ? kwargs.input : args[0];
  const payload = {
    input: normalizeLangchainValue(modelInput),
  };
  if (Object.prototype.hasOwnProperty.call(kwargs, "config")) {
    payload.config = normalizeLangchainValue(kwargs.config);
  }
  if (Object.prototype.hasOwnProperty.call(kwargs, "stop")) {
    payload.stop = normalizeLangchainValue(kwargs.stop);
  }
  const extra = Object.fromEntries(Object.entries(kwargs).filter(([key]) => !["input", "config", "stop"].includes(key)));
  if (Object.keys(extra).length) {
    payload.kwargs = normalizeLangchainValue(extra);
  }
  return payload;
}

function normalizeLangchainValue(value) {
  if (value == null || ["boolean", "number", "string"].includes(typeof value)) {
    return value;
  }
  if (Array.isArray(value)) {
    return value.map(normalizeLangchainValue);
  }
  if (typeof value === "object") {
    if (typeof value.model_dump === "function") {
      try {
        return value.model_dump();
      } catch (_) {}
    }
    if (typeof value.to_dict === "function") {
      try {
        return value.to_dict();
      } catch (_) {}
    }
    if (Object.prototype.hasOwnProperty.call(value, "content")) {
      const out = {
        type: value.constructor?.name || "Object",
        content: normalizeLangchainValue(value.content),
      };
      for (const attr of ["name", "id", "tool_calls", "invalid_tool_calls", "response_metadata"]) {
        if (value[attr]) {
          out[attr] = normalizeLangchainValue(value[attr]);
        }
      }
      return out;
    }
    return Object.fromEntries(Object.entries(value).map(([key, item]) => [String(key), normalizeLangchainValue(item)]));
  }
  return String(value);
}

module.exports = {
  LangChainAgentAdapter,
};

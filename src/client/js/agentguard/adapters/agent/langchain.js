"use strict";

const ev = require("../../schemas/events");
const { DecisionType } = require("../../schemas/decisions");
const { BaseAgentAdapter } = require("./base");
const {
  bindArguments,
  isGuarded,
  makeGuardedTool,
  markGuarded,
  registerToolMetadata,
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
  // Prefer raw tool callables so AgentGuard sees business arguments such as
  // `path`/`url` instead of LangChain's generic `input` wrapper.
  if (patchToolAttrs(tool, guard, name, { attrs: ["func", "coroutine"], bind: false })) {
    return 1;
  }
  if (patchToolAttrs(tool, guard, name, { attrs: ["_run", "_arun"], bind: true })) {
    return 1;
  }
  if (patchToolAttrs(tool, guard, name, { attrs: ["invoke", "ainvoke"], bind: true })) {
    return 1;
  }
  return 0;
}

function patchToolAttrs(tool, guard, name, { attrs, bind }) {
  let patched = false;
  for (const attr of attrs) {
    const fn = tool[attr];
    if (typeof fn !== "function" || isGuarded(fn)) {
      continue;
    }
    const target = bind ? fn.bind(tool) : fn;
    const wrapped = makeGuardedLangchainToolMethod(guard, target, { name, tool, attr });
    if (setAttr(tool, attr, wrapped)) {
      patched = true;
    }
  }
  return patched;
}

function makeGuardedLangchainToolMethod(guard, fn, { name, tool = null, attr = "invoke" } = {}) {
  const metadata = registerToolMetadata(guard, fn, { name, tool });
  const wrapper = async (...args) => {
    const toolCall = extractLangchainToolCall(args);
    const eventMetadata = buildLangchainToolEventMetadata({ attr, toolCall });
    try {
      const arguments_ = buildLangchainToolArguments(fn, args, toolCall);
      const invokeDecision = await guardToolBeforeWithMetadata(guard, metadata, arguments_, eventMetadata);
      const blockedInvoke = blockedLangchainToolValue(invokeDecision, metadata.name, toolCall);
      if (blockedInvoke) {
        return blockedInvoke;
      }
      let value;
      try {
        value = await fn(...args);
      } catch (error) {
        await guardToolAfterWithMetadata(
          guard,
          metadata.name,
          null,
          eventMetadata,
          { error: String(error?.message || error) }
        );
        throw error;
      }
      const resultDecision = await guardToolAfterWithMetadata(guard, metadata.name, value, eventMetadata);
      return blockedLangchainResultValue(resultDecision, metadata.name, toolCall) || value;
    } catch (error) {
      await guard.runtime.sync_local_cache_now({ reason: "client_error" });
      throw error;
    } finally {
      guard.runtime.sync_local_cache_async({ reason: "round_complete" });
    }
  };
  return markGuarded(wrapper);
}

async function guardToolBeforeWithMetadata(guard, metadata, arguments_, eventMetadata) {
  return (
    await guard.runtime.guard(
      ev.tool_invoke(guard.context, metadata.name, arguments_, {
        capabilities: [...(metadata.capabilities || [])],
        metadata: eventMetadata,
      })
    )
  ).decision;
}

async function guardToolAfterWithMetadata(guard, toolName, result, eventMetadata, { error = null } = {}) {
  return (
    await guard.runtime.guard(
      ev.tool_result(guard.context, toolName, result, {
        error,
        metadata: eventMetadata,
      }),
      { phase: "after" }
    )
  ).decision;
}

function buildLangchainToolArguments(fn, args, toolCall) {
  if (toolCall && Object.prototype.hasOwnProperty.call(toolCall, "args")) {
    return normalizeLangchainValue(toolCall.args);
  }
  return normalizeLangchainValue(bindArguments(fn, args));
}

function buildLangchainToolEventMetadata({ attr, toolCall }) {
  const metadata = {
    adapter: "langchain",
    call_attr: attr,
  };
  if (toolCall) {
    metadata.langchain_tool_call = normalizeLangchainValue({
      id: toolCall.id || null,
      name: toolCall.name || null,
      type: toolCall.type || "tool_call",
      args: toolCall.args,
    });
  }
  return metadata;
}

function blockedLangchainToolValue(decision, toolName, toolCall) {
  if (decision.decision_type === DecisionType.DENY) {
    return makeLangchainBlockedValue({ agentguard: "blocked", tool: toolName, reason: decision.reason }, toolName, toolCall);
  }
  if (decision.requires_user || decision.requires_remote) {
    return makeLangchainBlockedValue(
      { agentguard: "pending", tool: toolName, reason: decision.reason, decision: decision.decision_type },
      toolName,
      toolCall
    );
  }
  if (decision.decision_type === DecisionType.DEGRADE) {
    return makeLangchainBlockedValue(
      { agentguard: "degraded", tool: toolName, reason: decision.reason, decision: decision.decision_type },
      toolName,
      toolCall
    );
  }
  return null;
}

function blockedLangchainResultValue(decision, toolName, toolCall) {
  if (decision.decision_type === DecisionType.DENY) {
    return makeLangchainBlockedValue(
      { agentguard: "blocked", tool: toolName, reason: decision.reason, decision: decision.decision_type },
      toolName,
      toolCall
    );
  }
  if (decision.decision_type === DecisionType.SANITIZE) {
    return makeLangchainBlockedValue(
      { agentguard: "sanitized", tool: toolName, reason: decision.reason, decision: decision.decision_type },
      toolName,
      toolCall
    );
  }
  if (decision.requires_user || decision.requires_remote) {
    return makeLangchainBlockedValue(
      { agentguard: "pending", tool: toolName, reason: decision.reason, decision: decision.decision_type },
      toolName,
      toolCall
    );
  }
  return null;
}

function makeLangchainBlockedValue(payload, toolName, toolCall) {
  const ToolMessage = getLangchainToolMessageClass();
  if (ToolMessage && toolCall && toolCall.id) {
    try {
      return new ToolMessage({
        content: JSON.stringify(payload),
        name: toolName,
        tool_call_id: toolCall.id,
      });
    } catch (_) {}
  }
  return payload;
}

function extractLangchainToolCall(args) {
  const stack = [...args];
  while (stack.length) {
    const value = stack.shift();
    if (!value || typeof value !== "object") {
      continue;
    }
    if (isLangchainToolCall(value)) {
      return value;
    }
    if (isLangchainToolCall(value.toolCall)) {
      return value.toolCall;
    }
    if (isLangchainToolCall(value.config && value.config.toolCall)) {
      return value.config.toolCall;
    }
  }
  return null;
}

function isLangchainToolCall(value) {
  return Boolean(
    value
      && typeof value === "object"
      && typeof value.name === "string"
      && Object.prototype.hasOwnProperty.call(value, "args")
  );
}

let _langchainToolMessageClass;
let _langchainToolMessageLoaded = false;

function getLangchainToolMessageClass() {
  if (_langchainToolMessageLoaded) {
    return _langchainToolMessageClass;
  }
  _langchainToolMessageLoaded = true;
  try {
    ({ ToolMessage: _langchainToolMessageClass } = require("@langchain/core/messages"));
  } catch (_) {
    _langchainToolMessageClass = null;
  }
  return _langchainToolMessageClass;
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

"use strict";

const {
  LLMInputNormalization,
  LLMOutputNormalization,
  ToolInvokeNormalization,
  ToolResultNormalization,
} = require("./normalization");
const { BaseAgentAdapter } = require("./base");
const {
  bindArguments,
  guardLLMAfter,
  guardLLMBefore,
  guardToolAfter,
  guardToolBefore,
  isGuarded,
  markGuarded,
  registerToolMetadata,
  setAttr,
  toolName,
} = require("./patching");
const { DecisionType } = require("../../schemas/decisions");
const { AdapterError } = require("../../utils/errors");

function moduleName(obj) {
  const parts = [];
  if (obj && obj.constructor && obj.constructor.name) {
    parts.push(obj.constructor.name);
  }
  if (Array.isArray(obj && obj.lc_namespace)) {
    parts.push(...obj.lc_namespace);
  }
  return parts.join(".").toLowerCase();
}

class LangChainAgentAdapter extends BaseAgentAdapter {
  constructor() {
    super();
    this.name = "langchain";
  }

  _langchainMeta({ label = null, owner = null } = {}) {
    const meta = { adapter: this.name };
    if (label) {
      meta.label = String(label);
    }
    if (owner != null) {
      meta.owner_type = owner && owner.constructor && owner.constructor.name ? owner.constructor.name : typeof owner;
    }
    return meta;
  }

  can_wrap(agent) {
    const name = moduleName(agent);
    return name.includes("langchain") || name.includes("langgraph") || name.includes("reactagent");
  }

  async generate(agent, messages) {
    const prompt = messages.length ? messages[messages.length - 1].content || "" : "";
    for (const method of ["invoke", "run", "predict"]) {
      const fn = agent && agent[method];
      if (typeof fn !== "function") {
        continue;
      }
      try {
        return await fn.call(agent, prompt);
      } catch (error) {
        throw new AdapterError(`langchain agent invoke failed: ${String(error && error.message ? error.message : error)}`);
      }
    }
    throw new AdapterError("langchain agent exposes no invoke/run/predict");
  }

  gettools(agent) {
    const bindings = [];
    bindings.push(...collectContainerTools(agent, this));
    bindings.push(...collectContainerTools(agent && agent.options, this));
    for (const [, toolNode] of iterToolNodes(agent)) {
      bindings.push(...collectToolNode(toolNode, this));
    }

    for (const owner of [agent, agent && agent.builder]) {
      for (const node of iterGraphNodes(owner)) {
        bindings.push(...collectContainerTools(node, this));
        const runnable = node && node.runnable;
        if (runnable != null) {
          bindings.push(...collectContainerTools(runnable, this));
        }
      }
    }
    return bindings;
  }

  getllm(agent) {
    return collectLangchainLLM(agent, this);
  }

  normalize_llm_input({ label, args = [], kwargs = {}, fn = null, owner = null } = {}) {
    void fn;
    return new LLMInputNormalization({
      payload: normalizeLangchainRequest(args, kwargs),
      metadata: this._langchainMeta({ label, owner }),
    });
  }

  normalize_llm_output({ label, output, fn = null, owner = null } = {}) {
    void fn;
    return new LLMOutputNormalization({
      payload: normalizeLangchainOutput(output),
      metadata: this._langchainMeta({ label, owner }),
    });
  }

  normalize_tool_invoke({ tool_metadata, arguments: arguments_ = {}, fn = null, owner = null } = {}) {
    void fn;
    let normalized = normalizeLangchainValue(arguments_);
    if (!normalized || typeof normalized !== "object" || Array.isArray(normalized)) {
      normalized = { args: normalized };
    }
    return new ToolInvokeNormalization({
      arguments: normalized,
      capabilities: [...((tool_metadata && tool_metadata.capabilities) || [])],
      metadata: this._langchainMeta({ owner }),
    });
  }

  normalize_tool_result({ tool_name, result = null, error = null, fn = null, owner = null } = {}) {
    void tool_name;
    void fn;
    return new ToolResultNormalization({
      result: normalizeLangchainValue(result),
      error,
      metadata: this._langchainMeta({ owner }),
    });
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

function collectContainerTools(container, adapter) {
  const bindings = [];

  for (const attr of ["tools_by_name", "_tools_by_name"]) {
    const tools = container && container[attr];
    if (!tools || typeof tools !== "object" || Array.isArray(tools)) {
      continue;
    }
    for (const [name, tool] of Object.entries(tools)) {
      if (typeof tool === "function" && typeof tool.invoke !== "function") {
        bindings.push(
          adapter.buildToolBinding({
            name: String(name),
            fn: tool,
            container: tools,
            key: name,
            tool,
          })
        );
        continue;
      }
      bindings.push(...collectToolObject(tool, adapter, { name: String(name) }));
    }
  }

  for (const attr of ["tools", "_tools"]) {
    const tools = container && container[attr];
    if (tools && typeof tools === "object" && !Array.isArray(tools)) {
      for (const [name, tool] of Object.entries(tools)) {
        if (typeof tool === "function" && typeof tool.invoke !== "function") {
          bindings.push(
            adapter.buildToolBinding({
              name: String(name),
              fn: tool,
              container: tools,
              key: name,
              tool,
            })
          );
          continue;
        }
        bindings.push(...collectToolObject(tool, adapter, { name: String(name) }));
      }
      continue;
    }

    if (!Array.isArray(tools)) {
      continue;
    }
    tools.forEach((tool, index) => {
      if (typeof tool === "function" && typeof tool.invoke !== "function") {
        bindings.push(
          adapter.buildToolBinding({
            name: toolName(tool, null, `tool_${index}`),
            fn: tool,
            container: tools,
            key: index,
            tool,
          })
        );
        return;
      }
      bindings.push(...collectToolObject(tool, adapter, { name: toolName(tool, null, `tool_${index}`) }));
    });
  }

  return bindings;
}

function collectToolNode(toolNode, adapter) {
  const toolsByName = toolNode && toolNode.tools_by_name;
  if (!toolsByName || typeof toolsByName !== "object") {
    return [];
  }

  const bindings = [];
  for (const [name, tool] of Object.entries(toolsByName)) {
    bindings.push(...collectToolObject(tool, adapter, { name: String(name) }));
  }
  return bindings;
}

function collectLangchainLLM(agent, adapter) {
  const modelRunnable = getLangchainModelRunnable(agent);
  if (modelRunnable != null) {
    const bindings = collectLangchainAgentNodeLLM(modelRunnable, adapter);
    if (bindings.length) {
      return bindings;
    }
  }

  const baseModel = getLangchainBaseModel(agent);
  if (baseModel == null) {
    return [];
  }

  const target = unwrapLangchainLLMTarget(baseModel);
  if (target == null) {
    return [];
  }
  return collectLangchainConcreteLLM(target, adapter);
}

function collectLangchainAgentNodeLLM(runnable, adapter) {
  const fn = runnable && runnable.func;
  if (typeof fn !== "function" || isGuarded(fn)) {
    return [];
  }
  return [
    adapter.buildLLMBinding({
      label: "model_request",
      fn,
      owner: runnable,
      attr: "func",
      installer: installLangchainAgentNodeLLMBinding,
      metadata: { logical_id: "langchain:model_request" },
    }),
  ];
}

function iterGraphNodes(owner) {
  const nodes = owner && (owner.nodes || owner._nodes);
  if (Array.isArray(nodes)) {
    return nodes;
  }
  if (nodes && typeof nodes === "object") {
    return Object.values(nodes);
  }
  return [];
}

function getLangchainModelRunnable(agent) {
  for (const owner of [agent, agent && agent.builder]) {
    const nodes = owner && (owner.nodes || owner._nodes);
    if (!nodes || typeof nodes !== "object" || Array.isArray(nodes)) {
      continue;
    }
    for (const key of ["model", "model_request"]) {
      const modelNode = nodes[key];
      if (!modelNode || modelNode.runnable == null) {
        continue;
      }
      return modelNode.runnable;
    }
  }
  return null;
}

function getLangchainBaseModel(agent) {
  const directModel = agent && agent.model;
  if (directModel != null) {
    return directModel;
  }

  const optionsModel = agent && agent.options && agent.options.model;
  if (optionsModel != null) {
    return optionsModel;
  }

  const chainModel = agent && agent.agent && agent.agent.llm_chain && agent.agent.llm_chain.llm;
  if (chainModel != null) {
    return chainModel;
  }

  const runnable = getLangchainModelRunnable(agent);
  if (runnable == null) {
    return null;
  }

  for (const attr of ["func", "afunc"]) {
    const model = extractLangchainClosureModel(runnable[attr]);
    if (model != null) {
      return model;
    }
  }
  return null;
}

function extractLangchainClosureModel(fn) {
  if (typeof fn !== "function") {
    return null;
  }
  for (const attr of ["model", "llm", "bound"]) {
    const candidate = fn[attr];
    if (candidate && typeof candidate === "object") {
      return candidate;
    }
  }
  return null;
}

function collectLangchainConcreteLLM(model, adapter) {
  const target = unwrapLangchainLLMTarget(model);
  if (target == null) {
    return [];
  }
  return adapter.collectLLMMethods(target, { methods: ["invoke", "ainvoke"] });
}

function unwrapLangchainLLMTarget(model) {
  const seen = new Set();
  let current = model;
  while (current != null && !seen.has(current)) {
    seen.add(current);
    const inner = current.bound;
    if (inner == null || inner === current) {
      return current;
    }
    current = inner;
  }
  return current;
}

function normalizeLangchainRequest(args, kwargs = {}) {
  let modelInput = Object.prototype.hasOwnProperty.call(kwargs, "input") ? kwargs.input : null;
  if (modelInput == null && args.length) {
    modelInput = args[0];
  }

  if (modelInput && typeof modelInput === "object" && Array.isArray(modelInput.messages)) {
    return normalizeLangchainValue(modelInput.messages);
  }
  if (Array.isArray(modelInput)) {
    return normalizeLangchainValue(modelInput);
  }

  const payload = {
    input: normalizeLangchainValue(modelInput),
  };
  if (Object.prototype.hasOwnProperty.call(kwargs, "config")) {
    payload.config = normalizeLangchainValue(kwargs.config);
  }
  if (Object.prototype.hasOwnProperty.call(kwargs, "stop")) {
    payload.stop = normalizeLangchainValue(kwargs.stop);
  }

  const extra = Object.fromEntries(
    Object.entries(kwargs).filter(([key]) => !["input", "config", "stop"].includes(key))
  );
  if (Object.keys(extra).length) {
    payload.kwargs = normalizeLangchainValue(extra);
  }
  return payload;
}

function normalizeLangchainOutput(output) {
  const messages = extractLangchainMessages(output);
  if (messages.length === 1) {
    return normalizeLangchainValue(messages[0]);
  }
  if (messages.length > 1) {
    return normalizeLangchainValue(messages);
  }
  return normalizeLangchainValue(output);
}

function normalizeLangchainValue(value) {
  if (value == null || ["boolean", "number", "string"].includes(typeof value)) {
    return value;
  }
  if (Array.isArray(value)) {
    return value.map((item) => normalizeLangchainValue(item));
  }
  if (value && typeof value === "object") {
    if (Object.prototype.hasOwnProperty.call(value, "content")) {
      const out = {
        type: value && value.constructor && value.constructor.name ? value.constructor.name : "Object",
        content: normalizeLangchainValue(extractLangchainMessageContent(value)),
      };
      const role = langchainMessageRole(value);
      if (role) {
        out.role = role;
      }
      for (const attr of ["name", "id", "tool_calls", "invalid_tool_calls", "response_metadata"]) {
        const attrValue = readLangchainMessageAttr(value, attr);
        if (attrValue) {
          out[attr] = normalizeLangchainValue(attrValue);
        }
      }
      return out;
    }

    const serializer = getLangchainMessageSerializer();
    if (serializer) {
      try {
        return serializer(value);
      } catch (_) {}
    }

    for (const attr of ["model_dump", "to_dict", "toDict"]) {
      const dumper = value[attr];
      if (typeof dumper !== "function") {
        continue;
      }
      try {
        return dumper.call(value);
      } catch (_) {
        continue;
      }
    }

    return Object.fromEntries(
      Object.entries(value).map(([key, item]) => [String(key), normalizeLangchainValue(item)])
    );
  }
  return String(value);
}

function extractLangchainMessageContent(value) {
  const direct = value && value.content;
  if (direct !== undefined) {
    return direct;
  }
  if (value && value.data && Object.prototype.hasOwnProperty.call(value.data, "content")) {
    return value.data.content;
  }
  return null;
}

function readLangchainMessageAttr(value, attr) {
  if (value && value[attr] != null) {
    return value[attr];
  }
  if (value && value.data && value.data[attr] != null) {
    return value.data[attr];
  }
  return null;
}

function langchainMessageRole(value) {
  const directRole = value && value.role;
  if (typeof directRole === "string") {
    return directRole;
  }
  const type = String(value && (value.type || (value.constructor && value.constructor.name) || "")).toLowerCase();
  if (type.includes("human")) {
    return "user";
  }
  if (type.includes("ai")) {
    return "assistant";
  }
  if (type.includes("system")) {
    return "system";
  }
  if (type.includes("tool")) {
    return "tool";
  }
  return null;
}

function extractLangchainMessages(value, seen = new Set()) {
  if (value == null || !["object", "function"].includes(typeof value)) {
    return [];
  }
  if (seen.has(value)) {
    return [];
  }
  seen.add(value);

  if (Array.isArray(value)) {
    return value.flatMap((item) => extractLangchainMessages(item, seen));
  }
  if (isLangchainMessageLike(value)) {
    return [value];
  }
  if (Array.isArray(value.messages)) {
    return extractLangchainMessages(value.messages, seen);
  }
  if (value.update && Array.isArray(value.update.messages)) {
    return extractLangchainMessages(value.update.messages, seen);
  }
  return [];
}

function isLangchainMessageLike(value) {
  return Boolean(
    value
      && typeof value === "object"
      && Object.prototype.hasOwnProperty.call(value, "content")
      && (
        typeof value.role === "string"
        || typeof value.type === "string"
        || value.lc_namespace
      )
  );
}

let _langchainMessageSerializer;
let _langchainMessageSerializerLoaded = false;
let _langchainAIMessageClass;
let _langchainAIMessageClassLoaded = false;
let _langchainCommandClass;
let _langchainCommandClassLoaded = false;

function getLangchainMessageSerializer() {
  if (_langchainMessageSerializerLoaded) {
    return _langchainMessageSerializer;
  }
  _langchainMessageSerializerLoaded = true;
  try {
    ({ messageToDict: _langchainMessageSerializer } = require("@langchain/core/messages"));
  } catch (_) {
    _langchainMessageSerializer = null;
  }
  return _langchainMessageSerializer;
}

function getLangchainAIMessageClass() {
  if (_langchainAIMessageClassLoaded) {
    return _langchainAIMessageClass;
  }
  _langchainAIMessageClassLoaded = true;
  try {
    ({ AIMessage: _langchainAIMessageClass } = require("@langchain/core/messages"));
  } catch (_) {
    _langchainAIMessageClass = null;
  }
  return _langchainAIMessageClass;
}

function getLangchainCommandClass() {
  if (_langchainCommandClassLoaded) {
    return _langchainCommandClass;
  }
  _langchainCommandClassLoaded = true;
  try {
    ({ Command: _langchainCommandClass } = require("@langchain/langgraph"));
  } catch (_) {
    _langchainCommandClass = null;
  }
  return _langchainCommandClass;
}

function collectToolObject(tool, adapter, { name }) {
  if (tool == null || isGuarded(tool)) {
    return [];
  }

  let bindings = collectToolAttrBindings(tool, adapter, { name, attrs: ["func", "coroutine"] });
  if (bindings.length) {
    return bindings;
  }

  bindings = collectToolAttrBindings(tool, adapter, { name, attrs: ["_run", "_arun"] });
  if (bindings.length) {
    return bindings;
  }

  return collectToolAttrBindings(tool, adapter, { name, attrs: ["invoke", "ainvoke"] });
}

function collectToolAttrBindings(tool, adapter, { name, attrs }) {
  const bindings = [];
  for (const attr of attrs) {
    const fn = tool && tool[attr];
    if (typeof fn !== "function" || isGuarded(fn)) {
      continue;
    }
    bindings.push(
      adapter.buildToolBinding({
        name,
        fn,
        owner: tool,
        attr,
        tool,
        installer: installLangchainToolBinding,
      })
    );
  }
  return bindings;
}

function installLangchainToolBinding(guard, binding, adapter) {
  const fn = binding.callable;
  const name = binding.name;
  const tool = binding.tool || binding.owner;
  const metadata = registerToolMetadata(guard, fn, { name, tool });
  const attr = binding.attr || "invoke";

  const wrapper = async (...args) => {
    try {
      const eventMetadata = buildLangchainToolEventMetadata(args, {});
      const arguments_ = buildLangchainToolArguments(fn, args, {});
      const decision = await guardToolBefore(guard, metadata, arguments_, {
        normalizer: adapter,
        fn,
        owner: tool,
        extraMetadata: eventMetadata,
      });
      const blocked = blockedLangchainToolValue(decision, metadata.name, args, {});
      if (blocked !== null) {
        return blocked;
      }

      let value;
      try {
        value = await fn.apply(tool, args);
      } catch (error) {
        await guardToolAfter(guard, metadata.name, null, {
          error: String(error && error.message ? error.message : error),
          normalizer: adapter,
          fn,
          owner: tool,
          extraMetadata: eventMetadata,
        });
        throw error;
      }

      const resultDecision = await guardToolAfter(guard, metadata.name, value, {
        normalizer: adapter,
        fn,
        owner: tool,
        extraMetadata: eventMetadata,
      });
      const resultBlocked = blockedLangchainResultValue(resultDecision, metadata.name, args, {});
      return resultBlocked !== null ? resultBlocked : value;
    } catch (error) {
      if (guard && guard.runtime && typeof guard.runtime.sync_local_cache_now === "function") {
        await guard.runtime.sync_local_cache_now({ reason: "client_error" });
      }
      throw error;
    } finally {
      if (guard && guard.runtime && typeof guard.runtime.sync_local_cache_async === "function") {
        guard.runtime.sync_local_cache_async({ reason: "round_complete" });
      }
    }
  };

  return setAttr(tool, attr, markGuarded(wrapper)) ? 1 : 0;
}

function installLangchainAgentNodeLLMBinding(guard, binding, adapter) {
  const fn = binding.callable;
  const owner = binding.owner;
  const attr = binding.attr || "func";
  const label = owner && owner.name ? String(owner.name) : String(binding.label || "model_request");

  const wrapper = async (...args) => {
    try {
      const beforeDecision = await guardLLMBefore(guard, {
        label,
        args,
        normalizer: adapter,
        fn,
        owner,
      });
      const beforeBlocked = blockedLangchainLLMValue(beforeDecision, { label });
      if (beforeBlocked !== null) {
        return beforeBlocked;
      }

      const raw = await fn.apply(owner, args);
      const decision = await guardLLMAfter(guard, raw, {
        label,
        normalizer: adapter,
        fn,
        owner,
      });
      const blocked = blockedLangchainLLMValue(decision, { label });
      return blocked !== null ? blocked : raw;
    } catch (error) {
      if (guard && guard.runtime && typeof guard.runtime.sync_local_cache_now === "function") {
        await guard.runtime.sync_local_cache_now({ reason: "client_error" });
      }
      throw error;
    } finally {
      if (guard && guard.runtime && typeof guard.runtime.sync_local_cache_async === "function") {
        guard.runtime.sync_local_cache_async({ reason: "round_complete" });
      }
    }
  };

  return setAttr(owner, attr, markGuarded(wrapper)) ? 1 : 0;
}

function buildLangchainToolArguments(fn, args, kwargs = {}) {
  const toolCall = extractLangchainToolCall(args, kwargs);
  if (toolCall && Object.prototype.hasOwnProperty.call(toolCall, "args")) {
    const toolArgs = normalizeLangchainValue(toolCall.args);
    if (toolArgs && typeof toolArgs === "object" && !Array.isArray(toolArgs)) {
      return toolArgs;
    }
    return { args: toolArgs };
  }
  return bindArguments(fn, args, kwargs);
}

function blockedLangchainToolValue(decision, toolNameValue, args, kwargs = {}) {
  if (decision.decision_type === DecisionType.DENY) {
    return langchainToolMessage(
      {
        agentguard: "blocked",
        tool: toolNameValue,
        reason: decision.reason,
        decision: decision.decision_type,
      },
      { toolName: toolNameValue, args, kwargs }
    );
  }
  if (decision.requires_user || decision.requires_remote) {
    return langchainToolMessage(
      {
        agentguard: "pending",
        tool: toolNameValue,
        reason: decision.reason,
        decision: decision.decision_type,
      },
      { toolName: toolNameValue, args, kwargs }
    );
  }
  if (decision.decision_type === DecisionType.DEGRADE) {
    return langchainToolMessage(
      {
        agentguard: "degraded",
        tool: toolNameValue,
        reason: decision.reason,
        decision: decision.decision_type,
      },
      { toolName: toolNameValue, args, kwargs }
    );
  }
  return null;
}

function blockedLangchainResultValue(decision, toolNameValue, args, kwargs = {}) {
  if (decision.decision_type === DecisionType.DENY) {
    return langchainToolMessage(
      {
        agentguard: "blocked",
        tool: toolNameValue,
        reason: decision.reason,
        decision: decision.decision_type,
      },
      { toolName: toolNameValue, args, kwargs }
    );
  }
  if (decision.decision_type === DecisionType.SANITIZE) {
    return langchainToolMessage(
      {
        agentguard: "sanitized",
        tool: toolNameValue,
        reason: decision.reason,
        decision: decision.decision_type,
      },
      { toolName: toolNameValue, args, kwargs }
    );
  }
  if (decision.requires_user || decision.requires_remote) {
    return langchainToolMessage(
      {
        agentguard: "pending",
        tool: toolNameValue,
        reason: decision.reason,
        decision: decision.decision_type,
      },
      { toolName: toolNameValue, args, kwargs }
    );
  }
  return null;
}

function blockedLangchainLLMValue(decision, { label = "model" } = {}) {
  if (decision.decision_type === DecisionType.DENY) {
    return langchainLLMNodeResponse(
      {
        agentguard: "blocked",
        reason: decision.reason,
        decision: decision.decision_type,
      },
      { label }
    );
  }
  if (decision.decision_type === DecisionType.SANITIZE) {
    return langchainLLMNodeResponse(
      {
        agentguard: "sanitized",
        reason: decision.reason,
        decision: decision.decision_type,
      },
      { label }
    );
  }
  if (decision.requires_user || decision.requires_remote) {
    return langchainLLMNodeResponse(
      {
        agentguard: "pending",
        reason: decision.reason,
        decision: decision.decision_type,
      },
      { label }
    );
  }
  if (decision.decision_type === DecisionType.DEGRADE) {
    return langchainLLMNodeResponse(
      {
        agentguard: "degraded",
        reason: decision.reason,
        decision: decision.decision_type,
      },
      { label }
    );
  }
  return null;
}

function langchainToolMessage(payload, { toolName: toolNameValue, args, kwargs = {} } = {}) {
  const content = JSON.stringify(payload);
  const toolCall = extractLangchainToolCall(args, kwargs);
  const toolCallId = toolCall && toolCall.id;
  const ToolMessage = getLangchainToolMessageClass();
  if (ToolMessage && toolCallId) {
    try {
      return new ToolMessage({
        content,
        name: toolNameValue,
        tool_call_id: toolCallId,
      });
    } catch (_) {
      return content;
    }
  }
  return content;
}

function langchainLLMNodeResponse(payload, { label = "model" } = {}) {
  const message = langchainLLMMessage(payload, { label });
  const Command = getLangchainCommandClass();
  if (Command) {
    try {
      return [new Command({ update: { messages: [message] } })];
    } catch (_) {}
  }
  return { messages: [message] };
}

function langchainLLMMessage(payload, { label = "model" } = {}) {
  const content = JSON.stringify(payload);
  const AIMessage = getLangchainAIMessageClass();
  if (AIMessage) {
    try {
      return new AIMessage({
        content,
        name: label,
        tool_calls: [],
      });
    } catch (_) {}
  }
  return {
    role: "assistant",
    content,
    name: label,
  };
}

function buildLangchainToolEventMetadata(args, kwargs = {}) {
  const toolCall = extractLangchainToolCall(args, kwargs);
  const metadata = {
    adapter: "langchain",
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

function extractLangchainToolCall(args, kwargs = {}) {
  const candidates = [...args];
  if (Object.prototype.hasOwnProperty.call(kwargs, "input")) {
    candidates.push(kwargs.input);
  }
  if (Object.prototype.hasOwnProperty.call(kwargs, "tool_call")) {
    candidates.push(kwargs.tool_call);
  }
  if (Object.prototype.hasOwnProperty.call(kwargs, "toolCall")) {
    candidates.push(kwargs.toolCall);
  }
  if (Object.prototype.hasOwnProperty.call(kwargs, "config")) {
    candidates.push(kwargs.config);
  }

  const stack = [...candidates];
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
    if (isLangchainToolCall(value.tool_call)) {
      return value.tool_call;
    }
    if (value.config && typeof value.config === "object") {
      if (isLangchainToolCall(value.config.toolCall)) {
        return value.config.toolCall;
      }
      if (isLangchainToolCall(value.config.tool_call)) {
        return value.config.tool_call;
      }
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

module.exports = {
  LangChainAgentAdapter,
};

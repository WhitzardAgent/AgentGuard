"use strict";

const {
  LLMInputNormalization,
  LLMOutputNormalization,
  ToolInvokeNormalization,
  ToolResultNormalization,
} = require("./normalization");
const {
  isGuarded,
  makeGuardedLLMCallable,
  makeGuardedTool,
  markPatched,
  setAttr,
  toolName,
} = require("./patching");
const { ToolMetadata } = require("../../tools/metadata");
const { AdapterError } = require("../../utils/errors");

class ToolBinding {
  constructor(data = {}) {
    this.name = String(data.name || "tool");
    this.parameters = { ...(data.parameters || {}) };
    this.callable = data.callable;
    this.owner = data.owner ?? null;
    this.attr = data.attr ?? null;
    this.tool = data.tool ?? null;
    this.capabilities = data.capabilities ? [...data.capabilities] : null;
    this.container = data.container ?? null;
    this.key = data.key ?? null;
    this.installer = data.installer ?? null;
    this.metadata = { ...(data.metadata || {}) };
  }

  invoke(...args) {
    return this.callable(...args);
  }
}

class LLMBinding {
  constructor(data = {}) {
    this.label = String(data.label || "llm");
    this.callable = data.callable;
    this.owner = data.owner ?? null;
    this.attr = data.attr ?? null;
    this.container = data.container ?? null;
    this.key = data.key ?? null;
    this.installer = data.installer ?? null;
    this.metadata = { ...(data.metadata || {}) };
  }

  invoke(...args) {
    return this.callable(...args);
  }
}

class BaseAgentAdapter {
  constructor() {
    this.name = "base";
    this.toolslist = [];
    this.llms = [];
    this._objectIds = new WeakMap();
    this._nextObjectId = 1;
  }

  get adapter_name() {
    return String(this.name);
  }

  gettools() {
    throw new Error("gettools() must be implemented");
  }

  getllm() {
    throw new Error("getllm() must be implemented");
  }

  can_wrap() {
    return false;
  }

  normalizeValue(value) {
    if (value == null || ["boolean", "number", "string"].includes(typeof value)) {
      return value;
    }
    if (typeof Buffer !== "undefined" && Buffer.isBuffer(value)) {
      return value.toString("utf-8");
    }
    if (Array.isArray(value)) {
      return value.map((item) => this.normalizeValue(item));
    }
    if (value instanceof Set || value instanceof Map) {
      return [...value].map((item) => this.normalizeValue(item));
    }
    if (value && typeof value === "object") {
      for (const attr of ["model_dump", "to_dict", "dict", "toDict"]) {
        const dumper = value[attr];
        if (typeof dumper !== "function") {
          continue;
        }
        try {
          return this.normalizeValue(dumper.call(value));
        } catch (_) {
          continue;
        }
      }

      const content = value.content;
      const role = value.role;
      if (content !== undefined || role !== undefined) {
        const out = {};
        if (role !== undefined) {
          out.role = this.normalizeValue(role);
        }
        if (content !== undefined) {
          out.content = this.normalizeValue(content);
        }
        return out;
      }

      return Object.fromEntries(
        Object.entries(value).map(([key, item]) => [String(key), this.normalizeValue(item)])
      );
    }
    return String(value);
  }

  _metadata({ label = null, owner = null, extra = null } = {}) {
    const meta = { adapter: this.adapter_name };
    if (label) {
      meta.label = String(label);
    }
    if (owner != null) {
      meta.owner_type = owner && owner.constructor && owner.constructor.name ? owner.constructor.name : typeof owner;
      const ownerModule = owner && owner.constructor && owner.constructor.__module__;
      if (ownerModule) {
        meta.owner_module = ownerModule;
      }
    }
    if (extra) {
      Object.assign(meta, extra);
    }
    return meta;
  }

  normalize_llm_input({ label, args = [], kwargs = {}, fn = null, owner = null } = {}) {
    void fn;
    return new LLMInputNormalization({
      payload: {
        label,
        args: this.normalizeValue([...args]),
        kwargs: this.normalizeValue({ ...(kwargs || {}) }),
      },
      metadata: this._metadata({ label, owner }),
    });
  }

  normalize_llm_output({ label, output, fn = null, owner = null } = {}) {
    void fn;
    return new LLMOutputNormalization({
      payload: this.normalizeValue(output),
      metadata: this._metadata({ label, owner }),
    });
  }

  normalize_tool_invoke({ tool_metadata, arguments: arguments_ = {}, fn = null, owner = null } = {}) {
    void fn;
    return new ToolInvokeNormalization({
      arguments: this.normalizeValue(arguments_),
      capabilities: [...((tool_metadata && tool_metadata.capabilities) || [])],
      metadata: this._metadata({ owner }),
    });
  }

  normalize_tool_result({ tool_name, result = null, error = null, fn = null, owner = null } = {}) {
    void tool_name;
    void fn;
    return new ToolResultNormalization({
      result: this.normalizeValue(result),
      error,
      metadata: this._metadata({ owner }),
    });
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

  patchtool(agent, guard) {
    this.toolslist = this._dedupeBindings([...(this.gettools(agent) || [])]);
    let patched = 0;
    const counted = new Set();
    for (const binding of this.toolslist) {
      if (!this._patchToolBinding(binding, guard)) {
        continue;
      }
      const logicalKey = this._toolBindingKey(binding);
      if (counted.has(logicalKey)) {
        continue;
      }
      counted.add(logicalKey);
      patched += 1;
    }
    return patched;
  }

  patchLLM(agent, guard) {
    this.llms = this._dedupeBindings([...(this.getllm(agent) || [])]);
    let patched = 0;
    for (const binding of this.llms) {
      patched += this._patchLLMBinding(binding, guard);
    }
    return patched;
  }

  _dedupeBindings(bindings) {
    const unique = [];
    const seen = new Set();
    for (const binding of bindings) {
      const key = this._bindingInstallKey(binding);
      if (seen.has(key)) {
        continue;
      }
      seen.add(key);
      unique.push(binding);
    }
    return unique;
  }

  _bindingInstallKey(binding) {
    const owner = binding && binding.owner;
    const attr = binding && binding.attr;
    if (owner != null && attr) {
      return `owner:${this._objectId(owner)}:${attr}`;
    }

    const container = binding && binding.container;
    const key = binding && binding.key;
    if (container != null) {
      return `container:${this._objectId(container)}:${this._bindingValueKey(key)}`;
    }

    return `callable:${this._objectId(binding && binding.callable)}`;
  }

  _toolBindingKey(binding) {
    const logicalId = binding && binding.metadata && binding.metadata.logical_id;
    if (logicalId !== undefined) {
      return `logical:${this._bindingValueKey(logicalId)}`;
    }
    if (binding && binding.tool != null) {
      return `tool:${this._objectId(binding.tool)}`;
    }
    if (binding && binding.owner != null) {
      return `owner:${this._objectId(binding.owner)}`;
    }
    if (binding && binding.container != null) {
      return `container:${this._objectId(binding.container)}:${this._bindingValueKey(binding.key)}`;
    }
    return `callable:${this._objectId(binding && binding.callable)}`;
  }

  _bindingValueKey(value) {
    if (value == null) {
      return String(value);
    }
    const type = typeof value;
    if (["string", "number", "boolean", "bigint", "undefined"].includes(type)) {
      return `${type}:${String(value)}`;
    }
    if (type === "symbol") {
      return `symbol:${String(value.description || value)}`;
    }
    try {
      return `json:${JSON.stringify(value)}`;
    } catch (_) {
      return `ref:${this._objectId(value)}`;
    }
  }

  _objectId(value) {
    if (value == null || (!["object", "function"].includes(typeof value))) {
      return this._bindingValueKey(value);
    }
    if (!this._objectIds.has(value)) {
      this._objectIds.set(value, this._nextObjectId);
      this._nextObjectId += 1;
    }
    return this._objectIds.get(value);
  }

  run(agent, input_data, context) {
    void context;
    if (typeof agent === "function") {
      return agent(input_data);
    }
    throw new AdapterError(`${this.name}: agent is not runnable`);
  }

  async generate() {
    throw new Error("generate() must be implemented");
  }

  describeParameters(fn) {
    const metadata = ToolMetadata.infer(fn);
    return Object.fromEntries(
      metadata.required_args.map((name) => [String(name), { kind: "positional_or_keyword", required: true }])
    );
  }

  extractToolCallable(tool, { attrs = ["func", "_func"] } = {}) {
    for (const attr of attrs) {
      const fn = tool && tool[attr];
      if (typeof fn === "function" && !isGuarded(fn)) {
        return [fn, attr];
      }
    }
    return [null, null];
  }

  resolveAttrPath(obj, path) {
    if (!String(path || "").includes(".")) {
      return [obj, path, obj ? obj[path] : undefined];
    }

    const parts = String(path).split(".");
    let target = obj;
    for (const part of parts.slice(0, -1)) {
      target = target ? target[part] : null;
      if (target == null) {
        return [obj, parts[parts.length - 1], undefined];
      }
    }
    const leaf = parts[parts.length - 1];
    return [target, leaf, target ? target[leaf] : undefined];
  }

  buildToolBinding({
    name,
    fn,
    owner = null,
    attr = null,
    tool = null,
    capabilities = null,
    container = null,
    key = null,
    installer = null,
    metadata = null,
  } = {}) {
    return new ToolBinding({
      name,
      parameters: this.describeParameters(fn),
      callable: fn,
      owner,
      attr,
      tool,
      capabilities: capabilities ? [...capabilities] : null,
      container,
      key,
      installer,
      metadata: { ...(metadata || {}) },
    });
  }

  buildLLMBinding({
    label,
    fn,
    owner = null,
    attr = null,
    container = null,
    key = null,
    installer = null,
    metadata = null,
  } = {}) {
    return new LLMBinding({
      label,
      callable: fn,
      owner,
      attr,
      container,
      key,
      installer,
      metadata: { ...(metadata || {}) },
    });
  }

  collectToolList(toolsList, { funcAttrs = ["func", "_func"], runJsonAttr = "run_json" } = {}) {
    if (!Array.isArray(toolsList)) {
      return [];
    }

    const bindings = [];
    toolsList.forEach((tool, index) => {
      if (isGuarded(tool)) {
        return;
      }

      const [fn, attr] = this.extractToolCallable(tool, { attrs: funcAttrs });
      if (fn && attr) {
        bindings.push(
          this.buildToolBinding({
            name: toolName(tool, fn, `tool_${index}`),
            fn,
            owner: tool,
            attr,
            tool,
          })
        );
        return;
      }

      const runJson = tool && tool[runJsonAttr];
      if (typeof runJson === "function" && !isGuarded(runJson)) {
        bindings.push(
          this.buildToolBinding({
            name: toolName(tool, runJson, `tool_${index}`),
            fn: runJson,
            owner: tool,
            attr: runJsonAttr,
            tool,
          })
        );
        return;
      }

      if (typeof tool === "function" && !isGuarded(tool)) {
        bindings.push(
          this.buildToolBinding({
            name: toolName(tool, null, `tool_${index}`),
            fn: tool,
            container: toolsList,
            key: index,
            tool,
          })
        );
      }
    });
    return bindings;
  }

  collectFunctionMap(registry) {
    if (!registry || typeof registry !== "object" || Array.isArray(registry)) {
      return [];
    }

    const bindings = [];
    for (const [name, fn] of Object.entries(registry)) {
      if (typeof fn !== "function" || isGuarded(fn)) {
        continue;
      }
      bindings.push(
        this.buildToolBinding({
          name: String(name),
          fn,
          container: registry,
          key: name,
          tool: fn,
        })
      );
    }
    return bindings;
  }

  collectRegisterFunction(agent) {
    const original = agent && agent.register_function;
    if (typeof original !== "function" || isGuarded(original)) {
      return [];
    }
    return [
      this.buildToolBinding({
        name: "register_function",
        fn: original,
        owner: agent,
        attr: "register_function",
        tool: agent,
        installer: this._installRegisterFunctionBinding.bind(this),
      }),
    ];
  }

  collectLLMMethods(obj, { methods = [] } = {}) {
    const bindings = [];
    for (const label of methods) {
      const [target, attr, fn] = this.resolveAttrPath(obj, label);
      if (typeof fn !== "function" || isGuarded(fn)) {
        continue;
      }
      bindings.push(
        this.buildLLMBinding({
          label,
          fn,
          owner: target,
          attr,
        })
      );
    }
    return bindings;
  }

  _patchToolBinding(binding, guard) {
    if (binding.installer) {
      return Number(binding.installer(guard, binding, this) || 0);
    }
    if (typeof binding.callable !== "function" || isGuarded(binding.callable)) {
      return 0;
    }

    const wrapped = makeGuardedTool(guard, binding.callable, {
      name: binding.name,
      tool: binding.tool || binding.owner || binding.callable,
      capabilities: [...(binding.capabilities || [])],
      normalizer: this,
      owner: binding.tool || binding.owner,
      callTarget: binding.owner != null && binding.attr ? binding.owner : null,
    });
    return this._installBoundCallable(binding, wrapped);
  }

  _patchLLMBinding(binding, guard) {
    if (binding.installer) {
      return Number(binding.installer(guard, binding, this) || 0);
    }
    if (typeof binding.callable !== "function" || isGuarded(binding.callable)) {
      return 0;
    }

    const wrapped = makeGuardedLLMCallable(guard, binding.callable, {
      label: binding.label,
      normalizer: this,
      owner: binding.owner,
      callTarget: binding.owner != null && binding.attr ? binding.owner : null,
    });
    return this._installBoundCallable(binding, wrapped);
  }

  _installBoundCallable(binding, wrapped) {
    const owner = binding && binding.owner;
    const attr = binding && binding.attr;
    if (owner != null && attr) {
      if (setAttr(owner, attr, wrapped)) {
        markPatched(owner);
        return 1;
      }
      return 0;
    }

    const container = binding && binding.container;
    const key = binding && binding.key;
    if (container != null) {
      try {
        container[key] = wrapped;
        return 1;
      } catch (_) {
        return 0;
      }
    }
    return 0;
  }

  _installRegisterFunctionBinding(guard, binding, adapter) {
    const original = binding.callable;
    const agent = binding.owner;
    if (typeof original !== "function" || agent == null || isGuarded(original)) {
      return 0;
    }

    const patched = function patchedRegisterFunction(func = null, ...rest) {
      let nextFunc = func;
      const maybeOptions = rest[0];
      const name = maybeOptions && typeof maybeOptions === "object" && !Array.isArray(maybeOptions)
        ? maybeOptions.name
        : null;
      if (typeof nextFunc === "function" && !isGuarded(nextFunc)) {
        nextFunc = makeGuardedTool(guard, nextFunc, {
          name: name || toolName(nextFunc),
          tool: nextFunc,
          normalizer: adapter,
          owner: nextFunc,
        });
      }
      return original.call(agent, nextFunc, ...rest);
    };

    return setAttr(agent, "register_function", patched) ? 1 : 0;
  }
}

module.exports = {
  BaseAgentAdapter,
  LLMBinding,
  ToolBinding,
};

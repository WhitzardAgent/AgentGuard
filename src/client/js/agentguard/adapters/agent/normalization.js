"use strict";

class LLMInputNormalization {
  constructor(data = {}) {
    this.payload = data.payload;
    this.metadata = { ...(data.metadata || {}) };
  }
}

class LLMOutputNormalization {
  constructor(data = {}) {
    this.payload = data.payload;
    this.metadata = { ...(data.metadata || {}) };
  }
}

class LLMInputDenormalization {
  constructor(data = {}) {
    this.args = Array.isArray(data.args) ? [...data.args] : [];
    this.kwargs = { ...(data.kwargs || {}) };
    this.metadata = { ...(data.metadata || {}) };
  }
}

class LLMOutputDenormalization {
  constructor(data = {}) {
    this.output = data.output;
    this.metadata = { ...(data.metadata || {}) };
  }
}

class ToolInvokeNormalization {
  constructor(data = {}) {
    this.arguments = { ...(data.arguments || {}) };
    this.capabilities = data.capabilities ? [...data.capabilities] : null;
    this.metadata = { ...(data.metadata || {}) };
  }
}

class ToolInvokeDenormalization {
  constructor(data = {}) {
    this.args = Array.isArray(data.args) ? [...data.args] : [];
    this.kwargs = { ...(data.kwargs || {}) };
    this.metadata = { ...(data.metadata || {}) };
  }
}

class ToolResultNormalization {
  constructor(data = {}) {
    this.result = data.result;
    this.error = data.error ?? null;
    this.metadata = { ...(data.metadata || {}) };
  }
}

class ToolResultDenormalization {
  constructor(data = {}) {
    this.result = data.result;
    this.error = data.error ?? null;
    this.metadata = { ...(data.metadata || {}) };
  }
}

class FallbackAgentEventNormalizer {
  constructor() {
    this.adapter_name = "base";
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
    const meta = {};
    if (this.adapter_name) {
      meta.adapter = String(this.adapter_name);
    }
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

  denormalize_llm_input({ label, payload, args = [], kwargs = {}, fn = null, owner = null } = {}) {
    const denormalized = denormalizeLLMInputPayload({
      payload,
      args,
      kwargs,
      fn,
    });
    return new LLMInputDenormalization({
      args: denormalized.args,
      kwargs: denormalized.kwargs,
      metadata: this._metadata({ label, owner }),
    });
  }

  denormalize_llm_output({ label, payload, output, fn = null, owner = null } = {}) {
    void fn;
    return new LLMOutputDenormalization({
      output: denormalizeLLMOutputPayload({ payload, output }),
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

  denormalize_tool_invoke({ tool_metadata, payload, args = [], kwargs = {}, fn = null, owner = null } = {}) {
    void tool_metadata;
    const denormalized = denormalizeToolInvokePayload({ payload, args, kwargs, fn });
    return new ToolInvokeDenormalization({
      args: denormalized.args,
      kwargs: denormalized.kwargs,
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

  denormalize_tool_result({ tool_name, payload, result = null, error = null, fn = null, owner = null } = {}) {
    void tool_name;
    void fn;
    const denormalized = denormalizeToolResultPayload({ payload, result, error });
    return new ToolResultDenormalization({
      result: denormalized.result,
      error: denormalized.error,
      metadata: this._metadata({ owner }),
    });
  }
}

const DEFAULT_AGENT_EVENT_NORMALIZER = new FallbackAgentEventNormalizer();

function denormalizeLLMInputPayload({ payload, args = [], kwargs = {}, fn = null } = {}) {
  void fn;
  let currentArgs = Array.isArray(args) ? [...args] : [];
  let currentKwargs = isPlainObject(kwargs) ? { ...kwargs } : {};

  if (isPlainObject(payload)) {
    if (Object.prototype.hasOwnProperty.call(payload, "input")) {
      [currentArgs, currentKwargs] = replacePrimaryLLMInput(payload.input, {
        args: currentArgs,
        kwargs: currentKwargs,
        preferredKeys: ["input", "messages", "msg"],
      });
      if (Array.isArray(payload.args)) {
        currentArgs = [...currentArgs.slice(0, 1), ...payload.args];
      } else if (Object.prototype.hasOwnProperty.call(payload, "args")) {
        currentArgs = [...currentArgs.slice(0, 1), payload.args];
      }
      mergeLLMPayloadKwargs(currentKwargs, payload, { skipKeys: new Set(["label", "input", "args"]) });
      return new LLMInputDenormalization({ args: currentArgs, kwargs: currentKwargs });
    }

    if (Object.prototype.hasOwnProperty.call(payload, "messages") || Object.prototype.hasOwnProperty.call(payload, "msg")) {
      const primaryKey = Object.prototype.hasOwnProperty.call(payload, "messages") ? "messages" : "msg";
      [currentArgs, currentKwargs] = replacePrimaryLLMInput(payload[primaryKey], {
        args: currentArgs,
        kwargs: currentKwargs,
        preferredKeys: [primaryKey, "input"],
      });
      mergeLLMPayloadKwargs(currentKwargs, payload, { skipKeys: new Set(["label", primaryKey]) });
      return new LLMInputDenormalization({ args: currentArgs, kwargs: currentKwargs });
    }

    if (Object.prototype.hasOwnProperty.call(payload, "args") || Object.prototype.hasOwnProperty.call(payload, "kwargs")) {
      const rawArgs = Object.prototype.hasOwnProperty.call(payload, "args") ? payload.args : currentArgs;
      const rawKwargs = isPlainObject(payload.kwargs) ? payload.kwargs : currentKwargs;
      return new LLMInputDenormalization({
        args: Array.isArray(rawArgs) ? [...rawArgs] : [rawArgs],
        kwargs: { ...rawKwargs },
      });
    }
  }

  if (!currentArgs.length && !Object.keys(currentKwargs).length) {
    return new LLMInputDenormalization({ args: [payload], kwargs: {} });
  }

  [currentArgs, currentKwargs] = replacePrimaryLLMInput(payload, {
    args: currentArgs,
    kwargs: currentKwargs,
    preferredKeys: ["input", "messages", "msg"],
  });
  return new LLMInputDenormalization({ args: currentArgs, kwargs: currentKwargs });
}

function denormalizeLLMOutputPayload({ payload, output } = {}) {
  return denormalizeStructuredValue({
    payload,
    template: output,
    primaryKeys: ["output", "final_output", "content", "text", "message"],
  });
}

function denormalizeToolInvokePayload({ payload, args = [], kwargs = {}, fn = null } = {}) {
  let currentArgs = Array.isArray(args) ? [...args] : [];
  let currentKwargs = isPlainObject(kwargs) ? { ...kwargs } : {};
  const data = isPlainObject(payload) ? payload : { input: payload };

  if (typeof fn !== "function") {
    if (currentArgs.length) {
      currentArgs[0] = data;
      return new ToolInvokeDenormalization({ args: currentArgs, kwargs: currentKwargs });
    }
    if (Object.keys(currentKwargs).length) {
      Object.assign(currentKwargs, data);
      return new ToolInvokeDenormalization({ args: currentArgs, kwargs: currentKwargs });
    }
    return new ToolInvokeDenormalization({ args: [data], kwargs: {} });
  }

  const params = parseFnParams(fn);
  if (!params.length) {
    if (currentArgs.length) {
      currentArgs[0] = data;
      return new ToolInvokeDenormalization({ args: currentArgs, kwargs: currentKwargs });
    }
    return new ToolInvokeDenormalization({ args: [data], kwargs: currentKwargs });
  }

  const originalPositions = argumentPositions(params, currentArgs, currentKwargs);
  const nextArgs = [];
  const nextKwargs = {};
  const consumed = new Set();

  params.forEach((param, index) => {
    const name = normalizeParamName(param);
    if (!name || isStructuredParam(param) || name.startsWith("...")) {
      return;
    }

    let value;
    if (Object.prototype.hasOwnProperty.call(data, name)) {
      value = data[name];
      consumed.add(name);
    } else if (Object.prototype.hasOwnProperty.call(currentKwargs, name)) {
      value = currentKwargs[name];
    } else if (index < currentArgs.length) {
      value = currentArgs[index];
    } else {
      return;
    }

    if (originalPositions.has(name)) {
      nextArgs.push(value);
    } else {
      nextKwargs[name] = value;
    }
  });

  Object.entries(data).forEach(([key, value]) => {
    if (!consumed.has(key)) {
      nextKwargs[key] = value;
    }
  });

  return new ToolInvokeDenormalization({ args: nextArgs, kwargs: nextKwargs });
}

function denormalizeToolResultPayload({ payload, result = null, error = null } = {}) {
  if (isPlainObject(payload) && Object.prototype.hasOwnProperty.call(payload, "error")) {
    return new ToolResultDenormalization({
      result: denormalizeLLMOutputPayload({ payload, output: result }),
      error: payload.error == null ? error : String(payload.error),
    });
  }

  return new ToolResultDenormalization({
    result: denormalizeStructuredValue({
      payload,
      template: result,
      primaryKeys: ["result", "output", "final_output", "content", "text", "message", "value"],
    }),
    error,
  });
}

function denormalizeStructuredValue({ payload, template, primaryKeys = [] } = {}) {
  if (template == null) {
    return extractPrimaryValue(payload, primaryKeys);
  }
  if (typeof template === "string") {
    const value = extractPrimaryValue(payload, primaryKeys);
    return typeof value === "string" ? value : String(value);
  }
  if (Array.isArray(template)) {
    return Array.isArray(payload) ? payload : [payload];
  }
  if (isPlainObject(template)) {
    return denormalizeMapping(payload, template, { primaryKeys });
  }
  for (const attr of ["content", "text", "output", "message", "value"]) {
    if (!template || typeof template !== "object" || !Object.prototype.hasOwnProperty.call(template, attr)) {
      continue;
    }
    const updated = cloneLike(template);
    const value = extractPrimaryValue(payload, primaryKeys);
    if (setDenormalizedAttr(updated, attr, value)) {
      return updated;
    }
  }
  return payload;
}

function denormalizeMapping(payload, template, { primaryKeys = [] } = {}) {
  if (isPlainObject(payload)) {
    const updated = { ...template };
    Object.entries(payload).forEach(([key, value]) => {
      if (Object.prototype.hasOwnProperty.call(updated, key)) {
        updated[key] = value;
      }
    });
    if (isPlainObject(updated.message)) {
      updated.message = denormalizeMapping(payload, updated.message, { primaryKeys });
    } else if (typeof updated.message === "string" && primaryKeys.some((key) => Object.prototype.hasOwnProperty.call(payload, key))) {
      updated.message = extractPrimaryValue(payload, primaryKeys);
    }
    const primary = extractPrimaryValue(payload, primaryKeys);
    if (primary !== payload) {
      for (const key of primaryKeys) {
        if (Object.prototype.hasOwnProperty.call(updated, key)) {
          updated[key] = primary;
          break;
        }
      }
    }
    return updated;
  }

  const updated = { ...template };
  const primary = extractPrimaryValue(payload, primaryKeys);
  for (const key of primaryKeys) {
    if (Object.prototype.hasOwnProperty.call(updated, key)) {
      updated[key] = primary;
      return updated;
    }
  }
  updated[primaryKeys[0] || "output"] = primary;
  return updated;
}

function extractPrimaryValue(payload, primaryKeys = []) {
  if (isPlainObject(payload)) {
    for (const key of primaryKeys) {
      if (payload[key] != null) {
        return payload[key];
      }
    }
  }
  return payload;
}

function cloneLike(value) {
  if (value == null || typeof value !== "object") {
    return value;
  }
  try {
    return structuredClone(value);
  } catch (_) {
    if (Array.isArray(value)) {
      return value.map((item) => cloneLike(item));
    }
    return Object.assign(Object.create(Object.getPrototypeOf(value)), value);
  }
}

function setDenormalizedAttr(target, attr, value) {
  try {
    target[attr] = value;
    return true;
  } catch (_) {
    return false;
  }
}

function parseFnParams(fn) {
  try {
    const source = typeof fn.toString === "function" ? fn.toString() : "";
    const match = source.match(/^[^(]*\(([^)]*)\)/);
    return match ? splitTopLevelParams(match[1]) : [];
  } catch (_) {
    return [];
  }
}

function splitTopLevelParams(text) {
  const parts = [];
  let current = "";
  let depth = 0;
  for (const ch of String(text || "")) {
    if (ch === "," && depth === 0) {
      if (current.trim()) {
        parts.push(current.trim());
      }
      current = "";
      continue;
    }
    if (ch === "{" || ch === "[" || ch === "(") {
      depth += 1;
    } else if (ch === "}" || ch === "]" || ch === ")") {
      depth = Math.max(0, depth - 1);
    }
    current += ch;
  }
  if (current.trim()) {
    parts.push(current.trim());
  }
  return parts;
}

function normalizeParamName(param) {
  return String(param || "")
    .replace(/^\.{3}/, "")
    .replace(/=[\s\S]*$/, "")
    .replace(/[{}\[\]\s]/g, "")
    .trim();
}

function isStructuredParam(param) {
  const text = String(param || "").trim();
  return text.startsWith("{") || text.startsWith("[");
}

function argumentPositions(params, args = [], kwargs = {}) {
  const positions = new Set();
  params.forEach((param, index) => {
    const name = normalizeParamName(param);
    if (!name || name.startsWith("...") || isStructuredParam(param)) {
      return;
    }
    if (index < args.length && !Object.prototype.hasOwnProperty.call(kwargs, name)) {
      positions.add(name);
    }
  });
  return positions;
}

function replacePrimaryLLMInput(value, { args = [], kwargs = {}, preferredKeys = [] } = {}) {
  for (const key of preferredKeys) {
    if (Object.prototype.hasOwnProperty.call(kwargs, key)) {
      kwargs[key] = value;
      return [args, kwargs];
    }
  }

  if (args.length) {
    args[0] = value;
    return [args, kwargs];
  }

  kwargs[preferredKeys[0] || "input"] = value;
  return [args, kwargs];
}

function mergeLLMPayloadKwargs(kwargs, payload, { skipKeys = new Set() } = {}) {
  for (const [key, value] of Object.entries(payload || {})) {
    if (skipKeys.has(key)) {
      continue;
    }
    if (key === "kwargs" && isPlainObject(value)) {
      Object.assign(kwargs, value);
      continue;
    }
    kwargs[key] = value;
  }
}

function isPlainObject(value) {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

module.exports = {
  DEFAULT_AGENT_EVENT_NORMALIZER,
  LLMInputDenormalization,
  LLMInputNormalization,
  LLMOutputDenormalization,
  LLMOutputNormalization,
  ToolInvokeDenormalization,
  ToolInvokeNormalization,
  ToolResultDenormalization,
  ToolResultNormalization,
  denormalizeLLMInputPayload,
  denormalizeLLMOutputPayload,
  denormalizeToolInvokePayload,
  denormalizeToolResultPayload,
};

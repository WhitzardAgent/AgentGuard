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

class ToolInvokeNormalization {
  constructor(data = {}) {
    this.arguments = { ...(data.arguments || {}) };
    this.capabilities = data.capabilities ? [...data.capabilities] : null;
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
  LLMOutputNormalization,
  ToolInvokeNormalization,
  ToolResultNormalization,
  denormalizeLLMInputPayload,
};

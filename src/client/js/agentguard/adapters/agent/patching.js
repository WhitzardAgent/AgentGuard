"use strict";

const ev = require("../../schemas/events");
const { DecisionType } = require("../../schemas/decisions");
const { ToolMetadata } = require("../../tools/metadata");

const PATCHED_ATTR = "__agentguard_patched__";
const WRAPPED_ATTR = "__agentguard_wrapped__";

function isGuarded(obj) {
  return Boolean(obj && (obj[PATCHED_ATTR] || obj[WRAPPED_ATTR]));
}

function markGuarded(obj) {
  if (obj) {
    obj[WRAPPED_ATTR] = true;
  }
  return obj;
}

function markPatched(obj) {
  if (obj) {
    obj[PATCHED_ATTR] = true;
  }
}

function toolName(tool, fn = null, fallback = "tool") {
  return String((tool && (tool.name || tool.__name__)) || (fn && fn.name) || fallback);
}

function bindArguments(fn, args, kwargs = {}) {
  if (!args.length) {
    return { ...kwargs };
  }
  try {
    const source = fn && typeof fn.toString === "function" ? fn.toString() : "";
    const match = source.match(/^[^(]*\(([^)]*)\)/);
    const params = match ? splitTopLevelParams(match[1]) : [];
    const out = { ...kwargs };
    let remainder = [];
    let offset = 0;

    if (params.length && isStructuredParam(params[0]) && isPlainObject(args[0])) {
      Object.assign(out, args[0]);
      offset = 1;
    }

    args.forEach((value, index) => {
      if (index < offset) {
        return;
      }
      const param = params[index];
      const name = normalizeParamName(param);
      if (!name || isStructuredParam(param)) {
        remainder.push(value);
        return;
      }
      out[name] = value;
    });

    if (!Object.keys(out).length || remainder.length) {
      if (!remainder.length && offset === 0) {
        remainder = [...args];
      }
      out._args = remainder;
    }
    return out;
  } catch (_) {
    const out = { ...kwargs };
    out._args = [...args];
    return out;
  }
}

function splitTopLevelParams(text) {
  const parts = [];
  let current = "";
  let depth = 0;
  for (const ch of text) {
    if (ch === "," && depth === 0) {
      if (current.trim()) {
        parts.push(current.trim());
      }
      current = "";
      continue;
    }
    if (ch === "{" || ch === "[" || ch === "(") {
      depth += 1;
    } else if ((ch === "}" || ch === "]" || ch === ")") && depth > 0) {
      depth -= 1;
    }
    current += ch;
  }
  if (current.trim()) {
    parts.push(current.trim());
  }
  return parts;
}

function normalizeParamName(param) {
  return String(param || "").trim().replace(/=.*$/, "").replace(/^\.\.\./, "");
}

function isStructuredParam(param) {
  const value = String(param || "").trim();
  return value.startsWith("{") || value.startsWith("[");
}

function isPlainObject(value) {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function setAttr(obj, attr, value) {
  try {
    obj[attr] = value;
    return true;
  } catch (_) {
    return false;
  }
}

function inferRequiredArgsFromSchema(schema) {
  if (!schema || typeof schema !== "object") {
    return [];
  }
  const shape = schema.shape;
  if (shape && typeof shape === "object" && !Array.isArray(shape)) {
    return Object.keys(shape);
  }
  return [];
}

function inferToolRequiredArgs(fn, tool = null) {
  const directSchemaArgs = inferRequiredArgsFromSchema(tool && tool.schema);
  const lcSchemaArgs = inferRequiredArgsFromSchema(tool && tool.lc_kwargs && tool.lc_kwargs.schema);
  const schemaArgs = directSchemaArgs.length ? directSchemaArgs : lcSchemaArgs;
  if (schemaArgs.length) {
    return schemaArgs;
  }
  return ToolMetadata.infer(fn).required_args;
}

function registerToolMetadata(guard, fn, { name, tool = null, capabilities = null } = {}) {
  const description = (tool && tool.description) || "";
  const caps = capabilities || (tool && tool.capabilities) || [];
  return guard.register_tool(
    fn,
    new ToolMetadata({
      name,
      description: String(description).trim().split("\n")[0],
      capabilities: [...caps],
      required_args: inferToolRequiredArgs(fn, tool),
      is_async: fn && fn.constructor && fn.constructor.name === "AsyncFunction",
    })
  );
}

async function guardLLMBefore(guard, { label, args = [], kwargs = {} } = {}) {
  const request = { label, args: [...args], kwargs: { ...kwargs } };
  return (await guard.runtime.guard(ev.llm_input(guard.context, request))).decision;
}

async function guardLLMAfter(guard, output) {
  return (await guard.runtime.guard(ev.llm_output(guard.context, output), { phase: "after" })).decision;
}

async function guardToolBefore(guard, metadata, arguments_) {
  return (await guard.runtime.guard(ev.tool_invoke(guard.context, metadata.name, arguments_, {
    capabilities: [...(metadata.capabilities || [])],
  }))).decision;
}

async function guardToolAfter(guard, tool_name, result = null, { error = null } = {}) {
  return (await guard.runtime.guard(ev.tool_result(guard.context, tool_name, result, { error }), {
    phase: "after",
  })).decision;
}

function blockedToolValue(decision, tool) {
  if (decision.decision_type === DecisionType.DENY) {
    return { agentguard: "blocked", tool, reason: decision.reason };
  }
  if (decision.requires_user || decision.requires_remote) {
    return { agentguard: "pending", tool, reason: decision.reason, decision: decision.decision_type };
  }
  if (decision.decision_type === DecisionType.DEGRADE) {
    return { agentguard: "degraded", tool, reason: decision.reason };
  }
  return null;
}

function blockedResultValue(decision, tool) {
  if (decision.decision_type === DecisionType.DENY) {
    return { agentguard: "blocked", tool, reason: decision.reason };
  }
  if (decision.decision_type === DecisionType.SANITIZE) {
    return { agentguard: "sanitized", tool, reason: decision.reason };
  }
  if (decision.requires_user || decision.requires_remote) {
    return { agentguard: "pending", tool, reason: decision.reason, decision: decision.decision_type };
  }
  return null;
}

function makeGuardedTool(guard, fn, { name, tool = null, capabilities = null } = {}) {
  if (isGuarded(fn)) {
    return fn;
  }
  const metadata = registerToolMetadata(guard, fn, { name, tool, capabilities });
  const wrapper = async (...args) => {
    try {
      const arguments_ = bindArguments(fn, args);
      const decision = await guardToolBefore(guard, metadata, arguments_);
      const blocked = blockedToolValue(decision, metadata.name);
      if (blocked) {
        return blocked;
      }
      let value;
      try {
        value = await fn(...args);
      } catch (error) {
        await guardToolAfter(guard, metadata.name, null, { error: String(error.message || error) });
        throw error;
      }
      const resultDecision = await guardToolAfter(guard, metadata.name, value);
      return blockedResultValue(resultDecision, metadata.name) || value;
    } catch (error) {
      await guard.runtime.sync_local_cache_now({ reason: "client_error" });
      throw error;
    } finally {
      guard.runtime.sync_local_cache_async({ reason: "round_complete" });
    }
  };
  return markGuarded(wrapper);
}

function makeGuardedLLMCallable(guard, fn, { label } = {}) {
  if (isGuarded(fn)) {
    return fn;
  }
  const wrapper = async (...args) => {
    try {
      await guardLLMBefore(guard, { label, args });
      const raw = await fn(...args);
      const decision = await guardLLMAfter(guard, raw);
      if (decision.decision_type === DecisionType.DENY) {
        return { agentguard: "blocked", reason: decision.reason };
      }
      if (decision.decision_type === DecisionType.SANITIZE) {
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

function patchLLMMethods(guard, obj, { methods = ["create", "complete", "completion", "generate", "invoke", "ainvoke", "predict", "chat"] } = {}) {
  let patched = 0;
  for (const name of methods) {
    if (name.includes(".")) {
      const parts = name.split(".");
      let target = obj;
      for (const part of parts.slice(0, -1)) {
        target = target ? target[part] : null;
      }
      const leaf = parts[parts.length - 1];
      if (!target || typeof target[leaf] !== "function" || isGuarded(target[leaf])) {
        continue;
      }
      if (setAttr(target, leaf, makeGuardedLLMCallable(guard, target[leaf].bind(target), { label: name }))) {
        patched += 1;
      }
      continue;
    }
    if (!obj || typeof obj[name] !== "function" || isGuarded(obj[name])) {
      continue;
    }
    if (setAttr(obj, name, makeGuardedLLMCallable(guard, obj[name].bind(obj), { label: name }))) {
      patched += 1;
    }
  }
  return patched;
}

module.exports = {
  isGuarded,
  markGuarded,
  markPatched,
  toolName,
  bindArguments,
  setAttr,
  registerToolMetadata,
  guardLLMBefore,
  guardLLMAfter,
  guardToolBefore,
  guardToolAfter,
  makeGuardedTool,
  makeGuardedLLMCallable,
  patchLLMMethods,
};

"use strict";

const ev = require("../../schemas/events");
const { DecisionType } = require("../../schemas/decisions");
const { ToolMetadata } = require("../../tools/metadata");
const { DEFAULT_AGENT_EVENT_NORMALIZER } = require("./normalization");

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

function resolveNormalizer(normalizer) {
  return normalizer || DEFAULT_AGENT_EVENT_NORMALIZER;
}

function safeJSONStringify(value) {
  try {
    return JSON.stringify(value);
  } catch (_) {
    return String(value);
  }
}

function buildLLMInputMessages(payload) {
  if (Array.isArray(payload)) {
    return payload;
  }
  if (payload && typeof payload === "object" && Array.isArray(payload.messages)) {
    return payload.messages;
  }
  if (payload == null) {
    return [];
  }
  if (payload && typeof payload === "object" && typeof payload.role === "string") {
    return [payload];
  }
  return [{ role: "user", content: typeof payload === "string" ? payload : safeJSONStringify(payload) }];
}

function buildLLMOutputPayload(payload) {
  if (payload == null || typeof payload === "string") {
    return payload;
  }
  return safeJSONStringify(payload);
}

async function guardLLMBefore(
  guard,
  { label, args = [], kwargs = {}, normalizer = null, fn = null, owner = null, extraMetadata = null } = {}
) {
  const normalized = resolveNormalizer(normalizer).normalize_llm_input({
    label,
    args: [...args],
    kwargs: { ...(kwargs || {}) },
    fn,
    owner,
  });
  const metadata = { ...(normalized.metadata || {}), ...(extraMetadata || {}) };
  return (await guard.runtime.guard(ev.llm_input(guard.context, buildLLMInputMessages(normalized.payload), metadata))).decision;
}

async function guardLLMAfter(
  guard,
  output,
  { label, normalizer = null, fn = null, owner = null, extraMetadata = null } = {}
) {
  const normalized = resolveNormalizer(normalizer).normalize_llm_output({
    label,
    output,
    fn,
    owner,
  });
  const metadata = { ...(normalized.metadata || {}), ...(extraMetadata || {}) };
  return (
    await guard.runtime.guard(ev.llm_output(guard.context, buildLLMOutputPayload(normalized.payload), metadata), {
      phase: "after",
    })
  ).decision;
}

async function guardToolBefore(
  guard,
  metadata,
  arguments_,
  { normalizer = null, fn = null, owner = null, extraMetadata = null } = {}
) {
  const normalized = resolveNormalizer(normalizer).normalize_tool_invoke({
    tool_metadata: metadata,
    arguments: arguments_,
    fn,
    owner,
  });
  return (
    await guard.runtime.guard(
      ev.tool_invoke(guard.context, metadata.name, normalized.arguments, {
        capabilities: [...(normalized.capabilities || metadata.capabilities || [])],
        metadata: { ...(normalized.metadata || {}), ...(extraMetadata || {}) },
      })
    )
  ).decision;
}

async function guardToolAfter(
  guard,
  tool,
  result = null,
  { error = null, normalizer = null, fn = null, owner = null, extraMetadata = null } = {}
) {
  const normalized = resolveNormalizer(normalizer).normalize_tool_result({
    tool_name: tool,
    result,
    error,
    fn,
    owner,
  });
  return (
    await guard.runtime.guard(
      ev.tool_result(guard.context, tool, normalized.result, {
        error: normalized.error,
        metadata: { ...(normalized.metadata || {}), ...(extraMetadata || {}) },
      }),
      { phase: "after" }
    )
  ).decision;
}

function blockedToolValue(decision, tool) {
  if (decision.decision_type === DecisionType.DENY) {
    return { agentguard: "blocked", tool, reason: decision.reason };
  }
  if (decision.requires_user || decision.requires_remote) {
    return { agentguard: "pending", tool, reason: decision.reason, decision: decision.decision_type };
  }
  if (decision.decision_type === DecisionType.DEGRADE) {
    return { agentguard: "degraded", tool, reason: decision.reason, decision: decision.decision_type };
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

function blockedLLMValue(decision) {
  if (decision.decision_type === DecisionType.DENY) {
    return { agentguard: "blocked", reason: decision.reason };
  }
  if (decision.decision_type === DecisionType.SANITIZE) {
    return { agentguard: "sanitized", reason: decision.reason };
  }
  if (decision.requires_user || decision.requires_remote) {
    return { agentguard: "pending", reason: decision.reason, decision: decision.decision_type };
  }
  if (decision.decision_type === DecisionType.DEGRADE) {
    return { agentguard: "degraded", reason: decision.reason, decision: decision.decision_type };
  }
  return null;
}

async function syncLocalCacheNow(guard, { reason }) {
  const runtime = guard && guard.runtime;
  if (runtime && typeof runtime.sync_local_cache_now === "function") {
    await runtime.sync_local_cache_now({ reason });
  }
}

function syncLocalCacheAsync(guard, { reason }) {
  const runtime = guard && guard.runtime;
  if (runtime && typeof runtime.sync_local_cache_async === "function") {
    runtime.sync_local_cache_async({ reason });
  }
}

function makeGuardedTool(
  guard,
  fn,
  { name, tool = null, capabilities = null, normalizer = null, owner = null, callTarget = null } = {}
) {
  if (isGuarded(fn)) {
    return fn;
  }

  const metadata = registerToolMetadata(guard, fn, { name, tool, capabilities });
  const wrapper = async (...args) => {
    try {
      const arguments_ = bindArguments(fn, args);
      const decision = await guardToolBefore(guard, metadata, arguments_, {
        normalizer,
        fn,
        owner,
      });
      const blocked = blockedToolValue(decision, metadata.name);
      if (blocked !== null) {
        return blocked;
      }

      let value;
      try {
        value = await (callTarget != null ? fn.apply(callTarget, args) : fn(...args));
      } catch (error) {
        await guardToolAfter(guard, metadata.name, null, {
          error: String(error && error.message ? error.message : error),
          normalizer,
          fn,
          owner,
        });
        throw error;
      }

      const resultDecision = await guardToolAfter(guard, metadata.name, value, {
        normalizer,
        fn,
        owner,
      });
      const resultBlocked = blockedResultValue(resultDecision, metadata.name);
      return resultBlocked !== null ? resultBlocked : value;
    } catch (error) {
      await syncLocalCacheNow(guard, { reason: "client_error" });
      throw error;
    } finally {
      syncLocalCacheAsync(guard, { reason: "round_complete" });
    }
  };
  return markGuarded(wrapper);
}

function makeGuardedLLMCallable(
  guard,
  fn,
  { label, normalizer = null, owner = null, callTarget = null } = {}
) {
  if (isGuarded(fn)) {
    return fn;
  }

  const wrapper = async (...args) => {
    try {
      const beforeDecision = await guardLLMBefore(guard, {
        label,
        args,
        normalizer,
        fn,
        owner,
      });
      const beforeBlocked = blockedLLMValue(beforeDecision);
      if (beforeBlocked !== null) {
        return beforeBlocked;
      }

      const raw = await (callTarget != null ? fn.apply(callTarget, args) : fn(...args));
      const decision = await guardLLMAfter(guard, raw, {
        label,
        normalizer,
        fn,
        owner,
      });
      const blocked = blockedLLMValue(decision);
      return blocked !== null ? blocked : raw;
    } catch (error) {
      await syncLocalCacheNow(guard, { reason: "client_error" });
      throw error;
    } finally {
      syncLocalCacheAsync(guard, { reason: "round_complete" });
    }
  };
  return markGuarded(wrapper);
}

function resolveAttrPath(obj, path) {
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

function patchLLMMethods(
  guard,
  obj,
  {
    methods = ["create", "complete", "completion", "generate", "invoke", "ainvoke", "predict", "chat"],
    normalizer = null,
    owner = null,
  } = {}
) {
  let patched = 0;
  for (const name of methods) {
    const [target, leaf, fn] = resolveAttrPath(obj, name);
    if (typeof fn !== "function" || isGuarded(fn)) {
      continue;
    }
    if (
      setAttr(
        target,
        leaf,
        makeGuardedLLMCallable(guard, fn, {
          label: name,
          normalizer,
          owner: owner != null ? owner : target,
          callTarget: target,
        })
      )
    ) {
      patched += 1;
    }
  }
  return patched;
}

module.exports = {
  bindArguments,
  guardLLMAfter,
  guardLLMBefore,
  guardToolAfter,
  guardToolBefore,
  isGuarded,
  makeGuardedLLMCallable,
  makeGuardedTool,
  markGuarded,
  markPatched,
  patchLLMMethods,
  registerToolMetadata,
  setAttr,
  toolName,
};

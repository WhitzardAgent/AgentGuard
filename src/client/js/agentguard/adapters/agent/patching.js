"use strict";

const ev = require("../../schemas/events");
const { DecisionType } = require("../../schemas/decisions");
const { ToolMetadata } = require("../../tools/metadata");
const { DEFAULT_AGENT_EVENT_NORMALIZER } = require("./normalization");

const PATCHED_ATTR = "__agentguard_patched__";
const WRAPPED_ATTR = "__agentguard_wrapped__";
const MAX_LLM_LOOPBACK_ATTEMPTS = 3;

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
  if (payload && typeof payload === "object" && !Array.isArray(payload)) {
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
  if (decision.decision_type === DecisionType.LOOP_BACK_TO_LLM) {
    return {
      agentguard: "loop_back_to_llm",
      reason: decision.reason,
      decision: decision.processed_content,
    };
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
      const resolved = resolveNormalizer(normalizer);
      let currentArgs = Array.isArray(args) ? [...args] : [];
      let currentKwargs = {};
      const arguments_ = bindArguments(fn, currentArgs);
      const decision = await guardToolBefore(guard, metadata, arguments_, {
        normalizer,
        fn,
        owner,
      });
      if (decision.decision_type === DecisionType.MODIFY_TOOL_INVOKE) {
        ({ args: currentArgs, kwargs: currentKwargs } = modifiedToolArgs(decision, {
          toolMetadata: metadata,
          args: currentArgs,
          kwargs: currentKwargs,
          normalizer: resolved,
          fn,
          owner,
        }));
      }
      const blocked = blockedToolValue(decision, metadata.name);
      if (blocked !== null) {
        return blocked;
      }

      let value;
      try {
        value = await invokeWithArgsAndKwargs(fn, currentArgs, { callTarget, kwargs: currentKwargs });
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
      if (resultDecision.decision_type === DecisionType.MODIFY_TOOL_RESULT) {
        value = modifiedToolResult(resultDecision, {
          toolName: metadata.name,
          result: value,
          normalizer: resolved,
          fn,
          owner,
        });
      }
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

async function runGuardedLLM(
  guard,
  fn,
  {
    label,
    args = [],
    kwargs = {},
    normalizer = null,
    owner = null,
    callTarget = null,
    blockedValue = blockedLLMValue,
  } = {}
) {
  const resolved = resolveNormalizer(normalizer);
  let currentArgs = Array.isArray(args) ? [...args] : [];
  let currentKwargs = isPlainObject(kwargs) ? { ...kwargs } : {};
  let attempts = 0;

  while (true) {
    const beforeDecision = await guardLLMBefore(guard, {
      label,
      args: currentArgs,
      kwargs: currentKwargs,
      normalizer: resolved,
      fn,
      owner,
    });
    if (beforeDecision.decision_type === DecisionType.LOOP_BACK_TO_LLM) {
      if (attempts >= MAX_LLM_LOOPBACK_ATTEMPTS) {
        return blockedValue(beforeDecision);
      }
      ({ args: currentArgs, kwargs: currentKwargs } = loopbackLLMArgs(beforeDecision, {
        label,
        args: currentArgs,
        kwargs: currentKwargs,
        normalizer: resolved,
        fn,
        owner,
      }));
      attempts += 1;
      continue;
    }
    if (beforeDecision.decision_type === DecisionType.MODIFY_LLM_INPUT) {
      ({ args: currentArgs, kwargs: currentKwargs } = modifiedLLMArgs(beforeDecision, {
        label,
        args: currentArgs,
        kwargs: currentKwargs,
        normalizer: resolved,
        fn,
        owner,
      }));
    }

    const beforeBlocked = blockedValue(beforeDecision);
    if (beforeBlocked !== null) {
      return beforeBlocked;
    }

    const raw = await invokeLLM(fn, currentArgs, { callTarget, kwargs: currentKwargs });
    const decision = await guardLLMAfter(guard, raw, {
      label,
      normalizer: resolved,
      fn,
      owner,
    });
    if (decision.decision_type === DecisionType.LOOP_BACK_TO_LLM) {
      if (attempts >= MAX_LLM_LOOPBACK_ATTEMPTS) {
        return blockedValue(decision);
      }
      ({ args: currentArgs, kwargs: currentKwargs } = loopbackLLMArgs(decision, {
        label,
        args: currentArgs,
        kwargs: currentKwargs,
        normalizer: resolved,
        fn,
        owner,
      }));
      attempts += 1;
      continue;
    }
    let finalRaw = raw;
    if (decision.decision_type === DecisionType.MODIFY_LLM_OUTPUT) {
      finalRaw = modifiedLLMOutput(decision, {
        label,
        output: raw,
        normalizer: resolved,
        fn,
        owner,
      });
    }

    const blocked = blockedValue(decision);
    return blocked !== null ? blocked : finalRaw;
  }
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
      return await runGuardedLLM(guard, fn, {
        label,
        args,
        kwargs: {},
        normalizer,
        owner,
        callTarget,
      });
    } catch (error) {
      await syncLocalCacheNow(guard, { reason: "client_error" });
      throw error;
    } finally {
      syncLocalCacheAsync(guard, { reason: "round_complete" });
    }
  };
  return markGuarded(wrapper);
}

function loopbackLLMArgs(
  decision,
  { label, args = [], kwargs = {}, normalizer, fn = null, owner = null } = {}
) {
  const payload = processedPayloadFromDecision(decision);
  const denormalized = normalizer.denormalize_llm_input({
    label,
    payload,
    args,
    kwargs,
    fn,
    owner,
  });
  return {
    args: Array.isArray(denormalized.args) ? [...denormalized.args] : [],
    kwargs: isPlainObject(denormalized.kwargs) ? { ...denormalized.kwargs } : {},
  };
}

function modifiedLLMArgs(
  decision,
  { label, args = [], kwargs = {}, normalizer, fn = null, owner = null } = {}
) {
  const denormalized = normalizer.denormalize_llm_input({
    label,
    payload: processedPayloadFromDecision(decision),
    args,
    kwargs,
    fn,
    owner,
  });
  return {
    args: Array.isArray(denormalized.args) ? [...denormalized.args] : [],
    kwargs: isPlainObject(denormalized.kwargs) ? { ...denormalized.kwargs } : {},
  };
}

function modifiedLLMOutput(
  decision,
  { label, output, normalizer, fn = null, owner = null } = {}
) {
  const denormalized = normalizer.denormalize_llm_output({
    label,
    payload: processedPayloadFromDecision(decision),
    output,
    fn,
    owner,
  });
  return denormalized.output;
}

function modifiedToolArgs(
  decision,
  { toolMetadata, args = [], kwargs = {}, normalizer, fn = null, owner = null } = {}
) {
  const denormalized = normalizer.denormalize_tool_invoke({
    tool_metadata: toolMetadata,
    payload: processedPayloadFromDecision(decision),
    args,
    kwargs,
    fn,
    owner,
  });
  return {
    args: Array.isArray(denormalized.args) ? [...denormalized.args] : [],
    kwargs: isPlainObject(denormalized.kwargs) ? { ...denormalized.kwargs } : {},
  };
}

function modifiedToolResult(
  decision,
  { toolName, result, normalizer, fn = null, owner = null } = {}
) {
  const denormalized = normalizer.denormalize_tool_result({
    tool_name: toolName,
    payload: processedPayloadFromDecision(decision),
    result,
    fn,
    owner,
  });
  return denormalized.result;
}

function processedPayloadFromDecision(decision) {
  return coerceLoopbackPayload(decision && decision.processed_content);
}

function coerceLoopbackPayload(payload) {
  if (typeof payload !== "string") {
    return payload;
  }
  const text = payload.trim();
  if (!text || !["{", "["].includes(text[0])) {
    return payload;
  }
  try {
    return JSON.parse(text);
  } catch (_) {
    return payload;
  }
}

async function invokeLLM(fn, args, { callTarget = null, kwargs = {} } = {}) {
  return invokeWithArgsAndKwargs(fn, args, { callTarget, kwargs });
}

async function invokeWithArgsAndKwargs(fn, args, { callTarget = null, kwargs = {} } = {}) {
  const nextArgs = Array.isArray(args) ? [...args] : [];
  if (isPlainObject(kwargs) && Object.keys(kwargs).length) {
    if (!nextArgs.length) {
      nextArgs.push(kwargs);
    } else if (isPlainObject(nextArgs[0])) {
      nextArgs[0] = { ...nextArgs[0], ...kwargs };
    } else {
      nextArgs.push(kwargs);
    }
  }
  return callTarget != null ? fn.apply(callTarget, nextArgs) : fn(...nextArgs);
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

function isPlainObject(value) {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
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
  invokeWithArgsAndKwargs,
  makeGuardedLLMCallable,
  makeGuardedTool,
  markGuarded,
  markPatched,
  patchLLMMethods,
  registerToolMetadata,
  runGuardedLLM,
  setAttr,
  toolName,
};

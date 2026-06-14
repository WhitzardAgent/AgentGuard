"use strict";

const ev = require("../../schemas/events");
const { DecisionType } = require("../../schemas/decisions");

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

function toolName(tool, fn = null, fallback = "tool") {
  return String((tool && (tool.name || tool.__name__)) || (fn && fn.name) || fallback);
}

function bindArguments(args, kwargs = {}) {
  if (args.length === 1 && args[0] && typeof args[0] === "object" && !Array.isArray(args[0])) {
    return { ...args[0], ...kwargs };
  }
  const out = { ...kwargs };
  if (args.length) {
    out._args = [...args];
  }
  return out;
}

function setAttr(obj, attr, value) {
  try {
    obj[attr] = value;
    return true;
  } catch (_) {
    return false;
  }
}

function registerToolMetadata(guard, fn, { name, tool = null, capabilities = null } = {}) {
  const description = (tool && tool.description) || "";
  const caps = capabilities || (tool && tool.capabilities) || [];
  return guard.register_tool(fn, {
    name,
    description: String(description).trim().split("\n")[0],
    capabilities: [...caps],
  });
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
      const arguments_ = bindArguments(args);
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
      await guard.runtime.guard(ev.llm_input(guard.context, { label, args }));
      const raw = await fn(...args);
      const decision = (await guard.runtime.guard(ev.llm_output(guard.context, raw), { phase: "after" })).decision;
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
    if (typeof obj[name] !== "function" || isGuarded(obj[name])) {
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
  toolName,
  bindArguments,
  setAttr,
  makeGuardedTool,
  makeGuardedLLMCallable,
  patchLLMMethods,
};

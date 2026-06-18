"use strict";

const { BaseAgentAdapter } = require("./base");
const {
  guardToolAfter,
  guardToolBefore,
  isGuarded,
  makeGuardedTool,
  patchLLMMethods,
  setAttr,
  toolName,
} = require("./patching");
const { DecisionType } = require("../../schemas/decisions");
const { AdapterError } = require("../../utils/errors");

class OpenAIAgentsAdapter extends BaseAgentAdapter {
  constructor() {
    super();
    this.name = "openai_agents";
  }

  can_wrap(agent) {
    const mod = String(agent?.constructor?.module || agent?.constructor?.name || "").toLowerCase();
    return mod.includes("agents") || mod.includes("openai");
  }

  generate(agent, messages) {
    const prompt = messages.length ? messages[messages.length - 1].content || "" : "";
    const fn = agent?.run || agent?.invoke;
    if (typeof fn === "function") {
      try {
        return fn.call(agent, prompt);
      } catch (error) {
        throw new AdapterError(`openai agents run failed: ${String(error.message || error)}`);
      }
    }
    throw new AdapterError("openai agent exposes no run/invoke");
  }

  patchtool(agent, guard) {
    let patched = 0;
    const tools = agent?.tools || agent?._tools;
    if (tools && typeof tools === "object") {
      const list = Array.isArray(tools) ? tools.entries() : Object.entries(tools);
      for (const [key, tool] of list) {
        if (looksLikeFunctionTool(tool)) {
          patched += patchOpenAITool(tool, guard, { name: String(tool.name || key) });
        } else if (typeof tool === "function") {
          const name = Array.isArray(tools) ? toolName(tool, null, `tool_${key}`) : String(key);
          if (Array.isArray(tools)) {
            tools[key] = makeGuardedTool(guard, tool, { name, tool });
          } else {
            tools[key] = makeGuardedTool(guard, tool, { name, tool });
          }
          patched += 1;
        }
      }
    }
    return patched;
  }

  patchLLM(agent, guard) {
    let patched = 0;
    const seen = new Set();
    for (const candidate of iterOpenAILLMCandidates(agent)) {
      if (!candidate || seen.has(candidate)) {
        continue;
      }
      seen.add(candidate);
      patched += patchLLMMethods(guard, candidate, {
        methods: ["create", "complete", "completion", "generate", "invoke", "ainvoke"],
      });
      const completions = candidate.chat && candidate.chat.completions;
      if (completions && !seen.has(completions)) {
        seen.add(completions);
        patched += patchLLMMethods(guard, completions, { methods: ["create"] });
      }
      const responses = candidate.responses;
      if (responses && !seen.has(responses)) {
        seen.add(responses);
        patched += patchLLMMethods(guard, responses, { methods: ["create"] });
      }
    }
    return patched;
  }
}

function looksLikeFunctionTool(tool) {
  return Boolean(tool && typeof tool.on_invoke_tool === "function" && tool.name);
}

function* iterOpenAILLMCandidates(agent) {
  for (const slot of ["model", "_model", "client", "_client", "llm", "_llm"]) {
    if (agent && agent[slot]) {
      yield agent[slot];
    }
  }
}

function patchOpenAITool(tool, guard, { name }) {
  const original = tool && tool.on_invoke_tool;
  if (typeof original !== "function" || isGuarded(original)) {
    return 0;
  }
  const metadata = guard.register_tool(original, { name });
  const guardedInvoke = async (...args) => {
    try {
      const toolArgs = extractJsonArgs(args);
      const decision = await guardToolBefore(guard, metadata, toolArgs);
      if (decision.decision_type === DecisionType.DENY) {
        return JSON.stringify({ agentguard: "blocked", reason: decision.reason });
      }
      if (decision.requires_user || decision.requires_remote) {
        return JSON.stringify({
          agentguard: "pending",
          reason: decision.reason,
          decision: decision.decision_type,
        });
      }
      let value;
      try {
        value = await original.apply(tool, args);
      } catch (error) {
        await guardToolAfter(guard, name, null, { error: String(error.message || error) });
        throw error;
      }
      const resultDecision = await guardToolAfter(guard, name, value);
      if (resultDecision.decision_type === DecisionType.DENY) {
        return JSON.stringify({ agentguard: "blocked", reason: resultDecision.reason });
      }
      if (resultDecision.decision_type === DecisionType.SANITIZE) {
        return JSON.stringify({ agentguard: "sanitized", reason: resultDecision.reason });
      }
      return value;
    } catch (error) {
      await guard.runtime.sync_local_cache_now({ reason: "client_error" });
      throw error;
    } finally {
      guard.runtime.sync_local_cache_async({ reason: "round_complete" });
    }
  };
  guardedInvoke.__agentguard_wrapped__ = true;
  return setAttr(tool, "on_invoke_tool", guardedInvoke) ? 1 : 0;
}

function extractJsonArgs(args, kwargs = {}) {
  let raw = null;
  if (args.length >= 2) {
    raw = args[1];
  } else if (Object.prototype.hasOwnProperty.call(kwargs, "json_input")) {
    raw = kwargs.json_input;
  } else if (Object.prototype.hasOwnProperty.call(kwargs, "input")) {
    raw = kwargs.input;
  }
  if (typeof raw === "string") {
    try {
      const parsed = JSON.parse(raw);
      return parsed && typeof parsed === "object" && !Array.isArray(parsed) ? parsed : { _raw: parsed };
    } catch (_) {
      return { _raw: raw, _unparsed: true };
    }
  }
  if (raw && typeof raw === "object" && !Array.isArray(raw)) {
    return raw;
  }
  return { ...kwargs };
}

module.exports = {
  OpenAIAgentsAdapter,
};

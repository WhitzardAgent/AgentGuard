"use strict";

const ev = require("../schemas/events");
const { DecisionType } = require("../schemas/decisions");

function defaultLLMAdapters() {
  return [callableAdapter()];
}

function selectLLMAdapter(llm, adapters = []) {
  for (const adapter of adapters) {
    if (adapter && typeof adapter.supports === "function" && adapter.supports(llm)) {
      return adapter;
    }
  }
  throw new Error("no compatible llm adapter found");
}

function callableAdapter() {
  return {
    supports(llm) {
      return typeof llm === "function";
    },
    wrap(llm, runtime) {
      return {
        async complete(request = {}) {
          let currentRequest = request;
          const messages = Array.isArray(currentRequest.messages)
            ? currentRequest.messages
            : currentRequest.prompt != null
              ? [{ role: "user", content: currentRequest.prompt }]
              : [];
          const inputEvent = ev.llm_input(runtime.context, messages);
          const before = await runtime.guard(inputEvent, { phase: "before" });
          if (before.decision && before.decision.decision_type === DecisionType.DENY) {
            return {
              agentguard: "blocked",
              reason: before.decision.reason,
              decision: before.decision.decision_type,
            };
          }
          if (before.decision && before.decision.decision_type === DecisionType.MODIFY_LLM_INPUT) {
            currentRequest = denormalizeRequest(decisionPayload(before.decision), currentRequest);
          }

          const output = await llm(currentRequest);
          const outputText = typeof output === "string" ? output : output?.text ?? output?.output ?? output;
          const outputEvent = ev.llm_output(runtime.context, outputText);
          const after = await runtime.guard(outputEvent, { phase: "after" });
          if (after.decision && after.decision.decision_type === DecisionType.DENY) {
            return {
              agentguard: "blocked",
              reason: after.decision.reason,
              decision: after.decision.decision_type,
            };
          }
          if (after.decision && after.decision.decision_type === DecisionType.MODIFY_LLM_OUTPUT) {
            return denormalizeResponse(decisionPayload(after.decision), output);
          }
          return output;
        },
      };
    },
  };
}

function decisionPayload(decision) {
  const payload = decision && decision.processed_content;
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

function denormalizeRequest(payload, request) {
  if (request && typeof request === "object" && !Array.isArray(request)) {
    if (payload && typeof payload === "object" && !Array.isArray(payload)) {
      const updated = { ...request };
      Object.assign(updated, payload);
      return updated;
    }
    const updated = { ...request };
    if (Array.isArray(updated.messages)) {
      updated.messages = payload;
      return updated;
    }
    if (updated.prompt != null) {
      updated.prompt = payload;
      return updated;
    }
  }
  return payload;
}

function denormalizeResponse(payload, response) {
  if (typeof response === "string") {
    if (payload && typeof payload === "object" && !Array.isArray(payload)) {
      for (const key of ["output", "final_output", "content", "text", "message"]) {
        if (payload[key] != null) {
          return String(payload[key]);
        }
      }
    }
    return String(payload);
  }
  if (response && typeof response === "object" && !Array.isArray(response)) {
    if (payload && typeof payload === "object" && !Array.isArray(payload)) {
      return { ...response, ...payload };
    }
    const updated = { ...response };
    for (const key of ["output", "text", "content"]) {
      if (Object.prototype.hasOwnProperty.call(updated, key)) {
        updated[key] = payload;
        return updated;
      }
    }
  }
  return payload;
}

module.exports = {
  defaultLLMAdapters,
  selectLLMAdapter,
};

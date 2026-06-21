"use strict";

const ev = require("../schemas/events");

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
          const messages = Array.isArray(request.messages)
            ? request.messages
            : request.prompt != null
              ? [{ role: "user", content: request.prompt }]
              : [];
          const inputEvent = ev.llm_input(runtime.context, messages);
          const before = await runtime.guard(inputEvent, { phase: "before" });
          if (before.decision && before.decision.decision_type === "deny") {
            return {
              agentguard: "blocked",
              reason: before.decision.reason,
              decision: before.decision.decision_type,
            };
          }

          const output = await llm(request);
          const outputText = typeof output === "string" ? output : output?.text ?? output?.output ?? output;
          const outputEvent = ev.llm_output(runtime.context, outputText);
          const after = await runtime.guard(outputEvent, { phase: "after" });
          if (after.decision && after.decision.decision_type === "deny") {
            return {
              agentguard: "blocked",
              reason: after.decision.reason,
              decision: after.decision.decision_type,
            };
          }
          return output;
        },
      };
    },
  };
}

module.exports = {
  defaultLLMAdapters,
  selectLLMAdapter,
};

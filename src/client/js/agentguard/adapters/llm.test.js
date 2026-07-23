"use strict";

const assert = require("node:assert/strict");
const test = require("node:test");

const { defaultLLMAdapters } = require("./llm");
const { GuardDecision } = require("../schemas/decisions");

test("callableAdapter rewrites request and response for modify decisions", async () => {
  const calls = [];
  const runtime = {
    context: { session_id: "sess-llm" },
    async guard(event) {
      if (event && event.event_type === "llm_input") {
        return {
          decision: GuardDecision.modify_llm_input("rewrite request", {
            processed_content: JSON.stringify({
              messages: [{ role: "user", content: "rewritten prompt" }],
              temperature: 0.1,
            }),
          }),
        };
      }
      return {
        decision: GuardDecision.modify_llm_output("rewrite response", {
          processed_content: JSON.stringify({ text: "safe output" }),
        }),
      };
    },
  };

  const llm = async (request) => {
    calls.push(request);
    return { text: "raw output" };
  };

  const wrapped = defaultLLMAdapters()[0].wrap(llm, runtime);
  const result = await wrapped.complete({
    model: "gpt-test",
    messages: [{ role: "user", content: "original prompt" }],
  });

  assert.deepEqual(calls, [{
    model: "gpt-test",
    messages: [{ role: "user", content: "rewritten prompt" }],
    temperature: 0.1,
  }]);
  assert.deepEqual(result, { text: "safe output" });
});

test("callableAdapter keeps deny behavior unchanged", async () => {
  const runtime = {
    context: { session_id: "sess-llm-deny" },
    async guard() {
      return { decision: GuardDecision.deny("blocked") };
    },
  };

  const llm = async () => {
    throw new Error("should not be called");
  };

  const wrapped = defaultLLMAdapters()[0].wrap(llm, runtime);
  const result = await wrapped.complete({ prompt: "blocked prompt" });

  assert.deepEqual(result, {
    agentguard: "blocked",
    reason: "blocked",
    decision: "deny",
  });
});

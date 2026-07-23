"use strict";

const assert = require("node:assert/strict");
const test = require("node:test");

const { makeGuardedLLMCallable, makeGuardedTool } = require("./patching");
const { GuardDecision } = require("../../schemas/decisions");

function buildGuard(decisions) {
  let index = 0;
  return {
    context: { session_id: "sess-test" },
    register_tool(_fn, meta) {
      return meta;
    },
    runtime: {
      async guard() {
        const decision = decisions[index] || GuardDecision.allow("ok");
        index += 1;
        return { decision };
      },
      async sync_local_cache_now() {},
      sync_local_cache_async() {},
    },
  };
}

const TEST_NORMALIZER = {
  normalize_llm_input({ args = [] } = {}) {
    return { payload: { messages: args }, metadata: { adapter: "test" } };
  },
  normalize_llm_output({ output } = {}) {
    return { payload: output, metadata: { adapter: "test" } };
  },
  denormalize_llm_input({ payload } = {}) {
    return {
      args: [payload.input],
      kwargs: payload.options ? { ...payload.options } : {},
    };
  },
  denormalize_llm_output({ payload } = {}) {
    return { output: { text: payload.output } };
  },
  normalize_tool_invoke({ arguments: arguments_ = {} } = {}) {
    return { arguments: arguments_, capabilities: [], metadata: { adapter: "test" } };
  },
  denormalize_tool_invoke({ payload } = {}) {
    return {
      args: [payload.input],
      kwargs: payload.options ? { ...payload.options } : {},
    };
  },
  normalize_tool_result({ result, error = null } = {}) {
    return { result, error, metadata: { adapter: "test" } };
  },
  denormalize_tool_result({ payload } = {}) {
    return {
      result: payload.result,
      error: payload.error ?? null,
    };
  },
};

test("makeGuardedLLMCallable applies modify input/output decisions", async () => {
  const calls = [];
  const guard = buildGuard([
    GuardDecision.modify_llm_input("rewrite request", {
      processed_content: JSON.stringify({
        input: "rewritten prompt",
        options: { mode: "safe" },
      }),
    }),
    GuardDecision.modify_llm_output("rewrite response", {
      processed_content: JSON.stringify({ output: "sanitized reply" }),
    }),
  ]);

  const llm = async (input, options = null) => {
    calls.push([input, options]);
    return { text: "raw reply" };
  };

  const wrapped = makeGuardedLLMCallable(guard, llm, {
    label: "invoke",
    normalizer: TEST_NORMALIZER,
  });

  const result = await wrapped("original prompt");

  assert.deepEqual(calls, [["rewritten prompt", { mode: "safe" }]]);
  assert.deepEqual(result, { text: "sanitized reply" });
});

test("makeGuardedTool applies modify invoke/result decisions", async () => {
  const calls = [];
  const guard = buildGuard([
    GuardDecision.modify_tool_invoke("rewrite tool args", {
      processed_content: JSON.stringify({
        input: "rewritten input",
        options: { mode: "safe" },
      }),
    }),
    GuardDecision.modify_tool_result("rewrite tool result", {
      processed_content: JSON.stringify({ result: "safe result" }),
    }),
  ]);

  const tool = async (input, options = null) => {
    calls.push([input, options]);
    return "raw result";
  };

  const wrapped = makeGuardedTool(guard, tool, {
    name: "demo_tool",
    normalizer: TEST_NORMALIZER,
  });

  const result = await wrapped("original input");

  assert.deepEqual(calls, [["rewritten input", { mode: "safe" }]]);
  assert.equal(result, "safe result");
});

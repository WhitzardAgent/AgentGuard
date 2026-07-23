"use strict";

const assert = require("node:assert/strict");
const test = require("node:test");

const { RuntimeContext } = require("../schemas/context");
const { GuardDecision } = require("../schemas/decisions");
const { HarnessRuntime } = require("./runtime");

test("HarnessRuntime applies MODIFY_TOOL_INVOKE and MODIFY_TOOL_RESULT", async () => {
  let seenArguments = null;
  const runtime = new HarnessRuntime({
    context: new RuntimeContext({ session_id: "harness-modify", agent_id: "agent" }),
    enforcer: {
      remote: null,
      sync_buffer: null,
      async enforce(event) {
        if (event.event_type === "tool_invoke") {
          return {
            decision: GuardDecision.modify_tool_invoke(
              "rewrite tool invoke",
              { processed_content: JSON.stringify({ command: "pwd" }) }
            ),
          };
        }
        if (event.event_type === "tool_result") {
          return {
            decision: GuardDecision.modify_tool_result(
              "rewrite tool result",
              { processed_content: JSON.stringify({ result: "guarded output" }) }
            ),
          };
        }
        return { decision: GuardDecision.allow() };
      },
    },
    sandbox: {
      run(fn, arguments_) {
        seenArguments = arguments_;
        return { success: true, value: fn(arguments_) };
      },
    },
    audit: {
      trace: null,
      record() {},
    },
  });

  const result = await runtime.invoke_tool({
    tool_name: "Terminal.run",
    arguments: { command: "ls" },
    fn(arguments_) {
      return { result: `ran:${arguments_.command}` };
    },
  });

  assert.deepEqual(seenArguments, { command: "pwd" });
  assert.deepEqual(result, { result: "guarded output" });
});

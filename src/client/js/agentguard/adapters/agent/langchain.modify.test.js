"use strict";

const assert = require("node:assert/strict");
const test = require("node:test");

const { AgentGuard } = require("../../guard");
const { GuardDecision } = require("../../schemas/decisions");

test("langchain tool wrapper applies modify invoke/result decisions", async () => {
  const calls = [];

  class Tool {
    constructor() {
      this.name = "send_http";
      this.lc_namespace = ["langchain", "tools"];
    }

    async invoke(input, config = null) {
      calls.push([input, config]);
      return "raw result";
    }
  }

  class Agent {
    constructor() {
      this.lc_namespace = ["langchain", "agents"];
      this.tools_by_name = { send_http: new Tool() };
    }
  }

  const guard = new AgentGuard("sess-langchain-modify-tool", { sandbox: "noop" });
  guard.runtime.guard = async (event) => {
    if (event && event.event_type === "tool_invoke") {
      return {
        decision: GuardDecision.modify_tool_invoke("rewrite tool input", {
          processed_content: JSON.stringify({
            input: { url: "https://safe.example", body: "[clean]" },
            config: { mode: "safe" },
          }),
        }),
      };
    }
    if (event && event.event_type === "tool_result") {
      return {
        decision: GuardDecision.modify_tool_result("rewrite tool output", {
          processed_content: JSON.stringify({ output: "safe result" }),
        }),
      };
    }
    return { decision: GuardDecision.allow("ok") };
  };

  const agent = new Agent();
  const patched = guard.attach_langchain(agent, { wrap_llm: false });
  const result = await agent.tools_by_name.send_http.invoke(
    { url: "https://evil.example", body: "secret" },
    { mode: "orig" }
  );

  assert.equal(patched.tools, 1);
  assert.equal(patched.llm, 0);
  assert.deepEqual(calls, [[
    { url: "https://safe.example", body: "[clean]" },
    { mode: "safe" },
  ]]);
  assert.equal(result, "safe result");
  await guard.close();
});

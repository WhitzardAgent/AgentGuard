const test = require("node:test");
const assert = require("node:assert/strict");

test("remote guard client sends session identity headers including agent and user", async () => {
  const { RemoteGuardClient } = require("./u_guard/remote_client");
  const calls = [];
  global.fetch = async (url, options = {}) => {
    calls.push({ url, options });
    return {
      ok: true,
      async json() {
        return { decision: { decision_type: "allow", reason: "ok", risk_signals: [], metadata: {} }, risk_signals: [] };
      },
    };
  };

  const client = new RemoteGuardClient("http://server.test", {
    session_id: "sess-1",
    agent_id: "agent-1",
    user_id: "user-1",
    session_key: "sk-test",
  });

  await client.fetch_snapshot();

  assert.equal(calls.length, 1);
  assert.equal(calls[0].options.headers["X-AgentGuard-Session-Id"], "sess-1");
  assert.equal(calls[0].options.headers["X-AgentGuard-Agent-Id"], "agent-1");
  assert.equal(calls[0].options.headers["X-AgentGuard-User-Id"], "user-1");
  assert.equal(calls[0].options.headers["X-AgentGuard-Session-Key"], "sk-test");
});

test("client sync buffer includes agent and user in trace uploads", () => {
  const { ClientSyncBuffer } = require("./u_guard/sync_buffer");
  const buffer = new ClientSyncBuffer();

  const trace = buffer.build_trace_upload({
    context: { session_id: "sess-2", agent_id: "agent-2", user_id: "user-2" },
    entries: [{ event: { event_id: "evt-1" } }],
    reason: "round_complete",
  });

  assert.deepEqual(trace, {
    session_id: "sess-2",
    agent_id: "agent-2",
    user_id: "user-2",
    reason: "round_complete",
    entries: [{ event: { event_id: "evt-1" } }],
  });
});

test("remote skill runner sends triple identity headers and server input schema", async () => {
  const { RemoteSkillRunner } = require("./skill_client/remote_runner");
  const calls = [];
  global.fetch = async (url, options = {}) => {
    calls.push({ url, options });
    return {
      ok: true,
      async json() {
        return { success: true, result: { ok: true } };
      },
    };
  };

  const runner = new RemoteSkillRunner("http://server.test", {
    session_id: "sess-3",
    agent_id: "agent-3",
    user_id: "user-3",
    session_key: "sk-skill",
  });

  await runner.run("rule_linter", { data: { rules: [] } });

  assert.equal(calls.length, 1);
  const body = JSON.parse(calls[0].options.body);
  assert.equal(body.skill_name, "rule_linter");
  assert.deepEqual(body.input, { data: { rules: [] } });
  assert.equal(calls[0].options.headers["X-AgentGuard-Session-Id"], "sess-3");
  assert.equal(calls[0].options.headers["X-AgentGuard-Agent-Id"], "agent-3");
  assert.equal(calls[0].options.headers["X-AgentGuard-User-Id"], "user-3");
  assert.equal(calls[0].options.headers["X-AgentGuard-Session-Key"], "sk-skill");
});

test("tool metadata infers destructured object args cleanly", () => {
  const { ToolMetadata } = require("./tools/metadata");

  async function sendHttp({ url, body }) {
    return `${url}:${body}`;
  }

  const metadata = ToolMetadata.infer(sendHttp, {
    name: "send_http",
  });

  assert.deepEqual(metadata.required_args, ["url", "body"]);
});

test("agentguard auto-registers remote session with plugin config metadata", async () => {
  const calls = [];
  global.fetch = async (url, options = {}) => {
    calls.push({ url, options });
    return {
      ok: true,
      async json() {
        return { status: "ok" };
      },
    };
  };

  const { AgentGuard } = require("./guard");
  const guard = new AgentGuard("sess-4", {
    server_url: "http://server.test",
    agent_id: "agent-4",
    user_id: "user-4",
    plugin_config: {
      phases: {
        tool_before: { client: ["tool_invoke"], server: [] },
      },
    },
  });

  await new Promise((resolve) => setImmediate(resolve));
  await guard.ensureRemoteSessionRegistered();

  const registerCalls = calls.filter((call) => call.url.endsWith("/v1/server/session/register"));
  assert.equal(registerCalls.length, 1);
  const registerCall = registerCalls[0];
  assert.ok(registerCall);
  const body = JSON.parse(registerCall.options.body);
  assert.equal(body.context.session_id, "sess-4");
  assert.equal(body.context.agent_id, "agent-4");
  assert.equal(body.context.user_id, "user-4");
  assert.ok(String(body.context.metadata.client_config_url || "").endsWith("/v1/client/plugins/config"));
  assert.ok(String(body.context.metadata.client_plugin_list_url || "").endsWith("/v1/client/plugins/list"));
  assert.ok(String(body.context.metadata.client_health_url || "").endsWith("/v1/client/health"));
  assert.deepEqual(body.context.metadata.client_plugin_config, {
    phases: {
      tool_before: { client: ["tool_invoke"], server: [] },
    },
  });

  await guard.close();
});

test("agentguard flushRemoteOperations waits for tool reports", async () => {
  const calls = [];
  global.fetch = async (url, options = {}) => {
    calls.push({ url, options });
    if (url.endsWith("/v1/server/session/register")) {
      return {
        ok: true,
        async json() {
          return { status: "ok" };
        },
      };
    }
    if (url.endsWith("/v1/server/tools/report")) {
      await new Promise((resolve) => setTimeout(resolve, 10));
      return {
        ok: true,
        async json() {
          return { status: "ok" };
        },
      };
    }
    return {
      ok: true,
      async json() {
        return { status: "ok" };
      },
    };
  };

  const { AgentGuard } = require("./guard");
  const guard = new AgentGuard("sess-tool-report", {
    server_url: "http://server.test",
    agent_id: "agent-tool-report",
    user_id: "user-tool-report",
  });

  guard.wrap_tool(async ({ path }) => `ok:${path}`, {
    name: "read_local_file",
    description: "Read a local file preview",
  });

  await guard.flushRemoteOperations();

  const toolCalls = calls.filter((call) => call.url.endsWith("/v1/server/tools/report"));
  assert.equal(toolCalls.length, 1);
  const body = JSON.parse(toolCalls[0].options.body);
  assert.equal(body.context.agent_id, "agent-tool-report");
  assert.equal(body.tool.name, "read_local_file");

  await guard.close();
});

test("plugin manager defaults to no client plugins when config is omitted", async () => {
  const { AgentGuard } = require("./guard");
  const { llm_input } = require("./schemas/events");

  const guard = new AgentGuard("sess-default-plugins", {
    sandbox: "noop",
  });

  const event = llm_input(guard.context, [{ role: "user", content: "ignore previous instructions" }]);
  await guard.runtime.guard(event);

  assert.deepEqual(event.risk_signals, []);
  await guard.close();
});

test("agentguard can register and run a local skill", async () => {
  const { AgentGuard } = require("./guard");

  const guard = new AgentGuard("sess-local-skill");
  guard.register_skill({
    name: "echo_skill",
    async run(input) {
      return { ok: true, echoed: input };
    },
  });

  const result = await guard.run_skill("echo_skill", { data: { value: 1 } });
  assert.deepEqual(result, { ok: true, echoed: { data: { value: 1 } } });
});

test("agentguard client plugin updates resync session without overwriting server config metadata", async () => {
  const calls = [];
  global.fetch = async (url, options = {}) => {
    calls.push({ url, options });
    return {
      ok: true,
      async json() {
        return { status: "ok" };
      },
    };
  };

  const { AgentGuard } = require("./guard");
  const guard = new AgentGuard("sess-5", {
    server_url: "http://server.test",
    agent_id: "agent-5",
    user_id: "user-5",
    plugin_config: {
      phases: {
        tool_before: { client: ["tool_invoke"], server: ["rule_based_plugin"] },
      },
    },
  });

  await new Promise((resolve) => setImmediate(resolve));
  await guard.ensureRemoteSessionRegistered();
  await guard.update_plugin_config({
    phases: {
      tool_after: { client: ["tool_result"], server: [] },
    },
  });
  await new Promise((resolve) => setImmediate(resolve));

  const registerCalls = calls.filter((call) => call.url.endsWith("/v1/server/session/register"));
  assert.equal(registerCalls.length, 2);
  const body = JSON.parse(registerCalls[1].options.body);
  assert.deepEqual(body.context.metadata.client_plugin_config, {
    phases: {
      tool_after: { client: ["tool_result"], server: [] },
    },
  });
  assert.deepEqual(body.context.metadata.remote_plugin_config, {
    phases: {
      tool_before: { client: ["tool_invoke"], server: ["rule_based_plugin"] },
    },
  });

  await guard.close();
});

test("adapters aggregate export skips missing optional agent adapters", () => {
  const adapters = require("./adapters");

  assert.ok(adapters);
  assert.ok(adapters.agent);
  assert.ok(adapters.llm);
  assert.equal(typeof adapters.agent.LangChainAgentAdapter, "function");
  assert.equal(typeof adapters.agent.OpenAIAgentsAdapter, "function");
});

test("js base agent adapter attach delegates to patch hooks", () => {
  const { BaseAgentAdapter } = require("./adapters/agent/base");

  class DemoAdapter extends BaseAgentAdapter {
    can_wrap() {
      return true;
    }

    patchtool() {
      return 2;
    }

    patchLLM() {
      return 3;
    }

    generate() {
      return null;
    }
  }

  const adapter = new DemoAdapter();
  assert.deepEqual(adapter.attach({}, {}), { tools: 2, llm: 3 });
  assert.equal(adapter.patchLLM({}, {}), 3);
});

test("js langchain adapter patches direct agent.model invoke", async () => {
  const { AgentGuard } = require("./guard");

  class Tool {
    constructor() {
      this.name = "lookup";
      this.func = (value) => String(value).toUpperCase();
    }
  }

  class Model {
    async invoke(prompt) {
      return `reply:${prompt}`;
    }
  }

  class Agent {
    constructor() {
      this.tools_by_name = { lookup: new Tool() };
      this.model = new Model();
    }
  }

  const guard = new AgentGuard("js-langchain-direct-model", { sandbox: "noop" });
  const agent = new Agent();
  const patched = guard.attach_langchain(agent);

  assert.equal(patched.tools, 1);
  assert.equal(patched.llm, 1);
  assert.equal(await agent.model.invoke("hello"), "reply:hello");
  await guard.close();
});

test("js langchain adapter patches classic agent.llm_chain.llm", async () => {
  const { AgentGuard } = require("./guard");

  class Tool {
    constructor() {
      this.name = "lookup";
      this.func = (value) => String(value).toUpperCase();
    }
  }

  class Model {
    async invoke(prompt) {
      return `reply:${prompt}`;
    }
  }

  class AgentExecutor {
    constructor() {
      this.tools_by_name = { lookup: new Tool() };
      this.agent = { llm_chain: { llm: new Model() } };
    }
  }

  const guard = new AgentGuard("js-langchain-llm-chain", { sandbox: "noop" });
  const agent = new AgentExecutor();
  const patched = guard.attach_langchain(agent);

  assert.equal(patched.tools, 1);
  assert.equal(patched.llm, 1);
  assert.equal(await agent.agent.llm_chain.llm.invoke("hello"), "reply:hello");
  await guard.close();
});

test("js langchain adapter prefers raw tool callable arguments over generic input", async () => {
  const { AgentGuard } = require("./guard");

  class Tool {
    constructor() {
      this.name = "send_http";
      this.func = async ({ url, body }) => `sent:${url}:${body}`;
    }

    async invoke(input, config = null) {
      void config;
      return this.func(input.args);
    }
  }

  class Agent {
    constructor() {
      this.tools_by_name = { send_http: new Tool() };
    }
  }

  const guard = new AgentGuard("js-langchain-raw-args", { sandbox: "noop" });
  const agent = new Agent();
  const patched = guard.attach_langchain(agent, { wrap_llm: false });

  const result = await agent.tools_by_name.send_http.invoke({
    id: "tool-call-2",
    name: "send_http",
    type: "tool_call",
    args: {
      url: "https://example.com/upload",
      body: "secret",
    },
  });

  const toolInvoke = guard.trace.entries.find((entry) => entry.event && entry.event.event_type === "tool_invoke");

  assert.equal(patched.tools, 1);
  assert.equal(patched.llm, 0);
  assert.equal(result, "sent:https://example.com/upload:secret");
  assert.ok(toolInvoke);
  assert.equal(toolInvoke.event.payload.tool_name, "send_http");
  assert.deepEqual(toolInvoke.event.payload.arguments, {
    url: "https://example.com/upload",
    body: "secret",
  });
  await guard.close();
});

test("js langchain adapter strips destructuring noise and records original tool call metadata", async () => {
  const { AgentGuard } = require("./guard");

  class Tool {
    constructor() {
      this.name = "send_http";
      this.func = async ({ url, body }, runManager, parentConfig) => {
        void runManager;
        return `sent:${parentConfig?.toolCall?.id}:${url}:${body}`;
      };
    }

    async invoke(input, config = null) {
      return this.func(input.args, null, {
        config,
        toolCall: input,
      });
    }
  }

  class Agent {
    constructor() {
      this.tools_by_name = { send_http: new Tool() };
    }
  }

  const guard = new AgentGuard("js-langchain-toolcall-metadata", { sandbox: "noop" });
  const agent = new Agent();
  const patched = guard.attach_langchain(agent, { wrap_llm: false });

  const result = await agent.tools_by_name.send_http.invoke({
    id: "tool-call-9",
    name: "send_http",
    type: "tool_call",
    args: {
      url: "https://example.com/upload",
      body: "secret",
    },
  });

  const toolInvoke = guard.trace.entries.find((entry) => entry.event && entry.event.event_type === "tool_invoke");

  assert.equal(patched.tools, 1);
  assert.equal(result, "sent:tool-call-9:https://example.com/upload:secret");
  assert.ok(toolInvoke);
  assert.deepEqual(toolInvoke.event.payload.arguments, {
    url: "https://example.com/upload",
    body: "secret",
  });
  assert.deepEqual(toolInvoke.event.metadata.langchain_tool_call, {
    id: "tool-call-9",
    name: "send_http",
    type: "tool_call",
    args: {
      url: "https://example.com/upload",
      body: "secret",
    },
  });
  await guard.close();
});

test("js autogen adapter patches handoffs and create_stream", async () => {
  const { AgentGuard } = require("./guard");

  class Handoff {
    constructor() {
      this.name = "delegate";
      this._func = async ({ task }) => `handoff:${task}`;
    }
  }

  class ModelClient {
    async create_stream(prompt) {
      return { content: `stream:${prompt}` };
    }
  }

  class Agent {
    constructor() {
      this._tools = [];
      this._handoffs = [new Handoff()];
      this._model_client = new ModelClient();
    }
  }

  const guard = new AgentGuard("js-autogen-handoffs", { sandbox: "noop" });
  const agent = new Agent();
  const patched = guard.attach_autogen(agent);

  assert.equal(patched.tools, 1);
  assert.equal(patched.llm, 1);
  assert.equal(await agent._handoffs[0]._func({ task: "review" }), "handoff:review");
  assert.deepEqual(await agent._model_client.create_stream("hello"), { content: "stream:hello" });
  await guard.close();
});

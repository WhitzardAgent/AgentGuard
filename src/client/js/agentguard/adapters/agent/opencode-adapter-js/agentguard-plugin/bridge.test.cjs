"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const test = require("node:test");

const { AgentGuardOpenCodeBridge, __testing } = require("./bridge.cjs");
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

function buildOpenCodeInput(overrides = {}) {
  return {
    project: { id: "project-1", name: "Demo" },
    directory: "/repo/demo",
    worktree: "/repo/demo",
    ...overrides,
  };
}

function buildMessage(text = "raw prompt", sessionID = "session-1") {
  return {
    info: {
      id: "msg_1",
      role: "user",
      metadata: { sessionID },
    },
    parts: [{ type: "text", text }],
  };
}

function okJson(payload) {
  return {
    ok: true,
    status: 200,
    async json() {
      return payload;
    },
    async text() {
      return JSON.stringify(payload);
    },
  };
}

function runtimeIssue({
  agentID = "ag_opencode_created",
  sessionID = "ags_opencode_created",
  userID = "42",
  token = "runtime-token-opencode",
  expiresAt = Math.floor(Date.now() / 1000) + 3600,
} = {}) {
  return {
    status: "ok",
    agent_id: agentID,
    session_id: sessionID,
    user_id: userID,
    session_token: token,
    issued_at: Math.floor(Date.now() / 1000),
    expires_at: expiresAt,
    auth_method: "opencode_dpop",
    external_session_id: null,
  };
}

function bootstrapIssue(agents = [{ external_agent_id: "opencode:agentguard", agent_id: "ag_opencode_agentguard" }]) {
  return {
    status: "ok",
    provider: "opencode",
    user_id: "42",
    ticket_prefix: "agt_ticket",
    agents: agents.map((agent) => ({
      external_agent_id: agent.external_agent_id,
      agent_id: agent.agent_id,
      agent_identity_code: `agic_${agent.agent_id}`,
      public_key_thumbprint: `thumb_${agent.agent_id}`,
      status: "active",
      user_bound: true,
      user_binding_created: true,
      user_binding_updated: false,
    })),
  };
}

test("opencode configPath loads AgentGuard config JSON", () => {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "ag-opencode-config-"));
  const configPath = path.join(tmp, "agentguard.json");
  fs.writeFileSync(
    configPath,
    JSON.stringify({
      serverUrl: "http://127.0.0.1:38080",
      userTicket: "ticket-1",
      auditPath: "./audit.jsonl",
      phases: buildPhases(),
    }),
    "utf8",
  );

  const config = __testing.normalizePluginConfig({ configPath });

  assert.equal(config.serverUrl, "http://127.0.0.1:38080");
  assert.equal(config.userTicket, "ticket-1");
  assert.equal(config.auditPath, path.join(tmp, "audit.jsonl"));
  fs.rmSync(tmp, { recursive: true, force: true });
});

test("opencode tool.execute.before mutates args from modify_tool_invoke", async () => {
  class ModifyToolInvokePlugin extends BasePlugin {
    constructor() {
      super();
      this.event_types = [EventType.TOOL_INVOKE];
    }

    check() {
      return new CheckResult({
        decision_candidate: GuardDecision.modify_tool_invoke("rewrite args", {
          processed_content: JSON.stringify({
            args: { command: "pwd" },
          }),
        }),
        is_final: true,
      });
    }
  }

  const bridge = new AgentGuardOpenCodeBridge({
    opencode: buildOpenCodeInput(),
    pluginConfig: {
      phases: buildPhases({
        tool_before: { client: [ModifyToolInvokePlugin], server: [] },
      }),
    },
  });
  const output = { args: { command: "rm -rf /tmp/demo" } };

  await bridge.runToolExecuteBefore({
    input: { tool: "bash", sessionID: "session-1", callID: "call-1" },
    output,
  });

  assert.deepEqual(output.args, { command: "pwd" });
});

test("opencode tool.execute.before throws on local deny and records audit", async () => {
  class DenyToolPlugin extends BasePlugin {
    constructor() {
      super();
      this.event_types = [EventType.TOOL_INVOKE];
    }

    check() {
      return new CheckResult({
        decision_candidate: GuardDecision.deny("blocked by test policy"),
        is_final: true,
      });
    }
  }

  const bridge = new AgentGuardOpenCodeBridge({
    opencode: buildOpenCodeInput(),
    pluginConfig: {
      phases: buildPhases({
        tool_before: { client: [DenyToolPlugin], server: [] },
      }),
    },
  });

  await assert.rejects(
    bridge.runToolExecuteBefore({
      input: { tool: "bash", sessionID: "session-1", callID: "call-1" },
      output: { args: { command: "whoami" } },
    }),
    /blocked by test policy/,
  );

  const state = bridge.getState({ sessionID: "session-1" });
  assert.equal(state.audit.records()[0].decision_type, "deny");
});

test("opencode tool.execute.after mutates result from modify_tool_result", async () => {
  class ModifyToolResultPlugin extends BasePlugin {
    constructor() {
      super();
      this.event_types = [EventType.TOOL_RESULT];
    }

    check() {
      return new CheckResult({
        decision_candidate: GuardDecision.modify_tool_result("rewrite result", {
          processed_content: JSON.stringify({
            result: {
              title: "Safe result",
              output: "redacted output",
              metadata: { redacted: true },
            },
          }),
        }),
        is_final: true,
      });
    }
  }

  const bridge = new AgentGuardOpenCodeBridge({
    opencode: buildOpenCodeInput(),
    pluginConfig: {
      phases: buildPhases({
        tool_after: { client: [ModifyToolResultPlugin], server: [] },
      }),
    },
  });
  const output = { title: "Raw result", output: "secret output", metadata: {} };

  await bridge.runToolExecuteAfter({
    input: { tool: "read", sessionID: "session-1", callID: "call-1", args: {} },
    output,
  });

  assert.deepEqual(output, {
    title: "Safe result",
    output: "redacted output",
    metadata: { redacted: true },
  });
});

test("opencode chat.messages.transform rewrites latest user text", async () => {
  class ModifyLlmInputPlugin extends BasePlugin {
    constructor() {
      super();
      this.event_types = [EventType.LLM_INPUT];
    }

    check() {
      return new CheckResult({
        decision_candidate: GuardDecision.modify_llm_input("rewrite prompt", {
          processed_content: JSON.stringify({
            messages: [{ role: "user", content: "safe prompt" }],
          }),
        }),
        is_final: true,
      });
    }
  }

  const bridge = new AgentGuardOpenCodeBridge({
    opencode: buildOpenCodeInput(),
    pluginConfig: {
      phases: buildPhases({
        llm_before: { client: [ModifyLlmInputPlugin], server: [] },
      }),
    },
  });
  const output = { messages: [buildMessage()] };

  await bridge.runChatMessagesTransform({ input: {}, output });

  assert.equal(output.messages[0].parts[0].text, "safe prompt");
});

test("opencode text.complete rewrites model output", async () => {
  class ModifyLlmOutputPlugin extends BasePlugin {
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

  const bridge = new AgentGuardOpenCodeBridge({
    opencode: buildOpenCodeInput(),
    pluginConfig: {
      phases: buildPhases({
        llm_after: { client: [ModifyLlmOutputPlugin], server: [] },
      }),
    },
  });
  const output = { text: "raw answer" };

  await bridge.runTextComplete({
    input: { sessionID: "session-1", messageID: "msg_2", partID: "part_1" },
    output,
  });

  assert.equal(output.text, "safe answer");
});

test("opencode remote flow bootstraps agent before canonical runtime session", async (t) => {
  const originalFetch = global.fetch;
  const originalKeyDir = process.env.AGENTGUARD_DPOP_KEY_DIR;
  const originalAgentKeyDir = process.env.AGENTGUARD_AGENT_KEY_DIR;
  const keyDir = fs.mkdtempSync(path.join(os.tmpdir(), "ag-opencode-dpop-"));
  process.env.AGENTGUARD_DPOP_KEY_DIR = keyDir;
  process.env.AGENTGUARD_AGENT_KEY_DIR = keyDir;
  const calls = [];
  let createCount = 0;
  global.fetch = async (url, options = {}) => {
    const body = options.body ? JSON.parse(String(options.body)) : {};
    calls.push({ url: String(url), headers: options.headers || {}, body });
    if (String(url).endsWith("/v1/server/agents/bootstrap")) {
      return okJson(bootstrapIssue([{ external_agent_id: "opencode:default", agent_id: "ag_opencode_default" }]));
    }
    if (String(url).endsWith("/v1/server/session/create")) {
      return okJson(runtimeIssue({ agentID: "ag_opencode_default" }));
    }
    if (String(url).endsWith("/v1/server/guard/decide")) {
      return okJson({
        decision: {
          decision_type: "allow",
          reason: "allowed by test server",
          metadata: {},
        },
        risk_signals: [],
        plugin_result: {},
      });
    }
    throw new Error(`unexpected URL ${url}`);
  };
  t.after(() => {
    global.fetch = originalFetch;
    if (originalKeyDir === undefined) {
      delete process.env.AGENTGUARD_DPOP_KEY_DIR;
    } else {
      process.env.AGENTGUARD_DPOP_KEY_DIR = originalKeyDir;
    }
    if (originalAgentKeyDir === undefined) {
      delete process.env.AGENTGUARD_AGENT_KEY_DIR;
    } else {
      process.env.AGENTGUARD_AGENT_KEY_DIR = originalAgentKeyDir;
    }
    fs.rmSync(keyDir, { recursive: true, force: true });
  });

  const bridge = new AgentGuardOpenCodeBridge({
    opencode: buildOpenCodeInput(),
    logger: { warn() {} },
    pluginConfig: {
      serverUrl: "http://agentguard.test",
      userTicket: "agt_ticket_1",
      remoteRetries: 0,
      phases: buildPhases(),
    },
  });

  await bridge.runToolExecuteBefore({
    input: { tool: "bash", sessionID: "session-1", callID: "call-1" },
    output: { args: { command: "pwd" } },
  });

  assert.equal(calls.length, 3);
  assert.equal(calls[0].body.provider, "opencode");
  assert.equal(calls[0].body.user_ticket, "agt_ticket_1");
  assert.equal(calls[0].body.provider_instance_id, "opencode-local");
  assert.equal(calls[0].body.metadata.runtime_auth_provider, "opencode");
  assert.equal(calls[0].body.agents[0].external_agent_id, "opencode:default");
  assert.equal(calls[0].body.agents[0].agent_type, "agent");
  assert.match(calls[0].headers.DPoP, /^[^.]+\.[^.]+\.[^.]+$/);
  assert.equal(calls[1].body.provider, "opencode");
  assert.equal(calls[1].body.agent_id, "ag_opencode_default");
  assert.equal(calls[1].body.external_session_id, "session-1");
  assert.equal(calls[1].body.user_ticket, undefined);
  assert.match(calls[1].headers["X-AgentGuard-Agent-Proof"], /^[^.]+\.[^.]+\.[^.]+$/);
  assert.equal(calls[2].body.context.session_id, "ags_opencode_created");
  assert.equal(calls[2].body.context.agent_id, "ag_opencode_default");
  assert.equal(calls[2].body.current_event.event_type, "tool_invoke");
});

test("opencode startup bootstraps configured agents and later hooks create sessions", async (t) => {
  const originalFetch = global.fetch;
  const originalKeyDir = process.env.AGENTGUARD_DPOP_KEY_DIR;
  const originalAgentKeyDir = process.env.AGENTGUARD_AGENT_KEY_DIR;
  const keyDir = fs.mkdtempSync(path.join(os.tmpdir(), "ag-opencode-dpop-"));
  process.env.AGENTGUARD_DPOP_KEY_DIR = keyDir;
  process.env.AGENTGUARD_AGENT_KEY_DIR = keyDir;
  const calls = [];
  global.fetch = async (url, options = {}) => {
    const body = options.body ? JSON.parse(String(options.body)) : {};
    calls.push({ url: String(url), headers: options.headers || {}, body });
    if (String(url).endsWith("/v1/server/agents/bootstrap")) {
      return okJson(bootstrapIssue([{ external_agent_id: "opencode:agentguard", agent_id: "ag_opencode_agentguard" }]));
    }
    if (String(url).endsWith("/v1/server/session/create")) {
      return okJson(runtimeIssue({ agentID: "ag_opencode_agentguard" }));
    }
    if (String(url).endsWith("/v1/server/guard/decide")) {
      return okJson({
        decision: {
          decision_type: "allow",
          reason: "allowed by test server",
          metadata: {},
        },
        risk_signals: [],
        plugin_result: {},
      });
    }
    throw new Error(`unexpected URL ${url}`);
  };
  t.after(() => {
    global.fetch = originalFetch;
    if (originalKeyDir === undefined) {
      delete process.env.AGENTGUARD_DPOP_KEY_DIR;
    } else {
      process.env.AGENTGUARD_DPOP_KEY_DIR = originalKeyDir;
    }
    if (originalAgentKeyDir === undefined) {
      delete process.env.AGENTGUARD_AGENT_KEY_DIR;
    } else {
      process.env.AGENTGUARD_AGENT_KEY_DIR = originalAgentKeyDir;
    }
    fs.rmSync(keyDir, { recursive: true, force: true });
  });

  const bridge = new AgentGuardOpenCodeBridge({
    opencode: buildOpenCodeInput(),
    pluginConfig: {
      serverUrl: "http://agentguard.test",
      userTicket: "agt_ticket_1",
      opencodeAgent: "agentguard",
      agentDisplayName: "opencode:agentguard",
      remoteRetries: 0,
      phases: buildPhases(),
    },
  });

  assert.equal(await bridge.startRuntimeAuthSession(), true);
  await bridge.runToolExecuteBefore({
    input: { tool: "bash", sessionID: "session-1", callID: "call-1" },
    output: { args: { command: "pwd" } },
  });

  const createCalls = calls.filter((call) => call.url.endsWith("/v1/server/session/create"));
  const bootstrapCalls = calls.filter((call) => call.url.endsWith("/v1/server/agents/bootstrap"));
  const decideCalls = calls.filter((call) => call.url.endsWith("/v1/server/guard/decide"));
  assert.equal(bootstrapCalls.length, 1);
  assert.equal(createCalls.length, 1);
  assert.equal(decideCalls.length, 1);
  assert.equal(bootstrapCalls[0].body.user_ticket, "agt_ticket_1");
  assert.equal(bootstrapCalls[0].body.agents[0].external_agent_id, "opencode:agentguard");
  assert.equal(createCalls[0].body.user_ticket, undefined);
  assert.equal(createCalls[0].body.agent_id, "ag_opencode_agentguard");
  assert.equal(createCalls[0].body.external_session_id, "session-1");
  assert.equal(createCalls[0].body.metadata.opencode_agent, "agentguard");
  assert.equal(createCalls[0].body.metadata.external_agent_id, "opencode:agentguard");
  assert.equal(createCalls[0].body.metadata.display_agent_id, "opencode:agentguard");
  assert.equal(createCalls[0].body.metadata.agent_type, "agent");
  assert.equal(bridge.bootstrapTicketConsumed, true);
  assert.equal(decideCalls[0].body.context.session_id, "ags_opencode_created");
  assert.equal(decideCalls[0].body.context.agent_id, "ag_opencode_agentguard");
});

test("opencode configured catalog supports multiple agents with separate sessions", async (t) => {
  const originalFetch = global.fetch;
  const originalKeyDir = process.env.AGENTGUARD_DPOP_KEY_DIR;
  const originalAgentKeyDir = process.env.AGENTGUARD_AGENT_KEY_DIR;
  const keyDir = fs.mkdtempSync(path.join(os.tmpdir(), "ag-opencode-dpop-"));
  process.env.AGENTGUARD_DPOP_KEY_DIR = keyDir;
  process.env.AGENTGUARD_AGENT_KEY_DIR = keyDir;
  const calls = [];
  global.fetch = async (url, options = {}) => {
    const body = options.body ? JSON.parse(String(options.body)) : {};
    calls.push({ url: String(url), headers: options.headers || {}, body });
    if (String(url).endsWith("/v1/server/agents/bootstrap")) {
      return okJson(bootstrapIssue([
        { external_agent_id: "opencode:agentguard", agent_id: "ag_opencode_agentguard" },
        { external_agent_id: "opencode:reviewer", agent_id: "ag_opencode_reviewer" },
      ]));
    }
    if (String(url).endsWith("/v1/server/session/create")) {
      const sessionID = body.agent_id === "ag_opencode_reviewer"
        ? "ags_opencode_reviewer_session"
        : "ags_opencode_agentguard_session";
      return okJson(runtimeIssue({ agentID: body.agent_id, sessionID }));
    }
    throw new Error(`unexpected URL ${url}`);
  };
  t.after(() => {
    global.fetch = originalFetch;
    if (originalKeyDir === undefined) {
      delete process.env.AGENTGUARD_DPOP_KEY_DIR;
    } else {
      process.env.AGENTGUARD_DPOP_KEY_DIR = originalKeyDir;
    }
    if (originalAgentKeyDir === undefined) {
      delete process.env.AGENTGUARD_AGENT_KEY_DIR;
    } else {
      process.env.AGENTGUARD_AGENT_KEY_DIR = originalAgentKeyDir;
    }
    fs.rmSync(keyDir, { recursive: true, force: true });
  });

  const bridge = new AgentGuardOpenCodeBridge({
    opencode: buildOpenCodeInput(),
    pluginConfig: {
      serverUrl: "http://agentguard.test",
      userTicket: "agt_ticket_1",
      opencodeAgents: [
        { id: "agentguard", name: "OpenCode agentguard" },
        { id: "reviewer", name: "OpenCode reviewer" },
      ],
      remoteRetries: 0,
      phases: buildPhases(),
    },
  });

  assert.equal(await bridge.startRuntimeAuthSession(), true);
  await bridge.runEvent({
    event: { type: "session.created", properties: { info: { id: "session-main", agent: "agentguard" } } },
  });
  await bridge.runEvent({
    event: { type: "session.created", properties: { info: { id: "session-review", agent: "reviewer" } } },
  });

  const bootstrapCalls = calls.filter((call) => call.url.endsWith("/v1/server/agents/bootstrap"));
  const createCalls = calls.filter((call) => call.url.endsWith("/v1/server/session/create"));
  assert.equal(bootstrapCalls.length, 1);
  assert.deepEqual(
    bootstrapCalls[0].body.agents.map((agent) => agent.external_agent_id),
    ["opencode:agentguard", "opencode:reviewer"],
  );
  assert.equal(createCalls.length, 2);
  assert.deepEqual(
    createCalls.map((call) => [call.body.agent_id, call.body.external_session_id]),
    [
      ["ag_opencode_agentguard", "session-main"],
      ["ag_opencode_reviewer", "session-review"],
    ],
  );
  assert.equal(
    bridge.sessions.get("session:session-main:agent:opencode:agentguard").context.agent_id,
    "ag_opencode_agentguard",
  );
  assert.equal(
    bridge.sessions.get("session:session-review:agent:opencode:reviewer").context.agent_id,
    "ag_opencode_reviewer",
  );
});

test("opencode startup bootstraps missing catalog agents when first agent is cached", async (t) => {
  const originalFetch = global.fetch;
  const originalKeyDir = process.env.AGENTGUARD_DPOP_KEY_DIR;
  const originalAgentKeyDir = process.env.AGENTGUARD_AGENT_KEY_DIR;
  const keyDir = fs.mkdtempSync(path.join(os.tmpdir(), "ag-opencode-dpop-"));
  process.env.AGENTGUARD_DPOP_KEY_DIR = keyDir;
  process.env.AGENTGUARD_AGENT_KEY_DIR = keyDir;
  const calls = [];
  global.fetch = async (url, options = {}) => {
    const body = options.body ? JSON.parse(String(options.body)) : {};
    calls.push({ url: String(url), headers: options.headers || {}, body });
    if (String(url).endsWith("/v1/server/agents/bootstrap")) {
      return okJson(bootstrapIssue([
        { external_agent_id: "opencode:agentguard", agent_id: "ag_opencode_agentguard" },
        { external_agent_id: "opencode:reviewer", agent_id: "ag_opencode_reviewer" },
      ]));
    }
    throw new Error(`unexpected URL ${url}`);
  };
  t.after(() => {
    global.fetch = originalFetch;
    if (originalKeyDir === undefined) {
      delete process.env.AGENTGUARD_DPOP_KEY_DIR;
    } else {
      process.env.AGENTGUARD_DPOP_KEY_DIR = originalKeyDir;
    }
    if (originalAgentKeyDir === undefined) {
      delete process.env.AGENTGUARD_AGENT_KEY_DIR;
    } else {
      process.env.AGENTGUARD_AGENT_KEY_DIR = originalAgentKeyDir;
    }
    fs.rmSync(keyDir, { recursive: true, force: true });
  });

  const bridge = new AgentGuardOpenCodeBridge({
    opencode: buildOpenCodeInput(),
    pluginConfig: {
      serverUrl: "http://agentguard.test",
      userTicket: "agt_ticket_1",
      opencodeAgents: [
        { id: "agentguard", name: "OpenCode agentguard" },
        { id: "reviewer", name: "OpenCode reviewer" },
      ],
      remoteRetries: 0,
      phases: buildPhases(),
    },
  });
  bridge.agentRegistrations.set("opencode-local\x1fopencode:agentguard", {
    external_agent_id: "opencode:agentguard",
    agent_id: "ag_cached_agentguard",
    agent_identity_code: "agic_cached",
    public_key_thumbprint: "thumb_cached",
    agent_identity_key_id: "opencode-local\x1fopencode:agentguard\x1fagent",
    user_bound: true,
  });

  assert.equal(await bridge.startRuntimeAuthSession(), true);

  const bootstrapCalls = calls.filter((call) => call.url.endsWith("/v1/server/agents/bootstrap"));
  assert.equal(bootstrapCalls.length, 1);
  assert.deepEqual(
    bootstrapCalls[0].body.agents.map((agent) => agent.external_agent_id),
    ["opencode:agentguard", "opencode:reviewer"],
  );
  assert.equal(
    [...bridge.agentRegistrations.values()].some((item) => item.external_agent_id === "opencode:reviewer"),
    true,
  );
});

test("opencode same session routes switched agent events to that agent", async (t) => {
  const originalFetch = global.fetch;
  const originalKeyDir = process.env.AGENTGUARD_DPOP_KEY_DIR;
  const originalAgentKeyDir = process.env.AGENTGUARD_AGENT_KEY_DIR;
  const keyDir = fs.mkdtempSync(path.join(os.tmpdir(), "ag-opencode-dpop-"));
  process.env.AGENTGUARD_DPOP_KEY_DIR = keyDir;
  process.env.AGENTGUARD_AGENT_KEY_DIR = keyDir;
  const calls = [];
  global.fetch = async (url, options = {}) => {
    const body = options.body ? JSON.parse(String(options.body)) : {};
    calls.push({ url: String(url), headers: options.headers || {}, body });
    if (String(url).endsWith("/v1/server/agents/bootstrap")) {
      return okJson(bootstrapIssue([
        { external_agent_id: "opencode:agentguard", agent_id: "ag_opencode_agentguard" },
        { external_agent_id: "opencode:reviewer", agent_id: "ag_opencode_reviewer" },
      ]));
    }
    if (String(url).endsWith("/v1/server/session/create")) {
      return okJson(runtimeIssue({
        agentID: body.agent_id,
        sessionID: body.agent_id === "ag_opencode_reviewer"
          ? "ags_opencode_reviewer_session"
          : "ags_opencode_agentguard_session",
      }));
    }
    if (String(url).endsWith("/v1/server/guard/decide")) {
      return okJson({
        decision: {
          decision_type: "allow",
          reason: "allowed by test server",
          metadata: {},
        },
        risk_signals: [],
        plugin_result: {},
      });
    }
    throw new Error(`unexpected URL ${url}`);
  };
  t.after(() => {
    global.fetch = originalFetch;
    if (originalKeyDir === undefined) {
      delete process.env.AGENTGUARD_DPOP_KEY_DIR;
    } else {
      process.env.AGENTGUARD_DPOP_KEY_DIR = originalKeyDir;
    }
    if (originalAgentKeyDir === undefined) {
      delete process.env.AGENTGUARD_AGENT_KEY_DIR;
    } else {
      process.env.AGENTGUARD_AGENT_KEY_DIR = originalAgentKeyDir;
    }
    fs.rmSync(keyDir, { recursive: true, force: true });
  });

  const bridge = new AgentGuardOpenCodeBridge({
    opencode: buildOpenCodeInput(),
    pluginConfig: {
      serverUrl: "http://agentguard.test",
      userTicket: "agt_ticket_1",
      opencodeAgents: [
        { id: "agentguard", name: "OpenCode agentguard" },
        { id: "reviewer", name: "OpenCode reviewer" },
      ],
      remoteRetries: 0,
      phases: buildPhases(),
    },
  });

  await bridge.runToolExecuteBefore({
    input: { tool: "bash", sessionID: "session-1", agent: "agentguard", callID: "call-1" },
    output: { args: { command: "echo agentguard" } },
  });
  await bridge.runToolExecuteBefore({
    input: { tool: "bash", sessionID: "session-1", agent: "reviewer", callID: "call-2" },
    output: { args: { command: "pwd" } },
  });

  const createCalls = calls.filter((call) => call.url.endsWith("/v1/server/session/create"));
  const decideCalls = calls.filter((call) => call.url.endsWith("/v1/server/guard/decide"));
  assert.deepEqual(
    createCalls.map((call) => [call.body.agent_id, call.body.external_session_id]),
    [
      ["ag_opencode_agentguard", "session-1"],
      ["ag_opencode_reviewer", "session-1"],
    ],
  );
  assert.deepEqual(
    decideCalls.map((call) => call.body.context.agent_id),
    ["ag_opencode_agentguard", "ag_opencode_reviewer"],
  );
});

test("opencode session.created creates runtime session and later hooks reuse it", async (t) => {
  const originalFetch = global.fetch;
  const originalKeyDir = process.env.AGENTGUARD_DPOP_KEY_DIR;
  const originalAgentKeyDir = process.env.AGENTGUARD_AGENT_KEY_DIR;
  const keyDir = fs.mkdtempSync(path.join(os.tmpdir(), "ag-opencode-dpop-"));
  process.env.AGENTGUARD_DPOP_KEY_DIR = keyDir;
  process.env.AGENTGUARD_AGENT_KEY_DIR = keyDir;
  const calls = [];
  global.fetch = async (url, options = {}) => {
    const body = options.body ? JSON.parse(String(options.body)) : {};
    calls.push({ url: String(url), headers: options.headers || {}, body });
    if (String(url).endsWith("/v1/server/agents/bootstrap")) {
      return okJson(bootstrapIssue([{ external_agent_id: "opencode:agentguard", agent_id: "ag_opencode_agentguard" }]));
    }
    if (String(url).endsWith("/v1/server/session/create")) {
      return okJson(runtimeIssue({ agentID: "ag_opencode_agentguard" }));
    }
    if (String(url).endsWith("/v1/server/guard/decide")) {
      return okJson({
        decision: {
          decision_type: "allow",
          reason: "allowed by test server",
          metadata: {},
        },
        risk_signals: [],
        plugin_result: {},
      });
    }
    throw new Error(`unexpected URL ${url}`);
  };
  t.after(() => {
    global.fetch = originalFetch;
    if (originalKeyDir === undefined) {
      delete process.env.AGENTGUARD_DPOP_KEY_DIR;
    } else {
      process.env.AGENTGUARD_DPOP_KEY_DIR = originalKeyDir;
    }
    if (originalAgentKeyDir === undefined) {
      delete process.env.AGENTGUARD_AGENT_KEY_DIR;
    } else {
      process.env.AGENTGUARD_AGENT_KEY_DIR = originalAgentKeyDir;
    }
    fs.rmSync(keyDir, { recursive: true, force: true });
  });

  const bridge = new AgentGuardOpenCodeBridge({
    opencode: buildOpenCodeInput(),
    logger: { warn() {} },
    pluginConfig: {
      serverUrl: "http://agentguard.test",
      userTicket: "agt_ticket_1",
      remoteRetries: 0,
      phases: buildPhases(),
    },
  });

  await bridge.runEvent({
    event: {
      type: "session.created",
      properties: {
        info: {
          id: "session-1",
          agent: "agentguard",
          model: { providerID: "deepseek", modelID: "deepseek-v4-flash" },
          projectID: "project-1",
          directory: "/repo/demo",
          worktree: "/repo/demo",
        },
      },
    },
  });

  await bridge.runToolExecuteBefore({
    input: { tool: "bash", sessionID: "session-1", callID: "call-1" },
    output: { args: { command: "pwd" } },
  });

  const createCalls = calls.filter((call) => call.url.endsWith("/v1/server/session/create"));
  const bootstrapCalls = calls.filter((call) => call.url.endsWith("/v1/server/agents/bootstrap"));
  const decideCalls = calls.filter((call) => call.url.endsWith("/v1/server/guard/decide"));
  assert.equal(bootstrapCalls.length, 1);
  assert.equal(createCalls.length, 1);
  assert.equal(decideCalls.length, 1);
  assert.equal(bootstrapCalls[0].body.user_ticket, "agt_ticket_1");
  assert.equal(bootstrapCalls[0].body.agents[0].external_agent_id, "opencode:agentguard");
  assert.equal(createCalls[0].body.user_ticket, undefined);
  assert.equal(createCalls[0].body.agent_id, "ag_opencode_agentguard");
  assert.equal(createCalls[0].body.external_session_id, "session-1");
  assert.equal(createCalls[0].body.metadata.opencode_session_id, "session-1");
  assert.equal(createCalls[0].body.metadata.opencode_agent, "agentguard");
  assert.equal(createCalls[0].body.metadata.external_agent_id, "opencode:agentguard");
  assert.equal(createCalls[0].body.metadata.display_agent_id, "opencode:agentguard");
  assert.equal(createCalls[0].body.metadata.agent_type, "agent");
  assert.equal(bridge.clientRuntimeAuth.user_ticket, null);
  assert.equal(bridge.clientRuntimeAuth.ticket_consumed, true);
  assert.equal(decideCalls[0].body.context.session_id, "ags_opencode_created");
  assert.equal(decideCalls[0].body.context.agent_id, "ag_opencode_agentguard");
});

test("opencode runtime auth refresh uses current token without user ticket", async (t) => {
  const originalFetch = global.fetch;
  const originalKeyDir = process.env.AGENTGUARD_DPOP_KEY_DIR;
  const originalAgentKeyDir = process.env.AGENTGUARD_AGENT_KEY_DIR;
  const keyDir = fs.mkdtempSync(path.join(os.tmpdir(), "ag-opencode-dpop-"));
  process.env.AGENTGUARD_DPOP_KEY_DIR = keyDir;
  process.env.AGENTGUARD_AGENT_KEY_DIR = keyDir;
  const calls = [];
  global.fetch = async (url, options = {}) => {
    const body = options.body ? JSON.parse(String(options.body)) : {};
    calls.push({ url: String(url), headers: options.headers || {}, body });
    if (String(url).endsWith("/v1/server/agents/bootstrap")) {
      return okJson(bootstrapIssue([{ external_agent_id: "opencode:default", agent_id: "ag_opencode_default" }]));
    }
    if (String(url).endsWith("/v1/server/session/create")) {
      return okJson(runtimeIssue({ agentID: "ag_opencode_default", token: "runtime-token-old", expiresAt: Math.floor(Date.now() / 1000) + 10 }));
    }
    if (String(url).endsWith("/v1/server/session/refresh")) {
      return okJson(runtimeIssue({ agentID: "ag_opencode_default", token: "runtime-token-new", expiresAt: Math.floor(Date.now() / 1000) + 3600 }));
    }
    throw new Error(`unexpected URL ${url}`);
  };
  t.after(() => {
    global.fetch = originalFetch;
    if (originalKeyDir === undefined) {
      delete process.env.AGENTGUARD_DPOP_KEY_DIR;
    } else {
      process.env.AGENTGUARD_DPOP_KEY_DIR = originalKeyDir;
    }
    if (originalAgentKeyDir === undefined) {
      delete process.env.AGENTGUARD_AGENT_KEY_DIR;
    } else {
      process.env.AGENTGUARD_AGENT_KEY_DIR = originalAgentKeyDir;
    }
    fs.rmSync(keyDir, { recursive: true, force: true });
  });

  const bridge = new AgentGuardOpenCodeBridge({
    opencode: buildOpenCodeInput(),
    logger: { warn() {} },
    pluginConfig: {
      serverUrl: "http://agentguard.test",
      userTicket: "agt_ticket_1",
      remoteRetries: 0,
      phases: buildPhases(),
    },
  });

  const state = bridge.getState({ sessionID: "session-1" });
  await bridge.ensureRuntimeAuth(state);
  await bridge.ensureRuntimeAuth(state, { forceRefresh: true, allowCreate: false });

  const createCalls = calls.filter((call) => call.url.endsWith("/v1/server/session/create"));
  const bootstrapCalls = calls.filter((call) => call.url.endsWith("/v1/server/agents/bootstrap"));
  const refreshCalls = calls.filter((call) => call.url.endsWith("/v1/server/session/refresh"));
  assert.equal(bootstrapCalls.length, 1);
  assert.equal(createCalls.length, 1);
  assert.equal(refreshCalls.length, 1);
  assert.equal(refreshCalls[0].headers.Authorization, "DPoP runtime-token-old");
  assert.equal(refreshCalls[0].headers["X-AgentGuard-User-Ticket"], undefined);
  assert.deepEqual(refreshCalls[0].body, {});
  assert.equal(bridge.clientRuntimeAuth.session_token, "runtime-token-new");
});

test("opencode consumed ticket is not reused after runtime auth refresh failure", async (t) => {
  const originalFetch = global.fetch;
  const originalKeyDir = process.env.AGENTGUARD_DPOP_KEY_DIR;
  const originalAgentKeyDir = process.env.AGENTGUARD_AGENT_KEY_DIR;
  const keyDir = fs.mkdtempSync(path.join(os.tmpdir(), "ag-opencode-dpop-"));
  process.env.AGENTGUARD_DPOP_KEY_DIR = keyDir;
  process.env.AGENTGUARD_AGENT_KEY_DIR = keyDir;
  const calls = [];
  let createCount = 0;
  global.fetch = async (url, options = {}) => {
    const body = options.body ? JSON.parse(String(options.body)) : {};
    calls.push({ url: String(url), headers: options.headers || {}, body });
    if (String(url).endsWith("/v1/server/agents/bootstrap")) {
      return okJson(bootstrapIssue([{ external_agent_id: "opencode:default", agent_id: "ag_opencode_default" }]));
    }
    if (String(url).endsWith("/v1/server/session/create")) {
      createCount += 1;
      if (createCount > 1) {
        return {
          ok: false,
          status: 401,
          async json() {
            return { detail: "OpenCode external session is closed" };
          },
          async text() {
            return JSON.stringify({ detail: "OpenCode external session is closed" });
          },
        };
      }
      return okJson(runtimeIssue({ agentID: "ag_opencode_default", token: "runtime-token-old", expiresAt: Math.floor(Date.now() / 1000) + 10 }));
    }
    if (String(url).endsWith("/v1/server/session/refresh")) {
      return {
        ok: false,
        status: 401,
        async json() {
          return { detail: "runtime session is not active" };
        },
        async text() {
          return JSON.stringify({ detail: "runtime session is not active" });
        },
      };
    }
    throw new Error(`unexpected URL ${url}`);
  };
  t.after(() => {
    global.fetch = originalFetch;
    if (originalKeyDir === undefined) {
      delete process.env.AGENTGUARD_DPOP_KEY_DIR;
    } else {
      process.env.AGENTGUARD_DPOP_KEY_DIR = originalKeyDir;
    }
    if (originalAgentKeyDir === undefined) {
      delete process.env.AGENTGUARD_AGENT_KEY_DIR;
    } else {
      process.env.AGENTGUARD_AGENT_KEY_DIR = originalAgentKeyDir;
    }
    fs.rmSync(keyDir, { recursive: true, force: true });
  });

  const bridge = new AgentGuardOpenCodeBridge({
    opencode: buildOpenCodeInput(),
    logger: { warn() {} },
    pluginConfig: {
      serverUrl: "http://agentguard.test",
      userTicket: "agt_ticket_1",
      remoteRetries: 0,
      phases: buildPhases(),
    },
  });

  const state = bridge.getState({ sessionID: "session-1" });
  await bridge.ensureRuntimeAuth(state);

  await assert.rejects(
    bridge.runToolExecuteBefore({
      input: { tool: "bash", sessionID: "session-1", callID: "call-1" },
      output: { args: { command: "pwd" } },
    }),
    /AgentGuard runtime authentication failed/,
  );

  const createCalls = calls.filter((call) => call.url.endsWith("/v1/server/session/create"));
  const bootstrapCalls = calls.filter((call) => call.url.endsWith("/v1/server/agents/bootstrap"));
  const refreshCalls = calls.filter((call) => call.url.endsWith("/v1/server/session/refresh"));
  assert.equal(bootstrapCalls.length, 1);
  assert.equal(createCalls.length, 2);
  assert.equal(refreshCalls.length, 1);
  assert.equal(createCalls[1].headers["X-AgentGuard-User-Ticket"], undefined);
  assert.match(createCalls[1].headers["X-AgentGuard-Agent-Proof"], /^[^.]+\.[^.]+\.[^.]+$/);
  assert.equal(bridge.clientRuntimeAuth.user_ticket, null);
  assert.equal(bridge.clientRuntimeAuth.ticket_consumed, true);
  assert.equal(state.audit.records()[0].decision_type, "deny");
  assert.equal(state.audit.records()[0].metadata.decision_metadata.route, "runtime_auth_failed");
});

test("opencode dispose closes active runtime auth session", async (t) => {
  const originalFetch = global.fetch;
  const originalKeyDir = process.env.AGENTGUARD_DPOP_KEY_DIR;
  const originalAgentKeyDir = process.env.AGENTGUARD_AGENT_KEY_DIR;
  const keyDir = fs.mkdtempSync(path.join(os.tmpdir(), "ag-opencode-dpop-"));
  process.env.AGENTGUARD_DPOP_KEY_DIR = keyDir;
  process.env.AGENTGUARD_AGENT_KEY_DIR = keyDir;
  const calls = [];
  global.fetch = async (url, options = {}) => {
    const body = options.body ? JSON.parse(String(options.body)) : {};
    calls.push({ url: String(url), headers: options.headers || {}, body });
    if (String(url).endsWith("/v1/server/agents/bootstrap")) {
      return okJson(bootstrapIssue([{ external_agent_id: "opencode:agentguard", agent_id: "ag_opencode_agentguard" }]));
    }
    if (String(url).endsWith("/v1/server/session/create")) {
      return okJson(runtimeIssue({ agentID: "ag_opencode_agentguard", token: "runtime-token-open" }));
    }
    if (String(url).endsWith("/v1/server/session/close")) {
      return okJson({ status: "ok", session_id: "ags_opencode_created", closed: true });
    }
    throw new Error(`unexpected URL ${url}`);
  };
  t.after(() => {
    global.fetch = originalFetch;
    if (originalKeyDir === undefined) {
      delete process.env.AGENTGUARD_DPOP_KEY_DIR;
    } else {
      process.env.AGENTGUARD_DPOP_KEY_DIR = originalKeyDir;
    }
    if (originalAgentKeyDir === undefined) {
      delete process.env.AGENTGUARD_AGENT_KEY_DIR;
    } else {
      process.env.AGENTGUARD_AGENT_KEY_DIR = originalAgentKeyDir;
    }
    fs.rmSync(keyDir, { recursive: true, force: true });
  });

  const bridge = new AgentGuardOpenCodeBridge({
    opencode: buildOpenCodeInput(),
    pluginConfig: {
      serverUrl: "http://agentguard.test",
      userTicket: "agt_ticket_1",
      remoteRetries: 0,
      phases: buildPhases(),
    },
  });

  await bridge.runEvent({
    event: {
      type: "session.created",
      properties: {
        info: { id: "session-1", agent: "agentguard", projectID: "project-1" },
      },
    },
  });
  await bridge.dispose();

  const closeCalls = calls.filter((call) => call.url.endsWith("/v1/server/session/close"));
  assert.equal(closeCalls.length, 1);
  assert.equal(closeCalls[0].headers.Authorization, "DPoP runtime-token-open");
  assert.equal(bridge.clientRuntimeAuth.session_token, null);
});

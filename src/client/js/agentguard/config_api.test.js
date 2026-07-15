"use strict";

const assert = require("node:assert/strict");
const http = require("node:http");
const test = require("node:test");

const {
  CLIENT_SESSION_CONTROL_PATH,
  ClientConfigAPIServer,
  listRegisteredPlugins,
} = require("./config_api");

test("listRegisteredPlugins includes builtin JS runtime plugins", () => {
  const plugins = listRegisteredPlugins();
  const names = plugins.map((plugin) => plugin.name);

  assert.deepEqual(names, [
    "jailbreak_check",
    "llm_output",
    "qwen3guard_input",
    "qwen3guard_output",
    "tool_invoke",
    "tool_result",
  ]);
  assert.deepEqual(
    plugins.map((plugin) => plugin.event_types),
    [["llm_input"], ["llm_output"], ["llm_input"], ["llm_output"], ["tool_invoke"], ["tool_result"]],
  );
});

test("ClientConfigAPIServer uses advertised host and port in plugin urls", () => {
  const server = new ClientConfigAPIServer(
    { session_key: null, context: { session_id: "sess", agent_id: "agent", user_id: "user" } },
    {
      host: "0.0.0.0",
      port: 38181,
      advertise_host: "10.0.0.8",
      advertise_port: 39000,
    },
  );

  assert.equal(server.plugin_config_url, "http://10.0.0.8:39000/v1/client/plugins/config");
  assert.equal(server.plugin_list_url, "http://10.0.0.8:39000/v1/client/plugins/list");
  assert.equal(server.health_url, "http://10.0.0.8:39000/v1/client/health");
});

test("ClientConfigAPIServer forwards authorized runtime session close requests", async () => {
  const calls = [];
  const server = new ClientConfigAPIServer(
    {
      session_key: "agent:main:main",
      context: { session_id: "sess", agent_id: "agent", user_id: "user" },
      close_runtime_session(body) {
        calls.push(body);
        return { sendPolicy: "deny" };
      },
    },
    { host: "127.0.0.1", port: 0 },
  );

  try {
    await server.start();
    const response = await postJson(server.base_url + CLIENT_SESSION_CONTROL_PATH, {
      headers: { "X-AgentGuard-Session-Key": "agent:main:main" },
      body: { action: "close", openclaw_session_key: "agent:main:main" },
    });

    assert.equal(response.statusCode, 200);
    assert.equal(response.body.status, "ok");
    assert.deepEqual(calls, [{ action: "close", openclaw_session_key: "agent:main:main" }]);
    assert.equal(response.body.result.sendPolicy, "deny");
  } finally {
    await server.stop();
  }
});

function postJson(url, { headers = {}, body = {} } = {}) {
  return new Promise((resolve, reject) => {
    const payload = Buffer.from(JSON.stringify(body));
    const req = http.request(url, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "Content-Length": String(payload.length),
        ...headers,
      },
    }, (res) => {
      const chunks = [];
      res.on("data", (chunk) => chunks.push(chunk));
      res.on("end", () => {
        const raw = Buffer.concat(chunks).toString("utf8");
        resolve({
          statusCode: res.statusCode,
          body: raw ? JSON.parse(raw) : {},
        });
      });
    });
    req.on("error", reject);
    req.end(payload);
  });
}

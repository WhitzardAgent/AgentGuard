"use strict";

const assert = require("node:assert/strict");
const test = require("node:test");

const { ClientConfigAPIServer, listRegisteredPlugins } = require("./config_api");

test("listRegisteredPlugins includes builtin JS runtime plugins", () => {
  const plugins = listRegisteredPlugins();
  const names = plugins.map((plugin) => plugin.name);

  assert.deepEqual(names, ["llm_input", "llm_output", "tool_invoke", "tool_result"]);
  assert.deepEqual(
    plugins.map((plugin) => plugin.event_types),
    [["llm_input"], ["llm_output"], ["tool_invoke"], ["tool_result"]],
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

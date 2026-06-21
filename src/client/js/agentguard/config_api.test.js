"use strict";

const assert = require("node:assert/strict");
const test = require("node:test");

const { listRegisteredPlugins } = require("./config_api");

test("listRegisteredPlugins includes builtin JS runtime plugins", () => {
  const plugins = listRegisteredPlugins();
  const names = plugins.map((plugin) => plugin.name);

  assert.deepEqual(names, ["llm_input", "llm_output", "tool_invoke", "tool_result"]);
  assert.deepEqual(
    plugins.map((plugin) => plugin.event_types),
    [["llm_input"], ["llm_output"], ["tool_invoke"], ["tool_result"]],
  );
});

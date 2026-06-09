const test = require("node:test");
const assert = require("node:assert/strict");

const memoryStore = new Map();

global.localStorage = {
  getItem(key) {
    return memoryStore.has(key) ? memoryStore.get(key) : null;
  },
  setItem(key, value) {
    memoryStore.set(key, String(value));
  },
  removeItem(key) {
    memoryStore.delete(key);
  },
};

global.window = {};

require("../static/pages/rules/rule-storage.js");

const { loadList, saveList } = global.window.AgentGuardRuleStorage;

test("loadList migrates legacy stored rules to explicit status", () => {
  memoryStore.set("agentguard.ruleList", JSON.stringify([{ name: "legacy_rule" }]));

  const loaded = loadList();

  assert.equal(loaded.length, 1);
  assert.equal(loaded[0].id, "legacy_rule");
  assert.equal(loaded[0].name, "legacy_rule");
  assert.equal(loaded[0].status, "unpublished");
});

test("saveList persists normalized rule objects", () => {
  saveList([{ rule_id: "runtime_rule", status: "published" }]);

  const parsed = JSON.parse(memoryStore.get("agentguard.ruleList"));
  assert.deepEqual(parsed, [{ rule_id: "runtime_rule", status: "published", id: "runtime_rule", name: "runtime_rule" }]);
});

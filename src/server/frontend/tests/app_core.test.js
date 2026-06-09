const test = require("node:test");
const assert = require("node:assert/strict");

function createStorage() {
  const store = new Map();
  return {
    getItem(key) {
      return store.has(key) ? store.get(key) : null;
    },
    setItem(key, value) {
      store.set(key, String(value));
    },
    removeItem(key) {
      store.delete(key);
    },
  };
}

function createToastElement() {
  return {
    textContent: "",
    classList: {
      classes: new Set(),
      add(...items) {
        items.forEach((item) => this.classes.add(item));
      },
      remove(...items) {
        items.forEach((item) => this.classes.delete(item));
      },
    },
  };
}

test("shared app core clears cached tool catalog when API base changes", async () => {
  const listeners = {};
  global.window = {
    AgentGuardConfig: { apiBase: "http://127.0.0.1:9000" },
    AgentGuardShell: {
      setToolStatus() {},
      setApiStatus() {},
    },
    addEventListener(name, handler) {
      listeners[name] = handler;
    },
  };
  global.localStorage = createStorage();
  global.localStorage.setItem("agentguard.toolCatalogApiBase", "http://127.0.0.1:38080");
  global.localStorage.setItem("agentguard.toolCatalog", JSON.stringify([{ name: "email.send" }]));
  global.localStorage.setItem("agentguard.toolCatalogSyncedAt", "old-sync");
  global.document = {
    getElementById() {
      return createToastElement();
    },
  };
  global.fetch = async () => ({
    ok: true,
    async json() {
      return [];
    },
  });
  global.setTimeout = (fn) => {
    fn();
    return 1;
  };
  global.clearTimeout = () => {};

  delete require.cache[require.resolve("../static/common/app.js")];
  require("../static/common/app.js");

  assert.equal(global.localStorage.getItem("agentguard.toolCatalog"), null);
  assert.equal(global.localStorage.getItem("agentguard.agentCatalog"), null);
});

test("shared app core formats request errors and updates tool sync metadata", async () => {
  let apiStatus = "";
  let toolStatus = "";
  const listeners = {};

  global.window = {
    AgentGuardConfig: { apiBase: "http://127.0.0.1:38080" },
    AgentGuardText: {
      sidebarApiConnected: "Connected",
      sidebarApiUnavailable: "Unavailable",
      sidebarApiPartial: "Partial",
    },
    AgentGuardShell: {
      setToolStatus(value) {
        toolStatus = value;
      },
      setApiStatus(value) {
        apiStatus = value;
      },
    },
    addEventListener(name, handler) {
      listeners[name] = handler;
    },
  };
  global.localStorage = createStorage();
  global.document = {
    getElementById() {
      return createToastElement();
    },
  };
  global.fetch = async () => ({
    ok: true,
    async json() {
      return [{ owner_agent_id: "agent-a", name: "browser.open", labels: {}, input_params: [] }];
    },
  });
  global.setTimeout = (fn) => {
    fn();
    return 1;
  };
  global.clearTimeout = () => {};

  delete require.cache[require.resolve("../static/common/app.js")];
  require("../static/common/app.js");

  const catalog = await global.window.AgentGuardData.refreshAgentCatalog();
  assert.equal(catalog.length, 1);
  assert.equal(catalog[0].agent_id, "agent-a");
  assert.equal(catalog[0].tool_count, 1);
  assert.equal(apiStatus, "Connected");
  assert.match(toolStatus, /Last synced/);
  assert.equal(
    global.window.AgentGuardApi.formatErrorMessage(new Error("boom"), "fallback"),
    "boom",
  );
});

test("shared app core rejects legacy cached tool catalog entries without agent ownership", async () => {
  const listeners = {};
  global.window = {
    AgentGuardConfig: { apiBase: "http://127.0.0.1:38080" },
    AgentGuardShell: {
      setToolStatus() {},
      setApiStatus() {},
    },
    addEventListener(name, handler) {
      listeners[name] = handler;
    },
  };
  global.localStorage = createStorage();
  global.localStorage.setItem("agentguard.toolCatalog", JSON.stringify([{ name: "email.send" }]));
  global.document = {
    getElementById() {
      return createToastElement();
    },
  };
  global.fetch = async () => ({
    ok: true,
    async json() {
      return [];
    },
  });
  global.setTimeout = (fn) => {
    fn();
    return 1;
  };
  global.clearTimeout = () => {};

  delete require.cache[require.resolve("../static/common/app.js")];
  require("../static/common/app.js");

  assert.deepEqual(global.window.AgentGuardData.loadToolCatalog(), []);
});

test("shared app core exposes agent-aware tool helpers", async () => {
  const listeners = {};
  global.window = {
    AgentGuardConfig: { apiBase: "http://127.0.0.1:38080" },
    AgentGuardShell: {
      setToolStatus() {},
      setApiStatus() {},
    },
    addEventListener(name, handler) {
      listeners[name] = handler;
    },
  };
  global.localStorage = createStorage();
  global.document = {
    getElementById() {
      return createToastElement();
    },
  };
  global.fetch = async () => ({
    ok: true,
    async json() {
      return [];
    },
  });
  global.setTimeout = (fn) => {
    fn();
    return 1;
  };
  global.clearTimeout = () => {};

  delete require.cache[require.resolve("../static/common/app.js")];
  require("../static/common/app.js");

  const catalog = [
    global.window.AgentGuardData.normalizeTool({
      owner_agent_id: "agent-a",
      name: "email.send",
      input_params: ["to"],
    }),
    global.window.AgentGuardData.normalizeTool({
      owner_agent_id: "agent-b",
      name: "email.send",
      input_params: ["subject"],
    }),
  ];

  assert.equal(global.window.AgentGuardData.buildToolKey("agent-a", "email.send"), "agent-a::email.send");
  assert.deepEqual(global.window.AgentGuardData.listAgentIds(catalog), ["agent-a", "agent-b"]);
  assert.equal(global.window.AgentGuardData.groupToolsByAgent(catalog)["agent-b"][0].name, "email.send");
  assert.equal(
    global.window.AgentGuardData.findToolByKey(catalog, "agent-b::email.send")?.input_params?.[0],
    "subject",
  );
});

test("shared app core updates scoped tool labels through the new patch endpoint", async () => {
  const listeners = {};
  let lastFetchUrl = "";
  let lastFetchMethod = "";
  let lastFetchBody = "";

  global.window = {
    AgentGuardConfig: { apiBase: "http://127.0.0.1:38080" },
    AgentGuardShell: {
      getState() {
        return { selectedAgentId: "agent-a" };
      },
      setToolStatus() {},
      setApiStatus() {},
    },
    addEventListener(name, handler) {
      listeners[name] = handler;
    },
  };
  global.localStorage = createStorage();
  global.localStorage.setItem("agentguard.scopedAgentId", "agent-a");
  global.localStorage.setItem(
    "agentguard.scopedToolCatalog",
    JSON.stringify([{
      owner_agent_id: "agent-a",
      name: "email.send",
      labels: {
        boundary: "external",
        sensitivity: "high",
        integrity: "trusted",
        tags: [],
      },
      input_params: ["to"],
    }]),
  );
  global.document = {
    getElementById() {
      return createToastElement();
    },
  };
  global.fetch = async (url, options = {}) => {
    lastFetchUrl = String(url);
    lastFetchMethod = String(options.method || "GET");
    lastFetchBody = String(options.body || "");
    return {
      ok: true,
      async json() {
        return {
          ok: true,
          tool: {
            owner_agent_id: "agent-a",
            name: "email.send",
            labels: {
              boundary: "internal",
              sensitivity: "low",
              integrity: "trusted",
              tags: ["manual"],
            },
            input_params: ["to"],
          },
        };
      },
    };
  };
  global.setTimeout = (fn) => {
    fn();
    return 1;
  };
  global.clearTimeout = () => {};

  delete require.cache[require.resolve("../static/common/app.js")];
  require("../static/common/app.js");

  const updated = await global.window.AgentGuardData.updateToolLabels("agent-a", "email.send", {
    boundary: "internal",
    sensitivity: "low",
    integrity: "trusted",
    tags: ["manual"],
  });

  assert.equal(lastFetchUrl, "/api/agents/agent-a/tools/email.send/labels");
  assert.equal(lastFetchMethod, "PATCH");
  assert.match(lastFetchBody, /"boundary":"internal"/);
  assert.equal(updated.labels.boundary, "internal");
  assert.equal(global.window.AgentGuardData.loadToolCatalog("agent-a")[0].labels.sensitivity, "low");
});

test("shared app core clears scoped caches when selected agent changes", async () => {
  const listeners = {};
  global.window = {
    AgentGuardConfig: { apiBase: "http://127.0.0.1:38080" },
    AgentGuardShell: {
      getState() {
        return { selectedAgentId: "agent-a" };
      },
      setToolStatus() {},
      setApiStatus() {},
    },
    addEventListener(name, handler) {
      listeners[name] = handler;
    },
  };
  global.localStorage = createStorage();
  global.localStorage.setItem("agentguard.scopedAgentId", "agent-a");
  global.localStorage.setItem("agentguard.scopedToolCatalog", JSON.stringify([{ owner_agent_id: "agent-a", name: "email.send" }]));
  global.localStorage.setItem("agentguard.scopedRuleList", JSON.stringify([{ rule_id: "rule-a" }]));
  global.document = {
    getElementById() {
      return createToastElement();
    },
  };
  global.fetch = async () => ({
    ok: true,
    async json() {
      return [];
    },
  });
  global.setTimeout = (fn) => {
    fn();
    return 1;
  };
  global.clearTimeout = () => {};

  delete require.cache[require.resolve("../static/common/app.js")];
  require("../static/common/app.js");

  listeners["agentguard:selected-agent-change"]?.({ detail: { agentId: "agent-b" } });

  assert.equal(global.localStorage.getItem("agentguard.scopedAgentId"), null);
  assert.equal(global.localStorage.getItem("agentguard.scopedToolCatalog"), null);
  assert.equal(global.localStorage.getItem("agentguard.scopedRuleList"), null);
});

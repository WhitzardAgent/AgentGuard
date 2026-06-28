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

test("shared app core times out hanging requests instead of waiting forever", async () => {
  const listeners = {};
  let timeoutCallback = null;

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
  global.fetch = async (_url, options) => new Promise((_resolve, reject) => {
    if (options?.signal) {
      options.signal.addEventListener("abort", () => {
        reject(new Error("aborted"));
      }, { once: true });
    }
  });
  global.setTimeout = (fn) => {
    timeoutCallback = fn;
    return 1;
  };
  global.clearTimeout = () => {};

  delete require.cache[require.resolve("../static/common/app.js")];
  require("../static/common/app.js");

  const pending = global.window.AgentGuardApi.fetchJson("/api/tools");
  assert.equal(typeof timeoutCallback, "function");
  timeoutCallback();
  await assert.rejects(
    pending,
    /Request timed out after 6000ms while fetching \/api\/tools\./,
  );
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

test("shared app core builds multi-plugin config while preserving unrelated phase data", async () => {
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

  const available = [
    { name: "rule_based_plugin", description: "", event_types: [], phases: ["tool_before"] },
    { name: "tool_invoke", description: "", event_types: ["tool_invoke"], phases: ["tool_before"] },
    { name: "jailbreak_check", description: "", event_types: ["llm_input"], phases: ["llm_before"] },
  ];
  const existingConfig = {
    phases: {
      llm_before: {
        client: ["client_llm_guard"],
        server: ["jailbreak_check"],
      },
      tool_before: {
        client: ["client_tool_guard"],
        server: ["custom_hidden_plugin", "tool_invoke", "rule_based_plugin"],
      },
    },
  };

  const config = global.window.AgentGuardData.buildPluginConfig(
    [available[2]],
    available,
    existingConfig,
  );

  assert.deepEqual(config, {
    phases: {
      llm_before: {
        client: ["client_llm_guard"],
        server: ["jailbreak_check"],
      },
      tool_before: {
        client: ["client_tool_guard"],
        server: ["custom_hidden_plugin"],
      },
    },
  });

  const clientConfig = global.window.AgentGuardData.buildPluginConfig(
    [{ name: "tool_result", description: "", event_types: ["tool_result"], phases: ["tool_after"] }],
    [{ name: "tool_result", description: "", event_types: ["tool_result"], phases: ["tool_after"] }],
    existingConfig,
    "client",
  );

  assert.deepEqual(clientConfig, {
    phases: {
      llm_before: {
        client: ["client_llm_guard"],
        server: ["jailbreak_check"],
      },
      tool_before: {
        client: ["client_tool_guard"],
        server: ["custom_hidden_plugin", "tool_invoke", "rule_based_plugin"],
      },
      tool_after: {
        client: ["tool_result"],
        server: [],
      },
    },
  });
});

test("shared app core derives active plugin names and primary plugin from config", async () => {
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

  const configResponse = {
    agent_id: "agent-a",
    plugin_config: {
      phases: {
        llm_before: { client: ["client_prompt_guard"], server: ["jailbreak_check"] },
        tool_before: { client: [], server: ["tool_invoke", "rule_based_plugin"] },
        tool_after: { client: ["client_tool_result_guard"], server: [{ name: "tool_result" }] },
      },
    },
  };

  assert.deepEqual(
    global.window.AgentGuardData.selectedPluginsFromConfig(configResponse),
    ["jailbreak_check", "tool_invoke", "rule_based_plugin", "tool_result"],
  );
  assert.deepEqual(
    global.window.AgentGuardData.selectedPluginsFromConfig(configResponse, "client"),
    ["client_prompt_guard", "client_tool_result_guard"],
  );
  assert.deepEqual(
    global.window.AgentGuardData.activePluginsFromConfig(configResponse),
    [
      "jailbreak_check",
      "tool_invoke",
      "rule_based_plugin",
      "tool_result",
      "client_prompt_guard",
      "client_tool_result_guard",
    ],
  );
  assert.deepEqual(
    global.window.AgentGuardData.collapsePluginSelection(
      global.window.AgentGuardData.selectedPluginsFromConfig(configResponse),
    ),
    ["jailbreak_check", "tool_invoke", "rule_based_plugin", "tool_result"],
  );
  assert.deepEqual(
    global.window.AgentGuardData.expandPluginSelection(["rule_based_plugin", "tool_result"]),
    ["rule_based_plugin", "tool_result"],
  );
  assert.equal(
    global.window.AgentGuardData.selectedPluginFromConfig(configResponse),
    "rule_based_plugin",
  );
});

test("shared app core preserves plugin config source from the agent config endpoint", async () => {
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
  global.document = {
    getElementById() {
      return createToastElement();
    },
  };
  global.fetch = async () => ({
    ok: true,
    async json() {
      return {
        agent_id: "agent-a",
        plugin_config: {
          phases: {
            tool_before: { client: [], server: ["rule_based_plugin"] },
          },
        },
        config_source: "server_default",
      };
    },
  });
  global.setTimeout = (fn) => {
    fn();
    return 1;
  };
  global.clearTimeout = () => {};

  delete require.cache[require.resolve("../static/common/app.js")];
  require("../static/common/app.js");

  const config = await global.window.AgentGuardData.getAgentPluginConfig("agent-a");

  assert.equal(config.agent_id, "agent-a");
  assert.equal(config.config_source, "server_default");
  assert.deepEqual(config.plugin_config?.phases?.tool_before?.server, ["rule_based_plugin"]);
});

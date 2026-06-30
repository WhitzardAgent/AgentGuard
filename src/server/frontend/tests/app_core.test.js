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

test("shared app core sends skill LLM concurrency in detect requests", async () => {
  const listeners = {};
  let lastFetchUrl = "";
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
  global.localStorage.setItem("agentguard.scopedSkillList", JSON.stringify([{
    owner_agent_id: "agent-a",
    skill_unique_id: "skill-1",
    name: "demo skill",
    skill_resource: { files: [] },
  }]));
  global.document = {
    getElementById() {
      return createToastElement();
    },
  };
  global.fetch = async (url, options = {}) => {
    lastFetchUrl = String(url);
    lastFetchBody = String(options.body || "");
    return {
      ok: true,
      async json() {
        return {
          ok: true,
          results: [{
            skill_unique_id: "skill-1",
            detect_result: { label: "benign", metadata: {} },
          }],
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

  await global.window.AgentGuardData.detectSkills("agent-a", ["skill-1"], {
    useLlm: true,
    llmConcurrency: 8,
  });

  assert.equal(lastFetchUrl, "/api/agents/agent-a/skills/detect");
  assert.deepEqual(JSON.parse(lastFetchBody), {
    skill_unique_ids: ["skill-1"],
    use_llm: true,
    llm_config: null,
    llm_concurrency: 8,
  });
});

test("shared app core defaults skill detect LLM concurrency to one and uses 80s timeout", async () => {
  const listeners = {};
  let lastFetchBody = "";
  let requestTimeoutMs = 0;

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
  global.localStorage.setItem("agentguard.scopedSkillList", JSON.stringify([{
    owner_agent_id: "agent-a",
    skill_unique_id: "skill-1",
    name: "demo skill",
    skill_resource: { files: [] },
  }]));
  global.document = {
    getElementById() {
      return createToastElement();
    },
  };
  global.fetch = async (_url, options = {}) => {
    lastFetchBody = String(options.body || "");
    return {
      ok: true,
      async json() {
        return {
          ok: true,
          results: [{
            skill_unique_id: "skill-1",
            detect_result: { label: "benign", metadata: {} },
          }],
        };
      },
    };
  };
  global.setTimeout = (_fn, ms) => {
    requestTimeoutMs = ms;
    return 1;
  };
  global.clearTimeout = () => {};

  delete require.cache[require.resolve("../static/common/app.js")];
  require("../static/common/app.js");

  await global.window.AgentGuardData.detectSkills("agent-a", ["skill-1"], {
    useLlm: true,
  });

  assert.deepEqual(JSON.parse(lastFetchBody), {
    skill_unique_ids: ["skill-1"],
    use_llm: true,
    llm_config: null,
    llm_concurrency: 1,
  });
  assert.equal(requestTimeoutMs, 80000);
});

test("shared app core normalizes omitted skill file reasons", async () => {
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

  const skill = global.window.AgentGuardData.normalizeSkill({
    owner_agent_id: "agent-a",
    skill_unique_id: "skill-1",
    name: "demo skill",
    skill_resource: {
      files: [
        { relative_path: "asset.bin", binary: true, content_omitted: true, reason: "binary" },
        { relative_path: "large.md", content_omitted: true, reason: "too_large" },
      ],
    },
  });

  assert.equal(skill.skill_resource.files[0].content_omitted, "binary");
  assert.equal(skill.skill_resource.files[1].content_omitted, "too_large");
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

test("skills page clears stale cached detection after successful refresh without server result", async () => {
  const elements = new Map();
  let persistedSkills = null;
  let persistedAgentId = "";

  function getElement(id) {
    if (!elements.has(id)) {
      elements.set(id, {
        id,
        textContent: "",
        innerHTML: "",
        disabled: false,
        checked: false,
        dataset: {},
        classList: {
          add() {},
          remove() {},
          toggle() {},
        },
        addEventListener() {},
      });
    }
    return elements.get(id);
  }

  const cachedSkill = {
    owner_agent_id: "agent-a",
    agent_id: "agent-a",
    skill_unique_id: "skill-1",
    name: "cached skill",
    description: "Cached copy",
    source_framework: "openclaw",
    file_count: 1,
    total_size: 12,
    detect_result: {
      label: "malicious",
      reason: "Old cached finding",
      metadata: { rule_based: { findings: [] } },
    },
    skill_resource: {
      files: [{ relative_path: "SKILL.md", kind: "skill", size: 12 }],
      skill_markdown: { relative_path: "SKILL.md", content: "old" },
    },
  };
  const freshSkill = {
    ...cachedSkill,
    description: "Fresh copy",
    total_size: 13,
    detect_result: null,
    skill_resource: {
      files: [{ relative_path: "SKILL.md", kind: "skill", size: 13 }],
      skill_markdown: { relative_path: "SKILL.md", content: "fresh" },
    },
  };

  global.window = {
    AgentGuardData: {
      loadSkillList() {
        return [cachedSkill];
      },
      async refreshSkillList() {
        return [freshSkill];
      },
      persistSkillList(skills, agentId) {
        persistedSkills = skills;
        persistedAgentId = agentId;
      },
      getLastSkillSyncTime() {
        return "2026-06-30T00:00:00.000Z";
      },
    },
    AgentGuardShell: {
      getState() {
        return { selectedAgentId: "agent-a" };
      },
      setPageContext() {},
    },
    AgentGuardApi: {
      formatErrorMessage(error, fallback) {
        return error?.message || fallback;
      },
    },
    AgentGuardI18n: {
      t(value) {
        return value;
      },
      getLanguage() {
        return "en";
      },
    },
    AgentGuardUI: {
      showToast() {},
    },
    addEventListener() {},
  };
  global.document = {
    getElementById: getElement,
  };
  global.Element = function Element() {};
  global.HTMLInputElement = function HTMLInputElement() {};

  delete require.cache[require.resolve("../static/pages/skills/skills.js")];
  require("../static/pages/skills/skills.js");

  await new Promise((resolve) => setImmediate(resolve));

  assert.equal(persistedAgentId, "agent-a");
  assert.equal(persistedSkills?.[0]?.detect_result, null);
  assert.match(getElement("skill-list").innerHTML, /not detected/);
});

test("skills page does not render cached detection before server refresh completes", async () => {
  const elements = new Map();
  let resolveRefresh;

  function getElement(id) {
    if (!elements.has(id)) {
      elements.set(id, {
        id,
        textContent: "",
        innerHTML: "",
        disabled: false,
        checked: false,
        dataset: {},
        classList: {
          add() {},
          remove() {},
          toggle() {},
        },
        addEventListener() {},
      });
    }
    return elements.get(id);
  }

  global.window = {
    AgentGuardData: {
      loadSkillList() {
        return [{
          owner_agent_id: "agent-a",
          agent_id: "agent-a",
          skill_unique_id: "skill-1",
          name: "cached skill",
          description: "Cached copy",
          source_framework: "openclaw",
          file_count: 1,
          total_size: 12,
          detect_result: {
            label: "malicious",
            reason: "Old cached finding",
          },
          skill_resource: {
            files: [{ relative_path: "SKILL.md", kind: "skill", size: 12 }],
          },
        }];
      },
      refreshSkillList() {
        return new Promise((resolve) => {
          resolveRefresh = resolve;
        });
      },
      persistSkillList() {},
      getLastSkillSyncTime() {
        return "";
      },
    },
    AgentGuardShell: {
      getState() {
        return { selectedAgentId: "agent-a" };
      },
      setPageContext() {},
    },
    AgentGuardApi: {
      formatErrorMessage(error, fallback) {
        return error?.message || fallback;
      },
    },
    AgentGuardI18n: {
      t(value) {
        return value;
      },
      getLanguage() {
        return "en";
      },
    },
    AgentGuardUI: {
      showToast() {},
    },
    addEventListener() {},
  };
  global.document = {
    getElementById: getElement,
  };
  global.Element = function Element() {};
  global.HTMLInputElement = function HTMLInputElement() {};

  delete require.cache[require.resolve("../static/pages/skills/skills.js")];
  require("../static/pages/skills/skills.js");

  assert.match(getElement("skill-list").innerHTML, /not detected/);
  assert.doesNotMatch(getElement("skill-list").innerHTML, /malicious/);

  resolveRefresh([]);
  await new Promise((resolve) => setImmediate(resolve));
});

test("skills page keeps LLM review in waiting state after rule results finish", async () => {
  const elements = new Map();
  const listeners = {};
  const llmResolvers = [];
  let llmCalls = 0;

  function getElement(id) {
    if (!elements.has(id)) {
      elements.set(id, {
        id,
        textContent: "",
        innerHTML: "",
        disabled: false,
        checked: false,
        value: id === "skill-llm-concurrency" ? "1" : "",
        dataset: {},
        classList: {
          add() {},
          remove() {},
          toggle() {},
        },
        addEventListener(name, handler) {
          this.listeners = this.listeners || {};
          this.listeners[name] = handler;
        },
      });
    }
    return elements.get(id);
  }

  const skills = ["skill-1", "skill-2"].map((id) => ({
    owner_agent_id: "agent-a",
    agent_id: "agent-a",
    skill_unique_id: id,
    name: id,
    description: "demo",
    source_framework: "openclaw",
    file_count: 1,
    total_size: 12,
    detect_result: null,
    skill_resource: {
      files: [{ relative_path: "SKILL.md", kind: "skill", size: 12, content: "# demo" }],
    },
  }));

  function detectedSkill(id, withLlm = false) {
    const detectResult = {
      label: "malicious",
      reason: "rule result",
      metadata: {
        rule_based: {
          label: "malicious",
          finding_count: 1,
          parsed_summary: {
            signals: [{
              signal_id: "NET001_NETWORK_CALL",
              kind: "network",
              file_path: "SKILL.md",
              line_number: 1,
              evidence: "network call",
              severity: 1,
              confidence: 1,
            }],
          },
        },
        ...(withLlm ? {
          llm_review: {
            label: "malicious",
            reason: "llm result",
          },
        } : {}),
      },
    };
    const base = skills.find((skill) => skill.skill_unique_id === id);
    return {
      ...base,
      detect_result: detectResult,
    };
  }

  global.window = {
    AgentGuardData: {
      loadSkillList() {
        return skills;
      },
      async refreshSkillList() {
        return skills;
      },
      persistSkillList() {},
      async detectSkills(_agentId, ids, options = {}) {
        const id = ids[0];
        if (options.useLlm === true) {
          llmCalls += 1;
          return new Promise((resolve) => {
            llmResolvers.push(() => resolve({
              ok: true,
              results: [{
                skill_unique_id: id,
                detect_result: detectedSkill(id, true).detect_result,
                skill: detectedSkill(id, true),
              }],
            }));
          });
        }
        return {
          ok: true,
          results: [{
            skill_unique_id: id,
            detect_result: detectedSkill(id).detect_result,
            skill: detectedSkill(id),
          }],
        };
      },
      getLastSkillSyncTime() {
        return "";
      },
    },
    AgentGuardShell: {
      getState() {
        return { selectedAgentId: "agent-a" };
      },
      setPageContext() {},
    },
    AgentGuardApi: {
      formatErrorMessage(error, fallback) {
        return error?.message || fallback;
      },
    },
    AgentGuardI18n: {
      t(value) {
        return value;
      },
      getLanguage() {
        return "en";
      },
    },
    AgentGuardUI: {
      showToast() {},
    },
    addEventListener(name, handler) {
      listeners[name] = handler;
    },
    setInterval() {
      return 1;
    },
    clearInterval() {},
  };
  global.document = {
    getElementById: getElement,
  };
  global.Element = function Element() {};
  global.HTMLInputElement = function HTMLInputElement() {};

  delete require.cache[require.resolve("../static/pages/skills/skills.js")];
  require("../static/pages/skills/skills.js");

  await new Promise((resolve) => setImmediate(resolve));

  getElement("skill-use-llm").checked = true;
  getElement("select-all-skills").listeners.click();
  const detectPromise = getElement("detect-selected-skills").listeners.click();

  for (let i = 0; i < 10 && llmCalls === 0; i += 1) {
    await new Promise((resolve) => setImmediate(resolve));
  }

  const html = getElement("skill-list").innerHTML;
  assert.doesNotMatch(html, /Rule-based result only\. LLM review was not requested\./);
  assert.match(html, /Waiting for LLM response|Waiting for an LLM review slot/);

  llmResolvers.shift()?.();
  for (let i = 0; i < 10 && llmCalls < 2; i += 1) {
    await new Promise((resolve) => setImmediate(resolve));
  }
  llmResolvers.shift()?.();
  await detectPromise;
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

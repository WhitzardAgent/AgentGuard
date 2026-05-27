const test = require("node:test");
const assert = require("node:assert/strict");

const fetchCalls = [];
let fetchQueue = [];
let savedRuleLists = [];
let pathState = { path: "", pathSlots: [], finished: false };
let conditionState = { items: [], symbolToolMap: {}, expression: "" };

function createElement() {
  return {
    value: "",
    textContent: "",
    innerHTML: "",
    disabled: false,
    hidden: false,
    options: [],
    classList: {
      add() {},
      remove() {},
      toggle() {},
    },
    querySelector() {
      return {
        src: "",
      };
    },
    querySelectorAll() {
      return [];
    },
    addEventListener() {},
    appendChild(child) {
      if (child && Object.prototype.hasOwnProperty.call(child, "value")) {
        this.options.push(child);
      }
    },
    setAttribute() {},
  };
}

const elements = new Map();

function getElement(id) {
  if (!elements.has(id)) {
    elements.set(id, createElement());
  }
  return elements.get(id);
}

function resetFormState(rule = {}) {
  getElement("rule-name-input").value = rule.name || "";
  getElement("rule-action-input").value = rule.action || "";
  getElement("rule-degrade-target-input").value = "";
  getElement("rule-description-input").value = rule.description || "";
  getElement("rule-on-subtype-input").value = "";
  getElement("rule-on-input").value = "";
  getElement("rule-severity-input").value = rule.severity || "";
  getElement("rule-category-input").value = rule.category || "";
  getElement("rule-reason-input").value = rule.reason || "";
  pathState = {
    path: String(rule.path || ""),
    pathSlots: String(rule.path || "")
      .split("->")
      .map((segment) => segment.trim())
      .filter(Boolean)
      .map((segment) => ({ label: segment, value: segment })),
    finished: Boolean(rule.path),
  };
  conditionState = {
    items: Array.isArray(rule.conditionItems) ? rule.conditionItems : [],
    symbolToolMap: {},
    expression: String(rule.condition || ""),
  };
}

function queueFetchResponses(...items) {
  fetchQueue.push(...items);
}

global.window = {
  AgentGuardRuleStorage: {
    saveList(list) {
      savedRuleLists.push(list);
    },
    loadList() {
      return [];
    },
  },
  AgentGuardPathBuilder: {
    normalizeValue(value) {
      return {
        path: String(value?.path || ""),
        pathSlots: String(value?.path || "")
          .split("->")
          .map((segment) => segment.trim())
          .filter(Boolean)
          .map((segment) => ({ label: segment, value: segment })),
        finished: true,
      };
    },
    createPathBuilder() {
      return {
        getValue() {
          return pathState;
        },
        setValue() {},
        validate() {
          return { ok: true };
        },
        clear() {
          pathState = { path: "", pathSlots: [], finished: false };
        },
        modify() {},
        appendSegment() {},
        finish() {
          return { ok: true };
        },
      };
    },
  },
  AgentGuardConditionBuilder: {
    normalizeItems(value) {
      return {
        items: Array.isArray(value?.items)
          ? value.items.map((item) => ({
            ...item,
            expression: item.sourceType === "context"
              ? `${item.contextPath} ${item.operator === "contains" ? "CONTAINS" : item.operator} "${item.value}"`
              : `${item.symbol}.${item.feature === "name" ? "name" : item.feature.replace(/^label\./, "")} ${item.operator === "contains" ? "CONTAINS" : item.operator} "${item.value}"`,
          }))
          : [],
        symbolToolMap: {},
      };
    },
    createConditionBuilder() {
      return {
        getValue() {
          return conditionState;
        },
        setLocked() {},
        setAllowedSourceTypes() {},
        setCurrentCallToolKey() {},
        setPathSymbols() {},
        setValue() {},
        validate() {
          return { ok: true };
        },
        clear() {
          conditionState = { items: [], symbolToolMap: {}, expression: "" };
        },
      };
    },
  },
  AgentGuardRuleDSL: {
    isValidOnClause(value) {
      return String(value || "").startsWith("tool_call");
    },
    normalizeOnClause(rule) {
      return String(rule?.onClause || "").trim();
    },
    normalizeDegradeTarget(rule) {
      return String(rule?.degradeTarget || "").trim();
    },
    serializeRule(rule) {
      const lines = [
        `RULE: ${rule.name}`,
      ];
      if (rule.onClause) {
        lines.push(`ON: ${rule.onClause}`);
      }
      lines.push(`TRACE: ${rule.path.split("->").map((segment) => segment.trim()).filter(Boolean).join(" -> ")}`);
      lines.push('CONDITION: A.name == "email.send"');
      if (rule.action === "DEGRADE") {
        lines.push(`POLICY: DEGRADE TO "${rule.degradeTarget}"`);
      } else {
        lines.push(`POLICY: ${rule.action}`);
      }
      return lines.join("\n");
    },
    serializeRules() {
      return "";
    },
  },
  AgentGuardUI: {
    showToast() {},
  },
  AgentGuardData: {
    loadToolCatalog() {
      return [];
    },
    findToolByKey() {
      return null;
    },
  },
  AgentGuardApi: {
    async fetchJson(url, options = {}) {
      fetchCalls.push({ url, options });
      const next = fetchQueue.shift() || { ok: true, payload: [] };
      if (next.ok === false) {
        throw new Error(next.error || "Request failed.");
      }
      return next.payload;
    },
  },
  AgentGuardShell: {
    setPageContext() {},
    getState() {
      return { selectedAgentId: "agent-alpha" };
    },
  },
};

global.fetch = async () => ({
  ok: true,
  async json() {
    return [];
  },
});

global.document = {
  getElementById(id) {
    return getElement(id);
  },
  querySelector() {
    return createElement();
  },
  querySelectorAll() {
    return [];
  },
  createElement() {
    return createElement();
  },
};

require("../static/common/tool-catalog.js");
require("../static/common/ui-helpers.js");
require("../static/pages/rules/rule-parser.js");
require("../static/pages/rules/rules.js");

const { checkRuleSource, checkCurrentRule, disableRule, generateRule, publishRule } = global.window.AgentGuardRules;

function sampleRule(overrides = {}) {
  return {
    name: "review_external_email",
    path: "A->B",
    action: "HUMAN_CHECK",
    condition: 'A.name == "email.send"',
    conditionItems: [
      {
        connector: "",
        sourceType: "trace",
        openParen: "",
        closeParen: "",
        symbol: "A",
        feature: "name",
        syntaxField: "",
        operator: "==",
        value: "email.send",
      },
    ],
    ...overrides,
  };
}

test.beforeEach(() => {
  fetchCalls.length = 0;
  fetchQueue = [];
  savedRuleLists = [];
  resetFormState();
});

test("checkRuleSource posts DSL to the frontend check endpoint", async () => {
  queueFetchResponses({
    payload: {
      ok: true,
      rule_count: 1,
      source_file: "",
      errors: [],
      warnings: [],
      hints: [],
    },
  });

  const report = await checkRuleSource("RULE: test\nTRACE: A -> B\nCONDITION: A.name == \"email.send\"\nPOLICY: DENY");

  assert.equal(report.ok, true);
  assert.equal(fetchCalls.length, 1);
  assert.equal(fetchCalls[0].url, "/api/rules/check");
  assert.equal(JSON.parse(fetchCalls[0].options.body).source.startsWith("RULE: test"), true);
});

test("publishRule checks with backend before creating an agent-scoped runtime rule", async () => {
  queueFetchResponses(
    {
      payload: {
        ok: true,
        rule_count: 1,
        source_file: "",
        errors: [],
        warnings: [],
        hints: [],
      },
    },
    {
      payload: {
        ok: true,
        created: true,
        pack_id: "agent::agent-alpha",
        rule_id: "review_external_email",
      },
    },
    {
      payload: [],
    },
  );

  const published = await publishRule(sampleRule());

  assert.equal(published, true);
  assert.deepEqual(fetchCalls.map((item) => item.url), [
    "/api/rules/check",
    "/api/agents/agent-alpha/rules",
    "/api/agents/agent-alpha/rules",
  ]);
});

test("publishRule stops when backend check fails", async () => {
  queueFetchResponses({
    payload: {
      ok: false,
      rule_count: 0,
      source_file: "",
      errors: [{ message: "Parse error: expected POLICY" }],
      warnings: [],
      hints: [],
    },
  });

  const published = await publishRule(sampleRule());

  assert.equal(published, false);
  assert.deepEqual(fetchCalls.map((item) => item.url), ["/api/rules/check"]);
});

test("checkCurrentRule only validates the draft without publishing or saving", async () => {
  resetFormState(sampleRule({ condition: 'A.name == "email.send"' }));
  queueFetchResponses({
    payload: {
      ok: true,
      rule_count: 1,
      source_file: "",
      errors: [],
      warnings: [],
      hints: [],
    },
  });

  const checked = await checkCurrentRule();

  assert.equal(checked, true);
  assert.deepEqual(fetchCalls.map((item) => item.url), ["/api/rules/check"]);
  assert.equal(savedRuleLists.length, 0);
});

test("generateRule validates first and saves an unpublished rule when check passes", async () => {
  resetFormState(sampleRule({ condition: 'A.name == "email.send"' }));
  queueFetchResponses({
    payload: {
      ok: true,
      rule_count: 1,
      source_file: "",
      errors: [],
      warnings: [],
      hints: [],
    },
  });

  const generated = await generateRule();

  assert.equal(generated, true);
  assert.deepEqual(fetchCalls.map((item) => item.url), ["/api/rules/check"]);
  assert.equal(savedRuleLists.length, 1);
  assert.equal(savedRuleLists[0].length, 1);
  assert.equal(savedRuleLists[0][0].name, "review_external_email");
  assert.equal(savedRuleLists[0][0].status, "unpublished");
});

test("disableRule deletes the published rule through the agent-scoped endpoint", async () => {
  queueFetchResponses(
    {
      payload: {
        ok: true,
        pack_id: "agent::agent-alpha",
        rule_id: "review_external_email",
      },
    },
    {
      payload: [],
    },
  );

  const disabled = await disableRule({
    ...sampleRule({
      source: 'RULE: review_external_email\nTRACE: A -> B\nCONDITION: A.name == "email.send"\nPOLICY: HUMAN_CHECK',
    }),
    status: "published",
    rule_id: "review_external_email",
    id: "review_external_email",
    pack_id: "agent::agent-alpha",
    user_managed: true,
  });

  assert.equal(disabled, true);
  assert.deepEqual(fetchCalls.map((item) => item.url), [
    "/api/agents/agent-alpha/rules/review_external_email",
    "/api/agents/agent-alpha/rules",
  ]);
});

const test = require("node:test");
const assert = require("node:assert/strict");

function createClassList() {
  return {
    classes: new Set(),
    add(...items) {
      items.forEach((item) => this.classes.add(item));
    },
    remove(...items) {
      items.forEach((item) => this.classes.delete(item));
    },
    toggle(item, force) {
      if (force === undefined) {
        if (this.classes.has(item)) {
          this.classes.delete(item);
        } else {
          this.classes.add(item);
        }
        return;
      }
      if (force) {
        this.classes.add(item);
      } else {
        this.classes.delete(item);
      }
    },
  };
}

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

function bootShell(selectedAgentId = "", options = {}) {
  const elements = {};
  const agentRequired = [
    { hidden: false },
    { hidden: false },
  ];
  const ruleBasedRequired = [
    { hidden: false },
    { hidden: false },
  ];

  function getElement(id) {
    if (!elements[id]) {
      elements[id] = {
        id,
        textContent: options.initialTexts?.[id] || "",
        hidden: false,
        classList: createClassList(),
        setAttribute() {},
        addEventListener() {},
      };
    }
    return elements[id];
  }

  global.localStorage = createStorage();
  if (selectedAgentId) {
    global.localStorage.setItem("agentguard.selectedAgentId", selectedAgentId);
  }

  global.document = {
    body: {
      classList: createClassList(),
    },
    getElementById(id) {
      return getElement(id);
    },
    querySelectorAll(selector) {
      if (selector === "[data-agent-required='true']") {
        return agentRequired;
      }
      if (selector === "[data-rule-based-required='true']") {
        return ruleBasedRequired;
      }
      return [];
    },
    addEventListener() {},
  };

  global.window = {
    localStorage: global.localStorage,
    AgentGuardI18n: {
      t(value) {
        return options.translate ? options.translate(value) : String(value || "");
      },
    },
    dispatchEvent() {},
  };

  delete require.cache[require.resolve("../static/common/page-shell.js")];
  require("../static/common/page-shell.js");

  return {
    elements,
    agentRequired,
    ruleBasedRequired,
    shell: global.window.AgentGuardShell,
  };
}

test("sidebar hides agent-required links until an agent is selected", () => {
  const {
    agentRequired,
    ruleBasedRequired,
    elements,
    shell,
  } = bootShell("");

  assert.equal(elements["sidebar-current-user"].textContent, "Current User");
  assert.equal(agentRequired.every((item) => item.hidden), true);
  assert.equal(ruleBasedRequired.every((item) => item.hidden), true);
  assert.equal(elements["sidebar-agent-panel"].hidden, true);
  assert.equal(elements["sidebar-selected-agent-wrap"].hidden, true);
  assert.equal(elements["sidebar-selected-agent"].textContent, "");

  shell.setSelectedAgent("agent-a");

  assert.equal(agentRequired.every((item) => item.hidden === false), true);
  assert.equal(ruleBasedRequired.every((item) => item.hidden), true);
  assert.equal(elements["sidebar-agent-panel"].hidden, false);
  assert.equal(elements["sidebar-selected-agent-wrap"].hidden, false);
  assert.equal(elements["sidebar-selected-agent"].textContent, "agent-a");

  shell.setSelectedPlugin("rule_based_plugin");

  assert.equal(agentRequired.every((item) => item.hidden === false), true);
  assert.equal(ruleBasedRequired.every((item) => item.hidden === false), true);
});


test("page shell translates page context and default user label through i18n", () => {
  const { elements, shell } = bootShell("", {
    translate(value) {
      const dictionary = {
        "Current User": "当前用户",
        "Rule Studio": "规则工作台",
        "Build DSL rules from structured inputs and manage rule publication.": "通过结构化输入构建 DSL 规则，并管理规则发布。",
      };
      return dictionary[String(value)] || String(value || "");
    },
  });

  assert.equal(elements["sidebar-current-user"].textContent, "当前用户");

  shell.setPageContext({
    title: "Rule Studio",
    description: "Build DSL rules from structured inputs and manage rule publication.",
  });

  assert.equal(elements["sidebar-page-title"].textContent, "规则工作台");
  assert.equal(elements["sidebar-page-description"].textContent, "通过结构化输入构建 DSL 规则，并管理规则发布。");
});


test("page shell boots from server-rendered template context before runtime i18n", () => {
  const localizedDescription = "从结构化输入构建规则、预览 DSL 输出，并管理未发布与已发布状态。";
  const { elements, shell } = bootShell("", {
    initialTexts: {
      "sidebar-current-user": "当前用户",
      "agentguard-page-context-title": "规则构建器",
      "agentguard-page-context-description": localizedDescription,
    },
  });

  assert.equal(elements["sidebar-current-user"].textContent, "当前用户");
  assert.equal(elements["sidebar-page-title"].textContent, "规则构建器");
  assert.equal(elements["sidebar-page-description"].textContent, localizedDescription);
  assert.equal(shell.getState().pageTitle, "规则构建器");
  assert.equal(shell.getState().pageDescription, localizedDescription);
});


test("page shell exposes localized page copy from server-rendered templates", () => {
  const { shell } = bootShell("", {
    initialTexts: {
      "agentguard-copy-select-tool": "选择一个工具",
      "agentguard-copy-labels-to-write-for": "{tool} 待写入的标签：",
    },
  });

  assert.equal(shell.getPageCopy("select-tool", "Select a tool"), "选择一个工具");
  assert.equal(
    shell.getPageCopy("labels-to-write-for", "Labels to write for {tool}:", { tool: "HTTP Request" }),
    "HTTP Request 待写入的标签：",
  );
});

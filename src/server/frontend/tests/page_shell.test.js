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

function bootShell(selectedAgentId = "") {
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
        textContent: "",
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

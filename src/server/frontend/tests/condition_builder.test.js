const test = require("node:test");
const assert = require("node:assert/strict");

const catalog = [
  {
    owner_agent_id: "agent-a",
    name: "email.send",
    tool_key: "agent-a::email.send",
    labels: {},
    input_params: ["to", "body"],
  },
  {
    owner_agent_id: "agent-b",
    name: "email.send",
    tool_key: "agent-b::email.send",
    labels: {},
    input_params: ["subject", "markdown"],
  },
  {
    owner_agent_id: "agent-c",
    name: "http.post",
    tool_key: "agent-c::http.post",
    labels: {},
    input_params: ["url", "body"],
  },
];

function createElement(tagName = "div") {
  let innerHTML = "";
  const element = {
    tagName: String(tagName).toUpperCase(),
    value: "",
    textContent: "",
    disabled: false,
    checked: false,
    className: "",
    placeholder: "",
    attributes: {},
    options: [],
    children: [],
    classList: {
      add() {},
      remove() {},
      toggle() {},
    },
    _listeners: {},
    appendChild(child) {
      this.children.push(child);
      if (this.tagName === "SELECT" && child && Object.prototype.hasOwnProperty.call(child, "value")) {
        this.options.push(child);
      }
      return child;
    },
    addEventListener(type, handler) {
      this._listeners[type] = handler;
    },
    dispatchEvent(event) {
      const type = typeof event === "string" ? event : event?.type;
      const handler = this._listeners[type];
      if (handler) {
        handler({ target: this, type });
      }
    },
    setAttribute(name, value) {
      this.attributes[name] = value;
    },
    querySelectorAll() {
      return [];
    },
    closest() {
      return null;
    },
  };

  Object.defineProperty(element, "innerHTML", {
    configurable: true,
    enumerable: true,
    get() {
      return innerHTML;
    },
    set(value) {
      innerHTML = String(value || "");
      element.children = [];
      element.options = [];
    },
  });

  return element;
}

function findElement(root, predicate) {
  if (!root || typeof predicate !== "function") {
    return null;
  }
  if (predicate(root)) {
    return root;
  }
  for (const child of root.children || []) {
    const match = findElement(child, predicate);
    if (match) {
      return match;
    }
  }
  return null;
}

function collectElements(root, predicate, acc = []) {
  if (!root || typeof predicate !== "function") {
    return acc;
  }
  if (predicate(root)) {
    acc.push(root);
  }
  for (const child of root.children || []) {
    collectElements(child, predicate, acc);
  }
  return acc;
}

function buttonByText(root, text, index = 0) {
  return collectElements(root, (element) => element.tagName === "BUTTON" && element.textContent === text)[index] || null;
}

function buttonByLabel(root, label, index = 0) {
  return collectElements(root, (element) => (
    element.tagName === "BUTTON"
    && (element.attributes["aria-label"] === label || element.attributes.title === label)
  ))[index] || null;
}

function selectWithOption(root, optionValue, index = 0) {
  return collectElements(root, (element) => (
    element.tagName === "SELECT"
    && element.options.some((option) => option.value === optionValue)
  ))[index] || null;
}

function byClass(root, className, index = 0) {
  return collectElements(root, (element) => String(element.className || "").split(/\s+/).includes(className))[index] || null;
}

global.document = {
  createElement(tagName) {
    return createElement(tagName);
  },
};

global.window = {
  AgentGuardData: {
    loadToolCatalog() {
      return catalog;
    },
    findToolByKey(items, toolKey) {
      return items.find((tool) => tool.tool_key === toolKey) || null;
    },
  },
};

require("../static/common/tool-catalog.js");
require("../static/pages/rules/condition-builder.js");

const { createConditionBuilder, itemsToTree, normalizeItems } = global.window.AgentGuardConditionBuilder;

test("condition builder keeps selectedToolKey while emitting DSL-safe trace tool names", () => {
  const normalized = normalizeItems({
    items: [
      {
        sourceType: "trace",
        symbol: "A",
        feature: "name",
        operator: "==",
        value: "email.send",
        selectedToolKey: "agent-b::email.send",
      },
    ],
  }, ["A"]);

  assert.equal(normalized.items[0].selectedToolKey, "agent-b::email.send");
  assert.equal(normalized.items[0].value, "email.send");
  assert.equal(normalized.items[0].expression, 'A.name == "email.send"');
  assert.equal(normalized.symbolToolMap.A, "agent-b::email.send");
});

test("condition builder resolves trace syntax fields from the selected tool instance", () => {
  const normalized = normalizeItems({
    items: [
      {
        sourceType: "trace",
        symbol: "A",
        feature: "syntax",
        operator: "contains",
        value: "@example.com",
        selectedToolKey: "agent-b::email.send",
        syntaxField: "",
      },
    ],
  }, ["A"]);

  assert.equal(normalized.items[0].syntaxField, "subject");
  assert.equal(normalized.items[0].expression, 'A.subject CONTAINS "@example.com"');
});

test("condition builder resolves current-call tool syntax fields from the ON-selected tool", () => {
  const normalized = normalizeItems({
    items: [
      {
        sourceType: "context",
        contextPrefix: "tool",
        contextField: "tool.syntax",
        contextPath: "tool.subject",
        syntaxField: "",
        operator: "contains",
        value: "@external.com",
      },
    ],
  }, ["A"], { currentCallToolKey: "agent-b::email.send" });

  assert.equal(normalized.items[0].syntaxField, "subject");
  assert.equal(normalized.items[0].contextPath, "tool.subject");
  assert.equal(normalized.items[0].expression, 'tool.subject CONTAINS "@external.com"');
});

test("itemsToTree restores a mixed parenthesized expression", () => {
  const normalized = normalizeItems({
    items: [
      {
        sourceType: "trace",
        symbol: "A",
        feature: "name",
        operator: "==",
        value: "email.send",
        selectedToolKey: "agent-a::email.send",
        openParen: "(",
      },
      {
        sourceType: "context",
        connector: "AND",
        contextPrefix: "principal",
        contextField: "principal.role",
        contextPath: "principal.role",
        operator: "==",
        value: "basic",
        closeParen: ")",
      },
      {
        sourceType: "context",
        connector: "OR",
        contextPrefix: "principal",
        contextField: "principal.role",
        contextPath: "principal.role",
        operator: "==",
        value: "admin",
      },
    ],
  }, ["A"]);

  const tree = itemsToTree(normalized.items);
  assert.equal(tree.type, "OR");
  assert.equal(tree.children[0].type, "AND");
  assert.equal(tree.children[1].type, "condition");
});

test("builder falls back safely when stored items have malformed parentheses", () => {
  const root = createElement("div");
  const hint = createElement("p");
  const addButton = createElement("button");
  const builder = createConditionBuilder({
    root,
    hint,
    addButton,
    pathSymbols: ["A"],
    allowedSourceTypes: ["trace", "context"],
    value: {
      items: [
        {
          sourceType: "trace",
          symbol: "A",
          feature: "name",
          operator: "==",
          value: "email.send",
          selectedToolKey: "agent-a::email.send",
          openParen: "(",
        },
        {
          sourceType: "context",
          connector: "OR",
          contextPrefix: "principal",
          contextField: "principal.role",
          contextPath: "principal.role",
          operator: "==",
          value: "basic",
        },
      ],
    },
  });

  const value = builder.getValue();
  assert.equal(value.items.length, 2);
  assert.equal(value.tree.type, "AND");
});

test("condition builder coerces existing items when trace source becomes unavailable", () => {
  const root = createElement("div");
  const hint = createElement("p");
  const addButton = createElement("button");
  const builder = createConditionBuilder({
    root,
    hint,
    addButton,
    pathSymbols: ["A"],
    allowedSourceTypes: ["trace", "context"],
    value: {
      items: [
        {
          sourceType: "trace",
          symbol: "A",
          feature: "name",
          operator: "==",
          value: "email.send",
          selectedToolKey: "agent-a::email.send",
        },
      ],
    },
  });

  builder.setAllowedSourceTypes(["context"]);
  const value = builder.getValue();

  assert.equal(value.items[0].sourceType, "context");
  assert.equal(value.items[0].contextPath, "tool.name");
});

test("single tool builder offers only tool and user properties", () => {
  const root = createElement("div");
  const hint = createElement("p");
  const addButton = createElement("button");
  createConditionBuilder({
    root,
    hint,
    addButton,
    pathSymbols: ["A"],
    allowedSourceTypes: ["context"],
    value: { items: [] },
  });

  addButton.dispatchEvent("click");
  const propertySelect = collectElements(root, (element) => element.tagName === "SELECT")[0];
  assert.ok(propertySelect);
  assert.deepEqual(
    propertySelect.options.map((option) => option.value),
    ["", "tool", "principal"],
  );
  assert.deepEqual(
    propertySelect.options.map((option) => option.textContent),
    ["Select property", "tool", "user"],
  );
});

test("single tool builder hides tool params/result until they can be inferred and used", () => {
  const root = createElement("div");
  const hint = createElement("p");
  const addButton = createElement("button");
  createConditionBuilder({
    root,
    hint,
    addButton,
    pathSymbols: ["A"],
    allowedSourceTypes: ["context"],
    value: { items: [] },
  });

  addButton.dispatchEvent("click");
  let selects = collectElements(root, (element) => element.tagName === "SELECT");
  const propertySelect = selects[0];
  propertySelect.value = "tool";
  propertySelect.dispatchEvent("change");

  selects = collectElements(root, (element) => element.tagName === "SELECT");
  const subpropertySelect = selects[1];
  assert.ok(subpropertySelect);
  assert.deepEqual(
    subpropertySelect.options.map((option) => option.value),
    [
      "",
      "tool.name",
      "tool.boundary",
      "tool.sensitivity",
      "tool.integrity",
    ],
  );
  assert.deepEqual(
    subpropertySelect.options.map((option) => option.textContent),
    [
      "Select sub-property",
      "name",
      "label-boundary",
      "label-sensitivity",
      "label-integrity",
    ],
  );
});

test("single tool builder infers concrete tool params from the ON clause and shows result only for completed", () => {
  const root = createElement("div");
  const hint = createElement("p");
  const addButton = createElement("button");
  createConditionBuilder({
    root,
    hint,
    addButton,
    pathSymbols: ["A"],
    allowedSourceTypes: ["context"],
    currentCallToolKey: "agent-b::email.send",
    currentCallSubtype: "completed",
    value: { items: [] },
  });

  addButton.dispatchEvent("click");
  let selects = collectElements(root, (element) => element.tagName === "SELECT");
  const propertySelect = selects[0];
  propertySelect.value = "tool";
  propertySelect.dispatchEvent("change");

  selects = collectElements(root, (element) => element.tagName === "SELECT");
  const subpropertySelect = selects[1];
  assert.ok(subpropertySelect);
  assert.deepEqual(
    subpropertySelect.options.map((option) => option.value),
    [
      "",
      "tool.name",
      "tool.boundary",
      "tool.sensitivity",
      "tool.integrity",
      "tool.subject",
      "tool.markdown",
      "tool.result",
    ],
  );
  assert.deepEqual(
    subpropertySelect.options.map((option) => option.textContent),
    [
      "Select sub-property",
      "name",
      "label-boundary",
      "label-sensitivity",
      "label-integrity",
      "param-subject",
      "param-markdown",
      "result",
    ],
  );
});

test("single tool builder exposes IN, NOT IN, MATCHES and CONTAINS for tool params", () => {
  const root = createElement("div");
  const hint = createElement("p");
  const addButton = createElement("button");
  createConditionBuilder({
    root,
    hint,
    addButton,
    pathSymbols: ["A"],
    allowedSourceTypes: ["context"],
    currentCallToolKey: "agent-b::email.send",
    currentCallSubtype: "completed",
    value: { items: [] },
  });

  addButton.dispatchEvent("click");
  let selects = collectElements(root, (element) => element.tagName === "SELECT");
  selects[0].value = "tool";
  selects[0].dispatchEvent("change");

  selects = collectElements(root, (element) => element.tagName === "SELECT");
  selects[1].value = "tool.subject";
  selects[1].dispatchEvent("change");
  buttonByText(root, ">").dispatchEvent("click");

  selects = collectElements(root, (element) => element.tagName === "SELECT");
  const comparisonSelect = selects[0];
  assert.deepEqual(
    comparisonSelect.options.map((option) => option.value),
    ["", "==", "!=", ">", ">=", "<", "<=", "IN", "NOT IN", "MATCHES", "contains"],
  );
  assert.deepEqual(
    comparisonSelect.options.map((option) => option.textContent),
    ["Select comparison", "==", "!=", ">", ">=", "<", "<=", "IN", "NOT IN", "MATCHES", "CONTAINS"],
  );
});

test("membership target values use checkboxes for enum-based IN comparisons", () => {
  const root = createElement("div");
  const hint = createElement("p");
  const addButton = createElement("button");
  const builder = createConditionBuilder({
    root,
    hint,
    addButton,
    pathSymbols: ["A"],
    allowedSourceTypes: ["context"],
    value: { items: [] },
  });

  addButton.dispatchEvent("click");
  let selects = collectElements(root, (element) => element.tagName === "SELECT");
  selects[0].value = "principal";
  selects[0].dispatchEvent("change");

  selects = collectElements(root, (element) => element.tagName === "SELECT");
  selects[1].value = "principal.role";
  selects[1].dispatchEvent("change");
  buttonByText(root, ">").dispatchEvent("click");

  selects = collectElements(root, (element) => element.tagName === "SELECT");
  selects[0].value = "IN";
  selects[0].dispatchEvent("change");

  const checkboxes = collectElements(root, (element) => element.tagName === "INPUT" && element.type === "checkbox");
  assert.equal(checkboxes.length >= 2, true);
  assert.equal(collectElements(root, (element) => element.tagName === "TEXTAREA").length, 0);
  const basicCheckbox = checkboxes.find((element) => element.value === "basic");
  const systemCheckbox = checkboxes.find((element) => element.value === "system");
  assert.ok(basicCheckbox);
  assert.ok(systemCheckbox);
  basicCheckbox.checked = true;
  basicCheckbox.dispatchEvent("change");
  systemCheckbox.checked = true;
  systemCheckbox.dispatchEvent("change");
  buttonByText(root, "Create >").dispatchEvent("click");

  assert.equal(builder.getValue().savedConditions[0].items[0].value, '{"basic", "system"}');
  assert.equal(builder.getValue().savedConditions[0].expression, 'principal.role IN {"basic", "system"}');
});

test("trace name IN comparisons keep a set literal in the live preview and saved expression", () => {
  const root = createElement("div");
  const hint = createElement("p");
  const addButton = createElement("button");
  const builder = createConditionBuilder({
    root,
    hint,
    addButton,
    pathSymbols: ["A"],
    allowedSourceTypes: ["trace"],
    value: { items: [] },
  });

  addButton.dispatchEvent("click");
  buttonByText(root, ">").dispatchEvent("click");

  let selects = collectElements(root, (element) => element.tagName === "SELECT");
  selects[0].value = "name";
  selects[0].dispatchEvent("change");
  buttonByText(root, ">").dispatchEvent("click");

  selects = collectElements(root, (element) => element.tagName === "SELECT");
  const comparisonSelect = selects.find((element) => element.options.some((option) => option.value === "IN"));
  assert.ok(comparisonSelect);
  comparisonSelect.value = "IN";
  comparisonSelect.dispatchEvent("change");

  const checkboxes = collectElements(root, (element) => element.tagName === "INPUT" && element.type === "checkbox");
  const docsCheckbox = checkboxes.find((element) => element.value === "email.send");
  const emailCheckbox = checkboxes.find((element) => element.value === "http.post");
  assert.ok(docsCheckbox);
  assert.ok(emailCheckbox);
  docsCheckbox.checked = true;
  docsCheckbox.dispatchEvent("change");
  emailCheckbox.checked = true;
  emailCheckbox.dispatchEvent("change");

  const preview = byClass(root, "condition-step-preview");
  assert.ok(preview);
  assert.equal(preview.textContent, 'A.name IN {"email.send", "http.post"}');

  buttonByText(root, "Create >").dispatchEvent("click");
  assert.equal(builder.getValue().savedConditions[0].expression, 'A.name IN {"email.send", "http.post"}');
});

test("membership target list preserves collection references for IN comparisons", () => {
  const root = createElement("div");
  const hint = createElement("p");
  const addButton = createElement("button");
  const builder = createConditionBuilder({
    root,
    hint,
    addButton,
    pathSymbols: ["A"],
    allowedSourceTypes: ["context"],
    currentCallToolKey: "agent-c::http.post",
    value: { items: [] },
  });

  addButton.dispatchEvent("click");
  let selects = collectElements(root, (element) => element.tagName === "SELECT");
  selects[0].value = "tool";
  selects[0].dispatchEvent("change");

  selects = collectElements(root, (element) => element.tagName === "SELECT");
  selects[1].value = "tool.url";
  selects[1].dispatchEvent("change");
  buttonByText(root, ">").dispatchEvent("click");

  selects = collectElements(root, (element) => element.tagName === "SELECT");
  selects[0].value = "IN";
  selects[0].dispatchEvent("change");

  const textarea = collectElements(root, (element) => element.tagName === "TEXTAREA")[0];
  assert.ok(textarea);
  textarea.value = "allowlist.http";
  textarea.dispatchEvent("input");
  buttonByText(root, "Create >").dispatchEvent("click");

  assert.equal(builder.getValue().savedConditions[0].items[0].value, "allowlist.http");
  assert.equal(builder.getValue().savedConditions[0].expression, "tool.url IN allowlist.http");
});

test("single tool builder infers concrete tool params from an existing tool.name condition", () => {
  const root = createElement("div");
  const hint = createElement("p");
  const addButton = createElement("button");
  createConditionBuilder({
    root,
    hint,
    addButton,
    pathSymbols: ["A"],
    allowedSourceTypes: ["context"],
    value: {
      items: [
        {
          sourceType: "context",
          contextPrefix: "tool",
          contextField: "tool.name",
          contextPath: "tool.name",
          operator: "==",
          value: "email.send",
          selectedToolKey: "agent-b::email.send",
        },
      ],
    },
  });

  addButton.dispatchEvent("click");
  let selects = collectElements(root, (element) => element.tagName === "SELECT");
  const propertySelect = selects[0];
  propertySelect.value = "tool";
  propertySelect.dispatchEvent("change");

  selects = collectElements(root, (element) => element.tagName === "SELECT");
  const subpropertySelect = selects[1];
  assert.ok(subpropertySelect);
  assert.deepEqual(
    subpropertySelect.options.map((option) => option.value),
    [
      "",
      "tool.name",
      "tool.boundary",
      "tool.sensitivity",
      "tool.integrity",
      "tool.subject",
      "tool.markdown",
    ],
  );
});

test("single tool builder infers concrete tool params from normalized tool names like email_send", () => {
  const root = createElement("div");
  const hint = createElement("p");
  const addButton = createElement("button");
  createConditionBuilder({
    root,
    hint,
    addButton,
    pathSymbols: ["A"],
    allowedSourceTypes: ["context"],
    value: {
      items: [
        {
          sourceType: "context",
          contextPrefix: "tool",
          contextField: "tool.name",
          contextPath: "tool.name",
          operator: "==",
          value: "http_post",
        },
      ],
    },
  });

  addButton.dispatchEvent("click");
  let selects = collectElements(root, (element) => element.tagName === "SELECT");
  const propertySelect = selects[0];
  propertySelect.value = "tool";
  propertySelect.dispatchEvent("change");

  selects = collectElements(root, (element) => element.tagName === "SELECT");
  const subpropertySelect = selects[1];
  assert.ok(subpropertySelect);
  assert.deepEqual(
    subpropertySelect.options.map((option) => option.value),
    [
      "",
      "tool.name",
      "tool.boundary",
      "tool.sensitivity",
      "tool.integrity",
      "tool.url",
      "tool.body",
    ],
  );
});

test("single tool builder infers concrete tool params from saved tool.name conditions not yet inserted into the tree", () => {
  const root = createElement("div");
  const hint = createElement("p");
  const addButton = createElement("button");
  createConditionBuilder({
    root,
    hint,
    addButton,
    pathSymbols: ["A"],
    allowedSourceTypes: ["context"],
    value: {
      items: [],
      savedConditions: [
        {
          conditionId: "COND1",
          items: [
            {
              sourceType: "context",
              contextPrefix: "tool",
              contextField: "tool.name",
              contextPath: "tool.name",
              operator: "==",
              value: "email.send",
              selectedToolKey: "agent-b::email.send",
            },
          ],
        },
      ],
    },
  });

  addButton.dispatchEvent("click");
  let selects = collectElements(root, (element) => element.tagName === "SELECT");
  const propertySelect = selects[0];
  propertySelect.value = "tool";
  propertySelect.dispatchEvent("change");

  selects = collectElements(root, (element) => element.tagName === "SELECT");
  const subpropertySelect = selects[1];
  assert.ok(subpropertySelect);
  assert.deepEqual(
    subpropertySelect.options.map((option) => option.value),
    [
      "",
      "tool.name",
      "tool.boundary",
      "tool.sensitivity",
      "tool.integrity",
      "tool.subject",
      "tool.markdown",
    ],
  );
});

test("single tool builder infers concrete tool params from saved trace name conditions not yet inserted into the tree", () => {
  const root = createElement("div");
  const hint = createElement("p");
  const addButton = createElement("button");
  createConditionBuilder({
    root,
    hint,
    addButton,
    pathSymbols: ["A"],
    allowedSourceTypes: ["context"],
    value: {
      items: [],
      savedConditions: [
        {
          conditionId: "COND1",
          items: [
            {
              sourceType: "trace",
              symbol: "A",
              feature: "name",
              propertyGroup: "name",
              operator: "==",
              value: "email.send",
              selectedToolKey: "agent-b::email.send",
            },
          ],
        },
      ],
    },
  });

  addButton.dispatchEvent("click");
  let selects = collectElements(root, (element) => element.tagName === "SELECT");
  const propertySelect = selects[0];
  propertySelect.value = "tool";
  propertySelect.dispatchEvent("change");

  selects = collectElements(root, (element) => element.tagName === "SELECT");
  const subpropertySelect = selects[1];
  assert.ok(subpropertySelect);
  assert.deepEqual(
    subpropertySelect.options.map((option) => option.value),
    [
      "",
      "tool.name",
      "tool.boundary",
      "tool.sensitivity",
      "tool.integrity",
      "tool.subject",
      "tool.markdown",
    ],
  );
});

test("single tool builder offers enum user roles", () => {
  const root = createElement("div");
  const hint = createElement("p");
  const addButton = createElement("button");
  createConditionBuilder({
    root,
    hint,
    addButton,
    pathSymbols: ["A"],
    allowedSourceTypes: ["context"],
    currentCallToolKey: "agent-b::email.send",
    currentCallSubtype: "completed",
    value: { items: [] },
  });

  addButton.dispatchEvent("click");
  let selects = collectElements(root, (element) => element.tagName === "SELECT");
  const propertySelect = selects[0];

  propertySelect.value = "principal";
  propertySelect.dispatchEvent("change");
  selects = collectElements(root, (element) => element.tagName === "SELECT");
  const principalSubpropertySelect = selects[1];
  principalSubpropertySelect.value = "principal.role";
  principalSubpropertySelect.dispatchEvent("change");
  buttonByText(root, ">").dispatchEvent("click");

  selects = collectElements(root, (element) => element.tagName === "SELECT");
  const targetValueSelect = selects[1];
  assert.deepEqual(
    targetValueSelect.options.map((option) => option.value),
    ["", "basic", "default", "privileged", "system"],
  );
});

test("saved trace conditions created through the draft flow inform later context tool params", () => {
  const root = createElement("div");
  const hint = createElement("p");
  const addButton = createElement("button");
  createConditionBuilder({
    root,
    hint,
    addButton,
    pathSymbols: ["A"],
    allowedSourceTypes: ["trace", "context"],
    value: { items: [] },
  });

  addButton.dispatchEvent("click");
  buttonByText(root, ">").dispatchEvent("click");
  buttonByText(root, ">").dispatchEvent("click");

  let selects = collectElements(root, (element) => element.tagName === "SELECT");
  selects[0].value = "name";
  selects[0].dispatchEvent("change");
  buttonByText(root, ">").dispatchEvent("click");

  selects = collectElements(root, (element) => element.tagName === "SELECT");
  selects[0].value = "==";
  selects[0].dispatchEvent("change");
  selects[1].value = "agent-b::email.send";
  selects[1].dispatchEvent("change");
  buttonByText(root, "Create >").dispatchEvent("click");

  addButton.dispatchEvent("click");
  selects = collectElements(root, (element) => element.tagName === "SELECT");
  selects[0].value = "context";
  selects[0].dispatchEvent("change");

  selects = collectElements(root, (element) => element.tagName === "SELECT");
  selects[0].value = "tool";
  selects[0].dispatchEvent("change");

  selects = collectElements(root, (element) => element.tagName === "SELECT");
  const subpropertySelect = selects[1];
  assert.ok(subpropertySelect);
  assert.deepEqual(
    subpropertySelect.options.map((option) => option.value),
    [
      "",
      "tool.name",
      "tool.boundary",
      "tool.sensitivity",
      "tool.integrity",
      "tool.subject",
      "tool.markdown",
    ],
  );
});

test("saved trace conditions expose syntax in later trace drafts before insertion into the tree", () => {
  const root = createElement("div");
  const hint = createElement("p");
  const addButton = createElement("button");
  createConditionBuilder({
    root,
    hint,
    addButton,
    pathSymbols: ["A"],
    allowedSourceTypes: ["trace"],
    value: {
      items: [],
      savedConditions: [
        {
          conditionId: "COND1",
          items: [
            {
              sourceType: "trace",
              symbol: "A",
              feature: "name",
              propertyGroup: "name",
              operator: "==",
              value: "http.post",
              selectedToolKey: "agent-c::http.post",
            },
          ],
        },
      ],
    },
  });

  addButton.dispatchEvent("click");
  buttonByText(root, ">").dispatchEvent("click");

  const propertySelect = collectElements(root, (element) => element.tagName === "SELECT")[0];
  assert.ok(propertySelect);
  assert.deepEqual(
    propertySelect.options.map((option) => option.value),
    ["", "name", "label", "syntax"],
  );
});

test("saved trace conditions expose syntax params in later trace drafts before insertion into the tree", () => {
  const root = createElement("div");
  const hint = createElement("p");
  const addButton = createElement("button");
  createConditionBuilder({
    root,
    hint,
    addButton,
    pathSymbols: ["A"],
    allowedSourceTypes: ["trace"],
    value: {
      items: [],
      savedConditions: [
        {
          conditionId: "COND1",
          items: [
            {
              sourceType: "trace",
              symbol: "A",
              feature: "name",
              propertyGroup: "name",
              operator: "==",
              value: "http.post",
              selectedToolKey: "agent-c::http.post",
            },
          ],
        },
      ],
    },
  });

  addButton.dispatchEvent("click");
  buttonByText(root, ">").dispatchEvent("click");

  let selects = collectElements(root, (element) => element.tagName === "SELECT");
  const propertySelect = selects[0];
  propertySelect.value = "syntax";
  propertySelect.dispatchEvent("change");

  selects = collectElements(root, (element) => element.tagName === "SELECT");
  const subpropertySelect = selects[1];
  assert.ok(subpropertySelect);
  assert.deepEqual(
    subpropertySelect.options.map((option) => option.value),
    ["", "url", "body"],
  );
});

test("tree builder creates a saved condition and inserts it into the root group", () => {
  const root = createElement("div");
  const hint = createElement("p");
  const addButton = createElement("button");
  const builder = createConditionBuilder({
    root,
    hint,
    addButton,
    pathSymbols: ["A"],
    allowedSourceTypes: ["trace"],
    value: { items: [] },
  });

  addButton.dispatchEvent("click");
  buttonByText(root, ">").dispatchEvent("click");
  const propertySelect = selectWithOption(root, "name");
  assert.ok(propertySelect);
  propertySelect.value = "name";
  propertySelect.dispatchEvent("change");
  buttonByText(root, ">").dispatchEvent("click");
  const operatorSelect = selectWithOption(root, "==");
  assert.ok(operatorSelect);
  operatorSelect.value = "==";
  operatorSelect.dispatchEvent("change");
  const valueSelect = selectWithOption(root, "agent-a::email.send");
  assert.ok(valueSelect);
  valueSelect.value = "agent-a::email.send";
  valueSelect.dispatchEvent("change");
  buttonByText(root, "Create >").dispatchEvent("click");

  const saved = builder.getValue().savedConditions;
  assert.equal(saved.length, 1);
  assert.equal(saved[0].conditionId, "COND1");

  buttonByLabel(root, "Add node").dispatchEvent("click");
  buttonByText(root, "COND1").dispatchEvent("click");
  const value = builder.getValue();
  assert.equal(value.items.length, 1);
  assert.equal(value.expression, 'A.name == "email.send"');
});

test("tree builder keeps earlier saved conditions when creating a new one", () => {
  const root = createElement("div");
  const hint = createElement("p");
  const addButton = createElement("button");
  const builder = createConditionBuilder({
    root,
    hint,
    addButton,
    pathSymbols: ["A"],
    allowedSourceTypes: ["trace"],
    value: { items: [] },
  });

  addButton.dispatchEvent("click");
  buttonByText(root, ">").dispatchEvent("click");
  let propertySelect = selectWithOption(root, "name");
  assert.ok(propertySelect);
  propertySelect.value = "name";
  propertySelect.dispatchEvent("change");
  buttonByText(root, ">").dispatchEvent("click");
  let operatorSelect = selectWithOption(root, "==");
  assert.ok(operatorSelect);
  operatorSelect.value = "==";
  operatorSelect.dispatchEvent("change");
  let valueSelect = selectWithOption(root, "agent-a::email.send");
  assert.ok(valueSelect);
  valueSelect.value = "agent-a::email.send";
  valueSelect.dispatchEvent("change");
  buttonByText(root, "Create >").dispatchEvent("click");

  addButton.dispatchEvent("click");
  buttonByText(root, ">").dispatchEvent("click");
  propertySelect = selectWithOption(root, "name");
  assert.ok(propertySelect);
  propertySelect.value = "name";
  propertySelect.dispatchEvent("change");
  buttonByText(root, ">").dispatchEvent("click");
  operatorSelect = selectWithOption(root, "==");
  assert.ok(operatorSelect);
  operatorSelect.value = "==";
  operatorSelect.dispatchEvent("change");
  valueSelect = selectWithOption(root, "agent-c::http.post");
  assert.ok(valueSelect);
  valueSelect.value = "agent-c::http.post";
  valueSelect.dispatchEvent("change");
  buttonByText(root, "Create >").dispatchEvent("click");

  const saved = builder.getValue().savedConditions;
  assert.equal(saved.length, 2);
  assert.equal(saved[0].conditionId, "COND1");
  assert.equal(saved[1].conditionId, "COND2");
  assert.equal(saved[0].expression, 'A.name == "email.send"');
  assert.equal(saved[1].expression, 'A.name == "http.post"');
});

test("tree builder creates nested groups and reuses a saved condition", () => {
  const root = createElement("div");
  const hint = createElement("p");
  const addButton = createElement("button");
  const builder = createConditionBuilder({
    root,
    hint,
    addButton,
    pathSymbols: ["A"],
    allowedSourceTypes: ["trace"],
    value: {
      savedConditions: [
        {
          conditionId: "COND1",
          items: [
            {
              sourceType: "trace",
              symbol: "A",
              feature: "name",
              operator: "==",
              value: "email.send",
              selectedToolKey: "agent-a::email.send",
            },
          ],
        },
      ],
      currentConditionId: "COND1",
    },
  });

  buttonByLabel(root, "Add node", 0).dispatchEvent("click");
  buttonByText(root, "COND1", 0).dispatchEvent("click");
  buttonByLabel(root, "Add node", 0).dispatchEvent("click");
  buttonByText(root, "Group", 0).dispatchEvent("click");
  buttonByLabel(root, "Set group logic to OR", 0).dispatchEvent("click");
  buttonByLabel(root, "Add node", 1).dispatchEvent("click");
  buttonByText(root, "COND1", 0).dispatchEvent("click");

  const value = builder.getValue();
  assert.equal(value.items.length, 2);
  assert.equal(value.items[1].connector, "AND");
  assert.equal(value.tree.children[1].type, "OR");
  assert.match(value.expression, /^\(?A\.name == "email\.send"/);
  assert.match(value.expression, /\(A\.name == "email\.send"\)$/);
});

test("root logic can switch to OR without forcing an AND label in the canvas model", () => {
  const root = createElement("div");
  const hint = createElement("p");
  const addButton = createElement("button");
  const builder = createConditionBuilder({
    root,
    hint,
    addButton,
    pathSymbols: ["A"],
    allowedSourceTypes: ["trace"],
    value: {
      savedConditions: [
        {
          conditionId: "COND1",
          items: [
            {
              sourceType: "trace",
              symbol: "A",
              feature: "name",
              operator: "==",
              value: "email.send",
              selectedToolKey: "agent-a::email.send",
            },
          ],
        },
      ],
      currentConditionId: "COND1",
    },
  });

  buttonByLabel(root, "Add node", 0).dispatchEvent("click");
  buttonByText(root, "COND1", 0).dispatchEvent("click");
  buttonByLabel(root, "Add node", 0).dispatchEvent("click");
  buttonByText(root, "Group", 0).dispatchEvent("click");
  buttonByText(root, "OR", 0).dispatchEvent("click");
  buttonByLabel(root, "Add node", 1).dispatchEvent("click");
  buttonByText(root, "COND1", 0).dispatchEvent("click");

  const value = builder.getValue();
  assert.equal(value.tree.type, "OR");
  assert.equal(value.items[1].connector, "OR");
});

test("builder rehydrates from a stored tree and keeps flattened compatibility output", () => {
  const root = createElement("div");
  const hint = createElement("p");
  const addButton = createElement("button");
  const builder = createConditionBuilder({
    root,
    hint,
    addButton,
    pathSymbols: ["A"],
    allowedSourceTypes: ["trace", "context"],
    value: {
      tree: {
        id: "group_root",
        type: "OR",
        children: [
          {
            id: "cond_1",
            type: "condition",
            item: {
              sourceType: "trace",
              symbol: "A",
              feature: "name",
              operator: "==",
              value: "email.send",
              selectedToolKey: "agent-a::email.send",
            },
          },
          {
            id: "group_2",
            type: "AND",
            children: [
              {
                id: "cond_2",
                type: "condition",
                item: {
                  sourceType: "context",
                  contextPrefix: "principal",
                  contextField: "principal.role",
                  contextPath: "principal.role",
                  operator: "==",
                  value: "basic",
                },
              },
            ],
          },
        ],
      },
    },
  });

  const value = builder.getValue();
  assert.equal(value.tree.type, "OR");
  assert.equal(value.items.length, 2);
  assert.equal(value.items[1].connector, "OR");
  assert.match(value.expression, /principal\.role == "basic"/);
});

test("flattened tree output adds one pair of parentheses per nested group", () => {
  const root = createElement("div");
  const hint = createElement("p");
  const addButton = createElement("button");
  const builder = createConditionBuilder({
    root,
    hint,
    addButton,
    pathSymbols: ["A"],
    allowedSourceTypes: ["trace"],
    value: {
      tree: {
        id: "group_root",
        type: "OR",
        children: [
          {
            id: "cond_1",
            type: "condition",
            item: {
              sourceType: "trace",
              symbol: "A",
              feature: "name",
              operator: "==",
              value: "http.post",
              selectedToolKey: "agent-c::http.post",
            },
          },
          {
            id: "group_2",
            type: "AND",
            children: [
              {
                id: "cond_2",
                type: "condition",
                item: {
                  sourceType: "trace",
                  symbol: "A",
                  feature: "label.integrity",
                  operator: "!=",
                  value: "trusted",
                  openParen: "(",
                  closeParen: ")",
                },
              },
            ],
          },
          {
            id: "cond_3",
            type: "condition",
            item: {
              sourceType: "trace",
              symbol: "A",
              feature: "name",
              operator: "==",
              value: "email.send",
              selectedToolKey: "agent-a::email.send",
            },
          },
        ],
      },
    },
  });

  const value = builder.getValue();
  assert.equal(value.expression, 'A.name == "http.post" OR (A.integrity != "trusted") OR A.name == "email.send"');
});

test("tree leaf display omits structural parentheses while preview keeps grouped expression", () => {
  const root = createElement("div");
  const hint = createElement("p");
  const addButton = createElement("button");
  const builder = createConditionBuilder({
    root,
    hint,
    addButton,
    pathSymbols: ["B"],
    allowedSourceTypes: ["trace"],
    value: {
      tree: {
        id: "group_root",
        type: "AND",
        children: [
          {
            id: "group_nested",
            type: "AND",
            children: [
              {
                id: "cond_1",
                type: "condition",
                item: {
                  sourceType: "trace",
                  symbol: "B",
                  feature: "name",
                  operator: "==",
                  value: "docs.search",
                  selectedToolKey: "agent-c::http.post",
                },
              },
              {
                id: "cond_2",
                type: "condition",
                item: {
                  sourceType: "trace",
                  symbol: "B",
                  feature: "label.sensitivity",
                  operator: "==",
                  value: "high",
                },
              },
            ],
          },
        ],
      },
    },
  });

  const leafOne = byClass(root, "condition-tree-leaf-rule", 0);
  const leafTwo = byClass(root, "condition-tree-leaf-rule", 1);
  assert.equal(leafOne.textContent, 'B.name == "http.post"');
  assert.equal(leafTwo.textContent, 'B.sensitivity == "high"');
  assert.equal(builder.getValue().expression, '(B.name == "http.post" AND B.sensitivity == "high")');
});

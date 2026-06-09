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
    className: "",
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
      return {
        classList: {
          toggle() {},
        },
      };
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
require("../static/common/ui-helpers.js");
require("../static/pages/rules/condition-builder.js");

const { createConditionBuilder, normalizeItems } = global.window.AgentGuardConditionBuilder;

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
        feature: "name",
        operator: "==",
        value: "email.send",
        selectedToolKey: "agent-b::email.send",
      },
      {
        sourceType: "trace",
        symbol: "A",
        connector: "AND",
        feature: "syntax",
        operator: "contains",
        value: "@example.com",
      },
    ],
  }, ["A"]);

  assert.equal(normalized.items[1].syntaxField, "subject");
  assert.equal(normalized.items[1].resolvedToolName, "email.send");
  assert.equal(normalized.items[1].expression, 'A.subject CONTAINS "@example.com"');
});

test("condition builder normalizes current-call context conditions", () => {
  const normalized = normalizeItems({
    items: [
      {
        sourceType: "context",
        contextPrefix: "tool",
        contextField: "tool.boundary",
        contextPath: "tool.boundary",
        operator: "==",
        value: "external",
      },
      {
        sourceType: "context",
        connector: "AND",
        contextPrefix: "principal",
        contextField: "principal.trust_level",
        contextPath: "principal.trust_level",
        operator: ">=",
        value: "2",
      },
    ],
  }, ["A"]);

  assert.equal(normalized.items[0].expression, 'tool.boundary == "external"');
  assert.equal(normalized.items[1].expression, 'principal.trust_level >= "2"');
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

test("condition builder preserves mixed trace and context conditions", () => {
  const normalized = normalizeItems({
    items: [
      {
        sourceType: "trace",
        symbol: "A",
        feature: "name",
        operator: "==",
        value: "http.post",
        selectedToolKey: "agent-c::http.post",
      },
      {
        sourceType: "context",
        connector: "AND",
        contextPrefix: "event",
        contextField: "event.session_id",
        contextPath: "event.session_id",
        operator: "contains",
        value: "sess-",
      },
    ],
  }, ["A", "B"]);

  assert.equal(normalized.items[0].expression, 'A.name == "http.post"');
  assert.equal(normalized.items[1].expression, 'event.session_id CONTAINS "sess-"');
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

test("condition builder adds context conditions by default when only current call is available", () => {
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
  const value = builder.getValue();

  assert.equal(value.items[0].sourceType, "context");
  assert.equal(value.items[0].contextPath, "");
});

test("condition builder refreshes current-call syntax context when the ON-selected tool changes", () => {
  const root = createElement("div");
  const hint = createElement("p");
  const addButton = createElement("button");
  const builder = createConditionBuilder({
    root,
    hint,
    addButton,
    pathSymbols: ["A"],
    defaultMode: "direct",
    currentCallToolKey: "agent-a::email.send",
    allowedSourceTypes: ["context"],
    value: {
      items: [
        {
          sourceType: "context",
          contextPrefix: "tool",
          contextField: "tool.syntax",
          contextPath: "tool.to",
          syntaxField: "to",
          operator: "contains",
          value: "@example.com",
        },
      ],
    },
  });

  builder.setCurrentCallToolKey("agent-b::email.send");
  const value = builder.getValue();

  assert.equal(value.items[0].syntaxField, "subject");
  assert.equal(value.items[0].contextPath, "tool.subject");
  assert.equal(value.expression, 'tool.subject CONTAINS "@example.com"');
});

test("condition builder does not rerender the card on free-text value input", () => {
  const root = createElement("div");
  const hint = createElement("p");
  const addButton = createElement("button");
  let renderCount = 0;
  let innerHTMLValue = "";

  Object.defineProperty(root, "innerHTML", {
    configurable: true,
    enumerable: true,
    get() {
      return innerHTMLValue;
    },
    set(value) {
      innerHTMLValue = String(value || "");
      root.children = [];
      root.options = [];
      renderCount += 1;
    },
  });

  const builder = createConditionBuilder({
    root,
    hint,
    addButton,
    pathSymbols: ["A"],
    defaultMode: "direct",
    allowedSourceTypes: ["context"],
    value: {
      items: [
        {
          sourceType: "context",
          contextPrefix: "event",
          contextField: "event.session_id",
          contextPath: "event.session_id",
          operator: "contains",
          value: "",
        },
      ],
    },
  });

  const valueInput = findElement(
    root,
    (element) => element.tagName === "INPUT" && element.placeholder === "Value",
  );

  assert.ok(valueInput);
  const initialRenderCount = renderCount;
  valueInput.value = "session-123";
  valueInput.dispatchEvent("input");

  assert.equal(builder.getValue().items[0].value, "session-123");
  assert.equal(renderCount, initialRenderCount);
});

test("step condition builder is the default mode and only emits completed compositions", () => {
  const root = createElement("div");
  const hint = createElement("p");
  const addButton = createElement("button");
  const stepModeButton = createElement("button");
  const directModeButton = createElement("button");
  const modeCopy = createElement("p");
  const builder = createConditionBuilder({
    root,
    hint,
    addButton,
    stepModeButton,
    directModeButton,
    modeCopy,
    pathSymbols: ["A"],
    allowedSourceTypes: ["trace"],
    value: { items: [] },
  });

  assert.equal(builder.getMode(), "step");
  addButton.dispatchEvent("click");
  let value = builder.getValue();
  assert.equal(value.items.length, 1);
  assert.equal(value.expression, "");

  const nextButton = findElement(
    root,
    (element) => element.tagName === "BUTTON" && element.attributes?.["aria-label"] === "Next builder step",
  );
  assert.ok(nextButton);
  nextButton.dispatchEvent("click");

  let selects = collectElements(root, (element) => element.tagName === "SELECT");
  assert.equal(selects.length > 0, true);
  selects[0].value = "name";
  selects[0].dispatchEvent("change");
  findElement(root, (element) => element.tagName === "BUTTON" && element.attributes?.["aria-label"] === "Next builder step").dispatchEvent("click");

  selects = collectElements(root, (element) => element.tagName === "SELECT");
  assert.equal(selects.length >= 2, true);
  selects[0].value = "==";
  selects[0].dispatchEvent("change");
  selects[1].value = "agent-a::email.send";
  selects[1].dispatchEvent("change");

  const generateButton = findElement(
    root,
    (element) => element.tagName === "BUTTON" && element.attributes?.["aria-label"] === "Generate single rule",
  );
  assert.ok(generateButton);
  generateButton.dispatchEvent("click");

  value = builder.getValue();
  assert.equal(value.expression, 'A.name == "email.send"');
  assert.equal(value.items[0].conditionId, "COND1");
  assert.equal(value.savedConditions.length, 1);
  assert.equal(value.currentConditionId, "COND1");
});

test("step condition builder hides guided preview before comparison stage", () => {
  const root = createElement("div");
  const hint = createElement("p");
  const addButton = createElement("button");
  const stepModeButton = createElement("button");
  const directModeButton = createElement("button");
  const modeCopy = createElement("p");
  createConditionBuilder({
    root,
    hint,
    addButton,
    stepModeButton,
    directModeButton,
    modeCopy,
    pathSymbols: ["A"],
    allowedSourceTypes: ["trace"],
    value: { items: [] },
  });

  addButton.dispatchEvent("click");
  const previewBeforeComparison = findElement(
    root,
    (element) => element.tagName === "PRE" && element.className === "condition-step-preview",
  );
  assert.equal(previewBeforeComparison, null);

  findElement(root, (element) => element.tagName === "BUTTON" && element.attributes?.["aria-label"] === "Next builder step").dispatchEvent("click");
  let selects = collectElements(root, (element) => element.tagName === "SELECT");
  selects[0].value = "name";
  selects[0].dispatchEvent("change");
  findElement(root, (element) => element.tagName === "BUTTON" && element.attributes?.["aria-label"] === "Next builder step").dispatchEvent("click");

  const previewAtComparison = findElement(
    root,
    (element) => element.tagName === "PRE" && element.className === "condition-step-preview",
  );
  assert.ok(previewAtComparison);
});

test("step condition builder leaves guided comparison fields empty until the user selects them", () => {
  const root = createElement("div");
  const hint = createElement("p");
  const addButton = createElement("button");
  const stepModeButton = createElement("button");
  const directModeButton = createElement("button");
  const modeCopy = createElement("p");
  const builder = createConditionBuilder({
    root,
    hint,
    addButton,
    stepModeButton,
    directModeButton,
    modeCopy,
    pathSymbols: ["A"],
    allowedSourceTypes: ["trace"],
    value: { items: [] },
  });

  addButton.dispatchEvent("click");
  findElement(root, (element) => element.tagName === "BUTTON" && element.attributes?.["aria-label"] === "Next builder step").dispatchEvent("click");
  let selects = collectElements(root, (element) => element.tagName === "SELECT");
  selects[0].value = "name";
  selects[0].dispatchEvent("change");
  findElement(root, (element) => element.tagName === "BUTTON" && element.attributes?.["aria-label"] === "Next builder step").dispatchEvent("click");

  selects = collectElements(root, (element) => element.tagName === "SELECT");
  assert.equal(selects[0].value, "");
  assert.equal(selects[1].value, "");
  assert.equal(builder.getValue().expression, "");
});

test("step condition builder expands sub-property inside the property step", () => {
  const root = createElement("div");
  const hint = createElement("p");
  const addButton = createElement("button");
  const stepModeButton = createElement("button");
  const directModeButton = createElement("button");
  const modeCopy = createElement("p");
  createConditionBuilder({
    root,
    hint,
    addButton,
    stepModeButton,
    directModeButton,
    modeCopy,
    pathSymbols: ["A"],
    allowedSourceTypes: ["trace"],
    value: { items: [] },
  });

  addButton.dispatchEvent("click");
  findElement(root, (element) => element.tagName === "BUTTON" && element.attributes?.["aria-label"] === "Next builder step").dispatchEvent("click");

  let selects = collectElements(root, (element) => element.tagName === "SELECT");
  const propertySelect = selects.find((element) => element.options.some((option) => option.value === "label"));
  propertySelect.value = "label";
  propertySelect.dispatchEvent("change");

  selects = collectElements(root, (element) => element.tagName === "SELECT");
  const subPropertySelect = selects.find((element) => element.options.some((option) => option.value === "label.boundary"));
  assert.ok(subPropertySelect);
  const comparisonSelect = selects.find((element) => element.options.some((option) => option.value === "=="));
  assert.equal(comparisonSelect, undefined);
});

test("step condition builder skips rule scope when only one source type is allowed", () => {
  const root = createElement("div");
  const hint = createElement("p");
  const addButton = createElement("button");
  const stepModeButton = createElement("button");
  const directModeButton = createElement("button");
  const modeCopy = createElement("p");
  createConditionBuilder({
    root,
    hint,
    addButton,
    stepModeButton,
    directModeButton,
    modeCopy,
    pathSymbols: ["A"],
    allowedSourceTypes: ["trace"],
    value: { items: [] },
  });

  addButton.dispatchEvent("click");
  const ruleScopeLabel = findElement(root, (element) => element.textContent === "Rule Scope");
  assert.equal(ruleScopeLabel, null);
  const stepKicker = findElement(root, (element) => element.className === "condition-step-kicker");
  assert.equal(stepKicker?.textContent, "Step 1");
});

test("step condition builder creates saved conditions with stable ids", () => {
  const root = createElement("div");
  const hint = createElement("p");
  const addButton = createElement("button");
  const stepModeButton = createElement("button");
  const directModeButton = createElement("button");
  const modeCopy = createElement("p");
  const builder = createConditionBuilder({
    root,
    hint,
    addButton,
    stepModeButton,
    directModeButton,
    modeCopy,
    pathSymbols: ["A"],
    allowedSourceTypes: ["trace"],
    value: { items: [] },
  });

  function generateSingleRule() {
    addButton.dispatchEvent("click");
    findElement(root, (element) => element.tagName === "BUTTON" && element.attributes?.["aria-label"] === "Next builder step").dispatchEvent("click");
    let selects = collectElements(root, (element) => element.tagName === "SELECT");
    const fieldSelect = selects.find((element) => element.options.some((option) => option.value === "name"));
    fieldSelect.value = "name";
    fieldSelect.dispatchEvent("change");
    findElement(root, (element) => element.tagName === "BUTTON" && element.attributes?.["aria-label"] === "Next builder step").dispatchEvent("click");
    selects = collectElements(root, (element) => element.tagName === "SELECT");
    const operatorSelect = selects.find((element) => element.options.some((option) => option.value === "=="));
    operatorSelect.value = "==";
    operatorSelect.dispatchEvent("change");
    const valueSelect = selects.find((element) => element.options.some((option) => option.value === "agent-a::email.send"));
    valueSelect.value = "agent-a::email.send";
    valueSelect.dispatchEvent("change");
    findElement(root, (element) => element.tagName === "BUTTON" && element.attributes?.["aria-label"] === "Generate single rule").dispatchEvent("click");
  }

  generateSingleRule();
  generateSingleRule();
  generateSingleRule();

  const value = builder.getValue();
  assert.equal(value.savedConditions[0].conditionId, "COND1");
  assert.equal(value.savedConditions[1].conditionId, "COND2");
  assert.equal(value.savedConditions[2].conditionId, "COND3");
  assert.equal(value.currentConditionId, "COND3");
  assert.equal(value.expression, 'A.name == "email.send"');
});

test("step condition builder combines two saved conditions into a new intermediate rule", () => {
  const root = createElement("div");
  const hint = createElement("p");
  const addButton = createElement("button");
  const stepModeButton = createElement("button");
  const directModeButton = createElement("button");
  const modeCopy = createElement("p");
  const builder = createConditionBuilder({
    root,
    hint,
    addButton,
    stepModeButton,
    directModeButton,
    modeCopy,
    pathSymbols: ["A"],
    allowedSourceTypes: ["trace"],
    value: { items: [] },
  });

  function generateSingleRule() {
    addButton.dispatchEvent("click");
    findElement(root, (element) => element.tagName === "BUTTON" && element.attributes?.["aria-label"] === "Next builder step").dispatchEvent("click");
    let selects = collectElements(root, (element) => element.tagName === "SELECT");
    const fieldSelect = selects.find((element) => element.options.some((option) => option.value === "name"));
    fieldSelect.value = "name";
    fieldSelect.dispatchEvent("change");
    findElement(root, (element) => element.tagName === "BUTTON" && element.attributes?.["aria-label"] === "Next builder step").dispatchEvent("click");
    selects = collectElements(root, (element) => element.tagName === "SELECT");
    const operatorSelect = selects.find((element) => element.options.some((option) => option.value === "=="));
    operatorSelect.value = "==";
    operatorSelect.dispatchEvent("change");
    const valueSelect = selects.find((element) => element.options.some((option) => option.value === "agent-a::email.send"));
    valueSelect.value = "agent-a::email.send";
    valueSelect.dispatchEvent("change");
    findElement(root, (element) => element.tagName === "BUTTON" && element.attributes?.["aria-label"] === "Generate single rule").dispatchEvent("click");
  }

  generateSingleRule();
  generateSingleRule();

  let checkboxes = collectElements(root, (element) => element.tagName === "INPUT");
  assert.equal(checkboxes.length >= 2, true);
  checkboxes[0].checked = true;
  checkboxes[0].dispatchEvent("change");
  checkboxes[1].checked = true;
  checkboxes[1].dispatchEvent("change");

  const combineSelect = collectElements(root, (element) => element.tagName === "SELECT")
    .find((element) => element.options.some((option) => option.textContent === "Combine with OR"));
  assert.ok(combineSelect);
  combineSelect.value = "OR";
  combineSelect.dispatchEvent("change");

  const value = builder.getValue();
  assert.equal(value.savedConditions.length, 3);
  assert.equal(value.savedConditions[2].conditionId, "COND3");
  assert.equal(value.currentConditionId, "COND3");
  assert.equal(value.expression, 'A.name == "email.send" OR A.name == "email.send"');
  assert.equal(value.items.length, 2);
  assert.equal(value.items[1].connector, "OR");
});

test("step condition builder reuses one saved condition as current result without creating a new rule", () => {
  const root = createElement("div");
  const hint = createElement("p");
  const addButton = createElement("button");
  const stepModeButton = createElement("button");
  const directModeButton = createElement("button");
  const modeCopy = createElement("p");
  const toastMessages = [];
  global.window.AgentGuardUI = {
    showToast(message, tone) {
      toastMessages.push({ message, tone });
    },
  };

  const builder = createConditionBuilder({
    root,
    hint,
    addButton,
    stepModeButton,
    directModeButton,
    modeCopy,
    pathSymbols: ["A"],
    allowedSourceTypes: ["trace"],
    value: { items: [] },
  });

  function generateSingleRule() {
    addButton.dispatchEvent("click");
    findElement(root, (element) => element.tagName === "BUTTON" && element.attributes?.["aria-label"] === "Next builder step").dispatchEvent("click");
    let selects = collectElements(root, (element) => element.tagName === "SELECT");
    const fieldSelect = selects.find((element) => element.options.some((option) => option.value === "name"));
    fieldSelect.value = "name";
    fieldSelect.dispatchEvent("change");
    findElement(root, (element) => element.tagName === "BUTTON" && element.attributes?.["aria-label"] === "Next builder step").dispatchEvent("click");
    selects = collectElements(root, (element) => element.tagName === "SELECT");
    const operatorSelect = selects.find((element) => element.options.some((option) => option.value === "=="));
    operatorSelect.value = "==";
    operatorSelect.dispatchEvent("change");
    const valueSelect = selects.find((element) => element.options.some((option) => option.value === "agent-a::email.send"));
    valueSelect.value = "agent-a::email.send";
    valueSelect.dispatchEvent("change");
    findElement(root, (element) => element.tagName === "BUTTON" && element.attributes?.["aria-label"] === "Generate single rule").dispatchEvent("click");
  }

  generateSingleRule();
  generateSingleRule();

  let checkboxes = collectElements(root, (element) => element.tagName === "INPUT");
  checkboxes[0].checked = true;
  checkboxes[0].dispatchEvent("change");

  const combineSelect = collectElements(root, (element) => element.tagName === "SELECT")
    .find((element) => element.options.some((option) => option.textContent === "Use as current result"));
  assert.ok(combineSelect);
  combineSelect.value = "reuse";
  combineSelect.dispatchEvent("change");

  const value = builder.getValue();
  assert.equal(value.savedConditions.length, 2);
  assert.equal(value.currentConditionId, "COND1");
  assert.equal(value.expression, 'A.name == "email.send"');
  assert.equal(toastMessages.length, 1);
  assert.equal(toastMessages[0].tone, "success");
  const currentResultLabel = findElement(root, (element) => element.textContent === "Current Result");
  assert.ok(currentResultLabel);
});

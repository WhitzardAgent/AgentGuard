const test = require("node:test");
const assert = require("node:assert/strict");

function createElement(tagName = "div") {
  return {
    tagName: String(tagName).toUpperCase(),
    value: "",
    textContent: "",
    innerHTML: "",
    disabled: false,
    hidden: false,
    options: [],
    children: [],
    attributes: {},
    classList: {
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
    querySelectorAll() {
      return this.children.filter((child) => ["INPUT", "SELECT", "TEXTAREA", "BUTTON"].includes(child.tagName));
    },
    querySelector(selector) {
      if (selector === "img") {
        return this.children.find((child) => child.tagName === "IMG") || null;
      }
      return null;
    },
    setAttribute(name, value) {
      this.attributes[name] = value;
    },
  };
}

function createSelect() {
  const element = createElement("select");
  element.appendChild(createElement("option"));
  return element;
}

function setupController() {
  const elementsById = {};
  const ids = [
    "path-builder-segments",
    "path-builder-hint",
    "condition-builder-grid",
    "condition-builder-hint",
  ];
  ids.forEach((id) => {
    elementsById[id] = createElement("div");
  });

  const pathContinueButton = createElement("button");
  const pathContinueIcon = createElement("img");
  pathContinueButton.appendChild(pathContinueIcon);

  global.document = {
    getElementById(id) {
      return elementsById[id] || null;
    },
    createElement(tagName) {
      return createElement(tagName);
    },
  };

  global.window = {
    AgentGuardPathBuilder: {
      createPathBuilder() {
        let value = { path: "", pathSlots: [], finished: false };
        return {
          getValue() {
            return value;
          },
          setValue(nextValue) {
            value = nextValue || { path: "", pathSlots: [], finished: false };
          },
          validate() {
            return { ok: true };
          },
          clear() {
            value = { path: "", pathSlots: [], finished: false };
          },
          modify() {},
          appendSegment() {},
          finish() {
            value = { ...value, finished: true };
            return { ok: true };
          },
        };
      },
    },
    AgentGuardConditionBuilder: {
      createConditionBuilder() {
        let value = { items: [], symbolToolMap: {}, expression: "" };
        return {
          getValue() {
            return value;
          },
          setPathSymbols() {},
          setCurrentCallToolKey() {},
          setAllowedSourceTypes() {},
          setLocked() {},
          setValue(nextValue) {
            value = {
              items: nextValue?.items || [],
              symbolToolMap: nextValue?.symbolToolMap || {},
              expression: nextValue?.expression || "",
            };
          },
          validate() {
            return { ok: true };
          },
          clear() {
            value = { items: [], symbolToolMap: {}, expression: "" };
          },
        };
      },
    },
    AgentGuardUI: {
      showToast() {},
    },
  };

  delete require.cache[require.resolve("../static/pages/rules/rule-form-controller.js")];
  require("../static/pages/rules/rule-form-controller.js");

  const promptField = createElement("div");
  const rulePromptInput = createElement("textarea");
  promptField.appendChild(rulePromptInput);

  const degradeTargetField = createElement("div");
  const ruleDegradeTargetInput = createSelect();
  degradeTargetField.appendChild(ruleDegradeTargetInput);

  const onField = createElement("div");
  const ruleOnSubtypeInput = createSelect();
  const ruleOnInput = createSelect();
  onField.appendChild(ruleOnSubtypeInput);
  onField.appendChild(ruleOnInput);

  const pathField = createElement("div");
  const rulePreviewBlock = createElement("pre");

  const elements = {
    ruleNameInput: createElement("input"),
    ruleActionInput: createSelect(),
    rulePromptInput,
    ruleDegradeTargetInput,
    ruleDescriptionInput: createElement("textarea"),
    ruleOnSubtypeInput,
    ruleOnInput,
    ruleSeverityInput: createSelect(),
    ruleCategoryInput: createElement("input"),
    ruleReasonInput: createElement("textarea"),
    pathField,
    onField,
    promptField,
    degradeTargetField,
    generateRuleButton: createElement("button"),
    checkRuleButton: createElement("button"),
    clearRuleFormButton: createElement("button"),
    pathContinueButton,
    pathFinishButton: createElement("button"),
    pathContinueButtonIcon: pathContinueIcon,
    addConditionButton: createElement("button"),
    rulePreviewBlock,
  };

  const controller = global.window.AgentGuardRuleFormController.create({
    elements,
    toolData: {
      loadToolCatalog() {
        return [];
      },
    },
    toolCatalogHelpers: {},
    uiHelpers: {},
    shell: {
      getState() {
        return { selectedAgentId: "" };
      },
    },
    model: {
      pathSymbolsFromState(pathState) {
        return Array.isArray(pathState?.pathSlots) ? pathState.pathSlots.map((slot) => slot.label || slot.value).filter(Boolean) : [];
      },
      normalizeRule(rule) {
        return {
          name: String(rule?.name || "").trim(),
          action: String(rule?.action || "").trim(),
          path: String(rule?.path || "").trim(),
          pathSlots: Array.isArray(rule?.pathSlots) ? rule.pathSlots : [],
          condition: String(rule?.condition || "").trim(),
          conditionItems: Array.isArray(rule?.conditionItems) ? rule.conditionItems : [],
          symbolToolMap: rule?.symbolToolMap || {},
          onClause: String(rule?.onClause || "").trim(),
          severity: String(rule?.severity || "").trim(),
          category: String(rule?.category || "").trim(),
          reason: String(rule?.reason || "").trim(),
          prompt: String(rule?.prompt || "").trim(),
          description: String(rule?.description || "").trim(),
          degradeTarget: String(rule?.degradeTarget || "").trim(),
        };
      },
    },
    onClause: {
      buildOnClause() {
        return "";
      },
      parseOnClauseParts() {
        return { subtype: "", toolPattern: "" };
      },
      deriveOnClause(rule) {
        return String(rule?.onClause || "").trim();
      },
    },
    preview: {
      buildPreview(rule) {
        return JSON.stringify(rule);
      },
    },
    validation: {
      validateRuleData() {
        return { ok: true };
      },
    },
  });

  controller.initialize();
  controller.initEventHandlers({
    onGenerateRule() {},
    onCheckRule() {},
    onClearRuleForm() {},
  });

  return { controller, elements };
}

test("rule form controller shows prompt only for llm_check and preserves its value when hidden", () => {
  const { controller, elements } = setupController();

  controller.writeRuleFormState({
    name: "review_external_http",
    action: "LLM_CHECK",
    description: "",
    prompt: "Escalate ambiguous outbound HTTP requests.",
    onSubtype: "",
    onToolKey: "",
    severity: "",
    category: "",
    reason: "",
    degradeTargetKey: "",
    path: { path: "", pathSlots: [], finished: false },
    condition: { items: [], symbolToolMap: {}, expression: "" },
  });

  assert.equal(elements.promptField.hidden, false);
  assert.equal(elements.rulePromptInput.value, "Escalate ambiguous outbound HTTP requests.");

  elements.ruleActionInput.value = "DENY";
  elements.ruleActionInput.dispatchEvent("change");

  assert.equal(elements.promptField.hidden, true);
  assert.equal(elements.rulePromptInput.value, "Escalate ambiguous outbound HTTP requests.");
});

test("rule form controller clears prompt on reset", () => {
  const { controller, elements } = setupController();

  controller.writeRuleFormState({
    name: "review_external_http",
    action: "LLM_CHECK",
    description: "",
    prompt: "Escalate ambiguous outbound HTTP requests.",
    onSubtype: "",
    onToolKey: "",
    severity: "",
    category: "",
    reason: "",
    degradeTargetKey: "",
    path: { path: "", pathSlots: [], finished: false },
    condition: { items: [], symbolToolMap: {}, expression: "" },
  });

  controller.resetRuleForm();

  assert.equal(elements.rulePromptInput.value, "");
  assert.equal(elements.promptField.hidden, true);
});

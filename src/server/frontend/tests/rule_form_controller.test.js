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
          setCurrentCallSubtype() {},
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
        return [{ tool_key: "tool://mailer", name: "email.send" }];
      },
      findToolByKey(catalog, toolKey) {
        return (Array.isArray(catalog) ? catalog : []).find((tool) => tool?.tool_key === toolKey) || null;
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
      buildOnClause(subtype, toolName) {
        const normalizedSubtype = String(subtype || "").trim();
        const normalizedToolName = String(toolName || "").trim();
        if (normalizedSubtype && normalizedToolName) {
          return `tool_call.${normalizedSubtype}(${normalizedToolName})`;
        }
        if (normalizedSubtype) {
          return `tool_call.${normalizedSubtype}`;
        }
        if (normalizedToolName) {
          return `tool_call(${normalizedToolName})`;
        }
        return "";
      },
      parseOnClauseParts(value) {
        const source = String(value || "").trim();
        const matched = source.match(/^tool_call(?:\.([A-Za-z_][A-Za-z0-9_]*))?(?:\(([A-Za-z_][A-Za-z0-9_.]*|[A-Za-z_][A-Za-z0-9_]*\.\*)\))?$/);
        if (!matched) {
          return { subtype: "", toolPattern: "" };
        }
        return {
          subtype: String(matched[1] || "").trim(),
          toolPattern: String(matched[2] || "").trim(),
        };
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

test("rule form controller keeps ON inputs visible in trace mode", () => {
  const { controller, elements } = setupController();

  controller.resetRuleForm();

  assert.equal(elements.onField.hidden, false);
  assert.equal(elements.pathField.hidden, false);
});

test("rule form controller does not clear trace-mode ON selections during preview refresh", () => {
  const { controller, elements } = setupController();

  elements.ruleOnSubtypeInput.value = "requested";
  elements.ruleOnInput.value = "tool://mailer";

  controller.renderPreview();

  assert.equal(elements.ruleOnSubtypeInput.value, "requested");
  assert.equal(elements.ruleOnInput.value, "tool://mailer");
});

test("rule form controller includes ON clause in trace mode rules", () => {
  const { controller, elements } = setupController();

  elements.ruleNameInput.value = "trace_with_on";
  elements.ruleOnSubtypeInput.value = "requested";
  elements.ruleOnInput.value = "tool://mailer";

  const rule = controller.currentRule();

  assert.equal(rule.entryMode, "trace");
  assert.equal(rule.onClause, "tool_call.requested(email.send)");
});

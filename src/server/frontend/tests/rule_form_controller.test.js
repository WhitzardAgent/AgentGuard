const test = require("node:test");
const assert = require("node:assert/strict");

function createElement(tagName = "div") {
  let innerHTML = "";
  const element = {
    tagName: String(tagName).toUpperCase(),
    value: "",
    textContent: "",
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
        if (child.selected) {
          this.value = child.value;
        }
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
  Object.defineProperty(element, "innerHTML", {
    configurable: true,
    enumerable: true,
    get() {
      return innerHTML;
    },
    set(value) {
      innerHTML = String(value || "");
      element.options = [];
      element.children = [];
    },
  });
  return element;
}

function createSelect() {
  const element = createElement("select");
  element.appendChild(createElement("option"));
  return element;
}

function createCheckbox(value) {
  const element = createElement("input");
  element.type = "checkbox";
  element.value = value;
  element.checked = false;
  return element;
}

function createRadio(value, checked = false) {
  const element = createElement("input");
  element.type = "radio";
  element.name = "rule-match-mode";
  element.value = value;
  element.checked = checked;
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
  const rulePhaseButton = createElement("button");
  const rulePhaseSummary = createElement("span");
  const rulePhaseMenu = createElement("div");
  rulePhaseButton.appendChild(rulePhaseSummary);
  const rulePhaseInputs = [
    createCheckbox("llm_before"),
    createCheckbox("llm_after"),
    createCheckbox("tool_before"),
    createCheckbox("tool_after"),
  ];
  rulePhaseInputs.forEach((input) => {
    rulePhaseMenu.appendChild(input);
  });

  const pathField = createElement("div");
  const rulePreviewBlock = createElement("pre");
  const matchModeInputs = [
    createRadio("on", false),
    createRadio("trace", true),
  ];

  const elements = {
    matchModeInputs,
    ruleNameInput: createElement("input"),
    ruleActionInput: createSelect(),
    rulePromptInput,
    ruleDegradeTargetInput,
    ruleDescriptionInput: createElement("textarea"),
    rulePhaseButton,
    rulePhaseSummary,
    rulePhaseMenu,
    rulePhaseInputs,
    ruleOnButton: null,
    ruleOnSummary: null,
    ruleOnMenu: null,
    ruleOnInput: null,
    ruleSeverityInput: createSelect(),
    ruleCategoryInput: createElement("input"),
    ruleReasonInput: createElement("textarea"),
    pathField,
    onField,
    onToolFilterRow: null,
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
      getPageCopy(key, fallback, variables = {}) {
        const dictionary = {
          "rules-guided-builder": "引导式规则构建器",
          "rules-create-subtitle": "按步骤创建新规则。",
          "rules-select-phases": "选择运行阶段",
          "rules-all-phases": "所有运行阶段",
          "rules-phase-selected": "{count} 个阶段已选择",
          "rules-default-description": "用这个字段记录面向运维人员的规则说明。",
          "rules-edit-path": "编辑路径",
          "rules-add-path": "添加路径段",
        };
        const template = dictionary[key] || String(fallback || "");
        return String(template).replace(/\{(\w+)\}/g, (match, name) => (Object.prototype.hasOwnProperty.call(variables, name) ? String(variables[name] ?? "") : match));
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
          phases: Array.isArray(rule?.phases) ? rule.phases : [],
        };
      },
    },
    onClause: {
      buildOnClause(toolName, _subtype = "", options = {}) {
        const normalizedToolName = String(toolName || "").trim();
        const effectiveToolName = normalizedToolName || (options?.wildcardWhenEmpty ? "*" : "");
        return effectiveToolName ? `tool_call(${effectiveToolName})` : "";
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
    phases: ["llm_before"],
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
    phases: ["llm_before"],
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

test("rule form controller keeps phase selection visible and only reveals TRACE matching for tool phases", () => {
  const { controller, elements } = setupController();

  controller.resetRuleForm();

  assert.equal(elements.onField.hidden, false);
  assert.equal(elements.pathField.hidden, true);

  elements.rulePhaseInputs[2].checked = true;
  elements.rulePhaseInputs[2].dispatchEvent("change");

  assert.equal(elements.onField.hidden, false);
  assert.equal(elements.pathField.hidden, false);
});

test("rule form controller keeps ON clause empty in tool-phase on mode", () => {
  const { controller, elements } = setupController();

  controller.resetRuleForm();
  elements.matchModeInputs[0].checked = true;
  elements.matchModeInputs[1].checked = false;
  elements.matchModeInputs[0].dispatchEvent("change");
  elements.rulePhaseInputs[2].checked = true;
  elements.rulePhaseInputs[2].dispatchEvent("change");

  assert.equal(controller.currentRule().onClause, "");
});

test("rule form controller summarizes selected phases in the phase dropdown", () => {
  const { controller, elements } = setupController();

  controller.resetRuleForm();
  assert.equal(elements.rulePhaseSummary.textContent, "选择运行阶段");

  elements.rulePhaseInputs[0].checked = true;
  elements.rulePhaseInputs[0].dispatchEvent("change");
  assert.equal(elements.rulePhaseSummary.textContent, "llm_before");

  elements.rulePhaseInputs[1].checked = true;
  elements.rulePhaseInputs[1].dispatchEvent("change");
  assert.equal(elements.rulePhaseSummary.textContent, "llm_before, llm_after");
});

test("rule form controller preview refresh keeps phase-driven state stable without ON controls", () => {
  const { controller, elements } = setupController();

  elements.rulePhaseInputs[2].checked = true;

  controller.renderPreview();

  assert.equal(controller.currentRule().onClause, "");
});

test("rule form controller does not include ON clause in trace mode rules", () => {
  const { controller, elements } = setupController();

  elements.ruleNameInput.value = "trace_with_on";
  elements.rulePhaseInputs[2].checked = true;

  const rule = controller.currentRule();

  assert.equal(rule.entryMode, "trace");
  assert.deepEqual(rule.phases, ["tool_before"]);
  assert.equal(rule.onClause, "");
});


test("rule form controller reads builder copy from shell page copy", () => {
  const { controller, elements } = setupController();

  controller.resetRuleForm();
  controller.renderPreview();

  assert.equal(elements.ruleDescriptionInput.value, "用这个字段记录面向运维人员的规则说明。");
  assert.equal(elements.pathContinueButton.attributes["aria-label"], "添加路径段");
  assert.equal(elements.pathContinueButton.attributes.title, "添加路径段");
});

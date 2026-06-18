function resolveRuleModule(globalName, requirePath) {
  if (typeof window !== "undefined" && window[globalName]) {
    return window[globalName];
  }
  if (typeof require === "function") {
    require(requirePath);
    if (typeof window !== "undefined" && window[globalName]) {
      return window[globalName];
    }
  }
  throw new Error(`Missing rule module ${globalName}.`);
}

const storage = window.AgentGuardRuleStorage;
const ruleDsl = window.AgentGuardRuleDSL;
const ruleParser = window.AgentGuardRuleParser || {};
const toolCatalogHelpers = window.AgentGuardToolCatalog || {};
const uiHelpers = window.AgentGuardUIHelpers || {};
const toolData = window.AgentGuardData;
const api = window.AgentGuardApi;
const shell = window.AgentGuardShell;

const ruleUtils = resolveRuleModule("AgentGuardRuleUtils", "./rule-utils.js");
const ruleOnClause = resolveRuleModule("AgentGuardRuleOnClause", "./rule-on-clause.js");
const ruleModel = resolveRuleModule("AgentGuardRuleModel", "./rule-model.js");
const ruleValidation = resolveRuleModule("AgentGuardRuleValidation", "./rule-validation.js");
const rulePreview = resolveRuleModule("AgentGuardRulePreview", "./rule-preview.js");
const ruleServiceModule = resolveRuleModule("AgentGuardRuleService", "./rule-service.js");
const ruleStoreModule = resolveRuleModule("AgentGuardGeneratedRuleStore", "./rule-store.js");
const ruleFormControllerModule = resolveRuleModule("AgentGuardRuleFormController", "./rule-form-controller.js");
const ruleListControllerModule = resolveRuleModule("AgentGuardRuleListController", "./rule-list-controller.js");

const parseConditionItems = ruleParser.parseConditionItems || function fallbackParseConditionItems() {
  return [];
};

const parsePublishedRuleSource = function parsePublishedRuleSourceWrapper(source) {
  if (!ruleParser.parsePublishedRuleSource) {
    return null;
  }
  return ruleParser.parsePublishedRuleSource(
    source,
    ruleModel.normalizeRule,
    ruleUtils.RULE_STATUS_PUBLISHED,
  );
};

const extractPublishedRuleSource = ruleParser.extractPublishedRuleSource || function fallbackExtractPublishedRuleSource(source) {
  return String(source || "").trim();
};

const extractRuleMetadata = ruleParser.extractRuleMetadata || function fallbackExtractRuleMetadata() {
  return { onClause: "", severity: "", category: "", reason: "", prompt: "" };
};

const actionTone = uiHelpers.actionTone || function fallbackActionTone(action) {
  const normalized = String(action || "").trim().toUpperCase();
  if (normalized === "DENY") {
    return "danger";
  }
  if (normalized === "HUMAN_CHECK" || normalized === "LLM_CHECK" || normalized === "DEGRADE") {
    return "warn";
  }
  return "";
};

function queryElement(selector) {
  if (typeof document === "undefined" || typeof document.querySelector !== "function") {
    return null;
  }
  return document.querySelector(selector);
}

function queryElements(selector) {
  if (typeof document === "undefined" || typeof document.querySelectorAll !== "function") {
    return [];
  }
  return Array.from(document.querySelectorAll(selector));
}

function getElement(id) {
  if (typeof document === "undefined" || typeof document.getElementById !== "function") {
    return null;
  }
  return document.getElementById(id);
}

function queryChild(element, selector) {
  if (!element || typeof element.querySelector !== "function") {
    return null;
  }
  return element.querySelector(selector);
}

const pathContinueButton = getElement("path-continue-button");

const elements = {
  ruleGeneratorCard: queryElement(".rule-generator-card"),
  ruleBuilderTitle: getElement("rule-builder-title"),
  ruleBuilderSubtitle: getElement("rule-builder-subtitle"),
  returnToWizardButton: getElement("return-to-wizard-button"),
  ruleBuilderStepper: getElement("rule-builder-stepper"),
  ruleStepButtons: queryElements(".rule-step-chip"),
  wizardStepCards: queryElements(".wizard-step-card"),
  wizardPrevButtons: queryElements("[data-prev-step]"),
  wizardNextButtons: queryElements("[data-next-step]"),
  matchModeInputs: queryElements("input[name='rule-match-mode']"),
  ruleBuilderActions: queryElement(".rule-builder-actions"),
  ruleNameInput: getElement("rule-name-input"),
  ruleActionInput: getElement("rule-action-input"),
  rulePromptInput: getElement("rule-prompt-input"),
  ruleDegradeTargetInput: getElement("rule-degrade-target-input"),
  ruleDescriptionInput: getElement("rule-description-input"),
  ruleOnSubtypeInput: getElement("rule-on-subtype-input"),
  ruleOnInput: getElement("rule-on-input"),
  ruleSeverityInput: getElement("rule-severity-input"),
  ruleCategoryInput: getElement("rule-category-input"),
  ruleReasonInput: getElement("rule-reason-input"),
  traceOnFieldHint: getElement("trace-on-field-hint"),
  pathField: getElement("path-field"),
  onField: getElement("on-field"),
  promptField: getElement("prompt-field"),
  degradeTargetField: getElement("degrade-target-field"),
  generateRuleButton: getElement("generate-rule-button"),
  checkRuleButton: getElement("check-rule-button"),
  clearRuleFormButton: getElement("clear-rule-form-button"),
  pathContinueButton,
  pathFinishButton: getElement("path-finish-button"),
  pathContinueButtonIcon: queryChild(pathContinueButton, "img"),
  addConditionButton: getElement("add-condition-button"),
  conditionBuilderStepModeButton: getElement("condition-builder-step-mode-button"),
  conditionBuilderDirectModeButton: getElement("condition-builder-direct-mode-button"),
  conditionBuilderModeCopy: getElement("condition-builder-mode-copy"),
  rulePreviewBlock: getElement("rule-preview-block"),
  ruleList: getElement("rule-list"),
  ruleFilterButtons: queryElements(".rule-list-filter .filter-chip"),
};

const state = {
  selectedAgentId: shell?.getState?.().selectedAgentId || "",
  generatedRules: [],
  activeRules: [],
  filter: "all",
  busy: false,
};

shell?.setPageContext({
  title: "Rule Builder",
  description: "Build rules from structured inputs, preview DSL output, and manage unpublished and published states.",
});

function showToast(message, tone) {
  window.AgentGuardUI.showToast(message, tone);
}

function normalizeActiveRule(rule) {
  const name = String(rule?.name || rule?.rule_id || rule?.id || "").trim();
  const ruleSource = extractPublishedRuleSource(rule?.source || "", name);
  const metadata = extractRuleMetadata(ruleSource);
  const path = String(ruleSource || "").match(/^TRACE:\s+(.+)$/m)?.[1]?.trim() || "";
  return ruleUtils.withRuleStatus({
    id: String(rule?.id || name).trim(),
    name,
    rule_id: name,
    entryMode: metadata.onClause ? "on" : "trace",
    path,
    tool_pattern: String(rule?.tool_pattern || "*").trim() || "*",
    action: String(rule?.action || "").trim().toUpperCase(),
    version: String(rule?.version || "unknown").trim() || "unknown",
    source: String(ruleSource || rule?.source || "").trim(),
    packId: String(rule?.packId || rule?.pack_id || "").trim(),
    userManaged: typeof rule?.userManaged === "boolean"
      ? rule.userManaged
      : typeof rule?.user_managed === "boolean"
        ? rule.user_managed
        : false,
    onClause: metadata.onClause,
    severity: ruleUtils.normalizeSeverityValue(rule?.severity || metadata.severity),
    category: String(rule?.category || metadata.category || "").trim(),
    reason: String(rule?.reason || metadata.reason || "").trim(),
    prompt: String(rule?.prompt || metadata.prompt || "").trim(),
    description: String(rule?.description || "").trim(),
    status: ruleUtils.RULE_STATUS_PUBLISHED,
  }, ruleUtils.RULE_STATUS_PUBLISHED);
}

function ruleSourceLabel(rule, status) {
  if (status === ruleUtils.RULE_STATUS_UNPUBLISHED) {
    return "Local draft";
  }
  if (!rule?.userManaged) {
    return "Built-in";
  }
  const packId = String(rule?.packId || rule?.pack_id || "").trim();
  if (!packId || packId === "__default__") {
    return "Default pack";
  }
  if (packId.startsWith("agent::")) {
    return "Agent runtime";
  }
  return `Pack: ${packId}`;
}

function normalizeStoredLocalRule(rule) {
  const entryMode = ruleUtils.normalizeEntryModeValue(rule);
  const normalized = ruleModel.normalizeRule(ruleUtils.withRuleStatus(rule, ruleUtils.RULE_STATUS_UNPUBLISHED));
  return ruleUtils.withRuleStatus({ ...normalized, entryMode }, ruleUtils.RULE_STATUS_UNPUBLISHED);
}

function createRuleActionButton(iconPath, label, onClick) {
  return (uiHelpers.createIconButton || function fallbackCreateIconButton(_, ariaLabel, clickHandler, options = {}) {
    const nextButton = document.createElement("button");
    nextButton.type = "button";
    nextButton.className = String(options.className || "");
    nextButton.setAttribute("aria-label", ariaLabel);
    nextButton.setAttribute("title", options.title || ariaLabel);
    const icon = document.createElement("img");
    icon.className = String(options.iconClassName || "btn-icon-image");
    icon.src = options.iconPathPrefix ? `${options.iconPathPrefix}${iconPath.split("/").pop()}` : iconPath;
    icon.alt = "";
    nextButton.appendChild(icon);
    nextButton.addEventListener("click", clickHandler);
    return nextButton;
  })(
    iconPath.split("/").pop(),
    label,
    (event) => {
      event.stopPropagation();
      if (state.busy) {
        return;
      }
      onClick();
    },
    {
      className: "rule-action-button",
      iconClassName: "btn-icon-image",
      iconPathPrefix: "/assets/",
      title: label,
    },
  );
}

const form = ruleFormControllerModule.create({
  elements,
  model: ruleModel,
  onClause: ruleOnClause,
  preview: rulePreview,
  shell,
  toolCatalogHelpers,
  toolData,
  uiHelpers,
  validation: ruleValidation,
});

const service = ruleServiceModule.create({
  api,
  normalizeActiveRule,
  normalizeRule: ruleModel.normalizeRule,
  parser: {
    extractPublishedRuleSource,
  },
  publishedStatus: ruleUtils.RULE_STATUS_PUBLISHED,
  ruleDsl,
  ruleKey: ruleUtils.ruleKey,
  summarizeCheckReport: ruleValidation.summarizeCheckReport,
  unpublishedStatus: ruleUtils.RULE_STATUS_UNPUBLISHED,
  validateCurrentRuleForm: form.validateCurrentRuleForm,
  validateRuleData: ruleValidation.validateRuleData,
  withRuleStatus: ruleUtils.withRuleStatus,
});

const store = ruleStoreModule.create({
  normalizeRule: ruleModel.normalizeRule,
  normalizeStoredLocalRule,
  ruleKey: ruleUtils.ruleKey,
  storage,
  unpublishedStatus: ruleUtils.RULE_STATUS_UNPUBLISHED,
  withRuleStatus: ruleUtils.withRuleStatus,
});

function ruleItems() {
  return [
    ...state.generatedRules.map((rule) => ({ status: rule.status, rule })),
    ...state.activeRules.map((rule) => ({ status: rule.status, rule })),
  ];
}

function renderRuleList() {
  list.render(ruleItems());
}

function setRuleFilter(nextFilter) {
  state.filter = nextFilter;
  list.setFilter(nextFilter);
  renderRuleList();
}

const list = ruleListControllerModule.create({
  actionTone,
  buildRuleListSource: rulePreview.buildRuleListSource,
  createRuleActionButton,
  filterRuleItems: ruleUtils.filterRuleItems,
  onDeleteLocalRule(rule) {
    deleteLocalRule(rule);
  },
  onDisableRule(rule) {
    disableRule(rule);
  },
  onPublishRule(rule) {
    publishRule(rule);
  },
  onSelectRule(rule) {
    const status = String(rule?.status || "").trim();
    if (status === ruleUtils.RULE_STATUS_PUBLISHED) {
      const parsed = parsePublishedRuleSource(
        extractPublishedRuleSource(rule.source, rule.rule_id || rule.name || rule.id || ""),
      );
      if (!parsed) {
        showToast(`Rule ${ruleUtils.ruleDisplayName(rule)} uses constructs that the editor cannot reconstruct yet.`, "warning");
        return;
      }
      form.applyRule({
        ...parsed,
        id: rule.id,
        source: rule.source,
        userManaged: rule.userManaged,
      });
      showToast(`Loaded published rule ${ruleUtils.ruleDisplayName(rule)} into the editor.`, "success");
      return;
    }
    form.applyRule(rule);
    showToast(`Loaded unpublished rule ${ruleUtils.ruleDisplayName(rule)} into the editor.`, "success");
  },
  publishedStatus: ruleUtils.RULE_STATUS_PUBLISHED,
  ruleDisplayName: ruleUtils.ruleDisplayName,
  ruleSourceLabel,
  ruleFilterButtons: elements.ruleFilterButtons,
  ruleList: elements.ruleList,
  unpublishedStatus: ruleUtils.RULE_STATUS_UNPUBLISHED,
});

function setBusy(isBusy) {
  state.busy = Boolean(isBusy);
  form.setBusy(state.busy);
}

function upsertGeneratedRule(rule) {
  const nextRule = store.upsert(rule);
  state.generatedRules = store.list();
  return nextRule;
}

function removeGeneratedRule(rule) {
  state.generatedRules = store.remove(rule);
}

async function checkCurrentRule({ saveGeneratedRule = false } = {}) {
  const rule = ruleUtils.withRuleStatus(ruleModel.normalizeRule(form.currentRule()), ruleUtils.RULE_STATUS_UNPUBLISHED);
  setBusy(true);

  try {
    const checked = await service.checkRule(rule, { validateWithForm: true });
    if (!checked.ok) {
      showToast(checked.message || "Rule validation failed.", "warning");
      return false;
    }

    const reportSummary = ruleValidation.summarizeCheckReport(checked.report);
    if (saveGeneratedRule) {
      upsertGeneratedRule(rule);
      form.renderPreview();
      renderRuleList();
      setRuleFilter(ruleUtils.RULE_STATUS_UNPUBLISHED);
      showToast(`Rule ${rule.name} passed check and was saved as an unpublished rule.`, "success");
      return true;
    }

    if (reportSummary.warningCount || reportSummary.hintCount) {
      showToast(
        `Rule check passed with ${reportSummary.warningCount} warning(s) and ${reportSummary.hintCount} hint(s).`,
        "success",
      );
    } else {
      showToast("Rule check passed.", "success");
    }
    return true;
  } catch (error) {
    showToast(error instanceof Error ? error.message : "Failed to check rule.", "warning");
    return false;
  } finally {
    setBusy(false);
  }
}

async function generateRule() {
  return checkCurrentRule({ saveGeneratedRule: true });
}

async function publishRule(ruleInput, { validateWithForm = false } = {}) {
  if (!state.selectedAgentId) {
    showToast("Select an agent before publishing a rule.", "warning");
    return false;
  }
  const rule = ruleUtils.withRuleStatus(ruleModel.normalizeRule(ruleInput), ruleUtils.RULE_STATUS_UNPUBLISHED);
  let source = "";
  try {
    source = ruleDsl.serializeRule(rule);
  } catch (error) {
    showToast(error instanceof Error ? error.message : "Failed to build DSL source.", "warning");
    return false;
  }

  setBusy(true);

  try {
    const checked = await service.checkRule(rule, { validateWithForm, source });
    if (!checked.ok) {
      showToast(checked.message || "Rule validation failed.", "warning");
      return false;
    }

    await service.createAgentRule(state.selectedAgentId, source);
    removeGeneratedRule(rule);
    await refreshActiveRules({ silent: true });
    setRuleFilter(ruleUtils.RULE_STATUS_PUBLISHED);
    showToast(`Published rule ${rule.name} to the runtime.`, "success");
    return true;
  } catch (error) {
    showToast(error instanceof Error ? error.message : "Failed to publish rules.", "warning");
    return false;
  } finally {
    setBusy(false);
  }
}

async function disableRule(ruleInput) {
  if (!state.selectedAgentId) {
    showToast("Select an agent before deleting a published rule.", "warning");
    return false;
  }
  const publishedRule = normalizeActiveRule(ruleInput);
  if (!publishedRule.userManaged) {
    showToast(`Rule ${publishedRule.name} was loaded at startup and cannot be disabled here.`, "warning");
    return false;
  }

  setBusy(true);

  try {
    await service.deleteAgentRule(
      state.selectedAgentId,
      publishedRule.rule_id || publishedRule.name || publishedRule.id || "",
    );
    const restoredPublishedRule = parsePublishedRuleSource(
      extractPublishedRuleSource(
        publishedRule.source,
        publishedRule.rule_id || publishedRule.name || publishedRule.id || "",
      ),
    );
    if (!restoredPublishedRule) {
      throw new Error("Failed to restore the disabled published rule back into the local rule list.");
    }

    upsertGeneratedRule({
      ...restoredPublishedRule,
      id: publishedRule.id,
      name: publishedRule.name,
      action: publishedRule.action,
      description: publishedRule.description,
      source: publishedRule.source,
      status: ruleUtils.RULE_STATUS_UNPUBLISHED,
    });
    await refreshActiveRules({ silent: true });
    setRuleFilter(ruleUtils.RULE_STATUS_UNPUBLISHED);
    showToast(`Disabled rule ${publishedRule.name}.`, "success");
    return true;
  } catch (error) {
    showToast(error instanceof Error ? error.message : "Failed to disable rule.", "warning");
    return false;
  } finally {
    setBusy(false);
  }
}

function deleteLocalRule(ruleInput) {
  const rule = ruleUtils.withRuleStatus(ruleModel.normalizeRule(ruleInput), ruleUtils.RULE_STATUS_UNPUBLISHED);
  if (!rule.userManaged) {
    showToast(`Rule ${rule.name} was not created in this workspace and cannot be deleted here.`, "warning");
    return;
  }
  removeGeneratedRule(rule);
  renderRuleList();
  showToast(`Deleted unpublished rule ${rule.name}.`, "success");
}

async function refreshActiveRules({ silent = false } = {}) {
  if (!state.selectedAgentId) {
    state.activeRules = [];
    renderRuleList();
    return;
  }

  try {
    state.activeRules = await service.listActive(state.selectedAgentId);
    renderRuleList();
    if (!silent) {
      showToast("Active rules refreshed.", "success");
    }
  } catch (error) {
    state.activeRules = [];
    renderRuleList();
    if (!silent) {
      showToast(error instanceof Error ? error.message : "Failed to load active rules.", "warning");
    }
  }
}

list.initFilterButtons((nextFilter) => {
  state.filter = nextFilter;
  renderRuleList();
});

form.onChange(() => {
  // Keep preview-driven UI updates local; list rendering remains explicit.
});

form.initEventHandlers({
  onCheckRule() {
    checkCurrentRule();
  },
  onClearRuleForm() {
    form.prepareNewRule();
    showToast("Guided rule builder reset.", "success");
  },
  onGenerateRule() {
    generateRule();
  },
});

state.generatedRules = store.load();
form.initialize();
form.prepareNewRule();
list.setFilter(state.filter);
renderRuleList();

if (typeof window !== "undefined" && typeof window.addEventListener === "function") {
  window.addEventListener("agentguard:selected-agent-change", (event) => {
    state.selectedAgentId = String(event?.detail?.agentId || "").trim();
    state.activeRules = [];
    form.setSelectedAgent(state.selectedAgentId);
    renderRuleList();
    form.renderOnToolOptions([], "");
    form.renderDegradeTargetOptions([], "");
    if (!state.selectedAgentId) {
      return;
    }
    form.refreshToolOptions();
    refreshActiveRules({ silent: true });
  });
}

if (state.selectedAgentId) {
  form.refreshToolOptions();
  refreshActiveRules({ silent: true });
}

window.AgentGuardRules = {
  RULE_STATUS_PUBLISHED: ruleUtils.RULE_STATUS_PUBLISHED,
  RULE_STATUS_UNPUBLISHED: ruleUtils.RULE_STATUS_UNPUBLISHED,
  buildPublishedSourceWithout: service.buildPublishedSourceWithout,
  buildRuleListSource: rulePreview.buildRuleListSource,
  checkCurrentRule,
  checkRule: service.checkRule,
  checkRuleSource: service.checkSource,
  disableRule,
  extractPublishedRuleSource,
  extractRuleMetadata,
  filterRuleItems: ruleUtils.filterRuleItems,
  generateRule,
  normalizeActiveRule,
  normalizeEntryModeValue: ruleUtils.normalizeEntryModeValue,
  normalizeSeverityValue: ruleUtils.normalizeSeverityValue,
  normalizeStoredLocalRule,
  parseConditionItems,
  parsePublishedRuleSource,
  publishRule,
  publishedRulesSourceWith: service.publishedRulesSourceWith,
  ruleDisplayName: ruleUtils.ruleDisplayName,
  ruleKey: ruleUtils.ruleKey,
  severityOptions: ruleUtils.severityOptions,
  withRuleStatus: ruleUtils.withRuleStatus,
};

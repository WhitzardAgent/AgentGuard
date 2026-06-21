(function () {
  function createRuleFormController(options) {
    const {
      elements,
      toolData,
      toolCatalogHelpers,
      shell,
      model,
      onClause,
      preview,
      validation,
    } = options;
    const {
      ruleGeneratorCard,
      ruleBuilderTitle,
      ruleBuilderSubtitle,
      returnToWizardButton,
      ruleStepButtons,
      wizardStepCards,
      wizardPrevButtons,
      wizardNextButtons,
      matchModeInputs,
      ruleBuilderActions,
      ruleNameInput,
      ruleActionInput,
      rulePromptInput,
      ruleDegradeTargetInput,
      ruleDescriptionInput,
      ruleOnSubtypeInput,
      ruleOnInput,
      ruleSeverityInput,
      ruleCategoryInput,
      ruleReasonInput,
      traceOnFieldHint,
      pathField,
      onField,
      promptField,
      degradeTargetField,
      generateRuleButton,
      checkRuleButton,
      pathContinueButton,
      pathFinishButton,
      pathContinueButtonIcon,
      addConditionButton,
      conditionBuilderStepModeButton,
      conditionBuilderDirectModeButton,
      conditionBuilderModeCopy,
      rulePreviewBlock,
    } = elements;

    const STEP_COUNT = 4;
    let selectedAgentId = String(shell?.getState?.().selectedAgentId || "").trim();
    let ruleActionInFlight = false;
    let changeHandler = function noop() {};
    let builderMode = "create";
    let currentStep = 1;

    function currentToolCatalog() {
      return toolData?.loadToolCatalog?.(selectedAgentId) || [];
    }

    function matchingMode() {
      const selected = (matchModeInputs || []).find((input) => input.checked);
      return String(selected?.value || "trace").trim() || "trace";
    }

    function setMatchingMode(nextMode) {
      const normalized = ["on", "trace"].includes(String(nextMode || "").trim())
        ? String(nextMode || "").trim()
        : "trace";
      (matchModeInputs || []).forEach((input) => {
        input.checked = input.value === normalized;
      });
      syncBuilderUI();
      syncConditionLock(pathBuilder.getValue());
      syncWizardUI();
      renderPreview();
    }

    function toolDisplayName(tool, catalog = currentToolCatalog()) {
      if (typeof toolCatalogHelpers.toolDisplayName === "function") {
        return toolCatalogHelpers.toolDisplayName(tool, catalog);
      }
      if (!tool) {
        return "";
      }
      const duplicates = (Array.isArray(catalog) ? catalog : []).filter((item) => item?.name === tool.name);
      return duplicates.length > 1 ? `${tool.owner_agent_id} / ${tool.name}` : String(tool.name || "").trim();
    }

    function toolKeyForName(toolName, catalog = currentToolCatalog()) {
      if (typeof toolCatalogHelpers.toolKeyForName === "function") {
        return toolCatalogHelpers.toolKeyForName(toolName, catalog);
      }
      const normalizedName = String(toolName || "").trim();
      if (!normalizedName) {
        return "";
      }
      const match = (Array.isArray(catalog) ? catalog : []).find((tool) => tool?.name === normalizedName);
      return String(match?.tool_key || "").trim();
    }

    function toolNameForKey(toolKey, catalog = currentToolCatalog()) {
      if (typeof toolCatalogHelpers.toolNameForKey === "function") {
        return toolCatalogHelpers.toolNameForKey(toolKey, catalog, toolData?.findToolByKey);
      }
      const normalizedKey = String(toolKey || "").trim();
      if (!normalizedKey) {
        return "";
      }
      const match = typeof toolData?.findToolByKey === "function"
        ? toolData.findToolByKey(Array.isArray(catalog) ? catalog : [], normalizedKey)
        : (Array.isArray(catalog) ? catalog : []).find((tool) => String(tool?.tool_key || "").trim() === normalizedKey);
      return match ? String(match.name || "").trim() : "";
    }

    function currentPathSymbols() {
      return model.pathSymbolsFromState(pathBuilder.getValue());
    }

    function hasFinishedTracePath(pathState = pathBuilder.getValue()) {
      return Boolean(pathState?.finished && Array.isArray(pathState?.pathSlots) && pathState.pathSlots.length);
    }

    function hasCurrentCallFilter() {
      const selectedOnSubtype = String(ruleOnSubtypeInput?.value || "").trim();
      const selectedOnTool = String(ruleOnInput.value || "").trim();
      return Boolean(onClause.buildOnClause(selectedOnSubtype, toolNameForKey(selectedOnTool)));
    }

    function currentCallToolKey() {
      return String(ruleOnInput?.value || "").trim();
    }

    function currentCallSubtype() {
      return String(ruleOnSubtypeInput?.value || "").trim();
    }

    function modeNeedsTrace() {
      const mode = matchingMode();
      return mode === "trace";
    }

    function modeNeedsOn() {
      const mode = matchingMode();
      return mode === "on";
    }

    function modeShowsOnOptions() {
      const mode = matchingMode();
      return mode === "on" || mode === "trace";
    }

    function allowedConditionSourceTypes(pathState = pathBuilder.getValue()) {
      const nextAllowed = [];
      if (modeNeedsTrace() && hasFinishedTracePath(pathState)) {
        nextAllowed.push("trace");
      }
      if (modeNeedsOn() && hasCurrentCallFilter()) {
        nextAllowed.push("context");
      }
      return nextAllowed;
    }

    const pathBuilder = window.AgentGuardPathBuilder.createPathBuilder({
      root: document.getElementById("path-builder-segments"),
      hint: document.getElementById("path-builder-hint"),
      onChange(pathState) {
        conditionBuilder.setPathSymbols(currentPathSymbols());
        syncConditionLock(pathState);
        syncWizardUI();
        renderPreview();
      },
    });

    const conditionBuilder = window.AgentGuardConditionBuilder.createConditionBuilder({
      root: document.getElementById("condition-builder-grid"),
      hint: document.getElementById("condition-builder-hint"),
      addButton: addConditionButton,
      stepModeButton: conditionBuilderStepModeButton,
      directModeButton: conditionBuilderDirectModeButton,
      modeCopy: conditionBuilderModeCopy,
      defaultMode: "step",
      pathSymbols: currentPathSymbols(),
      currentCallToolKey: currentCallToolKey(),
      currentCallSubtype: currentCallSubtype(),
      locked: allowedConditionSourceTypes(pathBuilder.getValue()).length === 0,
      allowedSourceTypes: allowedConditionSourceTypes(pathBuilder.getValue()),
      onChange() {
        syncWizardUI();
        renderPreview();
      },
    });

    function isDegradeAction(action = ruleActionInput.value) {
      return String(action || "").trim().toUpperCase() === "DEGRADE";
    }

    function isLlmCheckAction(action = ruleActionInput.value) {
      return String(action || "").trim().toUpperCase() === "LLM_CHECK";
    }

    function setFieldVisibility(field, visible) {
      if (!field) {
        return;
      }
      field.hidden = !visible;
      field.setAttribute("aria-hidden", visible ? "false" : "true");
      if (typeof field.querySelectorAll !== "function") {
        return;
      }
      field.querySelectorAll("input, select, textarea, button").forEach((element) => {
        element.disabled = !visible;
      });
    }

    function syncBuilderUI() {
      setFieldVisibility(pathField, modeNeedsTrace());
      setFieldVisibility(onField, modeShowsOnOptions());
      if (traceOnFieldHint) {
        traceOnFieldHint.hidden = !modeNeedsTrace();
      }

      const currentValue = String(ruleOnInput.value || "").trim();
      const optionCount = Array.isArray(ruleOnInput.options) || typeof ruleOnInput.options?.length === "number"
        ? ruleOnInput.options.length
        : 0;
      ruleOnInput.disabled = !currentValue && optionCount <= 1;
      if (ruleOnSubtypeInput) {
        ruleOnSubtypeInput.disabled = false;
      }

      syncConditionLock(pathBuilder.getValue());
    }

    function syncConditionLock(pathState = pathBuilder.getValue()) {
      const allowedSources = allowedConditionSourceTypes(pathState);
      if (typeof conditionBuilder.setCurrentCallToolKey === "function") {
        conditionBuilder.setCurrentCallToolKey(currentCallToolKey());
      }
      if (typeof conditionBuilder.setCurrentCallSubtype === "function") {
        conditionBuilder.setCurrentCallSubtype(currentCallSubtype());
      }
      if (typeof conditionBuilder.setAllowedSourceTypes === "function") {
        conditionBuilder.setAllowedSourceTypes(allowedSources);
      }
      if (typeof conditionBuilder.setLocked === "function") {
        conditionBuilder.setLocked(allowedSources.length === 0);
      }
    }

    function renderToolSelectOptions(select, catalog = currentToolCatalog(), selectedTool = "", { emptyLabel, allowEmpty = false } = {}) {
      if (!select) {
        return;
      }
      const tools = typeof toolCatalogHelpers.sortCatalogByDisplayName === "function"
        ? toolCatalogHelpers.sortCatalogByDisplayName(catalog).filter((item) => String(item?.tool_key || "").trim())
        : (Array.isArray(catalog) ? catalog : []);

      select.innerHTML = "";

      const placeholder = document.createElement("option");
      placeholder.value = "";
      placeholder.textContent = tools.length ? (emptyLabel || "Select tool") : "No tools available";
      placeholder.disabled = !allowEmpty;
      placeholder.hidden = !allowEmpty;
      placeholder.selected = !selectedTool;
      select.appendChild(placeholder);

      tools.forEach((tool) => {
        const option = document.createElement("option");
        option.value = tool.tool_key;
        option.textContent = toolDisplayName(tool, catalog);
        option.selected = selectedTool === tool.tool_key;
        select.appendChild(option);
      });

      if (selectedTool && !tools.some((tool) => tool.tool_key === selectedTool)) {
        const fallback = document.createElement("option");
        fallback.value = selectedTool;
        fallback.textContent = `${toolNameForKey(selectedTool, catalog) || selectedTool} (unavailable)`;
        fallback.selected = true;
        select.appendChild(fallback);
      }
    }

    function renderOnToolOptions(catalog = toolData?.loadToolCatalog?.() || [], selectedTool = String(ruleOnInput.value || "").trim()) {
      renderToolSelectOptions(ruleOnInput, catalog, selectedTool, { emptyLabel: "Select tool (optional)", allowEmpty: true });
      syncBuilderUI();
    }

    function syncActionUI(action = ruleActionInput.value) {
      const degradeVisible = isDegradeAction(action);
      const promptVisible = isLlmCheckAction(action);
      setFieldVisibility(degradeTargetField, degradeVisible);
      setFieldVisibility(promptField, promptVisible);
      if (!degradeVisible) {
        ruleDegradeTargetInput.disabled = true;
      } else {
        const currentValue = String(ruleDegradeTargetInput.value || "").trim();
        const optionCount = Array.isArray(ruleDegradeTargetInput.options) || typeof ruleDegradeTargetInput.options?.length === "number"
          ? ruleDegradeTargetInput.options.length
          : 0;
        ruleDegradeTargetInput.disabled = !currentValue && optionCount <= 1;
      }
    }

    function renderDegradeTargetOptions(catalog = toolData?.loadToolCatalog?.() || [], selectedTool = String(ruleDegradeTargetInput.value || "").trim()) {
      renderToolSelectOptions(ruleDegradeTargetInput, catalog, selectedTool, { emptyLabel: "Select target tool" });
      syncActionUI();
    }

    async function refreshToolOptions() {
      if (!toolData?.refreshToolCatalog) {
        return;
      }
      if (!selectedAgentId) {
        renderOnToolOptions([], String(ruleOnInput.value || "").trim());
        renderDegradeTargetOptions([], String(ruleDegradeTargetInput.value || "").trim());
        return;
      }
      try {
        const catalog = await toolData.refreshToolCatalog(selectedAgentId);
        renderOnToolOptions(catalog, String(ruleOnInput.value || "").trim());
        renderDegradeTargetOptions(catalog, String(ruleDegradeTargetInput.value || "").trim());
      } catch (error) {
        renderOnToolOptions([], String(ruleOnInput.value || "").trim());
        renderDegradeTargetOptions([], String(ruleDegradeTargetInput.value || "").trim());
      }
    }

    function readRuleFormState() {
      const path = pathBuilder.getValue();
      const condition = conditionBuilder.getValue();
      return {
        name: ruleNameInput.value.trim(),
        action: ruleActionInput.value || "",
        description: ruleDescriptionInput.value.trim(),
        prompt: rulePromptInput.value.trim(),
        onSubtype: String(ruleOnSubtypeInput?.value || "").trim(),
        onToolKey: String(ruleOnInput.value || "").trim(),
        severity: ruleSeverityInput.value,
        category: ruleCategoryInput.value.trim(),
        reason: ruleReasonInput.value.trim(),
        degradeTargetKey: String(ruleDegradeTargetInput.value || "").trim(),
        entryMode: matchingMode(),
        path,
        condition,
      };
    }

    function normalizedPathForMode(formState) {
      return formState.entryMode === "on" ? { path: "", pathSlots: [] } : formState.path;
    }

    function ruleFromFormState(formState) {
      const effectivePath = normalizedPathForMode(formState);
      const effectiveOnClause = onClause.buildOnClause(
        formState.onSubtype,
        toolNameForKey(formState.onToolKey),
      );
      return {
        name: formState.name,
        entryMode: formState.entryMode,
        path: effectivePath.path,
        pathSlots: effectivePath.pathSlots,
        condition: formState.condition.expression,
        conditionItems: formState.condition.items,
        conditionTree: formState.condition.tree || null,
        symbolToolMap: formState.condition.symbolToolMap,
        conditionSavedConditions: formState.condition.savedConditions || [],
        conditionCurrentId: formState.condition.currentConditionId || "",
        action: formState.action,
        degradeTarget: isDegradeAction(formState.action) ? toolNameForKey(formState.degradeTargetKey) : "",
        onClause: effectiveOnClause,
        severity: formState.severity,
        category: formState.category,
        reason: formState.reason,
        prompt: formState.prompt,
        description: formState.description,
      };
    }

    function formStateFromRule(rule) {
      const normalized = model.normalizeRule(rule);
      const onParts = onClause.parseOnClauseParts(normalized.onClause);
      return {
        name: normalized.name,
        action: normalized.action,
        description: normalized.description,
        prompt: normalized.prompt,
        onSubtype: onParts.subtype,
        onToolKey: toolKeyForName(onParts.toolPattern),
        severity: normalized.severity,
        category: normalized.category,
        reason: normalized.reason,
        degradeTargetKey: toolKeyForName(normalized.degradeTarget),
        entryMode: normalized.entryMode || "trace",
        path: {
          path: normalized.path,
          pathSlots: normalized.pathSlots,
          finished: Boolean(normalized.path),
        },
        condition: {
          items: normalized.conditionItems,
          tree: normalized.conditionTree || null,
          symbolToolMap: normalized.symbolToolMap || {},
          savedConditions: normalized.conditionSavedConditions || [],
          currentConditionId: normalized.conditionCurrentId || "",
          expression: normalized.condition,
        },
      };
    }

    function writeRuleFormState(formState) {
      ruleNameInput.value = formState.name || "";
      ruleActionInput.value = formState.action || "";
      renderDegradeTargetOptions(toolData?.loadToolCatalog?.(selectedAgentId) || [], formState.degradeTargetKey || "");
      rulePromptInput.value = formState.prompt || "";
      ruleDescriptionInput.value = formState.description || "";
      if (ruleOnSubtypeInput) {
        ruleOnSubtypeInput.value = formState.onSubtype || "";
      }
      renderOnToolOptions(toolData?.loadToolCatalog?.(selectedAgentId) || [], formState.onToolKey || "");
      ruleSeverityInput.value = formState.severity || "";
      ruleCategoryInput.value = formState.category || "";
      ruleReasonInput.value = formState.reason || "";
      setMatchingMode(formState.entryMode || "trace");
      syncActionUI(formState.action || "");
      pathBuilder.setValue(formState.path || { path: "", pathSlots: [], finished: false }, Boolean(formState.path?.path));
      conditionBuilder.setPathSymbols(currentPathSymbols());
      conditionBuilder.setValue({
        items: formState.condition?.items || [],
        tree: formState.condition?.tree || null,
        savedConditions: formState.condition?.savedConditions || [],
        currentConditionId: formState.condition?.currentConditionId || "",
      });
      syncConditionLock(pathBuilder.getValue());
      syncWizardUI();
      renderPreview();
    }

    function currentRule() {
      return ruleFromFormState(readRuleFormState());
    }

    function matchingStepIsReady() {
      if (modeNeedsTrace() && !hasFinishedTracePath()) {
        return { ok: false, message: "Please finish the TRACE builder before continuing." };
      }
      if (modeNeedsOn() && !hasCurrentCallFilter()) {
        return { ok: false, message: "Please configure the ON filter before continuing." };
      }
      return { ok: true, message: "" };
    }

    function validateStep(step) {
      const rule = currentRule();
      if (step === 1) {
        if (!rule.name) {
          return { ok: false, message: "Please enter a rule name first." };
        }
        return { ok: true, message: "" };
      }
      if (step === 2) {
        return matchingStepIsReady();
      }
      if (step === 3) {
        const matchingReady = matchingStepIsReady();
        if (!matchingReady.ok) {
          return matchingReady;
        }
        return conditionBuilder.validate();
      }
      if (step === 4) {
        if (!rule.action) {
          return { ok: false, message: "Please select an action first." };
        }
        if (rule.action === "DEGRADE" && !rule.degradeTarget) {
          return { ok: false, message: "Please select a DEGRADE target first." };
        }
        return { ok: true, message: "" };
      }
      return { ok: true, message: "" };
    }

    function completedSteps() {
      const steps = new Set();
      for (let step = 1; step <= STEP_COUNT; step += 1) {
        if (validateStep(step).ok) {
          steps.add(step);
        } else {
          break;
        }
      }
      return steps;
    }

    function syncWizardUI() {
      const completed = completedSteps();
      if (ruleGeneratorCard) {
        ruleGeneratorCard.classList.toggle("builder-mode-edit", builderMode === "edit");
      }
      if (returnToWizardButton) {
        returnToWizardButton.hidden = builderMode !== "edit";
      }
      if (ruleBuilderTitle) {
        ruleBuilderTitle.textContent = builderMode === "edit" ? "Rule Editor" : "Guided Rule Builder";
      }
      if (ruleBuilderSubtitle) {
        ruleBuilderSubtitle.textContent = builderMode === "edit"
          ? "The legacy full-form editor is kept for modifying existing rules and drafts."
          : "Create a new rule step by step.";
      }
      if (ruleBuilderActions) {
        ruleBuilderActions.hidden = builderMode === "create" && currentStep < STEP_COUNT;
      }
      (wizardStepCards || []).forEach((card) => {
        const step = Number(card.dataset.step || 0);
        card.classList.toggle("is-active", builderMode === "edit" || step === currentStep);
        card.classList.toggle("is-complete", completed.has(step));
      });
      (ruleStepButtons || []).forEach((button) => {
        const step = Number(button.dataset.step || 0);
        button.classList.toggle("active", builderMode === "edit" ? step === 1 : step === currentStep);
        button.classList.toggle("complete", completed.has(step));
      });
    }

    function setCurrentStep(step) {
      currentStep = Math.max(1, Math.min(STEP_COUNT, Number(step || 1)));
      syncWizardUI();
    }

    function goToStep(step) {
      if (builderMode === "edit") {
        return;
      }
      const nextStep = Math.max(1, Math.min(STEP_COUNT, Number(step || 1)));
      if (nextStep > currentStep) {
        const validationResult = validateStep(currentStep);
        if (!validationResult.ok) {
          window.AgentGuardUI.showToast(validationResult.message, "warning");
          return;
        }
      }
      setCurrentStep(nextStep);
    }

    function setBuilderMode(mode) {
      builderMode = mode === "edit" ? "edit" : "create";
      if (builderMode === "create") {
        setCurrentStep(1);
      } else {
        syncWizardUI();
      }
    }

    function prepareNewRule() {
      setBuilderMode("create");
      resetRuleForm();
      setCurrentStep(1);
    }

    function applyRule(rule) {
      setBuilderMode("edit");
      writeRuleFormState(formStateFromRule(rule));
    }

    function renderPreview() {
      const rule = currentRule();
      if (!rule.name && !rule.path && !rule.onClause && !rule.condition && !rule.action && !rule.description) {
        rulePreviewBlock.textContent = "";
      } else {
        rulePreviewBlock.textContent = preview.buildPreview(rule);
      }
      const shouldSyncOnInputs = rule.entryMode === "on" || Boolean(String(rule.onClause || "").trim());
      if (shouldSyncOnInputs) {
        const onParts = onClause.parseOnClauseParts(onClause.deriveOnClause(model.normalizeRule(rule)));
        if (ruleOnSubtypeInput) {
          ruleOnSubtypeInput.value = onParts.subtype;
        }
        ruleOnInput.value = toolKeyForName(onParts.toolPattern);
      }
      const pathState = pathBuilder.getValue();
      const finished = pathState.finished;
      pathFinishButton?.classList?.toggle("primary", finished);
      if (pathContinueButtonIcon) {
        pathContinueButtonIcon.src = finished ? "/assets/modify.png" : "/assets/add.png";
      }
      pathContinueButton?.setAttribute("aria-label", finished ? "Edit path" : "Add path segment");
      pathContinueButton?.setAttribute("title", finished ? "Edit path" : "Add path segment");
      changeHandler();
    }

    function resetRuleForm() {
      ruleNameInput.value = "";
      ruleActionInput.value = "";
      rulePromptInput.value = "";
      ruleDescriptionInput.value = "Use this field to capture the operator-facing explanation for the rule.";
      ruleSeverityInput.value = "";
      ruleCategoryInput.value = "";
      ruleReasonInput.value = "";
      if (ruleOnSubtypeInput) {
        ruleOnSubtypeInput.value = "";
      }
      renderOnToolOptions(toolData?.loadToolCatalog?.(selectedAgentId) || [], "");
      renderDegradeTargetOptions(toolData?.loadToolCatalog?.(selectedAgentId) || [], "");
      setMatchingMode("trace");
      conditionBuilder.clear();
      pathBuilder.clear();
      syncActionUI();
      syncConditionLock(pathBuilder.getValue());
      syncWizardUI();
      renderPreview();
    }

    function validateCurrentRuleForm(rule) {
      const baseValidation = validation.validateRuleData(rule);
      if (!baseValidation.ok) {
        return baseValidation;
      }
      if (String(rule.path || "").trim()) {
        const pathValidation = pathBuilder.validate();
        if (!pathValidation.ok) {
          return pathValidation;
        }
      }
      return conditionBuilder.validate();
    }

    function setBusy(isBusy) {
      ruleActionInFlight = Boolean(isBusy);
      generateRuleButton.disabled = ruleActionInFlight;
      if (checkRuleButton) {
        checkRuleButton.disabled = ruleActionInFlight;
      }
    }

    function isBusy() {
      return ruleActionInFlight;
    }

    function handleRuleFieldInput() {
      syncConditionLock(pathBuilder.getValue());
      syncWizardUI();
      renderPreview();
    }

    function onChange(handler) {
      changeHandler = typeof handler === "function" ? handler : function noop() {};
    }

    function setSelectedAgent(agentId) {
      selectedAgentId = String(agentId || "").trim();
    }

    function initEventHandlers({ onGenerateRule, onCheckRule, onClearRuleForm }) {
      [ruleNameInput, rulePromptInput, ruleDescriptionInput, ruleSeverityInput, ruleCategoryInput, ruleReasonInput].forEach((element) => {
        element.addEventListener("input", handleRuleFieldInput);
      });

      ruleActionInput.addEventListener("change", () => {
        syncActionUI(ruleActionInput.value);
        handleRuleFieldInput();
      });
      ruleActionInput.addEventListener("input", handleRuleFieldInput);
      ruleDegradeTargetInput.addEventListener("change", handleRuleFieldInput);
      ruleOnSubtypeInput?.addEventListener("change", () => {
        syncBuilderUI();
        handleRuleFieldInput();
      });
      ruleOnInput.addEventListener("change", () => {
        syncBuilderUI();
        handleRuleFieldInput();
      });
      ruleSeverityInput.addEventListener("change", handleRuleFieldInput);

      (matchModeInputs || []).forEach((input) => {
        input.addEventListener("change", () => {
          syncBuilderUI();
          handleRuleFieldInput();
        });
      });

      pathContinueButton.addEventListener("click", () => {
        if (pathBuilder.getValue().finished) {
          pathBuilder.modify();
          syncConditionLock(pathBuilder.getValue());
          syncWizardUI();
          return;
        }
        pathBuilder.appendSegment();
      });

      pathFinishButton.addEventListener("click", () => {
        const result = pathBuilder.finish();
        if (!result.ok) {
          window.AgentGuardUI.showToast(result.message, "warning");
          return;
        }
        syncConditionLock(pathBuilder.getValue());
        syncWizardUI();
      });

      (ruleStepButtons || []).forEach((button) => {
        button.addEventListener("click", () => {
          goToStep(button.dataset.step);
        });
      });

      (wizardPrevButtons || []).forEach((button) => {
        button.addEventListener("click", () => {
          goToStep(Number(button.dataset.prevStep || currentStep - 1));
        });
      });

      (wizardNextButtons || []).forEach((button) => {
        button.addEventListener("click", () => {
          goToStep(Number(button.dataset.nextStep || currentStep + 1));
        });
      });

      returnToWizardButton?.addEventListener("click", () => {
        prepareNewRule();
      });

      generateRuleButton.addEventListener("click", onGenerateRule);
      checkRuleButton?.addEventListener("click", onCheckRule);
      elements.clearRuleFormButton?.addEventListener("click", onClearRuleForm);
    }

    function initialize() {
      renderOnToolOptions();
      renderDegradeTargetOptions();
      syncActionUI();
      syncBuilderUI();
      syncConditionLock(pathBuilder.getValue());
      syncWizardUI();
    }

    return {
      applyRule,
      currentRule,
      formStateFromRule,
      initEventHandlers,
      initialize,
      isBusy,
      onChange,
      pathBuilder,
      prepareNewRule,
      readRuleFormState,
      refreshToolOptions,
      renderPreview,
      renderOnToolOptions,
      renderDegradeTargetOptions,
      resetRuleForm,
      ruleFromFormState,
      setBuilderMode,
      setBusy,
      setCurrentStep,
      setSelectedAgent,
      syncActionUI,
      syncBuilderUI,
      syncConditionLock,
      validateCurrentRuleForm,
      writeRuleFormState,
    };
  }

  window.AgentGuardRuleFormController = {
    create: createRuleFormController,
  };
})();

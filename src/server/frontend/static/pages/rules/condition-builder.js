(function () {
  const toolCatalogHelpers = window.AgentGuardToolCatalog || {};
  const uiHelpers = window.AgentGuardUIHelpers || {};

  const labelValues = {
    "label.boundary": ["internal", "external", "privileged"],
    "label.sensitivity": ["low", "moderate", "high"],
    "label.integrity": ["trusted", "unfiltered"],
  };

  const traceFeatureOperators = {
    name: ["=="],
    "label.boundary": ["==", "!="],
    "label.sensitivity": ["==", "!="],
    "label.integrity": ["==", "!="],
    syntax: ["==", "!=", ">", ">=", "<", "<=", "contains"],
  };

  const contextDefinitions = {
    tool: [
      { value: "tool.name", label: "tool.name", kind: "tool-name", operators: ["=="] },
      { value: "tool.boundary", label: "tool.boundary", kind: "enum", enumKey: "label.boundary", operators: ["==", "!="] },
      { value: "tool.sensitivity", label: "tool.sensitivity", kind: "enum", enumKey: "label.sensitivity", operators: ["==", "!="] },
      { value: "tool.integrity", label: "tool.integrity", kind: "enum", enumKey: "label.integrity", operators: ["==", "!="] },
      { value: "tool.syntax", label: "tool.<syntax field>", kind: "tool-syntax", operators: ["==", "!=", ">", ">=", "<", "<=", "contains"] },
    ],
    target: [
      { value: "target.domain", label: "target.domain", kind: "text", operators: ["==", "!=", "contains"] },
      { value: "target.raw", label: "target.<field>", kind: "free-field", fieldPrefix: "target", operators: ["==", "!=", ">", ">=", "<", "<=", "contains"] },
    ],
    principal: [
      { value: "principal.role", label: "principal.role", kind: "text", operators: ["==", "!="] },
      { value: "principal.trust_level", label: "principal.trust_level", kind: "number", operators: ["==", "!=", ">", ">=", "<", "<="] },
      { value: "principal.user_id", label: "principal.user_id", kind: "text", operators: ["==", "!=", "contains"] },
    ],
    caller: [
      { value: "caller.role", label: "caller.role", kind: "text", operators: ["==", "!="] },
      { value: "caller.trust_level", label: "caller.trust_level", kind: "number", operators: ["==", "!=", ">", ">=", "<", "<="] },
    ],
    event: [
      { value: "event.type", label: "event.type", kind: "text", operators: ["==", "!="] },
      { value: "event.session_id", label: "event.session_id", kind: "text", operators: ["==", "!=", "contains"] },
    ],
  };

  const tracePropertyGroups = [
    { value: "name", label: "Tool name" },
    { value: "label", label: "Tool label" },
    { value: "syntax", label: "Tool syntax" },
  ];

  const contextPropertyGroups = [
    { value: "tool", label: "tool" },
    { value: "target", label: "target" },
    { value: "principal", label: "principal" },
    { value: "caller", label: "caller" },
    { value: "event", label: "event" },
  ];

  const wizardStages = ["source", "symbol", "property", "comparison", "complete"];

  function toolCatalog() {
    return window.AgentGuardData ? window.AgentGuardData.loadToolCatalog() : [];
  }

  function toolOptions() {
    if (typeof toolCatalogHelpers.toToolOptions === "function") {
      return toolCatalogHelpers.toToolOptions(toolCatalog());
    }
    return toolCatalog().map((tool) => ({
      value: tool.tool_key,
      label: tool.name,
      name: tool.name,
    }));
  }

  function firstToolOption() {
    return toolOptions()[0] || null;
  }

  function toolNameForKey(toolKey) {
    if (typeof toolCatalogHelpers.toolNameForKey === "function") {
      return toolCatalogHelpers.toolNameForKey(toolKey, toolCatalog(), window.AgentGuardData?.findToolByKey);
    }
    const match = window.AgentGuardData?.findToolByKey?.(toolCatalog(), toolKey);
    return match ? match.name : "";
  }

  function inputParamsForTool(toolKey) {
    const match = window.AgentGuardData?.findToolByKey?.(toolCatalog(), toolKey);
    return match ? match.input_params : [];
  }

  function contextPrefixes() {
    return Object.keys(contextDefinitions);
  }

  function contextFieldsForPrefix(prefix) {
    return (contextDefinitions[prefix] || []).map((item) => ({
      value: item.value,
      label: item.label,
    }));
  }

  function contextDefinitionForPath(path, prefixHint = "") {
    if (path && path !== `${prefixHint}.raw`) {
      const prefix = String(path).split(".")[0];
      const exact = (contextDefinitions[prefix] || []).find((item) => item.value === path);
      if (exact) {
        return exact;
      }
      if (prefix === "tool") {
        return contextDefinitions.tool.find((item) => item.value === "tool.syntax");
      }
      const raw = (contextDefinitions[prefix] || []).find((item) => item.kind === "free-field");
      if (raw) {
        return raw;
      }
    }
    const hinted = (contextDefinitions[prefixHint] || [])[0];
    return hinted || contextDefinitions.tool[0];
  }

  function buildContextPath(prefix, fieldValue, fieldName, syntaxField) {
    if (fieldValue === "tool.syntax") {
      return syntaxField ? `tool.${syntaxField}` : "";
    }
    if (fieldValue === `${prefix}.raw`) {
      return fieldName ? `${prefix}.${fieldName}` : "";
    }
    return fieldValue || "";
  }

  function normalizeStepStage(raw) {
    const explicit = String(raw?.stepStage || "").trim();
    if (explicit === "subproperty") {
      return "property";
    }
    if (wizardStages.includes(explicit)) {
      return explicit;
    }
    return "complete";
  }

  function defaultItem(symbols) {
    return {
      conditionId: "",
      confirmed: false,
      stepStage: "source",
      connector: "",
      openParen: "",
      closeParen: "",
      sourceType: "trace",
      symbol: symbols[0] || "A",
      feature: "",
      propertyGroup: "",
      syntaxField: "",
      operator: "",
      value: "",
      selectedToolKey: "",
      contextPrefix: "",
      contextField: "",
      contextFieldName: "",
      contextPath: "",
    };
  }

  function inferSymbolToolMap(value) {
    const rawItems = Array.isArray(value?.items)
      ? value.items
      : value?.feature || value?.contextPath
        ? [value]
        : [];

    return rawItems.reduce((acc, item) => {
      if (item?.sourceType === "trace"
          && item?.feature === "name"
          && item?.operator === "=="
          && item?.symbol
          && item?.value) {
        acc[item.symbol] = String(item.selectedToolKey || "");
      }
      return acc;
    }, {});
  }

  function buildItemExpression(item) {
    const openParen = item.openParen || "";
    const closeParen = item.closeParen || "";
    const operator = item.operator === "contains" ? "CONTAINS" : item.operator;

    if (item.sourceType === "context") {
      if (!item.contextPath || !operator || !item.value) {
        return "";
      }
      return `${openParen}${item.contextPath} ${operator} "${item.value}"${closeParen}`;
    }

    if (!item.symbol || !item.feature || !item.operator || !item.value) {
      return "";
    }
    if (item.feature === "syntax") {
      if (!item.syntaxField) {
        return "";
      }
      return `${openParen}${item.symbol}.${item.syntaxField} ${operator} "${item.value}"${closeParen}`;
    }
    if (item.feature === "name") {
      return `${openParen}${item.symbol}.name ${operator} "${item.value}"${closeParen}`;
    }
    const field = item.feature.replace(/^label\./, "");
    return `${openParen}${item.symbol}.${field} ${operator} "${item.value}"${closeParen}`;
  }

  function normalizeTraceItem(raw, index, symbols, symbolToolMap) {
    const fallback = defaultItem(symbols);
    const stepStage = normalizeStepStage(raw);
    const hasExplicitStepStage = String(raw?.stepStage || "").trim() !== "";
    const isDraft = !raw?.confirmed && hasExplicitStepStage && stepStage !== "complete";
    const item = {
      conditionId: String(raw?.conditionId || ""),
      confirmed: Boolean(raw?.confirmed),
      stepStage,
      connector: index === 0 ? "" : String(raw?.connector || "AND"),
      openParen: String(raw?.openParen || ""),
      closeParen: String(raw?.closeParen || ""),
      sourceType: "trace",
      symbol: String(raw?.symbol || fallback.symbol || "A"),
      feature: String(raw?.feature || fallback.feature || ""),
      propertyGroup: String(raw?.propertyGroup || ""),
      syntaxField: String(raw?.syntaxField || ""),
      operator: String(raw?.operator || ""),
      value: String(raw?.value || ""),
      selectedToolKey: String(raw?.selectedToolKey || ""),
      contextPrefix: "",
      contextField: "",
      contextFieldName: "",
      contextPath: "",
    };

    if (!symbols.includes(item.symbol)) {
      item.symbol = symbols[0] || "A";
    }

    const featureOptions = ["name", "label.boundary", "label.sensitivity", "label.integrity"];
    if (symbolToolMap[item.symbol]) {
      featureOptions.splice(1, 0, "syntax");
    }
    if (!featureOptions.includes(item.feature)) {
      item.feature = isDraft ? "" : "name";
    }

    if (!item.propertyGroup) {
      if (item.feature === "syntax") {
        item.propertyGroup = "syntax";
      } else if (item.feature.startsWith("label.")) {
        item.propertyGroup = "label";
      } else if (item.feature === "name") {
        item.propertyGroup = "name";
      }
    }

    const operators = item.feature ? traceFeatureOperators[item.feature] : [];
    if (item.operator && !operators.includes(item.operator)) {
      item.operator = isDraft ? "" : (operators[0] || "");
    }
    if (!item.operator && !isDraft && operators.length) {
      item.operator = operators[0];
    }

    if (item.feature === "name") {
      const tools = toolOptions();
      const selectedTool = tools.find((option) => option.value === item.selectedToolKey);
      if (selectedTool) {
        item.value = selectedTool.name;
      } else if (tools.some((option) => option.name === item.value)) {
        const firstMatchingTool = tools.find((option) => option.name === item.value);
        item.selectedToolKey = firstMatchingTool?.value || "";
        item.value = firstMatchingTool?.name || item.value;
      } else if (!isDraft) {
        item.selectedToolKey = tools[0]?.value || "";
        item.value = tools[0]?.name || "";
      } else {
        item.selectedToolKey = "";
        item.value = "";
      }
      item.syntaxField = "";
    } else if (item.feature.startsWith("label.")) {
      const values = labelValues[item.feature] || [];
      if (item.value && !values.includes(item.value)) {
        item.value = isDraft ? "" : (values[0] || "");
      }
      if (!item.value && !isDraft) {
        item.value = values[0] || "";
      }
      item.syntaxField = "";
      item.selectedToolKey = "";
    } else if (item.feature === "syntax") {
      const params = inputParamsForTool(symbolToolMap[item.symbol] || "");
      if (item.syntaxField && !params.includes(item.syntaxField)) {
        item.syntaxField = isDraft ? "" : (params[0] || "");
      }
      if (!item.syntaxField && !isDraft) {
        item.syntaxField = params[0] || "";
      }
      item.selectedToolKey = "";
    } else {
      item.syntaxField = "";
      item.selectedToolKey = "";
      item.value = "";
    }

    return {
      ...item,
      resolvedToolName: toolNameForKey(symbolToolMap[item.symbol] || ""),
      expression: buildItemExpression(item),
    };
  }

  function normalizeContextItem(raw, index, options = {}) {
    const currentCallToolKey = String(options.currentCallToolKey || "");
    const stepStage = normalizeStepStage(raw);
    const hasExplicitStepStage = String(raw?.stepStage || "").trim() !== "";
    const isDraft = !raw?.confirmed && hasExplicitStepStage && stepStage !== "complete";
    const derivedPrefix = String(raw?.contextPath || "").split(".")[0] || "";
    const prefix = String(raw?.contextPrefix || (!isDraft ? (derivedPrefix || "tool") : ""));
    const definition = prefix ? contextDefinitionForPath(String(raw?.contextPath || ""), prefix) : null;
    const contextField = String(raw?.contextField || (!isDraft ? (definition?.value || "tool.name") : ""));
    const fieldName = String(raw?.contextFieldName || "");
    let syntaxField = String(raw?.syntaxField || "");
    if (contextField === "tool.syntax") {
      const params = inputParamsForTool(currentCallToolKey);
      if (syntaxField && !params.includes(syntaxField)) {
        syntaxField = isDraft ? "" : (params[0] || "");
      }
      if (!syntaxField && !isDraft) {
        syntaxField = params[0] || "";
      }
    }
    const contextPath = contextField ? buildContextPath(prefix, contextField, fieldName, syntaxField) : "";
    const nextDefinition = contextField
      ? contextDefinitionForPath(
        contextPath || (contextField === "tool.syntax" ? contextField : `${prefix}.raw`),
        prefix,
      )
      : null;
    const operators = nextDefinition?.operators || [];
    let value = String(raw?.value || "");
    let selectedToolKey = String(raw?.selectedToolKey || "");

    if (nextDefinition?.kind === "enum") {
      const options = labelValues[nextDefinition.enumKey] || [];
      if (value && !options.includes(value)) {
        value = isDraft ? "" : (options[0] || "");
      }
      if (!value && !isDraft) {
        value = options[0] || "";
      }
    }
    if (nextDefinition?.kind === "tool-name") {
      const tools = toolOptions();
      const selectedTool = tools.find((option) => option.value === selectedToolKey);
      if (selectedTool) {
        value = selectedTool.name;
      } else if (tools.some((option) => option.name === value)) {
        selectedToolKey = tools.find((option) => option.name === value)?.value || "";
      } else if (!isDraft) {
        selectedToolKey = tools[0]?.value || "";
        value = tools[0]?.name || value;
      } else {
        selectedToolKey = "";
        value = "";
      }
    }

    let operator = String(raw?.operator || "");
    if (operator && !operators.includes(operator)) {
      operator = isDraft ? "" : (operators[0] || "");
    }
    if (!operator && !isDraft && operators.length) {
      operator = operators[0];
    }

    return {
      conditionId: String(raw?.conditionId || ""),
      confirmed: Boolean(raw?.confirmed),
      stepStage,
      connector: index === 0 ? "" : String(raw?.connector || "AND"),
      openParen: String(raw?.openParen || ""),
      closeParen: String(raw?.closeParen || ""),
      sourceType: "context",
      symbol: "",
      feature: "",
      propertyGroup: "",
      syntaxField,
      operator,
      value,
      selectedToolKey,
      contextPrefix: prefix,
      contextField,
      contextFieldName: fieldName,
      contextPath,
      resolvedToolName: "",
      expression: "",
    };
  }

  function normalizeItem(raw, index, symbols, symbolToolMap, options = {}) {
    const sourceType = String(raw?.sourceType || "").trim() || "trace";
    if (sourceType === "context") {
      const normalized = normalizeContextItem(raw, index, options);
      return {
        ...normalized,
        expression: buildItemExpression(normalized),
      };
    }
    return normalizeTraceItem(raw, index, symbols, symbolToolMap);
  }

  function normalizeItems(value, symbols, options = {}) {
    if (Array.isArray(value?.items) && value.items.length === 0) {
      return {
        items: [],
        symbolToolMap: {},
      };
    }

    const sourceItems = Array.isArray(value?.items)
      ? value.items
      : value?.feature || value?.contextPath
        ? [value]
        : [defaultItem(symbols)];

    const baseMap = inferSymbolToolMap({ items: sourceItems });
    const normalized = sourceItems.map((item, index) => normalizeItem(item, index, symbols, baseMap, options));
    const symbolToolMap = inferSymbolToolMap({ items: normalized });

    return {
      items: normalized.map((item, index) => normalizeItem(item, index, symbols, symbolToolMap, options)),
      symbolToolMap,
    };
  }

  function exportItem(item, index = 0) {
    return {
      conditionId: item.conditionId || "",
      confirmed: Boolean(item.confirmed),
      stepStage: item.stepStage || "complete",
      connector: index === 0 ? "" : String(item.connector || "AND"),
      openParen: item.openParen || "",
      closeParen: item.closeParen || "",
      sourceType: item.sourceType || "trace",
      symbol: item.symbol || "",
      feature: item.feature || "",
      propertyGroup: item.propertyGroup || "",
      syntaxField: item.syntaxField || "",
      operator: item.operator || "",
      value: item.value || "",
      selectedToolKey: item.selectedToolKey || "",
      contextPrefix: item.contextPrefix || "",
      contextField: item.contextField || "",
      contextFieldName: item.contextFieldName || "",
      contextPath: item.contextPath || "",
    };
  }

  function exportItems(items) {
    return (Array.isArray(items) ? items : []).map((item, index) => exportItem(item, index));
  }

  function createField(labelText, child) {
    const wrap = document.createElement("div");
    wrap.className = "field condition-field";
    const label = document.createElement("label");
    label.textContent = labelText;
    wrap.appendChild(label);
    wrap.appendChild(child);
    return wrap;
  }

  function createSelect(options, selectedValue, onChange) {
    const select = document.createElement("select");
    options.forEach((optionValue) => {
      const option = document.createElement("option");
      if (typeof optionValue === "object" && optionValue !== null) {
        option.value = optionValue.value;
        option.textContent = optionValue.label;
        option.selected = optionValue.value === selectedValue;
      } else {
        option.value = optionValue;
        option.textContent = optionValue;
        option.selected = optionValue === selectedValue;
      }
      select.appendChild(option);
    });
    select.addEventListener("change", onChange);
    return select;
  }

  const createIconButton = uiHelpers.createIconButton || function fallbackCreateIconButton(iconName, ariaLabel, onClick) {
    const button = document.createElement("button");
    button.className = "condition-icon-button";
    button.type = "button";
    button.setAttribute("aria-label", ariaLabel);

    const icon = document.createElement("img");
    icon.className = "condition-action-icon";
    icon.src = `/assets/${iconName}`;
    icon.alt = "";
    button.appendChild(icon);

    button.addEventListener("click", onClick);
    return button;
  };

  function createConditionBuilder(options) {
    const root = options.root;
    const hint = options.hint;
    const addButton = options.addButton;
    const stepModeButton = options.stepModeButton;
    const directModeButton = options.directModeButton;
    const modeCopy = options.modeCopy;
    const onChange = options.onChange || (() => {});
    const shell = root.closest(".condition-builder");
    const flow = options.flow || shell?.querySelector?.("#condition-builder-flow") || null;
    const actionsBar = (hint && hint.closest(".condition-builder-actions"))
      || (addButton && addButton.closest(".condition-builder-actions"))
      || null;
    let symbols = options.pathSymbols && options.pathSymbols.length ? options.pathSymbols : ["A"];
    let currentCallToolKey = String(options.currentCallToolKey || "");
    let builderMode = String(options.defaultMode || "step").trim() === "direct" ? "direct" : "step";
    let state = normalizeItems(options.value, symbols, { currentCallToolKey });
    let locked = Boolean(options.locked);
    let allowedSourceTypes = new Set(
      Array.isArray(options.allowedSourceTypes) && options.allowedSourceTypes.length
        ? options.allowedSourceTypes
        : ["trace", "context"],
    );
    let stepSavedConditions = [];
    let stepCurrentConditionId = "";

    function buildSavedConditionEntry(conditionId, items) {
      const exportedItems = exportItems(items).map((item, index) => ({
        ...item,
        confirmed: true,
        stepStage: "complete",
        conditionId: index === 0 ? (conditionId || item.conditionId || "") : (item.conditionId || ""),
        connector: index === 0 ? "" : String(item.connector || "AND"),
      }));
      const normalizedEntry = normalizeItems({ items: exportedItems }, symbols, { currentCallToolKey });
      const normalizedItems = normalizedEntry.items.map((item, index) => ({
        ...item,
        confirmed: true,
        stepStage: "complete",
        conditionId: index === 0 ? (conditionId || item.conditionId || "") : (item.conditionId || ""),
        connector: index === 0 ? "" : String(item.connector || "AND"),
      }));
      const expression = expressionForItems(normalizedItems);
      return {
        conditionId: conditionId || normalizedItems[0]?.conditionId || "",
        items: exportItems(normalizedItems),
        expression,
      };
    }

    function deriveStepSavedConditions(value, normalizedItems) {
      const rawSaved = Array.isArray(value?.savedConditions) ? value.savedConditions : [];
      if (rawSaved.length) {
        return rawSaved
          .map((entry) => {
            const normalizedEntry = normalizeItems({ items: entry?.items || [] }, symbols, { currentCallToolKey });
            const completeItems = normalizedEntry.items
              .filter((item) => item.expression)
              .map((item) => ({ ...item, confirmed: true, stepStage: "complete" }));
            if (!completeItems.length) {
              return null;
            }
            return buildSavedConditionEntry(
              String(entry?.conditionId || completeItems[0]?.conditionId || ""),
              completeItems,
            );
          })
          .filter(Boolean);
      }

      return normalizedItems
        .filter((item) => item.stepStage === "complete" && item.expression)
        .map((item) => buildSavedConditionEntry(item.conditionId || "", [{ ...item, connector: "" }]));
    }

    function initializeStepState(value, normalizedItems = state.items) {
      stepSavedConditions = deriveStepSavedConditions(value, normalizedItems);
      const requestedCurrentId = String(value?.currentConditionId || "").trim();
      if (requestedCurrentId && stepSavedConditions.some((entry) => entry.conditionId === requestedCurrentId)) {
        stepCurrentConditionId = requestedCurrentId;
        return;
      }
      stepCurrentConditionId = stepSavedConditions[stepSavedConditions.length - 1]?.conditionId || "";
    }

    initializeStepState(options.value, state.items);
    if (!state.items.length && stepCurrentConditionId) {
      const initialActiveCondition = savedConditionById(stepCurrentConditionId);
      if (initialActiveCondition) {
        applyActiveStepCondition(initialActiveCondition);
      }
    }

    function defaultSourceType() {
      if (allowedSourceTypes.has("trace")) {
        return "trace";
      }
      if (allowedSourceTypes.has("context")) {
        return "context";
      }
      return "trace";
    }

    function hasSourceChoice() {
      return allowedSourceTypes.has("trace") && allowedSourceTypes.has("context");
    }

    function baseStageOrderForSourceType(sourceType) {
      return sourceType === "trace"
        ? ["source", "symbol", "property", "comparison", "complete"]
        : ["source", "property", "comparison", "complete"];
    }

    function buildDefaultDraft(connector = "") {
      if (defaultSourceType() === "context") {
        const item = {
          ...defaultItem(symbols),
          connector,
          sourceType: "context",
          symbol: "",
          feature: "",
          propertyGroup: "",
          syntaxField: "",
          selectedToolKey: "",
          contextPrefix: "",
          contextField: "",
          contextFieldName: "",
          contextPath: "",
          operator: "",
          value: "",
        };
        item.stepStage = stageOrderForItem(item)[0];
        return item;
      }
      const item = {
        ...defaultItem(symbols),
        connector,
      };
      item.stepStage = stageOrderForItem(item)[0];
      return item;
    }

    function normalizeSourceType(sourceType) {
      if (allowedSourceTypes.has(sourceType)) {
        return sourceType;
      }
      return defaultSourceType();
    }

    function traceGroupFromFeature(feature) {
      if (!feature) {
        return "";
      }
      if (feature === "syntax") {
        return "syntax";
      }
      if (String(feature || "").startsWith("label.")) {
        return "label";
      }
      return "name";
    }

    function contextDefinitionForItem(item) {
      if (!item.contextField && !item.contextPath) {
        return { operators: [] };
      }
      return contextDefinitionForPath(item.contextPath, item.contextPrefix || "tool");
    }

    function tracePropertyOptionsForItem(item) {
      return tracePropertyGroups.filter((option) => {
        if (option.value !== "syntax") {
          return true;
        }
        return Boolean(state.symbolToolMap[item.symbol]);
      });
    }

    function stageOrderForItem(item) {
      const sourceType = normalizeSourceType(item?.sourceType || defaultSourceType());
      const order = baseStageOrderForSourceType(sourceType);
      return hasSourceChoice() ? order : order.filter((stage) => stage !== "source");
    }

    function currentStageForItem(item) {
      const order = stageOrderForItem(item);
      const requested = normalizeStepStage(item);
      return order.includes(requested) ? requested : order[0];
    }

    function previousStage(item) {
      const order = stageOrderForItem(item);
      const index = order.indexOf(currentStageForItem(item));
      return index > 0 ? order[index - 1] : order[0];
    }

    function nextStage(item) {
      const order = stageOrderForItem(item);
      const index = order.indexOf(currentStageForItem(item));
      return index >= 0 && index < order.length - 1 ? order[index + 1] : "complete";
    }

    function canAdvanceStage(item) {
      const stage = currentStageForItem(item);
      if (stage === "source") {
        return allowedSourceTypes.has(item.sourceType);
      }
      if (stage === "symbol") {
        return Boolean(item.symbol);
      }
      if (stage === "property") {
        if (item.sourceType === "trace") {
          const traceGroup = item.propertyGroup || traceGroupFromFeature(item.feature);
          if (!traceGroup) {
            return false;
          }
          if (traceGroup === "label") {
            return Boolean(item.feature);
          }
          if (traceGroup === "syntax") {
            return Boolean(item.syntaxField);
          }
          return true;
        }
        if (!item.contextPrefix || !item.contextField) {
          return false;
        }
        const definition = contextDefinitionForItem(item);
        if (definition.kind === "free-field") {
          return Boolean(item.contextFieldName);
        }
        if (definition.kind === "tool-syntax") {
          return Boolean(item.syntaxField);
        }
        return true;
      }
      if (stage === "comparison") {
        return Boolean(item.expression);
      }
      return true;
    }

    function coerceItemSourceType(item, index) {
      const nextSourceType = normalizeSourceType(String(item?.sourceType || "").trim() || defaultSourceType());
      if (nextSourceType === "context") {
        const normalized = normalizeContextItem({
          ...item,
          connector: index === 0 ? "" : String(item?.connector || "AND"),
          sourceType: "context",
          contextPrefix: item?.contextPrefix || "",
          contextField: item?.contextField || "",
          contextFieldName: item?.contextFieldName || "",
          contextPath: item?.contextPath || "",
          operator: item?.operator || "",
          value: item?.value || "",
        }, index, { currentCallToolKey });
        return {
          ...normalized,
          conditionId: String(item?.conditionId || normalized.conditionId || ""),
          stepStage: item?.confirmed ? "complete" : normalizeStepStage(item),
          expression: buildItemExpression(normalized),
        };
      }
      const normalized = normalizeTraceItem({
        ...item,
        connector: index === 0 ? "" : String(item?.connector || "AND"),
        sourceType: "trace",
      }, index, symbols, state.symbolToolMap || {});
      return {
        ...normalized,
        conditionId: String(item?.conditionId || normalized.conditionId || ""),
        stepStage: item?.confirmed ? "complete" : normalizeStepStage(item),
      };
    }

    function hasIncompleteStep() {
      return builderMode === "step" && state.items.some((item) => item.stepStage !== "complete");
    }

    function expressionForItems(items, { completeOnly = false } = {}) {
      return items.reduce((acc, item, index) => {
        if (!item?.expression) {
          return acc;
        }
        if (completeOnly && item.stepStage !== "complete") {
          return acc;
        }
        acc.push(index === 0 ? item.expression : `${item.connector} ${item.expression}`);
        return acc;
      }, []).join(" ");
    }

    function stepItems() {
      return state.items.filter((item) => item.stepStage === "complete");
    }

    function savedConditionById(conditionId) {
      return stepSavedConditions.find((entry) => entry.conditionId === conditionId) || null;
    }

    function activeStepCondition() {
      return savedConditionById(stepCurrentConditionId);
    }

    function currentDraftIndex() {
      return state.items.findIndex((item) => item.stepStage !== "complete");
    }

    function nextConditionId(items = state.items) {
      const maxValue = items.reduce((acc, item) => {
        const matched = String(item?.conditionId || "").match(/^COND(\d+)$/);
        const numeric = matched ? Number(matched[1]) : 0;
        return Math.max(acc, Number.isFinite(numeric) ? numeric : 0);
      }, 0);
      return `COND${maxValue + 1}`;
    }

    function assignMissingConditionIds(items) {
      let nextId = items.reduce((acc, item) => {
        const matched = String(item?.conditionId || "").match(/^COND(\d+)$/);
        const numeric = matched ? Number(matched[1]) : 0;
        return Math.max(acc, Number.isFinite(numeric) ? numeric : 0);
      }, 0) + 1;

      return items.map((item) => {
        if (item.stepStage !== "complete" || item.conditionId) {
          return item;
        }
        const withId = {
          ...item,
          conditionId: `COND${nextId}`,
        };
        nextId += 1;
        return withId;
      });
    }

    function applyActiveStepCondition(entry) {
      const activeItems = exportItems(entry?.items || []).map((item, index) => ({
        ...item,
        confirmed: true,
        stepStage: "complete",
        connector: index === 0 ? "" : String(item.connector || "AND"),
      }));
      syncItems(activeItems);
    }

    function seedStepSavedConditionsFromState() {
      if (stepSavedConditions.length || !state.items.length || hasIncompleteStep()) {
        return;
      }
      const entry = buildSavedConditionEntry(
        state.items[0]?.conditionId || nextConditionId(state.items),
        stepItems(),
      );
      if (!entry.expression) {
        return;
      }
      stepSavedConditions = [entry];
      stepCurrentConditionId = entry.conditionId;
    }

    function updateModeUI() {
      if (stepModeButton) {
        stepModeButton.classList.toggle("active", builderMode === "step");
        stepModeButton.setAttribute("aria-pressed", builderMode === "step" ? "true" : "false");
      }
      if (directModeButton) {
        directModeButton.classList.toggle("active", builderMode === "direct");
        directModeButton.setAttribute("aria-pressed", builderMode === "direct" ? "true" : "false");
      }
      if (modeCopy) {
        modeCopy.textContent = builderMode === "step"
          ? "Build single conditions with guidance and combine them into complex rules."
          : "Direct mode exposes raw per-item editing, including connectors and parentheses on each row.";
      }
    }

    function emit() {
      updateHint();
      onChange(api.getValue());
    }

    function updateHint() {
      if (locked) {
        hint.textContent = "Confirm PATH or ON first to unlock CONDITION editing.";
        hint.classList.add("condition-builder-warning");
        return;
      }
      if (!allowedSourceTypes.size) {
        hint.textContent = "Add PATH or ON first to unlock CONDITION editing.";
        hint.classList.add("condition-builder-warning");
        return;
      }
      if (builderMode === "step") {
        hint.textContent = hasIncompleteStep()
          ? "Finish the guided builder card, then save the single condition before combining rules."
          : "Generate reusable single rules first.";
      } else {
        hint.textContent = "Build one or more conditions from TRACE symbols or the current-call context.";
      }
      hint.classList.remove("condition-builder-warning");
    }

    function mountDefaultActions() {
      if (!actionsBar || !shell || typeof shell.appendChild !== "function") {
        return;
      }
      actionsBar.classList?.remove?.("condition-builder-actions-inline");
      shell.appendChild(actionsBar);
    }

    function mountStepActions(container) {
      if (!actionsBar || !container || typeof container.appendChild !== "function") {
        return;
      }
      actionsBar.classList?.add?.("condition-builder-actions-inline");
      container.appendChild(actionsBar);
    }

    function syncLockState() {
      if (addButton) {
        addButton.disabled = locked || hasIncompleteStep();
      }
      if (shell) {
        shell.classList.toggle("is-locked", locked);
      }
      root.querySelectorAll("button, select, input, textarea").forEach((element) => {
        if (element === addButton) {
          element.disabled = locked || hasIncompleteStep();
          return;
        }
        if (element.attributes?.["data-allow-while-locked"] === "true") {
          return;
        }
        element.disabled = locked;
      });
    }

    function syncItems(nextItems) {
      const normalized = normalizeItems({ items: nextItems }, symbols, { currentCallToolKey });
      const coercedItems = normalized.items.map((item, index) => coerceItemSourceType(item, index));
      state = {
        ...normalized,
        items: assignMissingConditionIds(coercedItems),
      };
    }

    function removeItem(index) {
      const nextItems = state.items.filter((_, itemIndex) => itemIndex !== index);
      syncItems(nextItems);
      render();
      emit();
    }

    function updateItem(index, patch, options = {}) {
      const shouldRender = options.render !== false;
      const nextItems = state.items.slice();
      nextItems[index] = { ...nextItems[index], ...patch };
      syncItems(nextItems);
      if (shouldRender) {
        render();
      }
      emit();
    }

    function setCurrentStepCondition(conditionId) {
      const entry = savedConditionById(conditionId);
      if (!entry) {
        return;
      }
      stepCurrentConditionId = entry.conditionId;
      applyActiveStepCondition(entry);
      render();
      emit();
    }

    function removeSavedCondition(conditionId) {
      const nextSavedConditions = stepSavedConditions.filter((entry) => entry.conditionId !== conditionId);
      stepSavedConditions = nextSavedConditions;
      if (!nextSavedConditions.length) {
        stepCurrentConditionId = "";
        syncItems([]);
        render();
        emit();
        return;
      }

      if (stepCurrentConditionId === conditionId) {
        const replacement = nextSavedConditions[nextSavedConditions.length - 1];
        stepCurrentConditionId = replacement.conditionId;
        applyActiveStepCondition(replacement);
      }
      render();
      emit();
    }

    function selectedSavedConditionIds() {
      return stepSavedConditions
        .filter((entry) => Boolean(entry.selected))
        .map((entry) => entry.conditionId);
    }

    function toggleSavedConditionSelection(conditionId, selected) {
      stepSavedConditions = stepSavedConditions.map((entry) => (
        entry.conditionId === conditionId
          ? { ...entry, selected: Boolean(selected) }
          : entry
      ));
      render();
      emit();
    }

    function showStepToast(message, tone = "success") {
      if (window.AgentGuardUI?.showToast) {
        window.AgentGuardUI.showToast(message, tone);
      }
    }

    function combineSavedConditions(operation, selectedIds) {
      const selectedEntries = selectedIds
        .map((conditionId) => savedConditionById(conditionId))
        .filter(Boolean);
      if (!selectedEntries.length) {
        return;
      }

      if (operation === "reuse" && selectedEntries.length === 1) {
        stepSavedConditions = stepSavedConditions.map((entry) => ({ ...entry, selected: false }));
        stepCurrentConditionId = selectedEntries[0].conditionId;
        applyActiveStepCondition(selectedEntries[0]);
        render();
        emit();
        showStepToast(`Current result switched to ${selectedEntries[0].conditionId}.`);
        return;
      }

      let combinedItems = [];
      if (selectedEntries.length === 1) {
        combinedItems = exportItems(selectedEntries[0].items).map((item, index, items) => {
          const nextItem = {
            ...item,
            connector: index === 0 ? "" : String(item.connector || "AND"),
          };
          if (operation === "wrap") {
            if (index === 0) {
              nextItem.openParen = `${nextItem.openParen || ""}(`;
            }
            if (index === items.length - 1) {
              nextItem.closeParen = `)${nextItem.closeParen || ""}`;
            }
          }
          return nextItem;
        });
      } else {
        combinedItems = exportItems(selectedEntries[0].items).map((item, index) => ({
          ...item,
          connector: index === 0 ? "" : String(item.connector || "AND"),
        }));
        const appendedItems = exportItems(selectedEntries[1].items).map((item, index) => ({
          ...item,
          connector: index === 0 ? operation : String(item.connector || "AND"),
        }));
        combinedItems = combinedItems.concat(appendedItems);
      }

      const nextId = nextConditionId([
        ...state.items,
        ...stepSavedConditions.map((entry) => ({ conditionId: entry.conditionId })),
      ]);
      const nextEntry = buildSavedConditionEntry(nextId, combinedItems);
      stepSavedConditions = stepSavedConditions
        .map((entry) => ({ ...entry, selected: false }))
        .concat([{ ...nextEntry, selected: false }]);
      stepCurrentConditionId = nextEntry.conditionId;
      applyActiveStepCondition(nextEntry);
      render();
      emit();
    }

    function renderConfirmedItem(item, index, { showId = false, allowConnectorEdit = false } = {}) {
      const summary = document.createElement("div");
      summary.className = "condition-summary-line";

      const leading = document.createElement("div");
      leading.className = "condition-summary-main";

      if (showId) {
        const idTag = document.createElement("span");
        idTag.className = "condition-summary-id";
        idTag.textContent = item.conditionId || `COND${index + 1}`;
        leading.appendChild(idTag);
      } else {
        const label = document.createElement("span");
        label.className = "condition-summary-label";
        label.textContent = "COND: ";
        leading.appendChild(label);
      }

      const text = document.createElement("div");
      text.className = "condition-summary-rule";
      text.textContent = item.expression;
      leading.appendChild(text);
      summary.appendChild(leading);

      const trailing = document.createElement("div");
      trailing.className = "condition-summary-controls";
      if (allowConnectorEdit && index > 0) {
        const connectorSelect = createSelect(["AND", "OR"], item.connector || "AND", (event) => {
          updateItem(index, { connector: event.target.value });
        });
        trailing.appendChild(connectorSelect);
      }

      const actions = document.createElement("div");
      actions.className = "condition-summary-actions";
      actions.appendChild(createIconButton("modify.png", "Modify condition", () => modifyItem(index)));
      actions.appendChild(createIconButton("close.png", "Remove condition", () => removeItem(index)));
      trailing.appendChild(actions);
      summary.appendChild(trailing);

      return summary;
    }

    function modifyItem(index) {
      const nextItems = state.items.slice();
      if (builderMode === "step") {
        nextItems[index] = {
          ...nextItems[index],
          confirmed: false,
          stepStage: "comparison",
        };
      } else {
        nextItems[index] = {
          ...nextItems[index],
          confirmed: false,
          stepStage: "complete",
        };
      }
      syncItems(nextItems);
      render();
      emit();
    }

    function renderTraceFields(detailSection, item, index) {
      const symbolSelect = createSelect(symbols, item.symbol, (event) => {
        updateItem(index, { symbol: event.target.value });
      });
      detailSection.appendChild(createField("Tool Symbol", symbolSelect));

      const featureOptions = ["name", "label.boundary", "label.sensitivity", "label.integrity"];
      if (state.symbolToolMap[item.symbol]) {
        featureOptions.splice(1, 0, "syntax");
      }
      const featureSelect = createSelect(featureOptions, item.feature, (event) => {
        updateItem(index, { feature: event.target.value });
      });
      detailSection.appendChild(createField("Feature", featureSelect));

      if (item.feature === "syntax") {
        const params = inputParamsForTool(state.symbolToolMap[item.symbol] || "");
        const syntaxFieldSelect = createSelect(params.length ? params : [""], item.syntaxField, (event) => {
          updateItem(index, { syntaxField: event.target.value });
        });
        syntaxFieldSelect.disabled = !state.symbolToolMap[item.symbol];
        detailSection.appendChild(createField("Syntax Field", syntaxFieldSelect));
      }

      const operatorSelect = createSelect(traceFeatureOperators[item.feature], item.operator, (event) => {
        updateItem(index, { operator: event.target.value });
      });
      detailSection.appendChild(createField("Operator", operatorSelect));

      if (item.feature === "name") {
        const valueSelect = createSelect(toolOptions(), item.selectedToolKey, (event) => {
          const nextSelectedToolKey = event.target.value;
          updateItem(index, {
            selectedToolKey: nextSelectedToolKey,
            value: toolNameForKey(nextSelectedToolKey),
          });
        });
        detailSection.appendChild(createField("Value", valueSelect));
      } else if (item.feature.startsWith("label.")) {
        const valueSelect = createSelect(labelValues[item.feature], item.value, (event) => {
          updateItem(index, { value: event.target.value });
        });
        detailSection.appendChild(createField("Value", valueSelect));
      } else {
        const input = document.createElement("input");
        input.type = "text";
        input.value = item.value;
        input.placeholder = item.syntaxField ? `Value for ${item.syntaxField}` : "Value";
        input.addEventListener("input", (event) => {
          updateItem(index, { value: event.target.value }, { render: false });
        });
        detailSection.appendChild(createField("Value", input));
      }
    }

    function renderContextFields(detailSection, item, index) {
      const prefix = item.contextPrefix || "tool";
      const definition = contextDefinitionForPath(item.contextPath, prefix);

      const prefixSelect = createSelect(contextPrefixes(), prefix, (event) => {
        const nextPrefix = event.target.value;
        const firstField = contextFieldsForPrefix(nextPrefix)[0]?.value || `${nextPrefix}.raw`;
        updateItem(index, {
          contextPrefix: nextPrefix,
          contextField: firstField,
          contextFieldName: "",
          contextPath: buildContextPath(nextPrefix, firstField, "", ""),
          syntaxField: "",
          operator: contextDefinitionForPath(firstField, nextPrefix).operators?.[0] || "==",
          selectedToolKey: "",
          value: "",
        });
      });
      detailSection.appendChild(createField("Context", prefixSelect));

      const fieldSelect = createSelect(contextFieldsForPrefix(prefix), item.contextField, (event) => {
        const nextField = event.target.value;
        updateItem(index, {
          contextField: nextField,
          contextFieldName: "",
          contextPath: buildContextPath(prefix, nextField, "", ""),
          syntaxField: "",
          operator: contextDefinitionForPath(nextField, prefix).operators?.[0] || "==",
          selectedToolKey: "",
          value: "",
        });
      });
      detailSection.appendChild(createField("Field", fieldSelect));

      if (definition.kind === "free-field") {
        const fieldNameInput = document.createElement("input");
        fieldNameInput.type = "text";
        fieldNameInput.value = item.contextFieldName || "";
        fieldNameInput.placeholder = `${prefix} field`;
        fieldNameInput.addEventListener("input", (event) => {
          const nextFieldName = event.target.value;
          updateItem(index, {
            contextFieldName: nextFieldName,
            contextPath: buildContextPath(prefix, item.contextField, nextFieldName, ""),
          }, { render: false });
        });
        detailSection.appendChild(createField("Field Name", fieldNameInput));
      }

      if (definition.kind === "tool-syntax") {
        const params = inputParamsForTool(currentCallToolKey);
        const syntaxFieldSelect = createSelect(params.length ? params : [""], item.syntaxField, (event) => {
          const nextSyntaxField = event.target.value;
          updateItem(index, {
            syntaxField: nextSyntaxField,
            contextPath: buildContextPath(prefix, item.contextField, "", nextSyntaxField),
          });
        });
        detailSection.appendChild(createField("Syntax Field", syntaxFieldSelect));
      }

      const operatorSelect = createSelect(definition.operators || ["=="], item.operator, (event) => {
        updateItem(index, { operator: event.target.value });
      });
      detailSection.appendChild(createField("Operator", operatorSelect));

      if (definition.kind === "enum") {
        const valueSelect = createSelect(labelValues[definition.enumKey] || [""], item.value, (event) => {
          updateItem(index, { value: event.target.value });
        });
        detailSection.appendChild(createField("Value", valueSelect));
        return;
      }

      if (definition.kind === "tool-name") {
        const valueSelect = createSelect(toolOptions(), item.selectedToolKey, (event) => {
          const nextSelectedToolKey = event.target.value;
          updateItem(index, {
            selectedToolKey: nextSelectedToolKey,
            value: toolNameForKey(nextSelectedToolKey),
          });
        });
        detailSection.appendChild(createField("Value", valueSelect));
        return;
      }

      const input = document.createElement("input");
      input.type = "text";
      input.value = item.value;
      input.placeholder = definition.kind === "number" ? "Numeric value" : "Value";
      input.addEventListener("input", (event) => {
        updateItem(index, { value: event.target.value }, { render: false });
      });
      detailSection.appendChild(createField("Value", input));
    }

    function renderEditableItem(item, index, options = {}) {
      const showStructureFields = options.showStructureFields !== false;
      const card = document.createElement("div");
      card.className = "condition-card";

      const actions = document.createElement("div");
      actions.className = "condition-card-actions";
      actions.appendChild(createIconButton("confirm.png", "Confirm condition", () => confirmItem(index)));
      if (state.items.length > 1) {
        actions.appendChild(createIconButton("close.png", "Remove condition", () => removeItem(index)));
      }
      card.appendChild(actions);

      const detailSection = document.createElement("div");
      detailSection.className = "condition-detail-section";

      if (showStructureFields) {
        const openParenSelect = createSelect(["", "(", "(("], item.openParen || "", (event) => {
          updateItem(index, { openParen: event.target.value });
        });
        detailSection.appendChild(createField("Open Paren", openParenSelect));
      }

      const sourceTypeSelect = createSelect([
        { value: "trace", label: "Trace symbol" },
        { value: "context", label: "Current call context" },
      ], item.sourceType || "trace", (event) => {
        const nextSourceType = event.target.value;
        if (nextSourceType === "context") {
          updateItem(index, {
            sourceType: "context",
            symbol: "",
            feature: "",
            syntaxField: "",
            selectedToolKey: "",
            contextPrefix: "tool",
            contextField: "tool.name",
            contextFieldName: "",
            contextPath: "tool.name",
            operator: "==",
            value: toolOptions()[0]?.name || "",
          });
          return;
        }
        const firstTool = firstToolOption();
        updateItem(index, {
          sourceType: "trace",
          symbol: symbols[0] || "A",
          feature: "name",
          syntaxField: "",
          selectedToolKey: firstTool?.value || "",
          operator: "==",
          value: firstTool?.name || "",
          contextPrefix: "",
          contextField: "",
          contextFieldName: "",
          contextPath: "",
        });
      });
      Array.from(sourceTypeSelect.options || []).forEach((option) => {
        option.disabled = !allowedSourceTypes.has(option.value);
      });
      sourceTypeSelect.value = normalizeSourceType(item.sourceType || "trace");
      detailSection.appendChild(createField("Source", sourceTypeSelect));

      if (item.sourceType === "context") {
        renderContextFields(detailSection, item, index);
      } else {
        renderTraceFields(detailSection, item, index);
      }

      if (showStructureFields) {
        const closeParenSelect = createSelect(["", ")", "))"], item.closeParen || "", (event) => {
          updateItem(index, { closeParen: event.target.value });
        });
        detailSection.appendChild(createField("Close Paren", closeParenSelect));
      }

      card.appendChild(detailSection);
      return card;
    }

    function renderStepStageHeader(item, stage) {
      const order = stageOrderForItem(item).filter((entry) => entry !== "complete");
      const stepNumber = Math.max(order.indexOf(stage) + 1, 1);
      const stageMeta = {
        source: { title: "Choose rule scope", copy: "Select the tool format" },
        symbol: { title: "Choose tool node", copy: "Choose the tool node you want to inspect." },
        property: { title: "Choose property", copy: "Select the property and subproperty to constrain." },
        comparison: { title: "Choose relation and target value", copy: "Set the comparison operator and the target value." },
      }[stage];

      const header = document.createElement("div");
      header.className = "condition-step-header";

      const kicker = document.createElement("p");
      kicker.className = "condition-step-kicker";
      kicker.textContent = `Step ${stepNumber}`;
      header.appendChild(kicker);

      const title = document.createElement("h5");
      title.className = "condition-step-title";
      title.textContent = stageMeta.title;
      header.appendChild(title);

      const copy = document.createElement("p");
      copy.className = "condition-step-copy";
      copy.textContent = stageMeta.copy;
      header.appendChild(copy);
      return header;
    }

    function renderStepProgress(item) {
      const order = stageOrderForItem(item).filter((stage) => stage !== "complete");
      const currentStage = currentStageForItem(item);
      const activeIndex = order.indexOf(currentStage);
      const progress = document.createElement("div");
      progress.className = "condition-step-progress";

      order.forEach((stageName, index) => {
        const dot = document.createElement("span");
        dot.className = "condition-step-progress-dot";
        if (index < activeIndex) {
          dot.classList.add("is-complete");
        } else if (index === activeIndex) {
          dot.classList.add("is-active");
        }
        progress.appendChild(dot);

        if (index < order.length - 1) {
          const segment = document.createElement("span");
          segment.className = "condition-step-progress-segment";
          if (index < activeIndex) {
            segment.classList.add("is-complete");
          }
          progress.appendChild(segment);
        }
      });

      return progress;
    }

    function renderStepSource(detailSection, item, index) {
      const options = [
        { value: "trace", label: "Path rule" },
        { value: "context", label: "Single tool rule" },
      ];
      const select = createSelect(options, item.sourceType, (event) => {
        const nextSourceType = event.target.value;
        if (nextSourceType === "context") {
          updateItem(index, {
            sourceType: "context",
            symbol: "",
            feature: "",
            propertyGroup: "",
            syntaxField: "",
            selectedToolKey: "",
            contextPrefix: "",
            contextField: "",
            contextFieldName: "",
            contextPath: "",
            operator: "",
            value: "",
          });
          return;
        }
        updateItem(index, {
          sourceType: "trace",
          symbol: symbols[0] || "A",
          feature: "",
          propertyGroup: "",
          syntaxField: "",
          selectedToolKey: "",
          operator: "",
          value: "",
          contextPrefix: "",
          contextField: "",
          contextFieldName: "",
          contextPath: "",
        });
      });
      Array.from(select.options || []).forEach((option) => {
        option.disabled = !allowedSourceTypes.has(option.value);
      });
      detailSection.appendChild(createField("Rule Scope", select));
    }

    function renderStepProperty(detailSection, item, index) {
      if (item.sourceType === "trace") {
        const propertyOptions = tracePropertyOptionsForItem(item);
        const selectedGroup = item.propertyGroup || traceGroupFromFeature(item.feature);
        const select = createSelect([
          { value: "", label: "Select property" },
          ...propertyOptions,
        ], selectedGroup, (event) => {
          const nextGroup = event.target.value;
          if (!nextGroup) {
            updateItem(index, {
              propertyGroup: "",
              feature: "",
              syntaxField: "",
              operator: "",
              selectedToolKey: "",
              value: "",
            });
            return;
          }
          if (nextGroup === "name") {
            updateItem(index, {
              propertyGroup: "name",
              feature: "name",
              syntaxField: "",
              operator: "",
              selectedToolKey: "",
              value: "",
            });
            return;
          }
          if (nextGroup === "label") {
            updateItem(index, {
              propertyGroup: "label",
              feature: "",
              syntaxField: "",
              operator: "",
              selectedToolKey: "",
              value: "",
            });
            return;
          }
          updateItem(index, {
            propertyGroup: "syntax",
            feature: "syntax",
            syntaxField: "",
            operator: "",
            selectedToolKey: "",
            value: "",
          });
        });
        detailSection.appendChild(createField("Property", select));
        renderStepSubproperty(detailSection, item, index);
        return;
      }

      const select = createSelect([
        { value: "", label: "Select property" },
        ...contextPropertyGroups,
      ], item.contextPrefix || "", (event) => {
        const nextPrefix = event.target.value;
        if (!nextPrefix) {
          updateItem(index, {
            contextPrefix: "",
            contextField: "",
            contextFieldName: "",
            contextPath: "",
            syntaxField: "",
            operator: "",
            selectedToolKey: "",
            value: "",
          });
          return;
        }
        updateItem(index, {
          contextPrefix: nextPrefix,
          contextField: "",
          contextFieldName: "",
          contextPath: "",
          syntaxField: "",
          operator: "",
          selectedToolKey: "",
          value: "",
        });
      });
      detailSection.appendChild(createField("Property", select));
      renderStepSubproperty(detailSection, item, index);
    }

    function renderStepSubproperty(detailSection, item, index) {
      if (item.sourceType === "trace") {
        const group = item.propertyGroup || traceGroupFromFeature(item.feature);
        if (!group || group === "name") {
          return;
        }
        if (group === "label") {
          const labelOptions = [
            { value: "", label: "Select sub-property" },
            { value: "label.boundary", label: "boundary" },
            { value: "label.sensitivity", label: "sensitivity" },
            { value: "label.integrity", label: "integrity" },
          ];
          const select = createSelect(labelOptions, item.feature, (event) => {
            const nextFeature = event.target.value;
            updateItem(index, {
              feature: nextFeature,
              operator: "",
              value: "",
            });
          });
          detailSection.appendChild(createField("Sub-property", select));
          return;
        }

        const params = inputParamsForTool(state.symbolToolMap[item.symbol] || "");
        const syntaxFieldSelect = createSelect([{ value: "", label: "Select sub-property" }, ...(params.length ? params : [""])], item.syntaxField, (event) => {
          updateItem(index, { syntaxField: event.target.value });
        });
        detailSection.appendChild(createField("Sub-property", syntaxFieldSelect));
        return;
      }

      const prefix = item.contextPrefix || "tool";
      if (!item.contextField && !item.contextPath) {
        return;
      }
      const fieldSelect = createSelect([{ value: "", label: "Select sub-property" }, ...contextFieldsForPrefix(prefix)], item.contextField, (event) => {
        const nextField = event.target.value;
        if (!nextField) {
          updateItem(index, {
            contextField: "",
            contextFieldName: "",
            contextPath: "",
            syntaxField: "",
            operator: "",
            selectedToolKey: "",
            value: "",
          });
          return;
        }
        updateItem(index, {
          contextField: nextField,
          contextFieldName: "",
          contextPath: buildContextPath(prefix, nextField, "", ""),
          syntaxField: "",
          operator: "",
          selectedToolKey: "",
          value: "",
        });
      });
      detailSection.appendChild(createField("Sub-property", fieldSelect));

      const definition = contextDefinitionForItem(item);
      if (definition.kind === "free-field") {
        const fieldNameInput = document.createElement("input");
        fieldNameInput.type = "text";
        fieldNameInput.value = item.contextFieldName || "";
        fieldNameInput.placeholder = `${prefix} field`;
        fieldNameInput.addEventListener("input", (event) => {
          const nextFieldName = event.target.value;
          updateItem(index, {
            contextFieldName: nextFieldName,
            contextPath: buildContextPath(prefix, item.contextField, nextFieldName, ""),
          }, { render: false });
        });
        detailSection.appendChild(createField("Custom field name", fieldNameInput));
      }

      if (definition.kind === "tool-syntax") {
        const params = inputParamsForTool(currentCallToolKey);
        const syntaxFieldSelect = createSelect(params.length ? params : [""], item.syntaxField, (event) => {
          const nextSyntaxField = event.target.value;
          updateItem(index, {
            syntaxField: nextSyntaxField,
            contextPath: buildContextPath(prefix, item.contextField, "", nextSyntaxField),
          });
        });
        detailSection.appendChild(createField("Syntax field", syntaxFieldSelect));
      }
    }

    function renderStepComparison(detailSection, item, index) {
      if (item.sourceType === "trace") {
        const operatorSelect = createSelect([{ value: "", label: "Select comparison" }, ...(traceFeatureOperators[item.feature] || [])], item.operator, (event) => {
          updateItem(index, { operator: event.target.value });
        });
        detailSection.appendChild(createField("Comparison", operatorSelect));

        if (item.feature === "name") {
          const valueSelect = createSelect([{ value: "", label: "Select target value" }, ...toolOptions()], item.selectedToolKey, (event) => {
            const nextSelectedToolKey = event.target.value;
            updateItem(index, {
              selectedToolKey: nextSelectedToolKey,
              value: toolNameForKey(nextSelectedToolKey),
            });
          });
          detailSection.appendChild(createField("Target value", valueSelect));
          return;
        }

        if (item.feature.startsWith("label.")) {
          const valueSelect = createSelect([{ value: "", label: "Select target value" }, ...labelValues[item.feature]], item.value, (event) => {
            updateItem(index, { value: event.target.value });
          });
          detailSection.appendChild(createField("Target value", valueSelect));
          return;
        }

        const input = document.createElement("input");
        input.type = "text";
        input.value = item.value;
        input.placeholder = item.syntaxField ? `Value for ${item.syntaxField}` : "Value";
        input.addEventListener("input", (event) => {
          updateItem(index, { value: event.target.value }, { render: false });
        });
        detailSection.appendChild(createField("Target value", input));
        return;
      }

      const definition = contextDefinitionForItem(item);
      const operatorSelect = createSelect([{ value: "", label: "Select comparison" }, ...(definition.operators || [])], item.operator, (event) => {
        updateItem(index, { operator: event.target.value });
      });
      detailSection.appendChild(createField("Comparison", operatorSelect));

      if (definition.kind === "enum") {
        const valueSelect = createSelect([{ value: "", label: "Select target value" }, ...(labelValues[definition.enumKey] || [""])], item.value, (event) => {
          updateItem(index, { value: event.target.value });
        });
        detailSection.appendChild(createField("Target value", valueSelect));
        return;
      }

      if (definition.kind === "tool-name") {
        const valueSelect = createSelect([{ value: "", label: "Select target value" }, ...toolOptions()], item.selectedToolKey, (event) => {
          const nextSelectedToolKey = event.target.value;
          updateItem(index, {
            selectedToolKey: nextSelectedToolKey,
            value: toolNameForKey(nextSelectedToolKey),
          });
        });
        detailSection.appendChild(createField("Target value", valueSelect));
        return;
      }

      const input = document.createElement("input");
      input.type = "text";
      input.value = item.value;
      input.placeholder = definition.kind === "number" ? "Numeric value" : "Value";
      input.addEventListener("input", (event) => {
        updateItem(index, { value: event.target.value }, { render: false });
      });
      detailSection.appendChild(createField("Target value", input));
    }

    function renderGuidedItem(item, index) {
      const stage = currentStageForItem(item);
      const card = document.createElement("div");
      card.className = "condition-card condition-step-card";

      const actions = document.createElement("div");
      actions.className = "condition-card-actions";
      if (state.items.length > 1 || stepItems().length > 0) {
        actions.appendChild(createIconButton("close.png", "Remove condition", () => removeItem(index)));
      }
      card.appendChild(actions);
      card.appendChild(renderStepStageHeader(item, stage));

      const detailSection = document.createElement("div");
      detailSection.className = "condition-detail-section";

      if (stage === "source") {
        renderStepSource(detailSection, item, index);
      } else if (stage === "symbol") {
        const symbolSelect = createSelect(symbols, item.symbol, (event) => {
          updateItem(index, { symbol: event.target.value });
        });
        detailSection.appendChild(createField("Path tool", symbolSelect));
      } else if (stage === "property") {
        renderStepProperty(detailSection, item, index);
      } else if (stage === "comparison") {
        renderStepComparison(detailSection, item, index);
      }

      card.appendChild(detailSection);

      if (stage === "comparison") {
        const preview = document.createElement("pre");
        preview.className = "condition-step-preview";
        preview.textContent = buildItemExpression(item) || "<incomplete>";
        card.appendChild(preview);
      }

      const actionRow = document.createElement("div");
      actionRow.className = "condition-step-nav";
      if (stage !== "source") {
        const backButton = document.createElement("button");
        backButton.type = "button";
        backButton.className = "btn condition-step-nav-button";
        backButton.textContent = "<";
        backButton.addEventListener("click", () => {
          updateItem(index, { stepStage: previousStage(item) });
        });
        actionRow.appendChild(backButton);
      } else {
        const spacer = document.createElement("span");
        spacer.className = "condition-step-nav-spacer";
        actionRow.appendChild(spacer);
      }

      actionRow.appendChild(renderStepProgress(item));

      const nextButton = document.createElement("button");
      nextButton.type = "button";
      nextButton.className = "btn primary condition-step-nav-button";
      if (stage === "comparison") {
        nextButton.setAttribute("aria-label", "Generate single rule");
        nextButton.textContent = "Create >";
        nextButton.disabled = !canAdvanceStage(item);
        nextButton.addEventListener("click", () => confirmItem(index));
      } else {
        nextButton.setAttribute("aria-label", "Next builder step");
        nextButton.textContent = ">";
        nextButton.disabled = !canAdvanceStage(item);
        nextButton.addEventListener("click", () => {
          updateItem(index, { stepStage: nextStage(item) });
        });
      }
      actionRow.appendChild(nextButton);
      card.appendChild(actionRow);
      return card;
    }

    function confirmItem(index) {
      const currentItem = state.items[index];
      if (!currentItem?.expression) {
        return;
      }

      const nextId = currentItem.conditionId || nextConditionId([
        ...state.items,
        ...stepSavedConditions.map((entry) => ({ conditionId: entry.conditionId })),
      ]);
      const confirmedItem = {
        ...exportItem(currentItem),
        conditionId: nextId,
        connector: "",
        confirmed: true,
        stepStage: "complete",
      };
      const nextEntry = buildSavedConditionEntry(nextId, [confirmedItem]);
      stepSavedConditions = stepSavedConditions
        .map((entry) => ({ ...entry, selected: false }))
        .concat([{ ...nextEntry, selected: false }]);
      stepCurrentConditionId = nextEntry.conditionId;
      applyActiveStepCondition(nextEntry);
      render();
      emit();
    }

    function renderStepList() {
      const wrap = document.createElement("div");
      wrap.className = "condition-step-list";

      const header = document.createElement("div");
      header.className = "condition-step-list-header";
      const title = document.createElement("strong");
      title.textContent = "Saved Conditions";
      header.appendChild(title);
      mountStepActions(header);
      wrap.appendChild(header);

      if (!stepSavedConditions.length) {
        const empty = document.createElement("div");
        empty.className = "empty-state";
        empty.textContent = "No saved conditions yet. Finish the guided builder to create COND1.";
        wrap.appendChild(empty);
        return wrap;
      }

      stepSavedConditions.forEach((entry) => {
        const row = document.createElement("div");
        row.className = "condition-summary-line";

        const leading = document.createElement("div");
        leading.className = "condition-summary-main";

        const checkbox = document.createElement("input");
        checkbox.type = "checkbox";
        checkbox.className = "condition-summary-checkbox";
        checkbox.checked = Boolean(entry.selected);
        checkbox.addEventListener("change", (event) => {
          toggleSavedConditionSelection(entry.conditionId, event.target.checked);
        });
        leading.appendChild(checkbox);

        const idTag = document.createElement("span");
        idTag.className = "condition-summary-id";
        idTag.textContent = entry.conditionId;
        leading.appendChild(idTag);

        const text = document.createElement("div");
        text.className = "condition-summary-rule";
        text.textContent = entry.expression;
        leading.appendChild(text);
        row.appendChild(leading);

        const trailing = document.createElement("div");
        trailing.className = "condition-summary-controls";

        const actions = document.createElement("div");
        actions.className = "condition-summary-actions";
        actions.appendChild(createIconButton("confirm.png", "Use saved condition", () => {
          setCurrentStepCondition(entry.conditionId);
        }));
        actions.appendChild(createIconButton("close.png", "Remove saved condition", () => {
          removeSavedCondition(entry.conditionId);
        }));
        trailing.appendChild(actions);
        row.appendChild(trailing);

        wrap.appendChild(row);
      });

      const combineRow = document.createElement("div");
      combineRow.className = "condition-step-combine";

      const selectedIds = selectedSavedConditionIds();
      const combineInfo = document.createElement("div");
      combineInfo.className = "condition-step-combine-copy";

      const combineHeader = document.createElement("div");
      combineHeader.className = "condition-step-combine-header";

      const combineLabel = document.createElement("strong");
      combineLabel.textContent = "Combine Mode";
      combineHeader.appendChild(combineLabel);

      const combineMeta = document.createElement("div");
      combineMeta.className = "condition-step-combine-meta";

      const combineDescription = document.createElement("p");
      combineDescription.className = "subtle";
      if (selectedIds.length === 1) {
        combineDescription.textContent = "Wrap with () or select as result";
      } else if (selectedIds.length === 2) {
        combineDescription.textContent = "Combine expressions with AND or OR.";
      } else {
        combineDescription.textContent = "";
      }
      combineMeta.appendChild(combineDescription);

      const infoWrap = document.createElement("div");
      infoWrap.className = "hint-wrap";

      const infoDot = document.createElement("span");
      infoDot.className = "hint-dot";
      infoDot.textContent = "i";
      infoWrap.appendChild(infoDot);

      const infoBubble = document.createElement("div");
      infoBubble.className = "hint-bubble";
      infoBubble.textContent = "Select one or two saved expressions, then choose how to combine them.";
      infoWrap.appendChild(infoBubble);

      combineHeader.appendChild(infoWrap);
      combineInfo.appendChild(combineHeader);
      combineInfo.appendChild(combineMeta);
      combineRow.appendChild(combineInfo);

      const combineOptions = [{ value: "", label: "Combine selected" }];
      if (selectedIds.length === 1) {
        combineOptions.push({ value: "wrap", label: "Wrap with ()" });
        combineOptions.push({ value: "reuse", label: "Use as current result" });
      } else if (selectedIds.length === 2) {
        combineOptions.push({ value: "AND", label: "Combine with AND" });
        combineOptions.push({ value: "OR", label: "Combine with OR" });
      }
      const combineSelect = createSelect(combineOptions, "", (event) => {
        const operation = event.target.value;
        if (!operation) {
          return;
        }
        combineSavedConditions(operation, selectedIds);
      });
      combineSelect.disabled = selectedIds.length === 0 || selectedIds.length > 2;
      combineRow.appendChild(combineSelect);
      wrap.appendChild(combineRow);
      return wrap;
    }

    function renderCurrentResult() {
      const currentEntry = activeStepCondition();
      if (!currentEntry) {
        return null;
      }

      const currentResult = document.createElement("div");
      currentResult.className = "condition-current-result";

      const currentLabel = document.createElement("div");
      currentLabel.className = "condition-current-result-label";
      currentLabel.textContent = "Current Result";
      currentResult.appendChild(currentLabel);

      const currentBody = document.createElement("div");
      currentBody.className = "condition-current-result-body";

      const currentId = document.createElement("span");
      currentId.className = "condition-summary-id";
      currentId.textContent = currentEntry.conditionId;
      currentBody.appendChild(currentId);

      const currentRule = document.createElement("div");
      currentRule.className = "condition-summary-rule";
      currentRule.textContent = currentEntry.expression;
      currentBody.appendChild(currentRule);

      currentResult.appendChild(currentBody);
      return currentResult;
    }

    function renderItem(item, index) {
      return item.confirmed ? renderConfirmedItem(item, index) : renderEditableItem(item, index);
    }

    function renderDirectMode() {
      if (flow) {
        flow.hidden = true;
      }
      root.innerHTML = "";
      mountDefaultActions();
      if (!state.items.length) {
        const empty = document.createElement("div");
        empty.className = "empty-state";
        empty.textContent = locked
          ? "CONDITION is locked until PATH is confirmed."
          : "CONDITION is empty. Click + to add the first condition.";
        root.appendChild(empty);
        syncLockState();
        return;
      }

      state.items.forEach((item, index) => {
        if (index > 0) {
          const connectorSection = document.createElement("div");
          connectorSection.className = item.confirmed
            ? "condition-connector-line"
            : "condition-connector-section";

          if (item.confirmed) {
            const connectorLabel = document.createElement("span");
            connectorLabel.className = "condition-connector-text";
            connectorLabel.textContent = item.connector || "AND";
            connectorSection.appendChild(connectorLabel);
          } else {
            const connectorSelect = createSelect(["AND", "OR"], item.connector || "AND", (event) => {
              updateItem(index, { connector: event.target.value });
            });
            connectorSection.appendChild(createField("Connector", connectorSelect));
          }

          root.appendChild(connectorSection);
        }

        root.appendChild(renderItem(item, index));
      });
      syncLockState();
    }

    function renderStepMode() {
      root.innerHTML = "";
      mountDefaultActions();
      seedStepSavedConditionsFromState();
      const draftIndex = currentDraftIndex();
      if (flow) {
        flow.hidden = true;
      }
      const hasVisibleStepState = Boolean(stepSavedConditions.length || draftIndex >= 0);
      if (!hasVisibleStepState) {
        const empty = document.createElement("div");
        empty.className = "empty-state";
        empty.textContent = locked
          ? "CONDITION is locked until PATH or ON is ready."
          : "Step condition builder is empty. Click + to start the guided condition wizard.";
        root.appendChild(empty);
        syncLockState();
        return;
      }

      if (draftIndex >= 0) {
        const marker = document.createElement("div");
        marker.className = "condition-step-marker";
        marker.textContent = "Guided Builder";
        root.appendChild(marker);
        if (flow) {
          flow.hidden = false;
          root.appendChild(flow);
        }
        root.appendChild(renderGuidedItem(state.items[draftIndex], draftIndex));
      }

      root.appendChild(renderStepList());

      const currentResult = renderCurrentResult();
      if (currentResult) {
        root.appendChild(currentResult);
      }

      syncLockState();
    }

    function render() {
      updateModeUI();
      if (builderMode === "step") {
        renderStepMode();
        return;
      }
      renderDirectMode();
    }

    function addCondition() {
      if (locked || hasIncompleteStep()) {
        return;
      }
      const connector = state.items.length ? "AND" : "";
      syncItems(state.items.concat([buildDefaultDraft(connector)]));
      render();
      emit();
    }

    if (addButton) {
      addButton.addEventListener("click", addCondition);
    }
    stepModeButton?.addEventListener("click", () => {
      builderMode = "step";
      render();
      emit();
    });
    directModeButton?.addEventListener("click", () => {
      builderMode = "direct";
      render();
      emit();
    });

    const api = {
      getValue() {
        return {
          items: exportItems(state.items),
          symbolToolMap: { ...state.symbolToolMap },
          savedConditions: stepSavedConditions.map((entry) => ({
            conditionId: entry.conditionId,
            expression: entry.expression,
            items: exportItems(entry.items),
          })),
          currentConditionId: stepCurrentConditionId,
          expression: expressionForItems(state.items, { completeOnly: builderMode === "step" }),
        };
      },
      getMode() {
        return builderMode;
      },
      setMode(nextMode) {
        builderMode = String(nextMode || "").trim() === "direct" ? "direct" : "step";
        if (builderMode === "step") {
          seedStepSavedConditionsFromState();
        }
        render();
        emit();
      },
      setValue(value) {
        syncItems(Array.isArray(value?.items) ? value.items : value);
        initializeStepState(value, state.items);
        if (builderMode === "step" && !hasIncompleteStep() && stepCurrentConditionId) {
          const activeEntry = savedConditionById(stepCurrentConditionId);
          if (activeEntry) {
            applyActiveStepCondition(activeEntry);
          }
        }
        render();
        updateHint();
      },
      setLocked(nextLocked) {
        locked = Boolean(nextLocked);
        render();
        updateHint();
      },
      setAllowedSourceTypes(nextAllowedSourceTypes) {
        allowedSourceTypes = new Set(
          Array.isArray(nextAllowedSourceTypes) && nextAllowedSourceTypes.length
            ? nextAllowedSourceTypes
            : [],
        );
        syncItems(state.items);
        render();
        emit();
      },
      setPathSymbols(nextSymbols) {
        symbols = nextSymbols && nextSymbols.length ? nextSymbols : ["A"];
        state = state.items.length
          ? normalizeItems({ items: state.items }, symbols, { currentCallToolKey })
          : { items: [], symbolToolMap: {} };
        syncItems(state.items);
        render();
        emit();
      },
      setCurrentCallToolKey(nextToolKey) {
        currentCallToolKey = String(nextToolKey || "");
        if (state.items.some((item) => item.sourceType === "context" && item.contextField === "tool.syntax")) {
          syncItems(state.items);
          render();
          emit();
        }
      },
      clear() {
        state = { items: [], symbolToolMap: {} };
        stepSavedConditions = [];
        stepCurrentConditionId = "";
        render();
        emit();
      },
      validate() {
        if (!state.items.length) {
          return { ok: false, message: "At least one condition is required." };
        }
        let balance = 0;
        for (const item of state.items) {
          if (builderMode === "step" && item.stepStage !== "complete") {
            return { ok: false, message: "Finish the guided condition builder before continuing." };
          }
          if (!item.expression) {
            return { ok: false, message: "One condition is incomplete." };
          }
          if (item.sourceType === "trace" && item.feature === "syntax" && !state.symbolToolMap[item.symbol]) {
            return { ok: false, message: "Trace syntax conditions need an inferred tool mapping first." };
          }
          balance += (item.openParen || "").length;
          balance -= (item.closeParen || "").length;
          if (balance < 0) {
            return { ok: false, message: "Parentheses are not balanced." };
          }
        }
        if (balance !== 0) {
          return { ok: false, message: "Parentheses are not balanced." };
        }
        return { ok: true, message: "CONDITION is valid." };
      },
    };

    render();
    updateHint();
    return api;
  }

  window.AgentGuardConditionBuilder = {
    createConditionBuilder,
    inferSymbolToolMap,
    normalizeItems,
  };
})();

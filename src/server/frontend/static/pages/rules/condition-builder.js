(function () {
  const toolCatalogHelpers = window.AgentGuardToolCatalog || {};
  const uiHelpers = window.AgentGuardUIHelpers || {};

  const labelValues = {
    "label.boundary": ["internal", "external", "privileged"],
    "label.sensitivity": ["low", "moderate", "high"],
    "label.integrity": ["trusted", "unfiltered"],
  };

  const principalRoleValues = ["basic", "default", "privileged", "system"];

  const traceFeatureOperators = {
    name: ["==", "!=", "IN", "NOT IN"],
    "label.boundary": ["==", "!=", "IN", "NOT IN"],
    "label.sensitivity": ["==", "!=", "IN", "NOT IN"],
    "label.integrity": ["==", "!=", "IN", "NOT IN"],
    syntax: ["==", "!=", ">", ">=", "<", "<=", "IN", "NOT IN", "MATCHES", "contains"],
  };

  const contextDefinitions = {
    tool: [
      { value: "tool.name", label: "tool.name", kind: "tool-name", operators: ["==", "!=", "IN", "NOT IN"] },
      { value: "tool.boundary", label: "tool.boundary", kind: "enum", enumKey: "label.boundary", operators: ["==", "!=", "IN", "NOT IN"] },
      { value: "tool.sensitivity", label: "tool.sensitivity", kind: "enum", enumKey: "label.sensitivity", operators: ["==", "!=", "IN", "NOT IN"] },
      { value: "tool.integrity", label: "tool.integrity", kind: "enum", enumKey: "label.integrity", operators: ["==", "!=", "IN", "NOT IN"] },
      { value: "tool.syntax", label: "tool.<syntax field>", kind: "tool-syntax", operators: ["==", "!=", ">", ">=", "<", "<=", "IN", "NOT IN", "MATCHES", "contains"] },
      { value: "tool.result", label: "tool.result", kind: "text", operators: ["==", "!=", "IN", "NOT IN", "MATCHES", "contains"] },
    ],
    principal: [
      { value: "principal.role", label: "user.role", kind: "enum", enumValues: principalRoleValues, operators: ["==", "!=", "IN", "NOT IN"] },
      { value: "principal.trust_level", label: "user.trust_level", kind: "number", operators: ["==", "!=", ">", ">=", "<", "<="] },
    ],
  };

  const tracePropertyGroups = [
    { value: "name", label: "Tool name" },
    { value: "label", label: "Tool label" },
    { value: "syntax", label: "Tool syntax" },
  ];

  const contextPropertyGroups = [
    { value: "tool", label: "tool" },
    { value: "principal", label: "user" }
  ];

  const principalContextSubpropertyGroups = [
    { value: "principal.role", label: "role" },
    { value: "principal.trust_level", label: "trust_level" },
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

  function toolNameForKey(toolKey) {
    if (typeof toolCatalogHelpers.toolNameForKey === "function") {
      return toolCatalogHelpers.toolNameForKey(toolKey, toolCatalog(), window.AgentGuardData?.findToolByKey);
    }
    const match = window.AgentGuardData?.findToolByKey?.(toolCatalog(), toolKey);
    return match ? match.name : "";
  }

  function displaySymbol(symbol) {
    const normalized = String(symbol || "").trim() || "A";
    return `Tool ${normalized}`;
  }

  function inputParamsForTool(toolKey) {
    const match = window.AgentGuardData?.findToolByKey?.(toolCatalog(), toolKey);
    return match ? match.input_params : [];
  }

  function toolContextSubpropertyLabel(value) {
    const normalized = String(value || "").trim();
    if (!normalized) {
      return "";
    }
    if (normalized === "tool.name") {
      return "name";
    }
    if (normalized === "tool.boundary") {
      return "label-boundary";
    }
    if (normalized === "tool.sensitivity") {
      return "label-sensitivity";
    }
    if (normalized === "tool.integrity") {
      return "label-integrity";
    }
    if (normalized === "tool.result") {
      return "result";
    }
    if (normalized.startsWith("tool.")) {
      return `param-${normalized.slice("tool.".length)}`;
    }
    return normalized;
  }

  function normalizeToolNameToken(value) {
    return String(value || "")
      .trim()
      .toLowerCase()
      .replace(/[.\s-]+/g, "_");
  }

  function serializeOperator(operator) {
    if (operator === "contains") {
      return "CONTAINS";
    }
    return String(operator || "").trim();
  }

  function serializeComparisonValue(item) {
    const rawValue = String(item?.value || "").trim();
    const operator = serializeOperator(item?.operator);
    const sourceType = String(item?.sourceType || "trace").trim() || "trace";
    if (operator === "IN" || operator === "NOT IN") {
      return rawValue;
    }
    if (
      (item?.feature === "syntax" || sourceType === "context")
      && /^-?\d+(?:\.\d+)?$/.test(rawValue)
    ) {
      return rawValue;
    }
    return `"${rawValue.replace(/\\/g, "\\\\").replace(/"/g, '\\"')}"`;
  }

  function comparisonOptionLabel(value) {
    const normalized = String(value || "").trim();
    return normalized === "contains" ? "CONTAINS" : normalized;
  }

  function isMembershipOperator(operator) {
    const normalized = String(operator || "").trim().toUpperCase();
    return normalized === "IN" || normalized === "NOT IN";
  }

  function formatSetLiteral(values) {
    const items = (Array.isArray(values) ? values : [])
      .map((value) => String(value || "").trim())
      .filter(Boolean)
      .map((value) => `"${value.replace(/\\/g, "\\\\").replace(/"/g, '\\"')}"`);
    return items.length ? `{${items.join(", ")}}` : "";
  }

  function parseSetLiteralEntries(rawValue) {
    const trimmed = String(rawValue || "").trim();
    if (!trimmed.startsWith("{") || !trimmed.endsWith("}")) {
      return [];
    }

    const inner = trimmed.slice(1, -1).trim();
    if (!inner) {
      return [];
    }

    const matches = inner.match(/"((?:\\.|[^"])*)"|([^,{}]+)/g) || [];
    return matches
      .map((entry) => {
        const candidate = String(entry || "").trim();
        if (!candidate) {
          return "";
        }
        const quoted = candidate.match(/^"((?:\\.|[^"])*)"$/);
        if (quoted) {
          return quoted[1]
            .replace(/\\"/g, "\"")
            .replace(/\\\\/g, "\\");
        }
        return candidate.replace(/^,\s*/, "").trim();
      })
      .filter(Boolean);
  }

  function membershipEditorValue(rawValue) {
    const entries = parseSetLiteralEntries(rawValue);
    if (entries.length) {
      return entries.join("\n");
    }
    return String(rawValue || "").trim();
  }

  function normalizeMembershipValueInput(rawValue) {
    const trimmed = String(rawValue || "").trim();
    if (!trimmed) {
      return "";
    }
    if (trimmed.startsWith("{") && trimmed.endsWith("}")) {
      return trimmed;
    }
    if (!/[\r\n,]/.test(trimmed)) {
      return trimmed;
    }

    const entries = trimmed
      .split(/\r?\n|,/)
      .map((entry) => String(entry || "").trim())
      .filter(Boolean);
    return formatSetLiteral(entries) || trimmed;
  }

  function membershipPlaceholder(definition) {
    if (definition?.kind === "enum") {
      return "One item per line, or a collection ref like denylist.roles";
    }
    if (definition?.kind === "tool-name") {
      return "One tool name per line, or a collection ref like allowlist.tools";
    }
    return "One item per line, or a collection ref like allowlist.http";
  }

  function uniqueToolNameOptions() {
    const seen = new Set();
    return toolOptions().reduce((acc, option) => {
      const name = String(option?.name || option?.label || "").trim();
      if (!name || seen.has(name)) {
        return acc;
      }
      seen.add(name);
      acc.push({ value: name, label: name });
      return acc;
    }, []);
  }

  function membershipOptionEntries(source) {
    if (!source) {
      return [];
    }
    if (source.feature === "name" || source.kind === "tool-name") {
      return uniqueToolNameOptions();
    }
    if (String(source.feature || "").startsWith("label.")) {
      return (labelValues[source.feature] || []).map((value) => ({ value, label: value }));
    }
    if (source.kind === "enum") {
      const values = Array.isArray(source.enumValues)
        ? source.enumValues
        : (labelValues[source.enumKey] || []);
      return values.map((value) => ({ value, label: value }));
    }
    return [];
  }

  function contextDefinitionForPath(path, prefixHint = "") {
    if (path) {
      const prefix = String(path).split(".")[0];
      const exact = (contextDefinitions[prefix] || []).find((item) => item.value === path);
      if (exact) {
        return exact;
      }
      if (prefix === "tool") {
        return contextDefinitions.tool.find((item) => item.value === "tool.syntax");
      }
    }
    const hinted = (contextDefinitions[prefixHint] || [])[0];
    return hinted || contextDefinitions.tool[0];
  }

  function buildContextPath(fieldValue, syntaxField) {
    if (fieldValue === "tool.syntax") {
      return syntaxField ? `tool.${syntaxField}` : "";
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

  function createField(labelText, control, className = "") {
    const wrap = document.createElement("label");
    wrap.className = `field condition-tree-field${className ? ` ${className}` : ""}`;
    const label = document.createElement("span");
    label.textContent = labelText;
    wrap.appendChild(label);
    wrap.appendChild(control);
    return wrap;
  }

  function createButton(text, className, onClick, ariaLabel = "") {
    const button = document.createElement("button");
    button.type = "button";
    button.className = className;
    button.textContent = text;
    if (ariaLabel) {
      button.setAttribute("aria-label", ariaLabel);
      button.setAttribute("title", ariaLabel);
    }
    button.addEventListener("click", onClick);
    return button;
  }

  function createAssetIconButton(iconName, ariaLabel, onClick) {
    const createIconButton = uiHelpers.createIconButton || function fallbackCreateIconButton(nextIconName, nextAriaLabel, nextOnClick) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "condition-icon-button";
      button.setAttribute("aria-label", nextAriaLabel);
      button.setAttribute("title", nextAriaLabel);
      const icon = document.createElement("img");
      icon.className = "condition-action-icon";
      icon.src = `/assets/${nextIconName}`;
      icon.alt = "";
      button.appendChild(icon);
      button.addEventListener("click", nextOnClick);
      return button;
    };

    return createIconButton(iconName, ariaLabel, onClick, {
      className: "condition-icon-button condition-tree-action-button",
      iconClassName: "condition-action-icon",
      title: ariaLabel,
    });
  }

  function createSelect(options, value, onChange) {
    const select = document.createElement("select");
    (options || []).forEach((optionValue) => {
      const option = document.createElement("option");
      if (typeof optionValue === "object") {
        option.value = optionValue.value;
        option.textContent = optionValue.label;
        option.disabled = Boolean(optionValue.disabled);
      } else {
        option.value = optionValue;
        option.textContent = optionValue;
      }
      option.selected = option.value === value;
      select.appendChild(option);
    });
    select.value = value;
    select.addEventListener("change", onChange);
    return select;
  }

  function createInput(value, onInput, placeholder = "Value") {
    const input = document.createElement("input");
    input.type = "text";
    input.value = value || "";
    input.placeholder = placeholder;
    input.addEventListener("input", onInput);
    return input;
  }

  function createTextarea(value, onInput, placeholder = "Value", className = "") {
    const textarea = document.createElement("textarea");
    textarea.className = className;
    textarea.value = value || "";
    textarea.placeholder = placeholder;
    textarea.rows = 3;
    textarea.addEventListener("input", onInput);
    return textarea;
  }

  function createMembershipCheckboxGroup(options, selectedValues, onChange) {
    const wrap = document.createElement("div");
    wrap.className = "condition-membership-checklist";
    const selected = new Set((Array.isArray(selectedValues) ? selectedValues : []).map((value) => String(value || "").trim()));

    (options || []).forEach((entry) => {
      const optionValue = String(entry?.value || "").trim();
      if (!optionValue) {
        return;
      }
      const label = document.createElement("label");
      label.className = "condition-membership-option";

      const checkbox = document.createElement("input");
      checkbox.type = "checkbox";
      checkbox.value = optionValue;
      checkbox.checked = selected.has(optionValue);
      checkbox.addEventListener("change", () => {
        const checkedValues = Array.from(wrap.children || [])
          .map((child) => child?.children?.[0] || null)
          .filter((input) => input && input.tagName === "INPUT" && input.type === "checkbox" && input.checked)
          .map((input) => String(input.value || "").trim())
          .filter(Boolean);
        onChange(checkedValues);
      });

      const text = document.createElement("span");
      text.textContent = String(entry?.label || optionValue);
      label.appendChild(checkbox);
      label.appendChild(text);
      wrap.appendChild(label);
    });

    return wrap;
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
    const operator = serializeOperator(item.operator);
    const serializedValue = serializeComparisonValue(item);

    if (item.sourceType === "context") {
      if (!item.contextPath || !operator || !item.value) {
        return "";
      }
      return `${openParen}${item.contextPath} ${operator} ${serializedValue}${closeParen}`;
    }

    if (!item.symbol || !item.feature || !item.operator || !item.value) {
      return "";
    }
    if (item.feature === "syntax") {
      if (!item.syntaxField) {
        return "";
      }
      return `${openParen}${item.symbol}.${item.syntaxField} ${operator} ${serializedValue}${closeParen}`;
    }
    if (item.feature === "name") {
      return `${openParen}${item.symbol}.name ${operator} ${serializedValue}${closeParen}`;
    }
    const field = item.feature.replace(/^label\./, "");
    return `${openParen}${item.symbol}.${field} ${operator} ${serializedValue}${closeParen}`;
  }

  function defaultItem(symbols, allowedSourceTypes) {
    const sourceType = Array.isArray(allowedSourceTypes) && allowedSourceTypes.length === 1
      ? allowedSourceTypes[0]
      : "trace";
    return {
      conditionId: "",
      confirmed: true,
      stepStage: "complete",
      connector: "",
      openParen: "",
      closeParen: "",
      sourceType,
      symbol: symbols[0] || "A",
      feature: sourceType === "trace" ? "name" : "",
      propertyGroup: sourceType === "trace" ? "name" : "",
      syntaxField: "",
      operator: sourceType === "trace" ? "==" : "",
      value: "",
      selectedToolKey: "",
      contextPrefix: sourceType === "context" ? "tool" : "",
      contextField: "",
      contextFieldName: "",
      contextPath: "",
    };
  }

  function normalizeTraceItem(raw, index, symbols, symbolToolMap) {
    const fallback = defaultItem(symbols, ["trace"]);
    const item = {
      conditionId: String(raw?.conditionId || ""),
      confirmed: true,
      stepStage: "complete",
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

    const featureOptions = ["name", "label.boundary", "label.sensitivity", "label.integrity", "syntax"];
    if (!featureOptions.includes(item.feature)) {
      item.feature = "name";
    }

    if (!item.propertyGroup) {
      if (item.feature === "syntax") {
        item.propertyGroup = "syntax";
      } else if (item.feature.startsWith("label.")) {
        item.propertyGroup = "label";
      } else {
        item.propertyGroup = "name";
      }
    }

    if (item.feature === "name" && !isMembershipOperator(item.operator)) {
      if (item.selectedToolKey) {
        item.value = toolNameForKey(item.selectedToolKey) || item.value;
      } else {
        const option = toolOptions().find((entry) => entry.name === item.value);
        item.selectedToolKey = option?.value || symbolToolMap[item.symbol] || "";
      }
    } else if (item.feature === "name") {
      item.selectedToolKey = "";
    }

    if (item.feature === "syntax") {
      const resolvedToolKey = item.selectedToolKey || symbolToolMap[item.symbol] || "";
      item.selectedToolKey = resolvedToolKey;
      const params = inputParamsForTool(resolvedToolKey);
      if (!item.syntaxField) {
        item.syntaxField = params[0] || "";
      } else if (params.length && !params.includes(item.syntaxField)) {
        item.syntaxField = params[0] || "";
      }
    } else {
      item.syntaxField = item.feature === "syntax" ? item.syntaxField : "";
    }

    const operators = traceFeatureOperators[item.feature] || ["=="];
    if (!operators.includes(item.operator)) {
      item.operator = operators[0] || "";
    }

    return {
      ...item,
      expression: buildItemExpression(item),
    };
  }

  function normalizeContextItem(raw, index, options = {}) {
    const prefix = String(raw?.contextPrefix || String(raw?.contextPath || "").split(".")[0] || "tool");
    const definition = contextDefinitionForPath(raw?.contextPath || raw?.contextField, prefix);
    const fieldValue = String(raw?.contextField || definition.value || "");
    const toolKey = String(options.currentCallToolKey || "");
    const params = inputParamsForTool(toolKey);
    const pathSegment = prefix === "tool" && String(raw?.contextPath || "").startsWith("tool.")
      ? String(raw.contextPath).slice("tool.".length)
      : "";
    let syntaxField = String(raw?.syntaxField || "");

    if (fieldValue === "tool.syntax") {
      if (!syntaxField && pathSegment && !contextDefinitions.tool.some((item) => item.value === `tool.${pathSegment}`)) {
        syntaxField = pathSegment;
      }
      if (!syntaxField || (params.length && !params.includes(syntaxField))) {
        syntaxField = params[0] || "";
      }
    } else {
      syntaxField = "";
    }

    const fieldName = fieldValue === "tool.syntax" ? "" : String(raw?.contextFieldName || "");
    const contextPath = buildContextPath(fieldValue, syntaxField);
    const operators = definition.operators || ["=="];
    const item = {
      conditionId: String(raw?.conditionId || ""),
      confirmed: true,
      stepStage: "complete",
      connector: index === 0 ? "" : String(raw?.connector || "AND"),
      openParen: String(raw?.openParen || ""),
      closeParen: String(raw?.closeParen || ""),
      sourceType: "context",
      symbol: "",
      feature: "",
      propertyGroup: "",
      syntaxField,
      operator: operators.includes(raw?.operator) ? raw.operator : (operators[0] || ""),
      value: String(raw?.value || ""),
      selectedToolKey: String(raw?.selectedToolKey || ""),
      contextPrefix: prefix,
      contextField: fieldValue,
      contextFieldName: fieldName,
      contextPath,
      expression: "",
    };
    return {
      ...item,
      expression: buildItemExpression(item),
    };
  }

  function coerceItemSourceType(item, allowedSourceTypes, symbols, options = {}) {
    if (!Array.isArray(allowedSourceTypes) || !allowedSourceTypes.length) {
      return item;
    }
    if (allowedSourceTypes.includes(item.sourceType)) {
      return item;
    }
    if (allowedSourceTypes.includes("context")) {
      return normalizeContextItem({
        sourceType: "context",
        contextPrefix: "tool",
        contextField: "tool.name",
        contextPath: "tool.name",
        operator: "==",
        value: item.value || "",
        selectedToolKey: item.selectedToolKey || "",
        connector: item.connector,
      }, item.connector ? 1 : 0, options);
    }
    return normalizeTraceItem({
      sourceType: "trace",
      symbol: symbols[0] || "A",
      feature: "name",
      operator: "==",
      value: item.value || "",
      connector: item.connector,
    }, item.connector ? 1 : 0, symbols, {});
  }

  function normalizeItem(raw, index, symbols, symbolToolMap, options = {}) {
    if (raw?.sourceType === "context" || raw?.contextPath) {
      return normalizeContextItem(raw, index, options);
    }
    return normalizeTraceItem(raw, index, symbols, symbolToolMap);
  }

  function createGroupNode(type, children = [], id = "") {
    return {
      id: id || "",
      type: type === "OR" ? "OR" : "AND",
      children,
    };
  }

  function createConditionNode(item, id = "") {
    return {
      id: id || "",
      type: "condition",
      item,
    };
  }

  function cloneItem(item) {
    return {
      conditionId: item.conditionId || "",
      confirmed: true,
      stepStage: "complete",
      connector: item.connector || "",
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

  function stripStructuralTokens(item) {
    return {
      ...cloneItem(item),
      connector: "",
      openParen: "",
      closeParen: "",
    };
  }

  function conditionDisplayExpression(item, symbols, options = {}) {
    const normalized = normalizeItems({ items: [stripStructuralTokens(item)] }, symbols, options);
    return normalized.items[0]?.expression || "";
  }

  function cloneNode(node) {
    if (!node) {
      return null;
    }
    if (node.type === "condition") {
      return createConditionNode(cloneItem(node.item), node.id);
    }
    return createGroupNode(node.type, (node.children || []).map(cloneNode).filter(Boolean), node.id);
  }

  function flattenGroup(group, wrap) {
    const children = Array.isArray(group?.children) ? group.children : [];
    const items = [];
    children.forEach((child, index) => {
      let childItems = [];
      if (child?.type === "condition") {
        childItems = [stripStructuralTokens(child.item)];
      } else if (child?.type === "AND" || child?.type === "OR") {
        childItems = flattenGroup(child, true);
      }
      if (!childItems.length) {
        return;
      }
      childItems[0].connector = index === 0 ? "" : group.type;
      items.push(...childItems);
    });
    if (wrap && items.length) {
      items[0].openParen = `${items[0].openParen || ""}(`;
      items[items.length - 1].closeParen = `${items[items.length - 1].closeParen || ""})`;
    }
    return items;
  }

  function expressionForItems(items) {
    return (items || [])
      .filter((item) => item?.expression)
      .map((item, index) => index === 0 ? item.expression : `${item.connector || "AND"} ${item.expression}`)
      .join(" ");
  }

  function groupFromOperator(operator, left, right) {
    const children = [];
    if (left?.type === operator) {
      children.push(...(left.children || []).map(cloneNode));
    } else if (left) {
      children.push(cloneNode(left));
    }
    if (right?.type === operator) {
      children.push(...(right.children || []).map(cloneNode));
    } else if (right) {
      children.push(cloneNode(right));
    }
    return createGroupNode(operator, children);
  }

  function tokenizeItems(items) {
    const tokens = [];
    (items || []).forEach((item, index) => {
      if (index > 0) {
        tokens.push({ type: "operator", value: item.connector || "AND" });
      }
      const opens = String(item.openParen || "");
      const closes = String(item.closeParen || "");
      for (let count = 0; count < opens.length; count += 1) {
        tokens.push({ type: "paren", value: "(" });
      }
      tokens.push({ type: "condition", value: createConditionNode(cloneItem(item)) });
      for (let count = 0; count < closes.length; count += 1) {
        tokens.push({ type: "paren", value: ")" });
      }
    });
    return tokens;
  }

  function itemsToTree(items) {
    if (!Array.isArray(items) || !items.length) {
      return createGroupNode("AND", []);
    }

    const values = [];
    const operators = [];
    const tokens = tokenizeItems(items);

    function applyOperator() {
      const operator = operators.pop();
      const right = values.pop();
      const left = values.pop();
      if (!operator || !left || !right) {
        throw new Error("Malformed condition expression.");
      }
      values.push(groupFromOperator(operator, left, right));
    }

    tokens.forEach((token) => {
      if (token.type === "condition") {
        values.push(token.value);
        return;
      }
      if (token.type === "paren" && token.value === "(") {
        operators.push("(");
        return;
      }
      if (token.type === "paren" && token.value === ")") {
        while (operators.length && operators[operators.length - 1] !== "(") {
          applyOperator();
        }
        if (!operators.length || operators[operators.length - 1] !== "(") {
          throw new Error("Unbalanced parentheses.");
        }
        operators.pop();
        return;
      }
      while (operators.length && operators[operators.length - 1] !== "(") {
        applyOperator();
      }
      operators.push(token.value);
    });

    while (operators.length) {
      if (operators[operators.length - 1] === "(") {
        throw new Error("Unbalanced parentheses.");
      }
      applyOperator();
    }

    if (values.length !== 1) {
      throw new Error("Malformed condition expression.");
    }

    const root = values[0];
    if (root.type === "condition") {
      return createGroupNode("AND", [root]);
    }
    return root;
  }

  function collectRawItemsFromTree(tree, acc = []) {
    if (!tree) {
      return acc;
    }
    if (tree.type === "condition") {
      acc.push(tree.item || {});
      return acc;
    }
    (tree.children || []).forEach((child) => collectRawItemsFromTree(child, acc));
    return acc;
  }

  function assignNormalizedItemsToTree(tree, normalizedItems) {
    let index = 0;

    function visit(node) {
      if (!node) {
        return null;
      }
      if (node.type === "condition") {
        const nextItem = normalizedItems[index] ? cloneItem(normalizedItems[index]) : cloneItem(node.item || {});
        index += 1;
        return createConditionNode(nextItem, node.id);
      }
      return createGroupNode(node.type, (node.children || []).map(visit).filter(Boolean), node.id);
    }

    return visit(tree);
  }

  function normalizeSavedConditionEntry(entry, symbols, options) {
    const preferredItems = Array.isArray(entry?.items) && entry.items.length
      ? entry.items
      : entry?.tree
        ? collectRawItemsFromTree(entry.tree)
        : [];
    const normalized = normalizeItems({ items: preferredItems }, symbols, options);
    let tree;
    if (entry?.tree) {
      tree = assignNormalizedItemsToTree(entry.tree, normalized.items);
    } else if (normalized.items.length) {
      tree = normalized.tree;
    } else {
      tree = createGroupNode("AND", []);
    }
    return {
      conditionId: String(entry?.conditionId || ""),
      expression: normalized.expression,
      items: normalized.items.map(cloneItem),
      tree,
    };
  }

  function normalizeItems(value, symbols, options = {}) {
    const nextSymbols = Array.isArray(symbols) && symbols.length ? symbols : ["A"];
    const preferredItems = Array.isArray(value?.items)
      ? value.items
      : value?.tree
        ? flattenGroup(value.tree, false)
      : value?.feature || value?.contextPath
        ? [value]
        : [];

    const rawSymbolToolMap = inferSymbolToolMap({ items: preferredItems });
    const normalizedItems = preferredItems.map((raw, index) => normalizeItem(raw, index, nextSymbols, rawSymbolToolMap, options));
    const coercedItems = normalizedItems.map((item) => coerceItemSourceType(item, options.allowedSourceTypes || [], nextSymbols, options));
    const symbolToolMap = inferSymbolToolMap({ items: coercedItems });
    const finalItems = coercedItems.map((item, index) => normalizeItem(item, index, nextSymbols, symbolToolMap, options));

    let tree;
    try {
      tree = value?.tree
        ? assignNormalizedItemsToTree(value.tree, finalItems)
        : itemsToTree(finalItems);
    } catch {
      tree = createGroupNode(
        "AND",
        finalItems.map((item) => createConditionNode(cloneItem(item))),
      );
    }

    return {
      items: finalItems,
      symbolToolMap,
      tree,
      expression: expressionForItems(finalItems),
    };
  }

  function createConditionBuilder(options) {
    const root = options.root;
    const hint = options.hint;
    const addButton = options.addButton;
    let symbols = Array.isArray(options.pathSymbols) && options.pathSymbols.length ? options.pathSymbols : ["A"];
    let currentCallToolKey = String(options.currentCallToolKey || "");
    let currentCallSubtype = String(options.currentCallSubtype || "");
    let allowedSourceTypes = Array.isArray(options.allowedSourceTypes) ? options.allowedSourceTypes.slice() : [];
    let locked = Boolean(options.locked);
    let onChange = typeof options.onChange === "function" ? options.onChange : function noop() {};
    let nodeCounter = 0;
    let openAddMenuGroupId = "";

    function nextNodeId(prefix = "node") {
      nodeCounter += 1;
      return `${prefix}_${nodeCounter}`;
    }

    function stampNodeIds(node) {
      if (!node) {
        return null;
      }
      if (node.type === "condition") {
        return createConditionNode(cloneItem(node.item), node.id || nextNodeId("cond"));
      }
      return createGroupNode(
        node.type,
        (node.children || []).map(stampNodeIds).filter(Boolean),
        node.id || nextNodeId("group"),
      );
    }

    function normalizeState(value) {
      const normalized = normalizeItems(value || {}, symbols, {
        currentCallToolKey,
        allowedSourceTypes,
      });
      const preferredTree = value?.tree
        ? assignNormalizedItemsToTree(value.tree, normalized.items)
        : normalized.tree;
      const saved = Array.isArray(value?.savedConditions)
        ? value.savedConditions.map((entry) => normalizeSavedConditionEntry(entry, symbols, { currentCallToolKey, allowedSourceTypes }))
        : [];
      return {
        items: normalized.items,
        symbolToolMap: normalized.symbolToolMap,
        tree: stampNodeIds(preferredTree || createGroupNode("AND", [])),
        savedConditions: saved,
        draftItem: null,
        expression: normalized.expression,
      };
    }

    let state = normalizeState(options.value || {});

    function syncFromTree() {
      const rawItems = flattenGroup(state.tree, false);
      const normalized = normalizeItems({ items: rawItems }, symbols, {
        currentCallToolKey,
        allowedSourceTypes,
      });
      state.items = normalized.items;
      state.symbolToolMap = normalized.symbolToolMap;
      state.expression = normalized.expression;
      state.tree = stampNodeIds(assignNormalizedItemsToTree(state.tree, normalized.items));
    }

    function emit() {
      onChange(api.getValue());
    }

    function updateHint(message = "") {
      if (!hint) {
        return;
      }
      if (locked) {
        hint.textContent = "CONDITION is locked until TRACE or ON is configured.";
        return;
      }
      if (message) {
        hint.textContent = message;
        return;
      }
      if (state.draftItem) {
        hint.textContent = "Finish the guided single-condition builder, then save it into the library.";
        return;
      }
      if (!state.savedConditions.length) {
        hint.textContent = "Create a saved single condition first, then insert it into the logic tree.";
        return;
      }
      hint.textContent = "Use each group's + menu to insert a saved condition or add a nested group.";
    }

    function ensureRootGroup() {
      if (!state.tree || (state.tree.type !== "AND" && state.tree.type !== "OR")) {
        state.tree = stampNodeIds(createGroupNode("AND", []));
      }
    }

    function defaultSourceType() {
      if (allowedSourceTypes.includes("trace")) {
        return "trace";
      }
      if (allowedSourceTypes.includes("context")) {
        return "context";
      }
      return "trace";
    }

    function hasSourceChoice() {
      return allowedSourceTypes.includes("trace") && allowedSourceTypes.includes("context");
    }

    function baseStageOrderForSourceType(sourceType) {
      return sourceType === "trace"
        ? ["source", "symbol", "property", "comparison", "complete"]
        : ["source", "property", "comparison", "complete"];
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

  function toolKeyForName(name) {
    if (!name) {
      return "";
    }
    const normalizedName = normalizeToolNameToken(name);
    const matches = toolOptions().filter((option) => (
      option.name === name || normalizeToolNameToken(option.name) === normalizedName
    ));
    return matches.length === 1 ? String(matches[0].value || "") : "";
  }

  function savedConditionItems(savedConditions = []) {
    return (Array.isArray(savedConditions) ? savedConditions : []).flatMap((entry) => (
      Array.isArray(entry?.items) ? entry.items : []
    ));
  }

  function inferredTraceToolKey(symbol, items = [], savedConditions = [], currentItem = null) {
    const allItems = [...(Array.isArray(items) ? items : []), ...savedConditionItems(savedConditions)];
    const matched = allItems.find((entry) => (
      entry
      && entry !== currentItem
      && entry.sourceType === "trace"
      && entry.symbol === symbol
      && entry.feature === "name"
      && entry.operator === "=="
      && (entry.selectedToolKey || entry.value)
    ));
    if (!matched) {
      return "";
    }
    return String(matched.selectedToolKey || toolKeyForName(String(matched.value || "")) || "");
  }

  function toolKeyFromConditionEntry(entry, currentItem) {
    if (!entry || entry === currentItem) {
      return "";
    }
    if (entry.selectedToolKey) {
      return String(entry.selectedToolKey || "");
    }
    if (entry.sourceType === "context" && entry.contextPath === "tool.name" && entry.operator === "==") {
      return toolKeyForName(String(entry.value || ""));
    }
    if (entry.sourceType === "trace" && entry.feature === "name" && entry.operator === "==") {
      return toolKeyForName(String(entry.value || ""));
    }
    return "";
  }

  function inferredContextToolKey(item, items = [], savedConditions = []) {
    if (item?.selectedToolKey) {
      return String(item.selectedToolKey || "");
    }
    if (item?.contextField === "tool.name") {
      const fromDraft = toolKeyForName(String(item.value || ""));
      if (fromDraft) {
        return fromDraft;
      }
    }
    const toolCondition = [...(Array.isArray(items) ? items : []), ...savedConditionItems(savedConditions)].find((entry) => (
      Boolean(toolKeyFromConditionEntry(entry, item))
    ));
    const inferred = toolKeyFromConditionEntry(toolCondition, item);
    if (inferred) {
      return inferred;
    }
    return String(currentCallToolKey || "");
  }

  function toolContextSubpropertyOptions(item, items = [], savedConditions = []) {
    const inferredToolKey = inferredContextToolKey(item, items, savedConditions);
    const params = inputParamsForTool(inferredToolKey);
    const options = [
      { value: "tool.name", label: toolContextSubpropertyLabel("tool.name") },
      { value: "tool.boundary", label: toolContextSubpropertyLabel("tool.boundary") },
      { value: "tool.sensitivity", label: toolContextSubpropertyLabel("tool.sensitivity") },
      { value: "tool.integrity", label: toolContextSubpropertyLabel("tool.integrity") },
      ...params.map((param) => ({ value: `tool.${param}`, label: toolContextSubpropertyLabel(`tool.${param}`) })),
    ];
    if (currentCallSubtype === "completed") {
      options.push({ value: "tool.result", label: toolContextSubpropertyLabel("tool.result") });
    }
    return options;
  }

  function toolContextSubpropertyValue(item) {
    if (item.contextField === "tool.syntax") {
      return item.contextPath || "";
    }
    return item.contextField || "";
  }

    function tracePropertyOptionsForItem(item) {
      return tracePropertyGroups.filter((option) => {
        if (option.value !== "syntax") {
          return true;
        }
        return Boolean(
          state.symbolToolMap[item.symbol]
          || inferredTraceToolKey(item.symbol, state.items, state.savedConditions, item),
        );
      });
    }

    function buildDefaultDraft() {
      const sourceType = defaultSourceType();
      return {
        conditionId: "",
        confirmed: false,
        stepStage: hasSourceChoice() ? "source" : (sourceType === "trace" ? "symbol" : "property"),
        connector: "",
        openParen: "",
        closeParen: "",
        sourceType,
        symbol: sourceType === "trace" ? (symbols[0] || "A") : "",
        feature: "",
        propertyGroup: "",
        syntaxField: "",
        operator: "",
        value: "",
        selectedToolKey: "",
        contextPrefix: sourceType === "context" ? "" : "",
        contextField: "",
        contextFieldName: "",
        contextPath: "",
      };
    }

    function stageOrderForDraft(item) {
      const sourceType = String(item?.sourceType || defaultSourceType()).trim() || defaultSourceType();
      const order = baseStageOrderForSourceType(sourceType);
      return hasSourceChoice() ? order : order.filter((stage) => stage !== "source");
    }

    function currentDraftStage(item) {
      const order = stageOrderForDraft(item);
      const requested = normalizeStepStage(item);
      return order.includes(requested) ? requested : order[0];
    }

    function previousDraftStage(item) {
      const order = stageOrderForDraft(item);
      const index = order.indexOf(currentDraftStage(item));
      return index > 0 ? order[index - 1] : order[0];
    }

    function nextDraftStage(item) {
      const order = stageOrderForDraft(item);
      const index = order.indexOf(currentDraftStage(item));
      return index >= 0 && index < order.length - 1 ? order[index + 1] : "complete";
    }

    function draftExpression(item) {
      const normalized = normalizeItems({ items: [{ ...item, confirmed: true, stepStage: "complete" }] }, symbols, {
        currentCallToolKey,
        allowedSourceTypes,
      });
      return normalized.items[0]?.expression || "";
    }

    function canAdvanceDraft(item) {
      const stage = currentDraftStage(item);
      if (stage === "source") {
        return allowedSourceTypes.includes(item.sourceType);
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
        if (definition.kind === "tool-syntax") {
          return Boolean(item.syntaxField);
        }
        return true;
      }
      if (stage === "comparison") {
        return Boolean(draftExpression(item));
      }
      return true;
    }

    function openDraft(item) {
      state.draftItem = item ? { ...cloneItem(item), confirmed: false, stepStage: "comparison" } : buildDefaultDraft();
      render();
      updateHint("Complete the single condition builder, then save it to the library.");
    }

    function closeDraft() {
      state.draftItem = null;
      render();
      updateHint();
    }

    function toggleAddMenu(groupId) {
      openAddMenuGroupId = openAddMenuGroupId === groupId ? "" : groupId;
      render();
    }

    function closeAddMenu() {
      if (!openAddMenuGroupId) {
        return;
      }
      openAddMenuGroupId = "";
    }

    function saveDraftCondition() {
      const normalized = normalizeItems({ items: [{ ...state.draftItem, confirmed: true, stepStage: "complete" }] }, symbols, {
        currentCallToolKey,
        allowedSourceTypes,
      });
      const item = normalized.items[0];
      if (!item?.expression) {
        updateHint("Finish the condition fields before saving.");
        return;
      }
      const editingExisting = state.draftItem?.conditionId
        && state.savedConditions.some((entry) => entry.conditionId === state.draftItem.conditionId);
      const existingIndex = editingExisting
        ? state.savedConditions.findIndex((entry) => entry.conditionId === state.draftItem.conditionId)
        : -1;
      const conditionId = editingExisting ? state.draftItem.conditionId : nextConditionId();
      const entry = {
        conditionId,
        expression: item.expression,
        items: [cloneItem(item)],
        tree: stampNodeIds(createGroupNode("AND", [createConditionNode(cloneItem(item))])),
      };
      if (existingIndex >= 0) {
        state.savedConditions[existingIndex] = entry;
      } else {
        state.savedConditions.push(entry);
      }
      state.draftItem = null;
      render();
      emit();
    }

    function removeSavedCondition(conditionId) {
      state.savedConditions = state.savedConditions.filter((entry) => entry.conditionId !== conditionId);
      render();
      emit();
    }

    function updateDraft(patch, renderAfter = true) {
      state.draftItem = { ...(state.draftItem || buildDefaultDraft()), ...patch };
      if (state.draftItem.sourceType === "trace") {
        state.draftItem.contextPrefix = "";
        state.draftItem.contextField = "";
        state.draftItem.contextFieldName = "";
        state.draftItem.contextPath = "";
      } else {
        state.draftItem.symbol = "";
        state.draftItem.feature = "";
        state.draftItem.propertyGroup = "";
      }
      if (renderAfter) {
        render();
      }
    }

    function draftProgress(item) {
      const order = stageOrderForDraft(item).filter((stage) => stage !== "complete");
      const currentStage = currentDraftStage(item);
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

    function renderDraftStageHeader(item, stage) {
      const order = stageOrderForDraft(item).filter((entry) => entry !== "complete");
      const stepNumber = Math.max(order.indexOf(stage) + 1, 1);
      const stageMeta = {
        source: { title: "Choose rule scope", copy: "Select the tool format." },
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

    function renderDraftSubproperty(detailSection, item) {
      if (item.sourceType === "trace") {
        const group = item.propertyGroup || traceGroupFromFeature(item.feature);
        if (!group || group === "name") {
          return;
        }
        if (group === "label") {
          detailSection.appendChild(createField("Sub-property", createSelect([
            { value: "", label: "Select sub-property" },
            { value: "label.boundary", label: "label-boundary" },
            { value: "label.sensitivity", label: "label-sensitivity" },
            { value: "label.integrity", label: "label-integrity" },
          ], item.feature, (event) => {
            updateDraft({ feature: event.target.value, operator: "", value: "" });
          })));
          return;
        }
        const inferredToolKey = state.symbolToolMap[item.symbol]
          || inferredTraceToolKey(item.symbol, state.items, state.savedConditions, item);
        const params = inputParamsForTool(inferredToolKey);
        detailSection.appendChild(createField("Sub-property", createSelect(
          [{ value: "", label: "Select sub-property" }, ...(params.length ? params.map((value) => ({ value, label: `param-${value}` })) : [])],
          item.syntaxField,
          (event) => updateDraft({ syntaxField: event.target.value }),
        )));
        return;
      }

      const prefix = item.contextPrefix || "tool";
      if (prefix === "tool") {
        const subpropertyOptions = toolContextSubpropertyOptions(item, state.items, state.savedConditions);
        detailSection.appendChild(createField("Sub-property", createSelect(
          [
            { value: "", label: "Select sub-property" },
            ...subpropertyOptions,
          ],
          toolContextSubpropertyValue(item),
          (event) => {
            const nextField = event.target.value;
            if (String(nextField || "").startsWith("tool.") && !contextDefinitions.tool.some((option) => option.value === nextField)) {
              const nextSyntaxField = String(nextField).slice("tool.".length);
              updateDraft({
                contextField: "tool.syntax",
                contextFieldName: "",
                contextPath: buildContextPath("tool.syntax", nextSyntaxField),
                syntaxField: nextSyntaxField,
                operator: "",
                value: "",
              });
              return;
            }
            updateDraft({
              contextField: nextField,
              contextFieldName: "",
              contextPath: buildContextPath(nextField, ""),
              syntaxField: "",
              operator: "",
              value: "",
            });
          },
        )));
        return;
      }

      if (prefix === "principal") {
        detailSection.appendChild(createField("Sub-property", createSelect(
          [{ value: "", label: "Select sub-property" }, ...principalContextSubpropertyGroups],
          item.contextField,
          (event) => {
            const nextField = event.target.value;
            updateDraft({
              contextField: nextField,
              contextFieldName: "",
              contextPath: buildContextPath(nextField, ""),
              syntaxField: "",
              operator: "",
              value: "",
            });
          },
        )));
        return;
      }
    }

    function renderDraftProperty(detailSection, item) {
      if (item.sourceType === "trace") {
        const propertyOptions = tracePropertyOptionsForItem(item);
        const selectedGroup = item.propertyGroup || traceGroupFromFeature(item.feature);
        detailSection.appendChild(createField("Property", createSelect(
          [{ value: "", label: "Select property" }, ...propertyOptions],
          selectedGroup,
          (event) => {
            const nextGroup = event.target.value;
            if (!nextGroup) {
              updateDraft({ propertyGroup: "", feature: "", syntaxField: "", operator: "", selectedToolKey: "", value: "" });
            } else if (nextGroup === "name") {
              updateDraft({ propertyGroup: "name", feature: "name", syntaxField: "", operator: "", selectedToolKey: "", value: "" });
            } else if (nextGroup === "label") {
              updateDraft({ propertyGroup: "label", feature: "", syntaxField: "", operator: "", selectedToolKey: "", value: "" });
            } else {
              updateDraft({ propertyGroup: "syntax", feature: "syntax", syntaxField: "", operator: "", selectedToolKey: "", value: "" });
            }
          },
        )));
        renderDraftSubproperty(detailSection, item);
        return;
      }

      detailSection.appendChild(createField("Property", createSelect(
        [{ value: "", label: "Select property" }, ...contextPropertyGroups],
        item.contextPrefix || "",
        (event) => {
          const nextPrefix = event.target.value;
          updateDraft({
            contextPrefix: nextPrefix,
            contextField: "",
            contextFieldName: "",
            contextPath: "",
            syntaxField: "",
            operator: "",
            value: "",
          });
        },
      )));
      renderDraftSubproperty(detailSection, item);
    }

    function renderDraftComparison(detailSection, item) {
      if (item.sourceType === "trace") {
        detailSection.appendChild(createField("Comparison", createSelect(
          [{ value: "", label: "Select comparison" }, ...((traceFeatureOperators[item.feature] || []).map((value) => ({ value, label: comparisonOptionLabel(value) })))],
          item.operator,
          (event) => updateDraft({ operator: event.target.value }),
        )));
        if (isMembershipOperator(item.operator)) {
          const options = membershipOptionEntries(item);
          if (options.length) {
            detailSection.appendChild(createField("Target values", createMembershipCheckboxGroup(
              options,
              parseSetLiteralEntries(item.value),
              (nextValues) => updateDraft({ value: formatSetLiteral(nextValues) }),
            ), "condition-field-wide"));
            return;
          }
          detailSection.appendChild(createField("Target list", createTextarea(
            membershipEditorValue(item.value),
            (event) => updateDraft({ value: normalizeMembershipValueInput(event.target.value) }, false),
            item.feature === "name"
              ? "One tool name per line, or a collection ref like allowlist.tools"
              : "One item per line, or a collection ref like allowlist.http",
            "condition-target-list-input",
          ), "condition-field-wide"));
          return;
        }
        if (item.feature === "name") {
          detailSection.appendChild(createField("Target value", createSelect(
            [{ value: "", label: "Select target value" }, ...toolOptions()],
            item.selectedToolKey,
            (event) => updateDraft({
              selectedToolKey: event.target.value,
              value: toolNameForKey(event.target.value),
            }),
          )));
          return;
        }
        if (String(item.feature || "").startsWith("label.")) {
          detailSection.appendChild(createField("Target value", createSelect(
            [{ value: "", label: "Select target value" }, ...((labelValues[item.feature] || []).map((value) => ({ value, label: value })))],
            item.value,
            (event) => updateDraft({ value: event.target.value }),
          )));
          return;
        }
        detailSection.appendChild(createField("Target value", createInput(
          item.value,
          (event) => updateDraft({ value: event.target.value }, false),
          item.syntaxField ? `Value for ${item.syntaxField}` : "Value",
        )));
        return;
      }

      const definition = contextDefinitionForItem(item);
      detailSection.appendChild(createField("Comparison", createSelect(
        [{ value: "", label: "Select comparison" }, ...((definition.operators || []).map((value) => ({ value, label: comparisonOptionLabel(value) })))],
        item.operator,
        (event) => updateDraft({ operator: event.target.value }),
      )));
      if (isMembershipOperator(item.operator)) {
        const options = membershipOptionEntries(definition);
        if (options.length) {
          detailSection.appendChild(createField("Target values", createMembershipCheckboxGroup(
            options,
            parseSetLiteralEntries(item.value),
            (nextValues) => updateDraft({ value: formatSetLiteral(nextValues) }),
          ), "condition-field-wide"));
          return;
        }
        detailSection.appendChild(createField("Target list", createTextarea(
          membershipEditorValue(item.value),
          (event) => updateDraft({ value: normalizeMembershipValueInput(event.target.value) }, false),
          membershipPlaceholder(definition),
          "condition-target-list-input",
        ), "condition-field-wide"));
        return;
      }
      if (definition.kind === "enum") {
        const enumOptions = Array.isArray(definition.enumValues)
          ? definition.enumValues
          : (labelValues[definition.enumKey] || []);
        detailSection.appendChild(createField("Target value", createSelect(
          [{ value: "", label: "Select target value" }, ...(enumOptions.map((value) => ({ value, label: value })))],
          item.value,
          (event) => updateDraft({ value: event.target.value }),
        )));
        return;
      }
      if (definition.kind === "tool-name") {
        detailSection.appendChild(createField("Target value", createSelect(
          [{ value: "", label: "Select target value" }, ...toolOptions()],
          item.selectedToolKey,
          (event) => updateDraft({
            selectedToolKey: event.target.value,
            value: toolNameForKey(event.target.value),
          }),
        )));
        return;
      }
      detailSection.appendChild(createField("Target value", createInput(
        item.value,
        (event) => updateDraft({ value: event.target.value }, false),
        definition.kind === "number" ? "Numeric value" : "Value",
      )));
    }

    function renderDraftBuilder() {
      if (!state.draftItem) {
        return null;
      }
      const item = state.draftItem;
      const stage = currentDraftStage(item);
      const card = document.createElement("div");
      card.className = "condition-card condition-step-card";
      const cardActions = document.createElement("div");
      cardActions.className = "condition-card-actions condition-card-actions-start";
      cardActions.appendChild(createAssetIconButton("close.png", "Close condition builder", closeDraft));
      card.appendChild(cardActions);
      card.appendChild(renderDraftStageHeader(item, stage));

      const detailSection = document.createElement("div");
      detailSection.className = "condition-detail-section";
      if (stage === "source") {
        const options = [
          { value: "trace", label: "Path rule" },
          { value: "context", label: "Single tool rule" },
        ];
        const select = createSelect(options, item.sourceType, (event) => {
          const nextSourceType = event.target.value;
          updateDraft({
            sourceType: nextSourceType,
            stepStage: nextSourceType === "trace" ? "symbol" : "property",
            symbol: nextSourceType === "trace" ? (symbols[0] || "A") : "",
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
        });
        Array.from(select.options || []).forEach((option) => {
          option.disabled = !allowedSourceTypes.includes(option.value);
        });
        detailSection.appendChild(createField("Rule Scope", select));
      } else if (stage === "symbol") {
        detailSection.appendChild(createField("Path tool", createSelect(
          symbols.map((symbol) => ({ value: symbol, label: displaySymbol(symbol) })),
          item.symbol,
          (event) => updateDraft({ symbol: event.target.value }),
        )));
      } else if (stage === "property") {
        renderDraftProperty(detailSection, item);
      } else if (stage === "comparison") {
        renderDraftComparison(detailSection, item);
      }
      card.appendChild(detailSection);

      if (stage === "comparison") {
        const preview = document.createElement("pre");
        preview.className = "condition-step-preview";
        preview.textContent = draftExpression(item) || "<incomplete>";
        card.appendChild(preview);
      }

      const actionRow = document.createElement("div");
      actionRow.className = "condition-step-nav";
      if (stage !== stageOrderForDraft(item)[0]) {
        const backButton = document.createElement("button");
        backButton.type = "button";
        backButton.className = "btn condition-step-nav-button";
        backButton.textContent = "<";
        backButton.addEventListener("click", () => updateDraft({ stepStage: previousDraftStage(item) }));
        actionRow.appendChild(backButton);
      } else {
        const spacer = document.createElement("span");
        spacer.className = "condition-step-nav-spacer";
        actionRow.appendChild(spacer);
      }
      actionRow.appendChild(draftProgress(item));
      const nextButton = document.createElement("button");
      nextButton.type = "button";
      nextButton.className = "btn primary condition-step-nav-button";
      if (stage === "comparison") {
        nextButton.setAttribute("aria-label", "Generate single rule");
        nextButton.textContent = "Create >";
        nextButton.disabled = !canAdvanceDraft(item);
        nextButton.addEventListener("click", saveDraftCondition);
      } else {
        nextButton.setAttribute("aria-label", "Next builder step");
        nextButton.textContent = ">";
        nextButton.disabled = !canAdvanceDraft(item);
        nextButton.addEventListener("click", () => updateDraft({ stepStage: nextDraftStage(item) }));
      }
      actionRow.appendChild(nextButton);
      card.appendChild(actionRow);
      return card;
    }

    function nextConditionId(items = state.savedConditions) {
      const maxValue = items.reduce((acc, item) => {
        const matched = String(item?.conditionId || "").match(/^COND(\d+)$/);
        const numeric = matched ? Number(matched[1]) : 0;
        return Math.max(acc, Number.isFinite(numeric) ? numeric : 0);
      }, 0);
      return `COND${maxValue + 1}`;
    }

    function keepDraftInSync() {
      if (!state.draftItem) {
        return;
      }
      const nextDraft = { ...state.draftItem };
      if (!allowedSourceTypes.includes(nextDraft.sourceType)) {
        const fallback = buildDefaultDraft();
        state.draftItem = fallback;
        return;
      }
      if (nextDraft.sourceType === "trace" && !symbols.includes(nextDraft.symbol)) {
        nextDraft.symbol = symbols[0] || "A";
      }
      if (nextDraft.sourceType === "context" && nextDraft.contextField === "tool.syntax") {
        const inferredToolKey = inferredContextToolKey(nextDraft, state.items, state.savedConditions);
        const params = inputParamsForTool(inferredToolKey);
        if (!inferredToolKey || !params.length) {
          nextDraft.contextField = "";
          nextDraft.contextPath = "";
          nextDraft.syntaxField = "";
          nextDraft.operator = "";
          nextDraft.value = "";
        } else if (nextDraft.syntaxField && !params.includes(nextDraft.syntaxField)) {
          nextDraft.syntaxField = params[0] || "";
          nextDraft.contextPath = buildContextPath(nextDraft.contextField, nextDraft.syntaxField);
        }
      }
      if (nextDraft.sourceType === "context" && nextDraft.contextField === "tool.result" && currentCallSubtype !== "completed") {
        nextDraft.contextField = "";
        nextDraft.contextPath = "";
        nextDraft.operator = "";
        nextDraft.value = "";
      }
      state.draftItem = nextDraft;
    }

    keepDraftInSync();

    function findGroupById(node, id) {
      if (!node) {
        return null;
      }
      if (node.id === id && (node.type === "AND" || node.type === "OR")) {
        return node;
      }
      if (node.type === "condition") {
        return null;
      }
      for (const child of node.children || []) {
        const match = findGroupById(child, id);
        if (match) {
          return match;
        }
      }
      return null;
    }

    function removeNodeById(node, id) {
      if (!node || node.type === "condition") {
        return node;
      }
      return createGroupNode(
        node.type,
        (node.children || [])
          .filter((child) => child.id !== id)
          .map((child) => child.type === "condition" ? child : removeNodeById(child, id)),
        node.id,
      );
    }

    function insertSavedConditionIntoGroup(groupId, conditionId) {
      if (locked) {
        return;
      }
      const selected = state.savedConditions.find((entry) => entry.conditionId === conditionId) || null;
      if (!selected) {
        updateHint("Choose a saved condition from the group's + menu first.");
        return;
      }
      const group = findGroupById(state.tree, groupId);
      if (!group) {
        return;
      }
      const subtree = selected.tree
        ? stampNodeIds(cloneNode(selected.tree))
        : stampNodeIds(itemsToTree(selected.items));
      const children = subtree.type === "condition" ? [subtree] : (subtree.children || []).map(stampNodeIds);
      group.children.push(...children);
      closeAddMenu();
      syncFromTree();
      render();
      emit();
    }

    function addGroup(groupId) {
      if (locked) {
        return;
      }
      const group = findGroupById(state.tree, groupId);
      if (!group) {
        return;
      }
      group.children.push(stampNodeIds(createGroupNode("AND", [])));
      closeAddMenu();
      syncFromTree();
      render();
      emit();
    }

    function deleteTreeNode(nodeId) {
      if (state.tree.id === nodeId) {
        state.tree = stampNodeIds(createGroupNode("AND", []));
      } else {
        state.tree = removeNodeById(state.tree, nodeId);
      }
      syncFromTree();
      render();
      emit();
    }

    function setGroupType(nodeId, type) {
      const group = findGroupById(state.tree, nodeId);
      if (!group) {
        return;
      }
      group.type = type === "OR" ? "OR" : "AND";
      syncFromTree();
      render();
      emit();
    }

    function createSectionTitleWithHint(text, hintText) {
      const wrap = document.createElement("div");
      wrap.className = "on-filter-help-row";

      const title = document.createElement("strong");
      title.textContent = text;
      wrap.appendChild(title);

      if (hintText) {
        const hintWrap = document.createElement("div");
        hintWrap.className = "hint-wrap";

        const hintDot = document.createElement("span");
        hintDot.className = "hint-dot";
        hintDot.textContent = "i";
        hintWrap.appendChild(hintDot);

        const hintBubble = document.createElement("div");
        hintBubble.className = "hint-bubble";
        hintBubble.textContent = hintText;
        hintWrap.appendChild(hintBubble);

        wrap.appendChild(hintWrap);
      }

      return wrap;
    }

    function renderLibrary() {
      const section = document.createElement("section");
      section.className = "condition-tree-section";

      const header = document.createElement("div");
      header.className = "condition-tree-section-head";
      header.appendChild(createSectionTitleWithHint(
        "Saved Conditions",
        "You can build single conditions here with the guided flow."
      ));
      if (addButton) {
        header.appendChild(addButton);
      }
      section.appendChild(header);

      const list = document.createElement("div");
      list.className = "condition-tree-library";

      if (!state.savedConditions.length) {
        const empty = document.createElement("div");
        empty.className = "empty-state";
        empty.textContent = "No saved conditions yet.";
        list.appendChild(empty);
      } else {
        state.savedConditions.forEach((entry) => {
          const card = document.createElement("article");
          card.className = "condition-tree-library-card";

          const row = document.createElement("div");
          row.className = "condition-tree-library-head";
          const summary = document.createElement("div");
          summary.className = "condition-tree-library-summary";
          const id = document.createElement("span");
          id.className = "condition-summary-id";
          id.textContent = entry.conditionId;
          summary.appendChild(id);
          const body = document.createElement("div");
          body.className = "condition-summary-rule condition-tree-library-rule";
          body.textContent = entry.expression || "<condition pending>";
          summary.appendChild(body);
          row.appendChild(summary);
          const controls = document.createElement("div");
          controls.className = "condition-tree-library-actions";
          controls.appendChild(createAssetIconButton("modify.png", "Edit saved condition", () => {
            openDraft({ ...entry.items[0], conditionId: entry.conditionId });
          }));
          controls.appendChild(createAssetIconButton("close.png", "Delete saved condition", () => {
            removeSavedCondition(entry.conditionId);
          }));
          row.appendChild(controls);
          card.appendChild(row);

          list.appendChild(card);
        });
      }

      section.appendChild(list);
      return section;
    }

    function renderGroupAddMenu(node) {
      const wrap = document.createElement("div");
      wrap.className = "condition-tree-group-add-wrap";

      const trigger = createAssetIconButton("add.png", "Add node", () => toggleAddMenu(node.id));
      trigger.className = "condition-icon-button condition-tree-action-button condition-tree-group-add-trigger";
      wrap.appendChild(trigger);

      if (openAddMenuGroupId === node.id) {
        const menu = document.createElement("div");
        menu.className = "condition-tree-group-add-menu";

        const groupButton = document.createElement("button");
        groupButton.type = "button";
        groupButton.className = "condition-tree-group-add-item";
        groupButton.textContent = "Group";
        groupButton.addEventListener("click", () => addGroup(node.id));
        menu.appendChild(groupButton);

        state.savedConditions.forEach((entry) => {
          const conditionButton = document.createElement("button");
          conditionButton.type = "button";
          conditionButton.className = "condition-tree-group-add-item";
          conditionButton.textContent = entry.conditionId;
          conditionButton.setAttribute("title", entry.expression || entry.conditionId);
          conditionButton.addEventListener("click", () => insertSavedConditionIntoGroup(node.id, entry.conditionId));
          menu.appendChild(conditionButton);
        });

        wrap.appendChild(menu);
      }

      return wrap;
    }

    function renderTreeNode(node, isRoot) {
      if (node.type === "condition") {
        const card = document.createElement("div");
        card.className = "condition-tree-leaf";

        const expression = document.createElement("div");
        expression.className = "condition-summary-rule condition-tree-leaf-rule";
        expression.textContent = conditionDisplayExpression(node.item, symbols, {
          currentCallToolKey,
          allowedSourceTypes,
        }) || "<condition pending>";
        card.appendChild(expression);

        const controls = document.createElement("div");
        controls.className = "condition-tree-leaf-actions";
        controls.appendChild(createAssetIconButton("delete.png", "Delete condition", () => deleteTreeNode(node.id)));
        card.appendChild(controls);
        return card;
      }

      const group = document.createElement("div");
      group.className = "condition-tree-group";

      const header = document.createElement("div");
      header.className = "condition-tree-group-head";

      const title = document.createElement("div");
      title.className = "condition-tree-group-title";
      if (isRoot) {
        title.textContent = "Logic Root";
      } else {
        title.textContent = "Group";
      }
      header.appendChild(title);

      const actions = document.createElement("div");
      actions.className = "condition-tree-group-actions";
      const operatorToggle = document.createElement("div");
      operatorToggle.className = "condition-tree-group-toggle";
      const andButton = createButton("AND", `filter-chip${node.type === "AND" ? " active" : ""}`, () => setGroupType(node.id, "AND"));
      andButton.setAttribute("aria-label", isRoot ? "Set root logic to AND" : "Set group logic to AND");
      const orButton = createButton("OR", `filter-chip${node.type === "OR" ? " active" : ""}`, () => setGroupType(node.id, "OR"));
      orButton.setAttribute("aria-label", isRoot ? "Set root logic to OR" : "Set group logic to OR");
      operatorToggle.appendChild(andButton);
      operatorToggle.appendChild(orButton);
      actions.appendChild(operatorToggle);
      actions.appendChild(renderGroupAddMenu(node));
      if (!isRoot) {
        actions.appendChild(createAssetIconButton("delete.png", "Delete group", () => deleteTreeNode(node.id)));
      }
      header.appendChild(actions);
      group.appendChild(header);

      const body = document.createElement("div");
      body.className = "condition-tree-group-body";
      if (!node.children.length) {
        const empty = document.createElement("div");
        empty.className = "empty-state";
        empty.textContent = "Empty group. Insert a saved condition or a nested group.";
        body.appendChild(empty);
      } else {
        node.children.forEach((child) => body.appendChild(renderTreeNode(child, false)));
      }
      group.appendChild(body);
      return group;
    }

    function renderCanvas() {
      const section = document.createElement("section");
      section.className = "condition-tree-section";

      const header = document.createElement("div");
      header.className = "condition-tree-section-head";
      header.appendChild(createSectionTitleWithHint(
        "Logic Canvas",
        "You can combine saved single conditions here to package them into a complex rule."
      ));
      section.appendChild(header);
      section.appendChild(renderTreeNode(state.tree, true));
      return section;
    }

    function renderPreview() {
      const section = document.createElement("section");
      section.className = "condition-tree-section";
      const header = document.createElement("div");
      header.className = "condition-tree-section-head";
      const title = document.createElement("strong");
      title.textContent = "CONDITION Preview";
      header.appendChild(title);
      section.appendChild(header);
      const preview = document.createElement("pre");
      preview.className = "condition-tree-preview code-block";
      preview.textContent = state.expression || "<condition pending>";
      section.appendChild(preview);
      return section;
    }

    function syncLockState() {
      if (addButton) {
        addButton.disabled = locked;
      }
    }

    function render() {
      if (!root) {
        return;
      }
      ensureRootGroup();
      syncLockState();
      root.innerHTML = "";
      if (state.draftItem) {
        root.appendChild(renderDraftBuilder());
      }
      root.appendChild(renderLibrary());
      root.appendChild(renderCanvas());
      root.appendChild(renderPreview());
      updateHint();
    }

    if (addButton) {
      addButton.addEventListener("click", () => {
        if (locked) {
          return;
        }
        openDraft();
      });
    }

    const api = {
      getValue() {
        return {
          items: state.items.map(cloneItem),
          symbolToolMap: { ...state.symbolToolMap },
          savedConditions: state.savedConditions.map((entry) => ({
            conditionId: entry.conditionId,
            expression: entry.expression,
            items: entry.items.map(cloneItem),
            tree: cloneNode(entry.tree),
          })),
          tree: cloneNode(state.tree),
          expression: state.expression,
        };
      },
      getMode() {
        return "tree";
      },
      setMode() {},
      setValue(value) {
        state = normalizeState(value || {});
        syncFromTree();
        render();
        emit();
      },
      setLocked(nextLocked) {
        locked = Boolean(nextLocked);
        render();
      },
      setAllowedSourceTypes(nextAllowedSourceTypes) {
        allowedSourceTypes = Array.isArray(nextAllowedSourceTypes) ? nextAllowedSourceTypes.slice() : [];
        state = normalizeState(api.getValue());
        syncFromTree();
        render();
        emit();
      },
      setPathSymbols(nextSymbols) {
        symbols = Array.isArray(nextSymbols) && nextSymbols.length ? nextSymbols : ["A"];
        state = normalizeState(api.getValue());
        syncFromTree();
        render();
        emit();
      },
      setCurrentCallToolKey(nextToolKey) {
        currentCallToolKey = String(nextToolKey || "");
        state = normalizeState(api.getValue());
        syncFromTree();
        render();
        emit();
      },
      setCurrentCallSubtype(nextSubtype) {
        currentCallSubtype = String(nextSubtype || "");
        state = normalizeState(api.getValue());
        syncFromTree();
        render();
        emit();
      },
      clear() {
        state = normalizeState({
          items: [],
          tree: createGroupNode("AND", []),
          savedConditions: [],
        });
        render();
        emit();
      },
      validate() {
        if (!state.items.length) {
          return { ok: false, message: "At least one condition is required." };
        }
        for (const item of state.items) {
          if (!item.expression) {
            return { ok: false, message: "One condition is incomplete." };
          }
          if (item.sourceType === "trace" && item.feature === "syntax" && !item.selectedToolKey) {
            return { ok: false, message: "Trace syntax conditions need a tool selection first." };
          }
          if (item.sourceType === "context" && !item.contextPath) {
            return { ok: false, message: "Context conditions need a valid field path." };
          }
        }
        return { ok: true, message: "CONDITION is valid." };
      },
    };

    render();
    return api;
  }

  window.AgentGuardConditionBuilder = {
    createConditionBuilder,
    inferSymbolToolMap,
    itemsToTree,
    normalizeItems,
  };
})();

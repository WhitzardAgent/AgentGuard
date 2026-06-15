(function () {
  const SUPPORTED_ACTIONS = new Set(["DENY", "HUMAN_CHECK", "LLM_CHECK", "ALLOW", "DEGRADE"]);
  const CONTEXT_PREFIXES = new Set(["tool", "principal"]);
  const ON_SUBTYPES = ["requested", "completed", "failed"];

  function escapeString(value) {
    return String(value)
      .replace(/\\/g, "\\\\")
      .replace(/"/g, '\\"');
  }

  function normalizeRuleName(name) {
    const candidate = String(name || "").trim();
    if (!/^[A-Za-z_][A-Za-z0-9_]*$/.test(candidate)) {
      throw new Error(`Rule name "${candidate || "<empty>"}" is not a valid DSL identifier.`);
    }
    return candidate;
  }

  function normalizeAction(action) {
    const candidate = String(action || "").trim();
    if (!SUPPORTED_ACTIONS.has(candidate)) {
      throw new Error(`Action "${candidate || "<empty>"}" is not supported by the AgentGuard DSL.`);
    }
    return candidate;
  }

  function normalizeDegradeTarget(rule) {
    const explicit = String(rule?.degradeTarget || rule?.degrade_profile || rule?.profile || "").trim();
    return explicit || "";
  }

  function normalizeOnClause(rule) {
    const explicit = String(rule?.onClause || "").trim();
    if (explicit) {
      return explicit;
    }
    const onSubtype = String(rule?.onSubtype || rule?.on?.subtype || "").trim();
    const onTool = String(rule?.onToolPattern || rule?.on?.tool_pattern || rule?.on?.tool || "").trim();
    if (!onSubtype && !onTool) {
      return "";
    }
    if (onSubtype && onTool) {
      return `tool_call.${onSubtype}(${onTool})`;
    }
    if (onSubtype) {
      return `tool_call.${onSubtype}`;
    }
    return `tool_call(${onTool})`;
  }

  function isValidOnClause(value) {
    const candidate = String(value || "").trim();
    if (!candidate) {
      return false;
    }

    const ident = "[A-Za-z_][A-Za-z0-9_]*";
    const toolPattern = `(?:\\*|${ident}(?:\\.${ident})*(?:\\.\\*)?)`;
    const subtype = `(?:${ON_SUBTYPES.join("|")})`;
    const directPattern = new RegExp(`^tool_call\\(${toolPattern}\\)$`);
    const subtypeOnlyPattern = new RegExp(`^tool_call\\.${subtype}$`);
    const subtypeWithPattern = new RegExp(`^tool_call\\.${subtype}\\(${toolPattern}\\)$`);

    return directPattern.test(candidate) || subtypeOnlyPattern.test(candidate) || subtypeWithPattern.test(candidate);
  }

  function normalizePath(path) {
    const segments = String(path || "")
      .split("->")
      .map((segment) => segment.trim())
      .filter(Boolean);
    if (!segments.length) {
      return "";
    }
    return segments.join(" -> ");
  }

  function conditionPath(item) {
    const sourceType = String(item?.sourceType || "trace").trim() || "trace";
    if (sourceType === "context") {
      const contextPath = String(item?.contextPath || "").trim();
      if (!contextPath) {
        throw new Error("Context conditions require a context path before publishing.");
      }
      const prefix = contextPath.split(".")[0];
      if (!CONTEXT_PREFIXES.has(prefix)) {
        throw new Error(`Unsupported context path "${contextPath}".`);
      }
      return contextPath;
    }

    const symbol = String(item?.symbol || "A").trim() || "A";
    if (item.feature === "name") {
      return `${symbol}.name`;
    }
    if (item.feature === "label.boundary") {
      return `${symbol}.boundary`;
    }
    if (item.feature === "label.sensitivity") {
      return `${symbol}.sensitivity`;
    }
    if (item.feature === "label.integrity") {
      return `${symbol}.integrity`;
    }
    if (item.feature === "syntax") {
      if (!item.syntaxField) {
        throw new Error("Syntax conditions require a syntax field before publishing.");
      }
      return `${symbol}.${item.syntaxField}`;
    }
    throw new Error(`Unsupported condition feature "${item.feature || "<empty>"}".`);
  }

  function serializeValue(item) {
    const rawValue = String(item.value || "");
    const sourceType = String(item?.sourceType || "trace").trim() || "trace";
    const operator = serializeOperator(item?.operator);
    if (operator === "IN" || operator === "NOT IN") {
      return rawValue.trim();
    }
    if (
      (item.feature === "syntax" || sourceType === "context")
      && /^-?\d+(?:\.\d+)?$/.test(rawValue)
    ) {
      return rawValue;
    }
    return `"${escapeString(rawValue)}"`;
  }

  function serializeOperator(operator) {
    if (operator === "contains") {
      return "CONTAINS";
    }
    return String(operator || "").trim();
  }

  function serializeConditionItem(item) {
    const path = conditionPath(item);
    const operator = serializeOperator(item.operator);
    if (!operator) {
      throw new Error("Condition operator is required before publishing.");
    }
    if (item.value === undefined || item.value === null || String(item.value).trim() === "") {
      throw new Error("Condition value is required before publishing.");
    }

    const openParen = item.openParen || "";
    const closeParen = item.closeParen || "";
    return `${openParen}${path} ${operator} ${serializeValue(item)}${closeParen}`;
  }

  function serializeConditionItems(items) {
    if (!Array.isArray(items) || !items.length) {
      throw new Error("At least one condition is required before publishing.");
    }

    return items.map((item, index) => {
      const expression = serializeConditionItem(item);
      if (index === 0) {
        return expression;
      }
      const connector = String(item.connector || "AND").trim() || "AND";
      return `${connector} ${expression}`;
    }).join("\n  ");
  }

  function serializeRule(rule) {
    const name = normalizeRuleName(rule?.name);
    const action = normalizeAction(rule?.action);
    const degradeTarget = normalizeDegradeTarget(rule);
    const rawPath = String(rule?.path || "").trim();
    const onClause = normalizeOnClause(rule);
    const path = normalizePath(rawPath);
    const condition = serializeConditionItems(rule?.conditionItems || []);
    if (!path && !onClause) {
      throw new Error("At least one formal match is required before publishing.");
    }
    if (onClause && !isValidOnClause(onClause)) {
      throw new Error(`ON clause "${onClause}" is not a supported tool_call expression.`);
    }
    if (action === "DEGRADE" && !degradeTarget) {
      throw new Error("DEGRADE target is required before publishing.");
    }
    const lines = [
      `RULE: ${name}`,
      ...(onClause ? [`ON: ${onClause}`] : []),
      ...(path ? [`TRACE: ${path}`] : []),
      `CONDITION: ${condition}`,
      `POLICY: ${action === "DEGRADE" ? `DEGRADE TO "${escapeString(degradeTarget)}"` : action}`,
    ];
    const severity = String(rule?.severity || "").trim();
    const category = String(rule?.category || "").trim();
    const reason = String(rule?.reason || "").trim();
    const prompt = String(rule?.prompt || "").trim();
    if (action === "LLM_CHECK" && prompt) {
      lines.push(`Prompt: "${escapeString(prompt)}"`);
    }
    if (severity) {
      lines.push(`Severity: ${severity}`);
    }
    if (category) {
      lines.push(`Category: ${category}`);
    }
    if (reason) {
      lines.push(`Reason: "${escapeString(reason)}"`);
    }
    return lines.join("\n");
  }

  function serializeRules(rules) {
    if (!Array.isArray(rules) || !rules.length) {
      throw new Error("At least one local rule is required before publishing.");
    }
    return rules.map(serializeRule).join("\n\n");
  }

  window.AgentGuardRuleDSL = {
    isValidOnClause,
    normalizeDegradeTarget,
    normalizeOnClause,
    serializeRule,
    serializeRules,
  };
})();

(function () {
  const ruleDsl = window.AgentGuardRuleDSL || {};
  const parser = window.AgentGuardRuleParser || {};
  const model = window.AgentGuardRuleModel || {};
  const toolCatalogHelpers = window.AgentGuardToolCatalog || {};
  const toolData = window.AgentGuardData || {};
  const utils = window.AgentGuardRuleUtils || {};
  const onClause = window.AgentGuardRuleOnClause || {};

  function deriveCurrentCallToolKey(rule) {
    const explicitToolKey = String(rule?.onToolKey || rule?.selectedToolKey || "").trim();
    if (explicitToolKey) {
      return explicitToolKey;
    }

    const derivedOnClause = onClause.deriveOnClause(rule);
    const parsed = typeof onClause.parseOnClauseParts === "function"
      ? onClause.parseOnClauseParts(derivedOnClause)
      : { toolPattern: "" };
    const toolPattern = String(parsed?.toolPattern || "").trim();
    if (!toolPattern || toolPattern.includes("*")) {
      return "";
    }

    const catalog = typeof toolData?.loadToolCatalog === "function"
      ? toolData.loadToolCatalog()
      : [];
    if (typeof toolCatalogHelpers.toolKeyForName === "function") {
      return String(toolCatalogHelpers.toolKeyForName(toolPattern, catalog) || "").trim();
    }
    const matched = Array.isArray(catalog)
      ? catalog.find((tool) => String(tool?.name || "").trim() === toolPattern)
      : null;
    return String(matched?.tool_key || "").trim();
  }

  function buildPreview(rule) {
    const normalized = model.normalizeRule(rule, {
      currentCallToolKey: deriveCurrentCallToolKey(rule),
    });
    if (normalized.name && normalized.conditionItems.length && normalized.action && (normalized.path || normalized.onClause)) {
      try {
        return ruleDsl.serializeRule(normalized);
      } catch (error) {
        // Fall back to the preview-only formatter below while the form is incomplete.
      }
    }

    const condition = normalized.condition
      ? normalized.condition.replace(/\s+(AND|OR)\s+/g, "\n  $1 ")
      : "<condition pending>";
    const lines = [
      `RULE: ${normalized.name || "unnamed_rule"}`,
      ...(normalized.onClause ? [`ON: ${onClause.deriveOnClause(normalized)}`] : []),
      ...(normalized.path
        ? [`TRACE: ${normalized.path.split("->").map((segment) => segment.trim()).filter(Boolean).join(" -> ")}`]
        : []),
      `CONDITION: ${condition}`,
      `POLICY: ${normalized.action === "DEGRADE"
        ? `DEGRADE TO "${normalized.degradeTarget || "<target pending>"}"`
        : (normalized.action || "<action pending>")}`,
    ];
    if (normalized.action === "LLM_CHECK" && normalized.prompt) {
      lines.push(`Prompt: "${normalized.prompt}"`);
    }
    if (normalized.severity) {
      lines.push(`Severity: ${normalized.severity}`);
    }
    if (normalized.category) {
      lines.push(`Category: ${normalized.category}`);
    }
    if (normalized.reason) {
      lines.push(`Reason: "${normalized.reason}"`);
    }
    return lines.join("\n");
  }

  function buildRuleListSource(rule, status) {
    if (status === utils.RULE_STATUS_UNPUBLISHED) {
      return buildPreview(rule);
    }

    const currentRuleSource = (parser.extractPublishedRuleSource || function fallback(source) {
      return String(source || "").trim();
    })(
      rule?.source || "",
      rule?.rule_id || rule?.name || rule?.id || "",
    );
    const restored = parser.parsePublishedRuleSource
      ? parser.parsePublishedRuleSource(currentRuleSource, model.normalizeRule, utils.RULE_STATUS_PUBLISHED)
      : null;
    if (restored) {
      return buildPreview({
        ...restored,
        action: rule?.action || restored.action,
        onClause: restored.onClause || rule?.onClause,
        severity: restored.severity || rule?.severity,
        category: restored.category || rule?.category,
        reason: restored.reason || rule?.reason,
        prompt: restored.prompt || rule?.prompt,
        description: rule?.description || restored.description,
      });
    }

    return currentRuleSource || `RULE: ${rule?.rule_id || "-"}
TOOL_PATTERN: ${rule?.tool_pattern || "*"}
ACTION: ${rule?.action || "-"}
VERSION: ${rule?.version || "unknown"}`;
  }

  window.AgentGuardRulePreview = {
    buildPreview,
    buildRuleListSource,
  };
})();

(function () {
  const RULE_STATUS_PUBLISHED = "published";
  const RULE_STATUS_UNPUBLISHED = "unpublished";
  const severityOptions = ["critical", "high", "medium", "low", "info"];

  function ruleKey(rule) {
    return String(rule?.id || rule?.name || rule?.rule_id || "").trim();
  }

  function ruleDisplayName(rule) {
    return String(rule?.name || rule?.rule_id || rule?.id || "").trim() || "-";
  }

  function withRuleStatus(rule, fallbackStatus = RULE_STATUS_UNPUBLISHED) {
    const status = String(rule?.status || fallbackStatus).trim() || fallbackStatus;
    const isPublished = status === RULE_STATUS_PUBLISHED;
    return {
      ...rule,
      id: String(rule?.id || rule?.name || rule?.rule_id || "").trim(),
      name: String(rule?.name || rule?.rule_id || rule?.id || "").trim(),
      status: isPublished ? RULE_STATUS_PUBLISHED : RULE_STATUS_UNPUBLISHED,
      source: String(rule?.source || "").trim(),
      onClause: String(rule?.onClause || "").trim(),
      severity: String(rule?.severity || "").trim(),
      category: String(rule?.category || "").trim(),
      reason: String(rule?.reason || "").trim(),
      prompt: String(rule?.prompt || "").trim(),
      degradeTarget: String(rule?.degradeTarget || "").trim(),
      description: String(rule?.description || "").trim(),
      packId: String(rule?.packId || rule?.pack_id || "").trim(),
      userManaged: typeof rule?.userManaged === "boolean"
        ? rule.userManaged
        : typeof rule?.user_managed === "boolean"
          ? rule.user_managed
          : !isPublished,
    };
  }

  function normalizeSeverityValue(value) {
    const normalized = String(value || "").trim().toLowerCase();
    if (!normalized) {
      return "";
    }
    return severityOptions.includes(normalized) ? normalized : "";
  }

  function normalizeEntryModeValue(rule) {
    const explicit = String(rule?.entryMode || "").trim().toLowerCase();
    if (explicit === "trace" || explicit === "on") {
      return explicit;
    }
    const hasPath = String(rule?.path || "").trim() !== "";
    const hasOnClause = String(rule?.onClause || "").trim() !== "" || String(rule?.on?.tool || "").trim() !== "";
    if (hasOnClause) {
      return "on";
    }
    return "trace";
  }

  function filterRuleItems(items, kind) {
    if (kind === RULE_STATUS_PUBLISHED) {
      return items.filter((item) => item.status === RULE_STATUS_PUBLISHED);
    }
    if (kind === RULE_STATUS_UNPUBLISHED) {
      return items.filter((item) => item.status === RULE_STATUS_UNPUBLISHED);
    }
    return items;
  }

  window.AgentGuardRuleUtils = {
    RULE_STATUS_PUBLISHED,
    RULE_STATUS_UNPUBLISHED,
    filterRuleItems,
    normalizeEntryModeValue,
    normalizeSeverityValue,
    ruleDisplayName,
    ruleKey,
    severityOptions,
    withRuleStatus,
  };
})();

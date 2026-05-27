(function () {
  const utils = window.AgentGuardRuleUtils || {};
  const onClause = window.AgentGuardRuleOnClause || {};
  const normalizePathValue = window.AgentGuardPathBuilder?.normalizeValue;
  const normalizeConditionItems = window.AgentGuardConditionBuilder?.normalizeItems;
  const supportedActions = new Set(["DENY", "HUMAN_CHECK", "LLM_CHECK", "ALLOW", "DEGRADE"]);

  function pathSymbolsFromState(pathState) {
    return (Array.isArray(pathState?.pathSlots) ? pathState.pathSlots : [])
      .filter((segment) => segment.value === segment.label)
      .map((segment) => segment.label);
  }

  function normalizeRuleIdentity(rule) {
    return {
      id: String(rule?.id || rule?.name || "").trim(),
      name: String(rule?.name || "").trim(),
      status: String(rule?.status || utils.RULE_STATUS_UNPUBLISHED).trim() || utils.RULE_STATUS_UNPUBLISHED,
      entryMode: utils.normalizeEntryModeValue(rule),
    };
  }

  function normalizeRulePath(rule) {
    const pathState = typeof normalizePathValue === "function" ? normalizePathValue(rule) : {
      path: String(rule?.path || "").trim(),
      pathSlots: Array.isArray(rule?.pathSlots) ? rule.pathSlots : [],
    };
    return {
      path: pathState.path,
      pathSlots: pathState.pathSlots,
      pathSymbols: pathSymbolsFromState(pathState),
    };
  }

  function normalizeRuleCondition(rule, symbols, options = {}) {
    const normalizedCondition = typeof normalizeConditionItems === "function"
      ? normalizeConditionItems(
        { items: rule?.conditionItems || (rule?.conditionState ? [rule.conditionState] : []) },
        symbols.length ? symbols : ["A"],
        options,
      )
      : { items: Array.isArray(rule?.conditionItems) ? rule.conditionItems : [], symbolToolMap: rule?.symbolToolMap || {} };

    return {
      condition: normalizedCondition.items
        .map((item, index) => index === 0 ? item.expression : `${item.connector} ${item.expression}`)
        .join(" "),
      conditionItems: normalizedCondition.items.map((item, index) => ({
        conditionId: item.conditionId || "",
        confirmed: Boolean(item.confirmed),
        stepStage: item.stepStage || "complete",
        connector: index === 0 ? "" : item.connector,
        openParen: item.openParen || "",
        closeParen: item.closeParen || "",
        sourceType: item.sourceType || "trace",
        symbol: item.symbol,
        feature: item.feature,
        propertyGroup: item.propertyGroup || "",
        syntaxField: item.syntaxField,
        operator: item.operator,
        value: item.value,
        selectedToolKey: item.selectedToolKey || "",
        contextPrefix: item.contextPrefix || "",
        contextField: item.contextField || "",
        contextFieldName: item.contextFieldName || "",
        contextPath: item.contextPath || "",
      })),
      symbolToolMap: normalizedCondition.symbolToolMap || {},
      conditionSavedConditions: Array.isArray(rule?.conditionSavedConditions)
        ? rule.conditionSavedConditions.map((entry) => ({
          conditionId: String(entry?.conditionId || "").trim(),
          expression: String(entry?.expression || "").trim(),
          items: Array.isArray(entry?.items)
            ? entry.items.map((item, index) => ({
              conditionId: item?.conditionId || "",
              confirmed: Boolean(item?.confirmed),
              stepStage: item?.stepStage || "complete",
              connector: index === 0 ? "" : String(item?.connector || "AND"),
              openParen: item?.openParen || "",
              closeParen: item?.closeParen || "",
              sourceType: item?.sourceType || "trace",
              symbol: item?.symbol || "",
              feature: item?.feature || "",
              propertyGroup: item?.propertyGroup || "",
              syntaxField: item?.syntaxField || "",
              operator: item?.operator || "",
              value: item?.value || "",
              selectedToolKey: item?.selectedToolKey || "",
              contextPrefix: item?.contextPrefix || "",
              contextField: item?.contextField || "",
              contextFieldName: item?.contextFieldName || "",
              contextPath: item?.contextPath || "",
            }))
            : [],
        }))
        : [],
      conditionCurrentId: String(rule?.conditionCurrentId || "").trim(),
    };
  }

  function normalizeRuleAction(rule) {
    const rawAction = String(rule?.action || "ALLOW").trim() || "ALLOW";
    const action = supportedActions.has(rawAction) ? rawAction : "ALLOW";
    return {
      action,
      degradeTarget: action === "DEGRADE" ? onClause.deriveDegradeTarget(rule) : "",
    };
  }

  function normalizeRuleMetadata(rule) {
    return {
      onClause: onClause.deriveOnClause(rule),
      severity: utils.normalizeSeverityValue(rule?.severity),
      category: String(rule?.category || "").trim(),
      reason: String(rule?.reason || "").trim(),
      prompt: String(rule?.prompt || "").trim(),
      description: String(rule?.description || "").trim(),
      source: String(rule?.source || "").trim(),
    };
  }

  function normalizeRule(rule, options = {}) {
    const path = normalizeRulePath(rule);
    return {
      ...normalizeRuleIdentity(rule),
      path: path.path,
      pathSlots: path.pathSlots,
      ...normalizeRuleCondition(rule, path.pathSymbols, options),
      ...normalizeRuleAction(rule),
      ...normalizeRuleMetadata(rule),
    };
  }

  window.AgentGuardRuleModel = {
    normalizeRule,
    normalizeRuleAction,
    normalizeRuleCondition,
    normalizeRuleIdentity,
    normalizeRuleMetadata,
    normalizeRulePath,
    pathSymbolsFromState,
    supportedActions,
  };
})();

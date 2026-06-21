(function () {
  const validatePathState = window.AgentGuardPathBuilder?.validatePathState;

  function validatePathValue(rule) {
    if (typeof validatePathState === "function") {
      return validatePathState(window.AgentGuardPathBuilder.normalizeValue(rule));
    }
    const path = window.AgentGuardPathBuilder.normalizeValue(rule);
    return path.pathSlots.length
      ? { ok: true, message: "PATH is valid." }
      : { ok: false, message: "PATH must contain at least one concrete segment." };
  }

  function validateStoredRule(rule) {
    if (!rule.name || !rule.conditionItems?.length || !rule.action) {
      return { ok: false, message: "Stored rule is missing required fields." };
    }
    if (rule.action === "DEGRADE" && !String(rule.degradeTarget || "").trim()) {
      return { ok: false, message: "Stored DEGRADE rule is missing its target tool." };
    }

    let balance = 0;
    for (const item of rule.conditionItems) {
      if (!item.operator || String(item.value || "").trim() === "") {
        return { ok: false, message: "Stored rule has an incomplete condition." };
      }
      if ((item.sourceType || "trace") === "context") {
        if (!String(item.contextPath || "").trim()) {
          return { ok: false, message: "Stored rule has an incomplete context condition." };
        }
      } else if (!item.feature) {
        return { ok: false, message: "Stored rule has an incomplete trace condition." };
      }
      balance += (item.openParen || "").length;
      balance -= (item.closeParen || "").length;
      if (balance < 0) {
        return { ok: false, message: "Stored rule has unbalanced parentheses." };
      }
    }
    if (balance !== 0) {
      return { ok: false, message: "Stored rule has unbalanced parentheses." };
    }

    return { ok: true, message: "Stored rule is valid." };
  }

  function validateRuleData(rule) {
    if (!rule.name || !rule.condition || !rule.action) {
      return { ok: false, message: "Please fill RULENAME, CONDITION, and ACTION first." };
    }
    if (rule.action === "DEGRADE" && !rule.degradeTarget) {
      return { ok: false, message: "DEGRADE target is required for DEGRADE rules." };
    }
    const hasPath = Boolean(String(rule.path || "").trim());
    const hasOnClause = Boolean(String(rule.onClause || "").trim());
    if (!hasPath && !hasOnClause) {
      return { ok: false, message: "Please configure ON or TRACE before generating the rule." };
    }
    if (hasPath) {
      const pathValidation = validatePathValue(rule);
      if (!pathValidation.ok) {
        return pathValidation;
      }
    }
    return validateStoredRule(rule);
  }

  function firstDiagnostic(report, levels = ["error", "warning", "hint"]) {
    for (const level of levels) {
      const entries = Array.isArray(report?.[`${level}s`]) ? report[`${level}s`] : [];
      if (entries.length) {
        return entries[0];
      }
    }
    return null;
  }

  function summarizeCheckReport(report) {
    const errorCount = Array.isArray(report?.errors) ? report.errors.length : 0;
    const warningCount = Array.isArray(report?.warnings) ? report.warnings.length : 0;
    const hintCount = Array.isArray(report?.hints) ? report.hints.length : 0;
    const diagnostic = firstDiagnostic(report);
    const message = diagnostic?.message || "";
    return {
      errorCount,
      warningCount,
      hintCount,
      message,
    };
  }

  window.AgentGuardRuleValidation = {
    firstDiagnostic,
    summarizeCheckReport,
    validatePathValue,
    validateRuleData,
    validateStoredRule,
  };
})();

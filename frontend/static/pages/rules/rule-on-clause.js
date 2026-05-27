(function () {
  const ruleDsl = window.AgentGuardRuleDSL || {};
  const supportedOnSubtypes = ["requested", "attempted", "attempt", "completed", "result", "failed"];
  const supportedOnSubtypeSet = new Set(supportedOnSubtypes);

  function deriveOnClause(rule) {
    const explicit = ruleDsl.normalizeOnClause ? ruleDsl.normalizeOnClause(rule) : String(rule?.onClause || "").trim();
    return explicit || "";
  }

  function deriveDegradeTarget(rule) {
    const explicit = ruleDsl.normalizeDegradeTarget ? ruleDsl.normalizeDegradeTarget(rule) : String(rule?.degradeTarget || "").trim();
    return explicit || "";
  }

  function parseOnClauseParts(value) {
    const source = String(value || "").trim();
    if (!source) {
      return { subtype: "", toolPattern: "" };
    }
    const matched = source.match(/^tool_call(?:\.([A-Za-z_][A-Za-z0-9_]*))?(?:\(([A-Za-z_][A-Za-z0-9_.]*|[A-Za-z_][A-Za-z0-9_]*\.\*)\))?$/);
    if (!matched) {
      return { subtype: "", toolPattern: "" };
    }
    const subtype = supportedOnSubtypeSet.has(String(matched[1] || "").trim()) ? String(matched[1] || "").trim() : "";
    const toolPattern = String(matched[2] || "").trim();
    return { subtype, toolPattern };
  }

  function buildOnClause(subtype, toolName) {
    const normalizedSubtype = String(subtype || "").trim();
    const normalizedToolName = String(toolName || "").trim();
    if (!normalizedSubtype && !normalizedToolName) {
      return "";
    }
    if (normalizedSubtype && normalizedToolName) {
      return `tool_call.${normalizedSubtype}(${normalizedToolName})`;
    }
    if (normalizedSubtype) {
      return `tool_call.${normalizedSubtype}`;
    }
    return `tool_call(${normalizedToolName})`;
  }

  window.AgentGuardRuleOnClause = {
    buildOnClause,
    deriveDegradeTarget,
    deriveOnClause,
    parseOnClauseParts,
    supportedOnSubtypes,
  };
})();

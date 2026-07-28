(function () {
  const ruleDsl = window.AgentGuardRuleDSL || {};

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
    const matched = source.match(/^tool_call(?:\.([A-Za-z_][A-Za-z0-9_]*))?(?:\(([^()\s]+)\))?$/);
    if (!matched) {
      return { subtype: "", toolPattern: "" };
    }
    const subtype = String(matched[1] || "").trim();
    const toolPattern = String(matched[2] || "").trim();
    return { subtype, toolPattern };
  }

  function buildOnClause(toolName, subtype = "") {
    const normalizedToolName = String(toolName || "").trim();
    const normalizedSubtype = String(subtype || "").trim();
    if (!normalizedToolName) {
      return "";
    }
    return normalizedSubtype ? `tool_call.${normalizedSubtype}(${normalizedToolName})` : `tool_call(${normalizedToolName})`;
  }

  window.AgentGuardRuleOnClause = {
    buildOnClause,
    deriveDegradeTarget,
    deriveOnClause,
    parseOnClauseParts,
  };
})();

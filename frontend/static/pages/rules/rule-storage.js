(function () {
  const RULE_LIST_KEY = "agentguard.ruleList";
  const RULE_STATUS_PUBLISHED = "published";
  const RULE_STATUS_UNPUBLISHED = "unpublished";

  function normalizeStoredRule(rule) {
    const name = String(rule?.name || rule?.rule_id || rule?.id || "").trim();
    const status = String(rule?.status || RULE_STATUS_UNPUBLISHED).trim() || RULE_STATUS_UNPUBLISHED;

    return {
      ...rule,
      id: String(rule?.id || name).trim(),
      name,
      status: status === RULE_STATUS_PUBLISHED ? RULE_STATUS_PUBLISHED : RULE_STATUS_UNPUBLISHED,
    };
  }

  function loadList() {
    try {
      const raw = localStorage.getItem(RULE_LIST_KEY);
      const parsed = raw ? JSON.parse(raw) : null;
      return Array.isArray(parsed) && parsed.length ? parsed.map(normalizeStoredRule) : [];
    } catch {
      return [];
    }
  }

  function saveList(rules) {
    const normalized = Array.isArray(rules) ? rules.map(normalizeStoredRule) : [];
    localStorage.setItem(RULE_LIST_KEY, JSON.stringify(normalized));
  }

  window.AgentGuardRuleStorage = {
    loadList,
    saveList,
  };
})();

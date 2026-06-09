(function () {
  function createRuleStore({
    storage,
    normalizeStoredLocalRule,
    normalizeRule,
    withRuleStatus,
    ruleKey,
    unpublishedStatus,
  }) {
    let generatedRules = [];

    function persist() {
      storage.saveList(generatedRules);
    }

    function load() {
      generatedRules = storage.loadList().map(normalizeStoredLocalRule);
      return generatedRules.slice();
    }

    function list() {
      return generatedRules.slice();
    }

    function replace(nextRules) {
      generatedRules = Array.isArray(nextRules) ? nextRules.slice() : [];
      persist();
      return list();
    }

    function upsert(rule) {
      const nextRule = withRuleStatus(normalizeRule(rule), unpublishedStatus);
      const existingIndex = generatedRules.findIndex((item) => ruleKey(item) === ruleKey(nextRule));
      if (existingIndex >= 0) {
        generatedRules[existingIndex] = nextRule;
      } else {
        generatedRules.unshift(nextRule);
      }
      persist();
      return nextRule;
    }

    function remove(rule) {
      generatedRules = generatedRules.filter((item) => ruleKey(item) !== ruleKey(rule));
      persist();
      return list();
    }

    return {
      list,
      load,
      remove,
      replace,
      upsert,
    };
  }

  window.AgentGuardGeneratedRuleStore = {
    create: createRuleStore,
  };
})();

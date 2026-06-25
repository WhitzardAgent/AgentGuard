(function () {
  function createRuleService({
    api,
    ruleDsl,
    parser,
    normalizeRule,
    withRuleStatus,
    normalizeActiveRule,
    validateRuleData,
    validateCurrentRuleForm,
    summarizeCheckReport,
    ruleKey,
    publishedStatus,
    unpublishedStatus,
  }) {
    async function checkSource(source) {
      const payload = await api.fetchJson("/api/rules/check", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ source }),
      });
      if (!payload || typeof payload !== "object") {
        throw new Error("Rule check response has an unexpected format.");
      }
      return payload;
    }

    async function checkRule(ruleInput, { validateWithForm = false, source = "" } = {}) {
      const rule = withRuleStatus(normalizeRule(ruleInput), unpublishedStatus);
      const validation = validateWithForm
        ? validateCurrentRuleForm(rule)
        : validateRuleData(rule);
      if (!validation.ok) {
        return {
          ok: false,
          local: true,
          message: validation.message,
          report: null,
          source: "",
        };
      }

      let nextSource = String(source || "").trim();
      if (!nextSource) {
        try {
          nextSource = ruleDsl.serializeRule(rule);
        } catch (error) {
          return {
            ok: false,
            local: true,
            message: error instanceof Error ? error.message : "Failed to build DSL source.",
            report: null,
            source: "",
          };
        }
      }

      const report = await checkSource(nextSource);
      const summary = summarizeCheckReport(report);
      return {
        ok: Boolean(report?.ok),
        local: false,
        message: summary.message || (report?.ok ? "Rule validation passed." : "Rule validation failed."),
        report,
        source: nextSource,
      };
    }

    function publishedRulesSourceWith(nextRule, activeRuleList = []) {
      const nextRuleDsl = ruleDsl.serializeRule(nextRule);
      const existingPublishedSources = activeRuleList
        .filter((rule) => ruleKey(rule) !== ruleKey(nextRule))
        .map((rule) => parser.extractPublishedRuleSource(rule.source, ruleKey(rule)))
        .filter(Boolean)
        .filter((source, index, list) => list.indexOf(source) === index);

      return existingPublishedSources.concat(nextRuleDsl).join("\n\n");
    }

    function buildPublishedSourceWithout(targetRule, activeRuleList = []) {
      const remainingPublishedSources = activeRuleList
        .filter((rule) => ruleKey(rule) !== ruleKey(targetRule))
        .map((rule) => parser.extractPublishedRuleSource(rule.source, ruleKey(rule)))
        .filter(Boolean)
        .filter((source, index, list) => list.indexOf(source) === index);

      return remainingPublishedSources.join("\n\n");
    }

    async function reload(source) {
      return api.fetchJson("/api/rules/reload", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ source }),
      });
    }

    async function createAgentRule(agentId, source) {
      const normalizedAgentId = String(agentId || "").trim();
      if (!normalizedAgentId) {
        throw new Error("Select an agent before publishing a rule.");
      }
      return api.fetchJson(`/api/agents/${encodeURIComponent(normalizedAgentId)}/rules`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ source }),
      });
    }

    async function generateCandidate(agentId, payload) {
      const normalizedAgentId = String(agentId || "").trim();
      if (!normalizedAgentId) {
        throw new Error("Select an agent before generating a rule.");
      }
      const requirement = String(payload?.requirement || "").trim();
      if (!requirement) {
        throw new Error("Requirement is required before generating a rule.");
      }
      return api.fetchJson(
        `/api/agents/${encodeURIComponent(normalizedAgentId)}/rules/generate`,
        {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
          },
          body: JSON.stringify({
            requirement,
            user_feedback: String(payload?.user_feedback || "").trim(),
            current_candidate: payload?.current_candidate && typeof payload.current_candidate === "object"
              ? payload.current_candidate
              : null,
            max_rounds: Number(payload?.max_rounds || 4),
            llm_config: payload?.llm_config && typeof payload.llm_config === "object"
              ? payload.llm_config
              : null,
          }),
        },
      );
    }

    async function deleteAgentRule(agentId, ruleId) {
      const normalizedAgentId = String(agentId || "").trim();
      const normalizedRuleId = String(ruleId || "").trim();
      if (!normalizedAgentId) {
        throw new Error("Select an agent before deleting a published rule.");
      }
      if (!normalizedRuleId) {
        throw new Error("Rule ID is required before deleting a published rule.");
      }
      return api.fetchJson(
        `/api/agents/${encodeURIComponent(normalizedAgentId)}/rules/${encodeURIComponent(normalizedRuleId)}`,
        {
          method: "DELETE",
        },
      );
    }

    async function listActive(agentId) {
      const payload = await api.fetchJson(`/api/agents/${encodeURIComponent(agentId)}/rules`);
      if (!Array.isArray(payload)) {
        throw new Error("Active rules payload has an unexpected format.");
      }
      return payload.map(normalizeActiveRule);
    }

    return {
      buildPublishedSourceWithout,
      checkRule,
      checkSource,
      createAgentRule,
      deleteAgentRule,
      generateCandidate,
      listActive,
      publishedRulesSourceWith,
      reload,
    };
  }

  window.AgentGuardRuleService = {
    create: createRuleService,
  };
})();

const test = require("node:test");
const assert = require("node:assert/strict");

global.window = {
  AgentGuardRuleStorage: {
    saveList() {},
    loadList() {
      return [];
    },
  },
  AgentGuardPathBuilder: {
    normalizeValue(value) {
      return {
        path: String(value?.path || ""),
        pathSlots: [],
        finished: true,
      };
    },
    createPathBuilder() {
      return {
        getValue() {
          return { path: "", pathSlots: [], finished: false };
        },
        setValue() {},
        validate() {
          return { ok: true };
        },
        clear() {},
        modify() {},
        appendSegment() {},
        finish() {
          return { ok: true };
        },
      };
    },
  },
  AgentGuardConditionBuilder: {
    normalizeItems(value, _symbols, options = {}) {
      return {
        items: Array.isArray(value?.items)
          ? value.items.map((item) => ({
            ...item,
            contextPath: item.sourceType === "context"
              && item.contextField === "tool.syntax"
              && !item.contextPath
              && options.currentCallToolKey === "agent-alpha::shell.exec"
              && item.syntaxField
                ? `tool.${item.syntaxField}`
                : item.contextPath,
            expression: item.sourceType === "context"
              ? `${(
                item.contextField === "tool.syntax"
                && !item.contextPath
                && options.currentCallToolKey === "agent-alpha::shell.exec"
                && item.syntaxField
              ) ? `tool.${item.syntaxField}` : item.contextPath} ${item.operator === "contains" ? "CONTAINS" : item.operator} "${item.value}"`
              : `${item.symbol}.${item.feature === "name" ? "name" : item.feature.replace(/^label\./, "")} ${item.operator === "contains" ? "CONTAINS" : item.operator} "${item.value}"`,
          }))
          : [],
        symbolToolMap: {},
      };
    },
    createConditionBuilder() {
      return {
        getValue() {
          return { items: [], symbolToolMap: {}, expression: "" };
        },
        setLocked() {},
        setAllowedSourceTypes() {},
        setCurrentCallToolKey() {},
        setPathSymbols() {},
        setValue() {},
        validate() {
          return { ok: true };
        },
        clear() {},
      };
    },
  },
  AgentGuardRuleDSL: {
    isValidOnClause(value) {
      return String(value || "").startsWith("tool_call");
    },
    normalizeOnClause(rule) {
      return String(rule?.onClause || "").trim();
    },
    normalizeDegradeTarget(rule) {
      return String(rule?.degradeTarget || "").trim();
    },
    serializeRule(rule) {
      const lines = [
        `RULE: ${rule.name}`,
      ];
      if (rule.onClause) {
        lines.push(`ON: ${rule.onClause}`);
      }
      lines.push(`TRACE: ${rule.path}`);
      const renderedCondition = rule.conditionItems
        .map((item, index) => {
          const lhs = item.sourceType === "context"
            ? item.contextPath
            : `${item.symbol}.${item.feature === "name" ? "name" : item.feature.replace(/^label\./, "")}`;
          const text = `${lhs} ${item.operator === "contains" ? "CONTAINS" : item.operator} "${item.value}"`;
          return index === 0 ? text : `${item.connector} ${text}`;
      })
        .join("\n  ");
      lines.push(`CONDITION: ${renderedCondition}`);
      lines.push(`POLICY: ${rule.action}`);
      if (rule.action === "LLM_CHECK" && rule.prompt) {
        lines.push(`Prompt: "${rule.prompt}"`);
      }
      if (rule.severity) {
        lines.push(`Severity: ${rule.severity}`);
      }
      if (rule.category) {
        lines.push(`Category: ${rule.category}`);
      }
      if (rule.reason) {
        lines.push(`Reason: "${rule.reason}"`);
      }
      return lines.join("\n");
    },
    serializeRules() {
      return "";
    },
  },
  AgentGuardUI: {
    showToast() {},
  },
  AgentGuardData: {
    loadToolCatalog() {
      return [
        {
          owner_agent_id: "agent-alpha",
          name: "shell.exec",
          tool_key: "agent-alpha::shell.exec",
          input_params: ["cmd", "cwd"],
        },
        {
          owner_agent_id: "agent-alpha",
          name: "http.post",
          tool_key: "agent-alpha::http.post",
          input_params: ["url", "body"],
        },
      ];
    },
    findToolByKey(catalog, toolKey) {
      return (Array.isArray(catalog) ? catalog : []).find((tool) => tool.tool_key === toolKey) || null;
    },
  },
};

global.fetch = async () => ({
  ok: true,
  async json() {
    return [];
  },
});

global.document = {
  getElementById() {
    return {
      value: "",
      textContent: "",
      innerHTML: "",
      disabled: false,
      classList: {
        toggle() {},
      },
      querySelector() {
        return {
          src: "",
        };
      },
      addEventListener() {},
      appendChild() {},
      setAttribute() {},
    };
  },
  querySelectorAll() {
    return [];
  },
  createElement() {
    return {
      className: "",
      type: "",
      innerHTML: "",
      textContent: "",
      classList: {
        add() {},
        remove() {},
        toggle() {},
      },
      appendChild() {},
      addEventListener() {},
      setAttribute() {},
    };
  },
};

require("../static/common/tool-catalog.js");
require("../static/common/ui-helpers.js");
require("../static/pages/rules/rule-parser.js");
require("../static/pages/rules/rules.js");

const { parsePublishedRuleSource } = global.window.AgentGuardRules;
const {
  publishedRulesSourceWith,
  buildPublishedSourceWithout,
  buildRuleListSource,
  extractPublishedRuleSource,
  filterRuleItems,
  normalizeActiveRule,
  normalizeEntryModeValue,
  normalizeSeverityValue,
  normalizeStoredLocalRule,
  ruleKey,
  ruleDisplayName,
  RULE_STATUS_PUBLISHED,
  RULE_STATUS_UNPUBLISHED,
} = global.window.AgentGuardRules;

test("parsePublishedRuleSource restores a published trace rule generated by the studio", () => {
  const restored = parsePublishedRuleSource([
    "RULE: review_external_email",
    "TRACE: A -> ... -> C",
    'CONDITION: (A.sensitivity == "high"',
    '  OR A.subject CONTAINS "@external.com")',
    "POLICY: HUMAN_CHECK",
    "Severity: high",
    "Category: egress_review",
    'Reason: "External email requires approval"',
  ].join("\n"));

  assert.ok(restored);
  assert.equal(restored.name, "review_external_email");
  assert.equal(restored.status, RULE_STATUS_PUBLISHED);
  assert.equal(restored.path, "A -> ... -> C");
  assert.equal(restored.onClause, "");
  assert.equal(restored.action, "HUMAN_CHECK");
  assert.equal(restored.conditionItems.length, 2);
  assert.equal(restored.conditionItems[0].openParen, "(");
  assert.equal(restored.conditionItems[1].connector, "OR");
  assert.equal(restored.conditionItems[1].feature, "syntax");
  assert.equal(restored.conditionItems[1].syntaxField, "subject");
  assert.equal(restored.severity, "high");
});

test("parsePublishedRuleSource restores TRACE plus ON and context conditions", () => {
  const restored = parsePublishedRuleSource([
    "RULE: review_secret_egress",
    "TRACE: A -> ... -> C",
    "ON: tool_call(http.post)",
    'CONDITION: A.name == "secret.read"',
    '  AND tool.boundary == "external"',
    "POLICY: HUMAN_CHECK",
  ].join("\n"));

  assert.ok(restored);
  assert.equal(restored.path, "A -> ... -> C");
  assert.equal(restored.onClause, "tool_call(http.post)");
  assert.equal(restored.conditionItems[1].sourceType, "context");
  assert.equal(restored.conditionItems[1].contextPath, "tool.boundary");
});

test("parsePublishedRuleSource restores llm_check prompt metadata", () => {
  const restored = parsePublishedRuleSource([
    "RULE: review_external_http",
    "TRACE: A -> ... -> C",
    "ON: tool_call(http.post)",
    'CONDITION: C.name == "http.post"',
    "POLICY: LLM_CHECK",
    'Prompt: "Escalate ambiguous outbound HTTP requests."',
    "Severity: high",
  ].join("\n"));

  assert.ok(restored);
  assert.equal(restored.prompt, "Escalate ambiguous outbound HTTP requests.");
});

test("parsePublishedRuleSource preserves subtype plus tool_pattern ON clauses", () => {
  const restored = parsePublishedRuleSource([
    "RULE: review_failed_http",
    "TRACE: A -> ... -> C",
    "ON: tool_call.failed(http.post)",
    'CONDITION: C.name == "http.post"',
    "POLICY: HUMAN_CHECK",
  ].join("\n"));

  assert.ok(restored);
  assert.equal(restored.onClause, "tool_call.failed(http.post)");
});

test("parsePublishedRuleSource restores ON-only published rules into incomplete compatibility state", () => {
  const restored = parsePublishedRuleSource([
    "RULE: deny_shell_call",
    "ON: tool_call(shell.exec)",
    'CONDITION: tool.cmd == "rm -rf /"',
    "POLICY: DENY",
  ].join("\n"));

  assert.ok(restored);
  assert.equal(restored.path, "");
  assert.equal(restored.onClause, "tool_call(shell.exec)");
  assert.equal(restored.conditionItems[0].sourceType, "context");
});

test("parsePublishedRuleSource returns null for unsupported runtime DSL", () => {
  const restored = parsePublishedRuleSource([
    "RULE builtin_rule",
    "CONDITION: principal.role == \"basic\"",
    "POLICY: DENY",
  ].join("\n"));

  assert.equal(restored, null);
});

test("publishedRulesSourceWith preserves existing published rules and appends the edited rule", () => {
  const merged = publishedRulesSourceWith(
    {
      name: "draft_two",
      path: "A->C",
      action: "ALLOW",
      conditionItems: [
        {
          connector: "",
          sourceType: "trace",
          openParen: "",
          closeParen: "",
          symbol: "A",
          feature: "name",
          syntaxField: "",
          operator: "==",
          value: "http.post",
        },
      ],
    },
    [
      {
        rule_id: "published_one",
        source: [
          "RULE: published_one",
          "TRACE: A -> B",
          'CONDITION: A.name == "email.send"',
          "POLICY: DENY",
        ].join("\n"),
      },
    ],
  );

  assert.match(merged, /RULE: published_one/);
  assert.match(merged, /RULE: draft_two/);
});

test("buildPublishedSourceWithout removes the targeted published rule", () => {
  const merged = buildPublishedSourceWithout(
    { rule_id: "published_one" },
    [
      {
        rule_id: "published_one",
        source: [
          "RULE: published_one",
          "TRACE: A -> B",
          'CONDITION: A.name == "email.send"',
          "POLICY: DENY",
        ].join("\n"),
      },
      {
        rule_id: "published_two",
        source: [
          "RULE: published_two",
          "TRACE: A -> C",
          'CONDITION: A.name == "http.post"',
          "POLICY: ALLOW",
        ].join("\n"),
      },
    ],
  );

  assert.doesNotMatch(merged, /RULE: published_one/);
  assert.match(merged, /RULE: published_two/);
});

test("extractPublishedRuleSource returns only the requested rule block from a backend full-ruleset source", () => {
  const fullSource = [
    "RULE deny_destructive_shell",
    "ON tool_call(shell.exec)",
    'IF args.cmd == "rm -rf /"',
    "THEN DENY",
    "",
    "RULE review_external_email",
    "ON tool_call(email.send)",
    "IF principal.trust_level < 2",
    "THEN HUMAN_CHECK",
  ].join("\n");

  const extracted = extractPublishedRuleSource(fullSource, "review_external_email");

  assert.match(extracted, /^RULE review_external_email$/m);
  assert.doesNotMatch(extracted, /^RULE deny_destructive_shell$/m);
});

test("extractPublishedRuleSource supports hyphenated rule ids in colon syntax blocks", () => {
  const fullSource = [
    "RULE: deny-destructive-shell",
    "TRACE: A -> B",
    'CONDITION: A.name == "shell.exec"',
    "POLICY: DENY",
    "",
    "RULE: review-external-email",
    "TRACE: A -> C",
    'CONDITION: A.name == "email.send"',
    "POLICY: HUMAN_CHECK",
  ].join("\n");

  const extracted = extractPublishedRuleSource(fullSource, "review-external-email");

  assert.match(extracted, /^RULE: review-external-email$/m);
  assert.doesNotMatch(extracted, /^RULE: deny-destructive-shell$/m);
});

test("filterRuleItems returns only the requested rule bucket", () => {
  const items = [
    { status: RULE_STATUS_PUBLISHED, rule: { name: "live_rule" } },
    { status: RULE_STATUS_UNPUBLISHED, rule: { name: "draft_rule" } },
  ];

  assert.equal(filterRuleItems(items, "all").length, 2);
  assert.deepEqual(filterRuleItems(items, RULE_STATUS_PUBLISHED).map((item) => item.rule.name), ["live_rule"]);
  assert.deepEqual(filterRuleItems(items, RULE_STATUS_UNPUBLISHED).map((item) => item.rule.name), ["draft_rule"]);
});

test("normalizeActiveRule maps runtime rules into the same id/name shape", () => {
  const normalized = normalizeActiveRule({
    rule_id: "published_one",
    action: "deny",
    severity: "critical",
    category: "command_safety",
    user_managed: false,
    source: 'RULE: published_one\nTRACE: A -> B\nON: tool_call(shell.exec)\nCONDITION: tool.cmd == "rm -rf /"\nPOLICY: DENY\nReason: "Shell call blocked"',
  });

  assert.equal(normalized.id, "published_one");
  assert.equal(normalized.name, "published_one");
  assert.equal(normalized.status, RULE_STATUS_PUBLISHED);
  assert.equal(normalized.rule_id, "published_one");
  assert.equal(normalized.path, "A -> B");
  assert.equal(normalized.onClause, "tool_call(shell.exec)");
  assert.equal(normalized.severity, "critical");
  assert.equal(normalized.category, "command_safety");
  assert.equal(normalized.reason, "Shell call blocked");
  assert.equal(normalized.userManaged, false);
  assert.equal(ruleKey(normalized), "published_one");
  assert.equal(ruleDisplayName(normalized), "published_one");
});

test("normalizeActiveRule restores prompt from published source", () => {
  const normalized = normalizeActiveRule({
    rule_id: "review_external_http",
    action: "llm_check",
    source: [
      "RULE: review_external_http",
      "TRACE: A -> B",
      'CONDITION: B.name == "http.post"',
      "POLICY: LLM_CHECK",
      'Prompt: "Escalate ambiguous outbound HTTP requests."',
    ].join("\n"),
  });

  assert.equal(normalized.prompt, "Escalate ambiguous outbound HTTP requests.");
});

test("buildRuleListSource restores tool syntax conditions from ON tool mapping", () => {
  const preview = buildRuleListSource({
    rule_id: "demo_complete_deny_destructive_shell",
    action: "DENY",
    source: [
      "RULE: demo_complete_deny_destructive_shell",
      "ON: tool_call(shell.exec)",
      "TRACE: A -> * -> B",
      'CONDITION: tool.cmd == "rm -rf /"',
      "POLICY: DENY",
      "Severity: critical",
      "Category: command_safety",
      'Reason: "Destructive shell command blocked"',
    ].join("\n"),
  }, RULE_STATUS_PUBLISHED);

  assert.match(preview, /^ON: tool_call\(shell\.exec\)$/m);
  assert.match(preview, /^CONDITION: tool\.cmd == "rm -rf \/"$/m);
});

test("buildRuleListSource prefers restored rule metadata over pack-level metadata noise", () => {
  const preview = buildRuleListSource({
    rule_id: "demo_complete_review_external_http",
    action: "HUMAN_CHECK",
    onClause: "tool_call(shell.exec)",
    reason: "Destructive shell command blocked",
    source: [
      "RULE: demo_complete_review_external_http",
      "ON: tool_call(http.post)",
      "TRACE: A -> * -> B",
      'CONDITION: target.domain != "internal.corp"',
      "POLICY: HUMAN_CHECK",
      "Severity: high",
      "Category: egress_review",
      'Reason: "External webhook requires review"',
    ].join("\n"),
  }, RULE_STATUS_PUBLISHED);

  assert.match(preview, /^ON: tool_call\(http\.post\)$/m);
  assert.match(preview, /^Reason: "External webhook requires review"$/m);
});

test("buildRuleListSource includes prompt for llm_check rules", () => {
  const preview = buildRuleListSource({
    rule_id: "review_external_http",
    action: "LLM_CHECK",
    source: [
      "RULE: review_external_http",
      "ON: tool_call(http.post)",
      "TRACE: A -> * -> B",
      'CONDITION: B.name == "http.post"',
      "POLICY: LLM_CHECK",
      'Prompt: "Escalate ambiguous outbound HTTP requests."',
      "Severity: high",
    ].join("\n"),
  }, RULE_STATUS_PUBLISHED);

  assert.match(preview, /^Prompt: "Escalate ambiguous outbound HTTP requests\."$/m);
});

test("normalizeStoredLocalRule preserves prompt in unpublished drafts", () => {
  const normalized = normalizeStoredLocalRule({
    name: "draft_rule",
    path: "A->B",
    action: "LLM_CHECK",
    prompt: "Draft review prompt",
    conditionItems: [
      {
        connector: "",
        sourceType: "trace",
        openParen: "",
        closeParen: "",
        symbol: "A",
        feature: "name",
        syntaxField: "",
        operator: "==",
        value: "email.send",
      },
    ],
  });

  assert.equal(normalized.prompt, "Draft review prompt");
});

test("normalizeSeverityValue keeps supported severities and clears unknown values", () => {
  assert.equal(normalizeSeverityValue("high"), "high");
  assert.equal(normalizeSeverityValue(" INFO "), "info");
  assert.equal(normalizeSeverityValue("urgent"), "");
  assert.equal(normalizeSeverityValue(""), "");
});

test("normalizeStoredLocalRule migrates older local rules into explicit unpublished state", () => {
  const normalized = normalizeStoredLocalRule({
    name: "draft_rule",
    path: "A->B",
    action: "DENY",
    conditionItems: [
      {
        connector: "",
        sourceType: "trace",
        openParen: "",
        closeParen: "",
        symbol: "A",
        feature: "name",
        syntaxField: "",
        operator: "==",
        value: "email.send",
      },
    ],
  });

  assert.equal(normalized.id, "draft_rule");
  assert.equal(normalized.name, "draft_rule");
  assert.equal(normalized.path, "A->B");
  assert.equal(normalized.status, RULE_STATUS_UNPUBLISHED);
});

test("normalizeStoredLocalRule preserves old ON-only drafts as incomplete compatibility rules", () => {
  const normalized = normalizeStoredLocalRule({
    name: "shell_rule",
    onClause: "tool_call(shell.exec)",
    action: "DENY",
    conditionItems: [
      {
        connector: "",
        sourceType: "context",
        openParen: "",
        closeParen: "",
        contextPrefix: "tool",
        contextField: "tool.syntax",
        contextFieldName: "",
        contextPath: "tool.cmd",
        syntaxField: "cmd",
        operator: "==",
        value: "rm -rf /",
      },
    ],
  });

  assert.equal(normalized.onClause, "tool_call(shell.exec)");
  assert.equal(normalized.path, "");
});

test("buildRuleListSource renders published legacy DSL with normalized v3 preview text", () => {
  const preview = buildRuleListSource({
    rule_id: "published_one",
    action: "HUMAN_CHECK",
    source: [
      "RULE published_one",
      "TRACE: A -> ... -> C",
      'CONDITION: A.label.sensitivity == "high"',
      "POLICY: HUMAN_CHECK",
    ].join("\n"),
  }, RULE_STATUS_PUBLISHED);

  assert.match(preview, /^RULE: published_one$/m);
  assert.match(preview, /^TRACE: A -> \.\.\. -> C$/m);
});

test("buildRuleListSource keeps ON before TRACE in preview output", () => {
  const preview = buildRuleListSource({
    rule_id: "published_with_on",
    action: "HUMAN_CHECK",
    source: [
      "RULE: published_with_on",
      "ON: tool_call(http.post)",
      "TRACE: A -> ... -> C",
      'CONDITION: A.name == "secret.read"',
      "POLICY: HUMAN_CHECK",
    ].join("\n"),
  }, RULE_STATUS_PUBLISHED);

  assert.match(
    preview,
    /^RULE: published_with_on\nON: tool_call\(http\.post\)\nTRACE: A -> \.\.\. -> C/m,
  );
});

test("normalizeEntryModeValue keeps legacy compatibility inference only", () => {
  assert.equal(normalizeEntryModeValue({ path: "A->B", onClause: "tool_call(shell.exec)" }), "trace");
  assert.equal(normalizeEntryModeValue({ onClause: "tool_call(shell.exec)" }), "on");
});

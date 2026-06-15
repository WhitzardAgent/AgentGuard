const test = require("node:test");
const assert = require("node:assert/strict");

global.window = {};
require("../static/pages/rules/rule-dsl.js");

const { isValidOnClause, normalizeOnClause, serializeRule, serializeRules } = global.window.AgentGuardRuleDSL;

test("serializeRule builds a trace-only deny rule", () => {
  const dsl = serializeRule({
    name: "deny_external_email",
    path: "A->B->C",
    action: "DENY",
    conditionItems: [
      {
        connector: "",
        openParen: "",
        closeParen: "",
        sourceType: "trace",
        symbol: "A",
        feature: "name",
        syntaxField: "",
        operator: "==",
        value: "email.send",
      },
    ],
  });

  assert.equal(
    dsl,
    [
      "RULE: deny_external_email",
      "TRACE: A -> B -> C",
      'CONDITION: A.name == "email.send"',
      "POLICY: DENY",
    ].join("\n"),
  );
});

test("serializeRule supports TRACE plus optional ON and mixed condition references", () => {
  const dsl = serializeRule({
    name: "review_secret_egress",
    path: "A->...->C",
    onClause: "tool_call(http.post)",
    action: "HUMAN_CHECK",
    conditionItems: [
      {
        connector: "",
        openParen: "",
        closeParen: "",
        sourceType: "trace",
        symbol: "A",
        feature: "name",
        syntaxField: "",
        operator: "==",
        value: "secret.read",
      },
      {
        connector: "AND",
        openParen: "",
        closeParen: "",
        sourceType: "context",
        contextPrefix: "tool",
        contextField: "tool.boundary",
        contextFieldName: "",
        contextPath: "tool.boundary",
        operator: "==",
        value: "external",
      },
    ],
  });

  assert.match(
    dsl,
    /^RULE: review_secret_egress\nON: tool_call\(http\.post\)\nTRACE: A -> \.\.\. -> C\nCONDITION: A\.name == "secret\.read"\n  AND tool\.boundary == "external"\nPOLICY: HUMAN_CHECK$/m,
  );
});

test("serializeRules keeps multi-condition grouping and supports all runtime actions", () => {
  const dsl = serializeRules([
    {
      name: "review_high_sensitivity",
      path: "A->...->C",
      onClause: "tool_call(email.send)",
      action: "HUMAN_CHECK",
      conditionItems: [
        {
          connector: "",
          openParen: "(",
          closeParen: "",
          sourceType: "trace",
          symbol: "A",
          feature: "label.sensitivity",
          syntaxField: "",
          operator: "==",
          value: "high",
        },
        {
          connector: "OR",
          openParen: "",
          closeParen: ")",
          sourceType: "context",
          contextPrefix: "principal",
          contextField: "principal.trust_level",
          contextFieldName: "",
          contextPath: "principal.trust_level",
          operator: "<",
          value: "2",
        },
      ],
    },
    {
      name: "degrade_large_export",
      path: "A->*->C",
      action: "DEGRADE",
      degradeTarget: "safe_csv_export",
      conditionItems: [
        {
          connector: "",
          openParen: "",
          closeParen: "",
          sourceType: "context",
          contextPrefix: "tool",
          contextField: "tool.syntax",
          contextFieldName: "",
          contextPath: "tool.row_count",
          syntaxField: "row_count",
          operator: ">",
          value: "1000",
        },
      ],
    },
  ]);

  assert.match(dsl, /RULE: review_high_sensitivity/);
  assert.match(dsl, /RULE: review_high_sensitivity\nON: tool_call\(email\.send\)\nTRACE: A -> \.\.\. -> C/);
  assert.match(dsl, /CONDITION: \(A\.sensitivity == "high"\n  OR principal\.trust_level < 2\)/);
  assert.match(dsl, /POLICY: DEGRADE TO "safe_csv_export"/);
  assert.match(dsl, /CONDITION: tool\.row_count > 1000/);
});

test("serializeRule appends severity category and reason when provided", () => {
  const dsl = serializeRule({
    name: "review_external_email",
    path: "A->...->C",
    action: "HUMAN_CHECK",
    severity: "high",
    category: "egress_review",
    reason: "External email needs approval",
    conditionItems: [
      {
        connector: "",
        openParen: "",
        closeParen: "",
        sourceType: "trace",
        symbol: "C",
        feature: "name",
        syntaxField: "",
        operator: "==",
        value: "email.send",
      },
    ],
  });

  assert.match(dsl, /Severity: high/);
  assert.match(dsl, /Category: egress_review/);
  assert.match(dsl, /Reason: "External email needs approval"/);
});

test("serializeRule preserves IN and MATCHES operators with expected right-hand formatting", () => {
  const dsl = serializeRule({
    name: "review_allowlist_and_regex",
    path: "A->B",
    action: "HUMAN_CHECK",
    conditionItems: [
      {
        connector: "",
        openParen: "",
        closeParen: "",
        sourceType: "context",
        contextPrefix: "tool",
        contextField: "tool.syntax",
        contextFieldName: "",
        contextPath: "tool.domain",
        syntaxField: "domain",
        operator: "IN",
        value: "allowlist.http",
      },
      {
        connector: "AND",
        openParen: "",
        closeParen: "",
        sourceType: "context",
        contextPrefix: "tool",
        contextField: "tool.syntax",
        contextFieldName: "",
        contextPath: "tool.url",
        syntaxField: "url",
        operator: "MATCHES",
        value: ".*127\\\\.0\\\\.0\\\\.1.*",
      },
    ],
  });

  assert.match(dsl, /CONDITION: tool\.domain IN allowlist\.http/);
  assert.match(dsl, /AND tool\.url MATCHES ".*127\\\\\\\\\.0\\\\\\\\\.0\\\\\\\\\.1\.\*"/);
});

test("serializeRule appends prompt only for llm_check rules", () => {
  const dsl = serializeRule({
    name: "review_external_http",
    path: "A->...->C",
    action: "LLM_CHECK",
    prompt: "Escalate ambiguous outbound HTTP requests.",
    conditionItems: [
      {
        connector: "",
        openParen: "",
        closeParen: "",
        sourceType: "trace",
        symbol: "C",
        feature: "name",
        syntaxField: "",
        operator: "==",
        value: "http.post",
      },
    ],
  });

  assert.match(dsl, /POLICY: LLM_CHECK\nPrompt: "Escalate ambiguous outbound HTTP requests\."$/m);
});

test("serializeRule ignores prompt for non-llm_check rules", () => {
  const dsl = serializeRule({
    name: "deny_external_http",
    path: "A->...->C",
    action: "DENY",
    prompt: "Should not be serialized",
    conditionItems: [
      {
        connector: "",
        openParen: "",
        closeParen: "",
        sourceType: "trace",
        symbol: "C",
        feature: "name",
        syntaxField: "",
        operator: "==",
        value: "http.post",
      },
    ],
  });

  assert.doesNotMatch(dsl, /^Prompt:/m);
});

test("serializeRule escapes prompt content", () => {
  const dsl = serializeRule({
    name: "review_prompt_escape",
    path: "A->...->C",
    action: "LLM_CHECK",
    prompt: 'Review "dangerous" path C:\\temp',
    conditionItems: [
      {
        connector: "",
        openParen: "",
        closeParen: "",
        sourceType: "trace",
        symbol: "C",
        feature: "name",
        syntaxField: "",
        operator: "==",
        value: "shell.exec",
      },
    ],
  });

  assert.match(dsl, /^Prompt: "Review \\"dangerous\\" path C:\\\\temp"$/m);
});

test("serializeRule preserves info severity when selected from the UI enum", () => {
  const dsl = serializeRule({
    name: "audit_low_risk_flow",
    path: "A->...->C",
    action: "ALLOW",
    severity: "info",
    conditionItems: [
      {
        connector: "",
        openParen: "",
        closeParen: "",
        sourceType: "trace",
        symbol: "A",
        feature: "name",
        syntaxField: "",
        operator: "==",
        value: "kb.lookup",
      },
    ],
  });

  assert.match(dsl, /Severity: info/);
});

test("serializeRule rejects ON-only rules because TRACE is now required", () => {
  assert.throws(
    () => serializeRule({
      name: "deny_shell_call",
      onClause: "tool_call(shell.exec)",
      action: "DENY",
      conditionItems: [
        {
          connector: "",
          openParen: "",
          closeParen: "",
          sourceType: "context",
          contextPrefix: "tool",
          contextField: "tool.syntax",
          contextFieldName: "",
          contextPath: "tool.cmd",
          syntaxField: "cmd",
          operator: "==",
          value: "rm -rf /",
        },
      ],
    }),
    /TRACE \/ PATH is required|PATH is required/,
  );
});

test("serializeRule preserves single-step trace paths for backend v3 compatibility", () => {
  const dsl = serializeRule({
    name: "single_step_rule",
    path: "A",
    action: "ALLOW",
    conditionItems: [
      {
        connector: "",
        openParen: "",
        closeParen: "",
        sourceType: "trace",
        symbol: "A",
        feature: "name",
        syntaxField: "",
        operator: "==",
        value: "http.post",
      },
    ],
  });

  assert.match(dsl, /^TRACE: A$/m);
});

test("isValidOnClause accepts supported tool_call forms", () => {
  assert.equal(isValidOnClause("tool_call(shell.exec)"), true);
  assert.equal(isValidOnClause("tool_call.requested"), true);
  assert.equal(isValidOnClause("tool_call.attempt"), true);
  assert.equal(isValidOnClause("tool_call.failed(http.post)"), true);
  assert.equal(isValidOnClause("shell.exec"), false);
});

test("normalizeOnClause builds ON from optional subtype and tool_pattern selections", () => {
  assert.equal(normalizeOnClause({ onSubtype: "requested", onToolPattern: "http.post" }), "tool_call.requested(http.post)");
  assert.equal(normalizeOnClause({ onSubtype: "failed" }), "tool_call.failed");
  assert.equal(normalizeOnClause({ onToolPattern: "shell.exec" }), "tool_call(shell.exec)");
  assert.equal(normalizeOnClause({}), "");
});

test("serializeRule rejects unsupported actions", () => {
  assert.throws(
    () => serializeRule({
      name: "bad_action",
      path: "A->B",
      action: "BLOCK",
      conditionItems: [
        {
          connector: "",
          openParen: "",
          closeParen: "",
          sourceType: "trace",
          symbol: "A",
          feature: "name",
          syntaxField: "",
          operator: "==",
          value: "email.send",
        },
      ],
    }),
    /not supported/,
  );
});

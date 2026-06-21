# Policy DSL Structure

This page is for advanced users who need to manually write policies for the built-in `rule_based_plugin` server plugin. `rule_based_plugin` consumes AgentGuard's access-control DSL, evaluates the current runtime event plus recent session context, and uses configured rules to identify and intercept security risks in tool calls.

Enable the plugin in `config/plugins.json` before relying on these rules at runtime:

```json
{
  "phases": {
    "llm_before": {"client": [], "server": []},
    "llm_after": {"client": [], "server": []},
    "tool_before": {
      "client": [],
      "server": [{"name": "rule_based_plugin", "env": {}}]
    },
    "tool_after": {"client": [], "server": []}
  }
}
```

This page covers the DSL syntax structure, common fields, condition expressions, call-chain rules, and action semantics.

AgentGuard policy files typically use the `.rules` suffix. A single file can contain multiple rules, each describing what conditions should cause a tool call to be allowed, denied, or sent for review.

## Syntax overview

Here is the full syntax structure of the policy DSL:

```text
RULE: <rule_id>
[ON: <event_match>]
[TRACE: <placeholder> <trace_sep> <placeholder> ...]
[CONDITION: <expr>]
POLICY: <action>
[Severity: critical | high | medium | low | info]
[Category: <identifier-or-string>]
[Reason: <string>]
[Priority: <number>]
[ttl_ms: <number>]

# Event matching
<event_match> :=
    tool_call(<tool_pattern>)
  | tool_call.<subtype>
  | tool_call.<subtype>(<tool_pattern>)

<subtype> := requested | completed | failed
<tool_pattern> := * | tool_name | namespace.tool | namespace.*

# Condition expressions
<expr> :=
    <expr> OR <expr>
  | <expr> AND <expr>
  | NOT <expr>
  | (<expr>)
  | <path> <op> <value>
  | <function_call>

<op> := == | != | < | <= | > | >= | IN | NOT IN | MATCHES | CONTAINS

# Actions
<action> :=
    DENY
  | ALLOW
  | HUMAN_CHECK
  | LLM_CHECK

# TRACE separators
<trace_sep> :=
    ->
  | -> * ->
  | -> ... ->
  | -> ...? ->
```


## A minimal rule

This rule denies `shell.exec` calls from low-trust agents:

```text
RULE: deny_low_trust_shell
ON: tool_call(shell.exec)
CONDITION: principal.trust_level < 2
POLICY: DENY
Severity: high
Category: capability
Reason: "Low-trust agent cannot execute shell commands"
```

A rule has four core parts:

* `RULE` defines the rule ID.
* `ON` limits which tool calls the rule applies to.
* `CONDITION` defines when the rule matches.
* `POLICY` defines the action to take when matched.

`Severity`, `Category`, and `Reason` are metadata. They don't affect condition evaluation, but they appear in audit and alert records. We recommend filling them in for production rules.

## Rule ID

The value after `RULE` is the rule ID, used for auditing, debugging, and policy management. Choose stable, readable, unique names.

```text
RULE: deny_external_email_low_trust
```

Naming guidelines:

* Use lowercase letters, digits, underscores, or hyphens.
* Make the name reflect the action and scenario — e.g. `deny_...`, `review_...`.
* Don't reuse the same ID across rules in the same batch; duplicate IDs overwrite each other when loaded into the registry.

## ON: scoping rules to tool calls

`ON` pre-filters candidate rules by event type and tool name. The more precise the `ON` clause, the easier the rule is to understand and the less likely it is to cause false positives.

`ON` can be omitted. Omitting it means the rule isn't pre-filtered by tool name — this is generally only recommended for `TRACE` rules, since the last placeholder in a TRACE further constrains the current call. For single-step rules, always write an explicit `ON`.

### Matching by tool name

```text
RULE: deny_destructive_shell
ON: tool_call(shell.exec)
CONDITION: tool.cmd MATCHES ".*\\b(rm\\s+-rf|mkfs|dd\\s+if=)\\b.*"
POLICY: DENY
Severity: critical
Category: shell
```

`tool_call(shell.exec)` only matches the tool named `shell.exec`.

### Wildcards

```text
RULE: review_all_shell_tools
ON: tool_call(shell.*)
CONDITION: principal.trust_level < 3
POLICY: HUMAN_CHECK
Severity: high
Category: shell
```

`shell.*` matches `shell.exec`, `shell.run`, and similar names. `tool_call(*)` matches all tool calls.

### Matching by tool-call phase

```text
RULE: deny_external_http_before_execution
ON: tool_call.requested(http.post)
CONDITION: tool.domain NOT IN allowlist.http
POLICY: DENY
Severity: high
Category: egress
```

Common phases:

| Phase | Meaning |
| --- | --- |
| `requested` | Pre-execution request phase — most commonly used for access control |
| `completed` | Post-execution result phase, where `tool.result` is available |
| `failed` | Failed call phase |

If you want to intercept before execution, prefer `ON: tool_call.requested(...)` or simply `ON: tool_call(...)`.

## CONDITION: when a rule matches

`CONDITION` determines whether a rule fires. Conditions are built from paths, literals, comparison operators, functions, and boolean logic.

`CONDITION` can be omitted. If `TRACE` is present, the rule matches on call-chain pattern matching. If neither `TRACE` nor `CONDITION` is present, the rule fires whenever `ON` matches. Unless you truly want an unconditional rule, always write an explicit `CONDITION`.

```text
RULE: deny_external_email_without_scope
ON: tool_call(email.send)
CONDITION: tool.recipient_domain NOT IN allowlist.email
           AND caller.scope_missing("external_email")
POLICY: DENY
Severity: high
Category: egress
Reason: "External email requires external_email scope"
```

### Boolean logic precedence

Operator precedence (high to low):

| Precedence | Syntax |
| --- | --- |
| 1 | `(...)` |
| 2 | `NOT` |
| 3 | `AND` |
| 4 | `OR` |

When mixing `AND` and `OR`, use explicit parentheses:

```text
CONDITION: tool.boundary == "external"
           AND (caller.scope_missing("sensitive_export") OR goal_drift_detected())
```

### Literals

The DSL supports these literal types:

| Type | Examples |
| --- | --- |
| String | `"evil.com"`, `'internal'` |
| Number | `0`, `3`, `0.8` |
| Boolean | `true`, `false`, `TRUE`, `FALSE` |
| String set | `{"email.send", "http.post"}` |

String sets are commonly used with `IN` / `NOT IN`:

```text
CONDITION: tool.name IN {"email.send", "http.post", "slack.post"}
```

### Comparison and set operators

| Operator | Meaning | Example |
| --- | --- | --- |
| `==` / `!=` | Equal / not equal | `principal.role == "basic"` |
| `<` / `<=` / `>` / `>=` | Numeric or comparable comparison | `principal.trust_level < 2` |
| `IN` | Left value belongs to right set, list, dict keys, or identical string | `tool.recipient_domain IN allowlist.email` |
| `NOT IN` | Negation of `IN` | `tool.domain NOT IN allowlist.http` |
| `MATCHES` | Regex match | `tool.url MATCHES ".*127\\.0\\.0\\.1.*"` |
| `CONTAINS` | String contains, list membership, or dict key membership | `tool.body CONTAINS "password"` |

`MATCHES` uses Python `re` semantics on the right-hand regex string. Common backslash escapes need double escaping, e.g. `\\b`, `\\d`.

## Common paths

Paths read values from the current event, caller, tool-call arguments, and runtime context.

### Caller identity

`principal` represents the identity of the agent making the current tool call. `caller` is an alias for `principal`.

```text
CONDITION: principal.role == "basic"
CONDITION: caller.trust_level < 3
```

Common fields:

| Path | Meaning |
| --- | --- |
| `principal.agent_id` | Agent ID |
| `principal.session_id` | Session ID |
| `principal.role` | Role — common values: `basic`, `default`, `privileged`, `system` |
| `principal.trust_level` | Trust level — higher values typically grant more permissions |

### Tool info and arguments

`tool` is a convenience alias for `tool_call`.

```text
CONDITION: tool.name == "send_email_to"
CONDITION: tool.recipient MATCHES ".*@evil\\.com"
```

Common fields:

| Path | Meaning |
| --- | --- |
| `tool.name` | Current tool name |
| `tool.<param>` | Current tool parameter — e.g. `tool.domain`, `tool.sql`, `tool.recipient_domain` |
| `tool.result` | Tool execution result, mainly for result-phase events |

For tool function parameters, we recommend `tool.<param>` because it reads more naturally in policies:

```text
RULE: deny_sql_ddl
ON: tool_call(db.query)
CONDITION: tool.sql MATCHES "(?i).*\\b(DROP|TRUNCATE|ALTER|GRANT|REVOKE)\\b.*"
POLICY: DENY
Severity: critical
Category: database
```

### Tool static labels

When registering a tool, you can declare static labels. Policies can read them via `tool.boundary`, `tool.sensitivity`, `tool.integrity`, and `tool.tags`.

```python
@guard.tool(
    "send_email_to",
    sink_type="email",
    boundary="external",
    sensitivity="moderate",
    integrity="trusted",
    tags=["egress", "email"],
)
def send_email_to(recipient: str, subject: str, body: str) -> str:
    ...
```

Matching policy:

```text
RULE: deny_high_sensitivity_external_tool
ON: tool_call.requested
CONDITION: tool.boundary == "external" AND tool.sensitivity == "high"
POLICY: DENY
Severity: critical
Category: data_exfiltration
```

Label field values:

| Field | Values |
| --- | --- |
| `tool.boundary` | `internal`, `external`, `privileged` |
| `tool.sensitivity` | `low`, `moderate`, `high` |
| `tool.integrity` | `trusted`, `unfiltered` |
| `tool.tags` | String list |

### Event fields

```text
CONDITION: event.session_id == "session-001"
CONDITION: event.type == "tool_call_requested"
```

Common event aliases:

| Path | Meaning |
| --- | --- |
| `event.type` | Event type |
| `event.id` | Event ID |
| `event.timestamp` | Event timestamp |
| `event.session_id` | Current session ID |

### Allowlists

Allowlists are injected at runtime. Policies can access them via `allowlist.<name>` or `whitelist("<name>")`.

## POLICY: actions on match

`POLICY` defines what happens when a rule fires.

| Action | Meaning |
| --- | --- |
| `DENY` | Block the tool call; the original tool is not executed |
| `ALLOW` | Allow the tool call |
| `HUMAN_CHECK` | Send to human approval workflow |
| `LLM_CHECK` | Send to a configured LLM reviewer; falls back to human approval if LLM is not configured or fails |

If no rule matches, the runtime defaults to allowing the tool call. Therefore, to express "block unknown targets" or "block anything not on the allowlist," write `DENY` / `HUMAN_CHECK` conditional rules rather than only a positive `ALLOW` rule.

## Built-in functions

Functions can appear in conditions. Boolean-returning functions can be used directly as conditions; value-returning functions are typically combined with comparison operators.

### Allowlist and set functions

| Function | Returns | Description |
| --- | --- | --- |
| `whitelist("name")` | Set | Read a runtime-injected allowlist |
| `subset(values, container)` | Boolean | All elements in `values` are in `container` |
| `any_in(values, container)` | Boolean | Any element in `values` is in `container` |

Example: deny if any recipient is not in the user's address book.

```text
RULE: deny_unknown_recipients
ON: tool_call(send_email_to)
CONDITION: NOT subset(tool.recipients, whitelist("user_address_book"))
POLICY: DENY
Severity: high
Category: email
```

Example: deny if any recipient hits the blocked list.

```text
RULE: deny_blocked_recipient
ON: tool_call(send_email_to)
CONDITION: any_in(tool.recipients, whitelist("blocked_emails"))
POLICY: DENY
Severity: high
Category: email
```

### String, URL, and email functions

| Function | Description |
| --- | --- |
| `starts_with(text, prefix)` | Check string prefix |
| `ends_with(text, suffix)` | Check string suffix |
| `contains(container, value)` | Function-form containment check |
| `url.domain(url)` | Extract URL hostname |
| `url.is_external(url)` | Check whether a URL is outside the internal domain allowlist |
| `email.domain(address)` | Extract email domain |

Example: block access to local or private network addresses.

```text
RULE: deny_http_ssrf
ON: tool_call(http.get)
CONDITION: tool.url MATCHES ".*(?:localhost|127\\.0\\.0\\.1|10\\.\\d+|172\\.1[6-9]\\.|192\\.168\\.).*"
POLICY: DENY
Severity: critical
Category: ssrf
```

Example: use URL parsing to check domain.

```text
RULE: review_external_url
ON: tool_call(browser.open)
CONDITION: url.is_external(tool.url)
POLICY: HUMAN_CHECK
Severity: medium
Category: network
```

### Labels, scopes, and tag functions

| Function | Description |
| --- | --- |
| `input.has_label("pattern")` | Whether current session input or upstream data carries a label |
| `input.has_any_label({"a/*", "b/*"})` | Whether current session input or upstream data matches any label pattern |
| `caller.scope_missing("scope")` | Whether the caller lacks a specific scope |
| `tool.has_tag("tag")` | Whether the current tool has a specific static tag |

Example: deny external calls when sensitive labels appear and the caller lacks export authorization.

```text
RULE: deny_sensitive_label_external_without_scope
ON: tool_call.requested
CONDITION: tool.boundary == "external"
           AND input.has_any_label({"pii/*", "finance/*", "secret/*"})
           AND caller.scope_missing("sensitive_export")
POLICY: DENY
Severity: critical
Category: data_exfiltration
```

### Session history and behavioral functions

| Function | Returns | Description |
| --- | --- | --- |
| `upstream_contains_tool("tool")` | Boolean | Whether a specific tool was called upstream in the current session |
| `upstream_contains_any_tool({"a", "b"})` | Boolean | Whether any tool in the set was called upstream |
| `derived_from_tool("tool")` | Boolean | Currently evaluates by checking upstream tools |
| `tool_sequence_matches({"a", "b"})` | Boolean | Whether the session's tool sequence contains these tools in order |
| `repeated_attempts(tool="name", window="5m")` | Number | How many times a tool was attempted in the current session within the window |
| `distinct_targets()` | Number | Count of distinct recent targets |
| `path_length(source="tool")` | Number | Approximate path length from an upstream tool to the current call |

Example: LLM review for email after a database query.

```text
RULE: review_email_after_db_query
ON: tool_call(email.send)
CONDITION: upstream_contains_any_tool({"db.query", "database_query"})
POLICY: LLM_CHECK
Severity: high
Category: data_exfiltration
Reason: "Email send follows a database query"
```

Example: human review for HTTP burst behavior.

```text
RULE: review_http_burst
ON: tool_call(http.post)
CONDITION: repeated_attempts(tool="http.post", window="5m") > 4
POLICY: HUMAN_CHECK
Severity: medium
Category: behavioural_anomaly
```

### Historical parameters and results

| Function | Returns | Description |
| --- | --- | --- |
| `history_arg("tool", "param")` | Any | Read the most recent call's parameter for a tool in the current session |
| `history_result("tool")` | Any | Read the most recent call's result for a tool in the current session |
| `history_args_match("tool", "param", value)` | Boolean | Whether a historical parameter equals a specified value |

Example: deny external email if document classification result is "restricted".

```text
RULE: deny_restricted_doc_external_email
ON: tool_call(email.send)
CONDITION: history_result("classify_doc") == "restricted"
           AND tool.recipient_domain NOT IN allowlist.email
POLICY: DENY
Severity: critical
Category: data_exfiltration
```

Note: `history_arg` and `history_result` read from calls already written to session history. If you need to read parameters from "the current call being evaluated" in a `TRACE` rule, prefer TRACE placeholders — e.g. `Mailer.recipient`.

### Semantic signal functions

These functions read runtime-injected security signals.

| Function | Description |
| --- | --- |
| `goal_drift_detected()` | Goal drift detected |
| `scope_expansion_detected()` | Permission or scope expansion detected |
| `suspicious_exfil_pattern()` | Suspicious exfiltration pattern detected |
| `high_entropy_payload_detected()` | High-entropy payload detected |
| `goal_changed_from_initial()` | Current goal has deviated from the initial goal |

Example: human review when goal drift is detected before an external call.

```text
RULE: review_goal_drift_external_call
ON: tool_call.requested
CONDITION: tool.boundary == "external" AND goal_drift_detected()
POLICY: HUMAN_CHECK
Severity: high
Category: goal_drift
```


## TRACE: declarative call-chain rules

`TRACE` describes patterns where "an upstream call eventually flows to a downstream call." It's more powerful than `upstream_contains_tool(...)` because it lets you name each position in the chain and read its tool name, parameters, labels, and results in the `CONDITION`.

```text
RULE: deny_secret_to_external_sink
TRACE: SecRead ->...?-> Sink
CONDITION: SecRead.name == "secret.read"
           AND Sink.name IN {"http.post", "email.send", "slack.post"}
POLICY: DENY
Severity: critical
Category: secret_exfiltration
Reason: "Tool chain reads secret and then contacts an external sink"
```

### TRACE separators

| Syntax | Meaning | Example |
| --- | --- | --- |
| `A -> B` | B immediately follows A; no other calls between them | `Fetcher -> Executor` |
| `A -> * -> B` | Exactly one call between A and B | `Reader -> * -> Writer` |
| `A -> ... -> B` | At least one call between A and B | `A -> ... -> B` |
| `A -> ...? -> B` | A appears before B with zero or more calls between them | `DbOp -> ...? -> Mailer` |

The most commonly used separator is `-> ...? ->`, meaning "an upstream call eventually reaches a downstream call."

### TRACE placeholder fields

In `TRACE: Src -> ...? -> Dst`, `Src` and `Dst` are placeholders that bind to specific tool calls in the session's call chain.

| Field | Meaning |
| --- | --- |
| `Placeholder.name` | Bound call's tool name |
| `Placeholder.integrity` | Bound call's `label.integrity` |
| `Placeholder.sensitivity` | Bound call's `label.sensitivity` |
| `Placeholder.boundary` | Bound call's `label.boundary` |
| `Placeholder.result` | Bound call's return value |
| `Placeholder.<param>` | Bound call's parameter — e.g. `Mailer.recipient` |

Example: LLM review when external input reaches shell execution.

```text
RULE: review_external_input_to_shell
TRACE: Src ->...?-> Shell
CONDITION: Src.boundary == "external"
           AND Shell.name IN {"shell.exec", "shell_exec"}
POLICY: LLM_CHECK
Severity: critical
Category: prompt_injection
Reason: "External input reached shell execution"
```

Example: deny when a database query is immediately followed by email sending.

```text
RULE: deny_adjacent_db_to_email
TRACE: DbOp -> Mailer
CONDITION: DbOp.name IN {"db.query", "database_query"}
           AND Mailer.name IN {"email.send", "send_email_to"}
POLICY: DENY
Severity: critical
Category: data_exfiltration
```

## Metadata

```text
Severity: critical
Category: data_exfiltration
Reason: "Sensitive data sent to non-allowlisted endpoint"
```

We recommend at least filling in:

| Field | Recommendation |
| --- | --- |
| `Severity` | Used for alert triage; use `critical`, `high`, `medium`, `low`, or `info` |
| `Category` | Used for classification — e.g. `egress`, `database`, `data_exfiltration` |
| `Reason` | One sentence explaining the security reason for the rule match |


## Policy writing tips

### Start with high-risk tools

Write rules first for outbound, command execution, file writes, database writes, and sensitive data read tools.

```text
RULE: deny_db_write_basic
ON: tool_call(db.exec)
CONDITION: tool.sql MATCHES "(?i).*\\b(INSERT|UPDATE|DELETE|DROP|ALTER)\\b.*"
           AND principal.role == "basic"
POLICY: DENY
Severity: critical
Category: database
```

### Write narrow rules first

Use `ON: tool_call(email.send)` rather than `ON: tool_call(*)`. Use `tool.name IN {...}` rather than vague regexes.

Narrow rules are easier to audit and easier to troubleshoot for false positives.

### Separate deny and approve

Use `DENY` for clearly dangerous behavior, and `HUMAN_CHECK` or `LLM_CHECK` for uncertain-but-high-risk behavior.

### Use TRACE for cross-step risks

Single-step rules can only see the current operation. For risks like "read sensitive data, then exfiltrate it," use `TRACE`, or session history functions.

```text
RULE: review_db_result_to_external_email
TRACE: DbOp ->...?-> Mailer
CONDITION: DbOp.name IN {"db.query", "database_query"}
           AND Mailer.name IN {"email.send", "send_email_to"}
           AND Mailer.boundary == "external"
POLICY: LLM_CHECK
Severity: high
Category: data_exfiltration
```

### Don't rely on rule order for business intent

A policy file can contain multiple rules. When several rules match simultaneously, the runtime merges the results and selects the appropriate action. For predictable behavior, write explicit mutually exclusive conditions rather than relying on rule ordering.

### Validate rules before deployment

After writing policies, run:

```bash
python -m agentguard check rules/my_policy.rules
```

You can also validate all `.rules` files in a directory:

```bash
python -m agentguard check rules/
```

This command parses, compiles, and runs semantic checks — it catches common issues like duplicate rule IDs, missing metadata, invalid TRACE separators, and incorrect label enum values.

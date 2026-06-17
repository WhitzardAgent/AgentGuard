# rule_based_check 策略 DSL 基本结构

本文面向需要手动编写内置 `rule_based_check` server plugin 策略的高级用户。`rule_based_check` 会消费 AgentGuard 的访问控制 DSL，结合当前运行时事件和近期 session 上下文进行规则评估，通过配置规则识别并拦截工具调用中的安全风险。

要让这些规则在运行时生效，需要先在 `config/plugins.json` 中启用该 plugin：

```json
{
  "phases": {
    "llm_before": {"local": [], "remote": []},
    "llm_after": {"local": [], "remote": []},
    "tool_before": {
      "local": [],
      "remote": [{"name": "rule_based_check", "env": {}}]
    },
    "tool_after": {"local": [], "remote": []}
  }
}
```

本文重点介绍策略 DSL 的语法结构、常用字段、条件表达式、调用链规则以及动作语义。

AgentGuard 的策略文件通常使用 `.rules` 后缀。一个文件可以包含多条规则，每条规则描述一类工具调用在什么条件下应当被允许、拒绝或进入审批。

## 语法总览

以下是策略 DSL 的整体语法结构：

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

# 事件匹配
<event_match> :=
    tool_call(<tool_pattern>)
  | tool_call.<subtype>
  | tool_call.<subtype>(<tool_pattern>)

<subtype> := requested | completed | failed
<tool_pattern> := * | tool_name | namespace.tool | namespace.*

# 条件表达式
<expr> :=
    <expr> OR <expr>
  | <expr> AND <expr>
  | NOT <expr>
  | (<expr>)
  | <path> <op> <value>
  | <function_call>

<op> := == | != | < | <= | > | >= | IN | NOT IN | MATCHES | CONTAINS

# 动作
<action> :=
    DENY
  | ALLOW
  | HUMAN_CHECK
  | LLM_CHECK

# 调用链 TRACE 分隔符
<trace_sep> :=
    ->
  | -> * ->
  | -> ... ->
  | -> ...? ->
```


## 一条最小规则

下面这条规则表示：当低信任智能体尝试调用 `shell.exec` 时，直接拒绝。

```text
RULE: deny_low_trust_shell
ON: tool_call(shell.exec)
CONDITION: principal.trust_level < 2
POLICY: DENY
Severity: high
Category: capability
Reason: "Low-trust agent cannot execute shell commands"
```

这条规则包含四个核心部分：

* `RULE` 定义规则 ID。
* `ON` 限定规则作用于哪些工具调用。
* `CONDITION` 定义命中条件。
* `POLICY` 定义命中后的处理动作。

`Severity`、`Category`、`Reason` 是元数据。它们不改变条件判断本身，但会进入审计与告警信息，建议为生产规则补齐。

## 规则 ID

`RULE` 后面的值是规则 ID，用于审计、调试和规则管理。建议使用稳定、可读、唯一的名字。

```text
RULE: deny_external_email_low_trust
```

推荐命名方式：

* 使用小写字母、数字、下划线或连字符。
* 让名字体现动作和场景，例如 `deny_...`、`review_...`。
* 不要在同一批规则中重复使用同一个 ID；重复 ID 在加载到注册表时会覆盖旧规则。

## ON：限定规则作用范围

`ON` 用于先按事件类型和工具名筛选候选规则。写得越精确，规则越容易理解，也越不容易误伤。

`ON` 可以省略。省略后等价于不按工具名预筛选，通常只建议用于 `TRACE` 规则，因为 `TRACE` 的最后一个占位符会进一步约束当前调用。普通单步规则建议显式写出 `ON`。

### 按工具名匹配

```text
RULE: deny_destructive_shell
ON: tool_call(shell.exec)
CONDITION: tool.cmd MATCHES ".*\\b(rm\\s+-rf|mkfs|dd\\s+if=)\\b.*"
POLICY: DENY
Severity: critical
Category: shell
```

`tool_call(shell.exec)` 只匹配名为 `shell.exec` 的工具。

### 使用通配符

```text
RULE: review_all_shell_tools
ON: tool_call(shell.*)
CONDITION: principal.trust_level < 3
POLICY: HUMAN_CHECK
Severity: high
Category: shell
```

`shell.*` 可以匹配 `shell.exec`、`shell.run` 等工具名。也可以使用 `tool_call(*)` 表示匹配所有工具调用。

### 按工具调用阶段匹配

```text
RULE: deny_external_http_before_execution
ON: tool_call.requested(http.post)
CONDITION: tool.domain NOT IN allowlist.http
POLICY: DENY
Severity: high
Category: egress
```

常用阶段包括：

| 阶段 | 含义 |
| --- | --- |
| `requested` | 工具执行前的请求阶段，最常用于访问控制 |
| `completed` | 工具执行完成后的结果阶段，可读取 `tool.result` |
| `failed` | 工具调用失败阶段 |

如果你只是想在工具执行前拦截，优先使用 `ON: tool_call.requested(...)` 或直接使用 `ON: tool_call(...)`。

## CONDITION：条件表达式

`CONDITION` 决定规则是否命中。条件表达式由路径、字面量、比较运算符、函数和布尔逻辑组成。

`CONDITION` 可以省略。若同时存在 `TRACE`，则规则会在调用链模式匹配时命中；若既没有 `TRACE` 也没有 `CONDITION`，规则会在 `ON` 匹配时直接命中。除非你确实想写一条“无条件规则”，否则建议显式写出 `CONDITION`。

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

### 布尔逻辑优先级

表达式优先级从高到低为：

| 优先级 | 语法 |
| --- | --- |
| 1 | `(...)` |
| 2 | `NOT` |
| 3 | `AND` |
| 4 | `OR` |

建议在混合使用 `AND` 和 `OR` 时显式加括号。

```text
CONDITION: tool.boundary == "external"
           AND (caller.scope_missing("sensitive_export") OR goal_drift_detected())
```

### 字面量

DSL 支持以下常见字面量：

| 类型 | 示例 |
| --- | --- |
| 字符串 | `"evil.com"`、`'internal'` |
| 数字 | `0`、`3`、`0.8` |
| 布尔值 | `true`、`false`、`TRUE`、`FALSE` |
| 字符串集合 | `{"email.send", "http.post"}` |

字符串集合常用于 `IN` / `NOT IN`。

```text
CONDITION: tool.name IN {"email.send", "http.post", "slack.post"}
```

### 比较与集合运算符

| 运算符 | 含义 | 示例 |
| --- | --- | --- |
| `==` / `!=` | 相等 / 不相等 | `principal.role == "basic"` |
| `<` / `<=` / `>` / `>=` | 数值或可比较值比较 | `principal.trust_level < 2` |
| `IN` | 左值属于右侧集合、列表、字典 key 或相同字符串 | `tool.recipient_domain IN allowlist.email` |
| `NOT IN` | `IN` 的否定 | `tool.domain NOT IN allowlist.http` |
| `MATCHES` | 正则表达式匹配 | `tool.url MATCHES ".*127\\.0\\.0\\.1.*"` |
| `CONTAINS` | 字符串包含、列表成员包含或字典 key 包含 | `tool.body CONTAINS "password"` |

`MATCHES` 的右侧是正则表达式字符串，底层使用 Python `re` 语义。正则字符串中常见反斜杠需要转义，例如 `\\b`、`\\d`。

## 常用路径

路径用于读取当前事件、调用者、工具调用参数和运行时上下文中的值。

### 调用者身份

`principal` 表示当前发起工具调用的智能体身份。`caller` 是 `principal` 的别名。

```text
CONDITION: principal.role == "basic"
CONDITION: caller.trust_level < 3
```

常用字段包括：

| 路径 | 含义 |
| --- | --- |
| `principal.agent_id` | 智能体 ID |
| `principal.session_id` | 会话 ID |
| `principal.role` | 角色，常见值如 `basic`、`default`、`privileged`、`system` |
| `principal.trust_level` | 信任级别，通常数值越高权限越大 |

### 工具信息与参数

`tool` 是 `tool_call` 的便捷别名。

```text
CONDITION: tool.name == "send_email_to"
CONDITION: tool.recipient MATCHES ".*@evil\\.com"
```

常用字段包括：

| 路径 | 含义 |
| --- | --- |
| `tool.name` | 当前工具名 |
| `tool.<param>` | 当前工具参数，例如 `tool.domain`、`tool.sql`、`tool.recipient_domain` |
| `tool.result` | 工具执行结果，主要用于结果阶段事件 |

对于工具函数参数，推荐使用 `tool.<param>`，因为它在策略中更接近自然语言。例如：

```text
RULE: deny_sql_ddl
ON: tool_call(db.query)
CONDITION: tool.sql MATCHES "(?i).*\\b(DROP|TRUNCATE|ALTER|GRANT|REVOKE)\\b.*"
POLICY: DENY
Severity: critical
Category: database
```

### 工具静态标签

注册工具时可以声明静态标签，规则可通过 `tool.boundary`、`tool.sensitivity`、`tool.integrity`、`tool.tags` 读取。

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

对应策略示例：

```text
RULE: deny_high_sensitivity_external_tool
ON: tool_call.requested
CONDITION: tool.boundary == "external" AND tool.sensitivity == "high"
POLICY: DENY
Severity: critical
Category: data_exfiltration
```

标签字段的取值范围：

| 字段 | 取值 |
| --- | --- |
| `tool.boundary` | `internal`、`external`、`privileged` |
| `tool.sensitivity` | `low`、`moderate`、`high` |
| `tool.integrity` | `trusted`、`unfiltered` |
| `tool.tags` | 字符串列表 |

### 事件字段

```text
CONDITION: event.session_id == "session-001"
CONDITION: event.type == "tool_call_requested"
```

常用事件别名包括：

| 路径 | 实际含义 |
| --- | --- |
| `event.type` | 事件类型 |
| `event.id` | 事件 ID |
| `event.timestamp` | 事件时间戳 |
| `event.session_id` | 当前会话 ID |

### Allowlist

Allowlist 通常由运行时配置注入，规则中可以用 `allowlist.<name>` 或 `whitelist("<name>")` 访问。

## POLICY：命中后的动作

`POLICY` 定义规则命中后的处理方式。

| 动作 | 含义 |
| --- | --- |
| `DENY` | 拒绝工具调用，不执行原工具 |
| `ALLOW` | 允许工具调用 |
| `HUMAN_CHECK` | 进入人工审批流程 |
| `LLM_CHECK` | 交给配置的 LLM 审查；未配置 LLM 或审查失败时会退化为人工审批 |

如果没有任何规则命中，当前运行时会默认允许工具调用。因此，表达“禁止未知目标”“禁止不在白名单中”的意图时，应写成 `DENY` / `HUMAN_CHECK` 条件规则，而不是只写一条正向 `ALLOW` 规则。

## 内置函数

函数可以出现在条件中。返回布尔值的函数可以直接作为条件；返回值的函数通常配合比较运算符使用。

### Allowlist 与集合函数

| 函数 | 返回 | 说明 |
| --- | --- | --- |
| `whitelist("name")` | 集合 | 读取运行时注入的 allowlist |
| `subset(values, container)` | 布尔值 | `values` 中所有元素都在 `container` 内 |
| `any_in(values, container)` | 布尔值 | `values` 中任意元素在 `container` 内 |

示例：所有收件人都必须在用户通讯录中，否则拒绝。

```text
RULE: deny_unknown_recipients
ON: tool_call(send_email_to)
CONDITION: NOT subset(tool.recipients, whitelist("user_address_book"))
POLICY: DENY
Severity: high
Category: email
```

示例：任意收件人命中黑名单就拒绝。

```text
RULE: deny_blocked_recipient
ON: tool_call(send_email_to)
CONDITION: any_in(tool.recipients, whitelist("blocked_emails"))
POLICY: DENY
Severity: high
Category: email
```

### 字符串、URL 和邮箱函数

| 函数 | 说明 |
| --- | --- |
| `starts_with(text, prefix)` | 判断字符串前缀 |
| `ends_with(text, suffix)` | 判断字符串后缀 |
| `contains(container, value)` | 函数形式的包含判断 |
| `url.domain(url)` | 提取 URL 主机名 |
| `url.is_external(url)` | 判断 URL 是否不属于内部域名 allowlist |
| `email.domain(address)` | 提取邮箱域名 |

示例：禁止访问本地或内网地址。

```text
RULE: deny_http_ssrf
ON: tool_call(http.get)
CONDITION: tool.url MATCHES ".*(?:localhost|127\\.0\\.0\\.1|10\\.\\d+|172\\.1[6-9]\\.|192\\.168\\.).*"
POLICY: DENY
Severity: critical
Category: ssrf
```

示例：使用 URL 解析函数判断域名。

```text
RULE: review_external_url
ON: tool_call(browser.open)
CONDITION: url.is_external(tool.url)
POLICY: HUMAN_CHECK
Severity: medium
Category: network
```

### 标签、scope 与工具 tag 函数

| 函数 | 说明 |
| --- | --- |
| `input.has_label("pattern")` | 当前会话输入或上游数据是否带有某个标签 |
| `input.has_any_label({"a/*", "b/*"})` | 当前会话输入或上游数据是否命中任意标签模式 |
| `caller.scope_missing("scope")` | 当前调用者是否缺少某个 scope |
| `tool.has_tag("tag")` | 当前工具是否带有某个静态 tag |

示例：会话中出现敏感标签，并且调用者缺少外发授权时拒绝外部调用。

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

### 会话历史与行为函数

| 函数 | 返回 | 说明 |
| --- | --- | --- |
| `upstream_contains_tool("tool")` | 布尔值 | 当前会话上游是否调用过某工具 |
| `upstream_contains_any_tool({"a", "b"})` | 布尔值 | 上游是否调用过集合中的任意工具 |
| `derived_from_tool("tool")` | 布尔值 | 当前实现中按上游工具判断 |
| `tool_sequence_matches({"a", "b"})` | 布尔值 | 会话工具序列是否按顺序包含这些工具 |
| `repeated_attempts(tool="name", window="5m")` | 数字 | 当前会话中某工具重复尝试次数 |
| `distinct_targets()` | 数字 | 最近目标去重数量 |
| `path_length(source="tool")` | 数字 | 从某上游工具到当前调用的大致路径长度 |

示例：数据库查询后发送邮件，进入 LLM 审查。

```text
RULE: review_email_after_db_query
ON: tool_call(email.send)
CONDITION: upstream_contains_any_tool({"db.query", "database_query"})
POLICY: LLM_CHECK
Severity: high
Category: data_exfiltration
Reason: "Email send follows a database query"
```

示例：5 分钟窗口内 HTTP 外发尝试过多，转人工审批。

```text
RULE: review_http_burst
ON: tool_call(http.post)
CONDITION: repeated_attempts(tool="http.post", window="5m") > 4
POLICY: HUMAN_CHECK
Severity: medium
Category: behavioural_anomaly
```

### 历史参数与历史结果

| 函数 | 返回 | 说明 |
| --- | --- | --- |
| `history_arg("tool", "param")` | 任意值 | 读取当前会话中某工具最近一次调用的参数 |
| `history_result("tool")` | 任意值 | 读取当前会话中某工具最近一次调用结果 |
| `history_args_match("tool", "param", value)` | 布尔值 | 判断某历史参数是否等于指定值 |

示例：如果文档分类结果为 restricted，则禁止外发邮件。

```text
RULE: deny_restricted_doc_external_email
ON: tool_call(email.send)
CONDITION: history_result("classify_doc") == "restricted"
           AND tool.recipient_domain NOT IN allowlist.email
POLICY: DENY
Severity: critical
Category: data_exfiltration
```

注意：`history_arg` 和 `history_result` 读取的是已经写入会话历史的调用。若你需要在 `TRACE` 规则中读取“当前正在判断的这个工具调用”的参数，优先使用 TRACE 占位符，例如 `Mailer.recipient`。

### 语义信号函数

这些函数读取运行时注入的安全信号。

| 函数 | 说明 |
| --- | --- |
| `goal_drift_detected()` | 检测到目标漂移 |
| `scope_expansion_detected()` | 检测到权限或范围扩张 |
| `suspicious_exfil_pattern()` | 检测到可疑外发模式 |
| `high_entropy_payload_detected()` | 检测到高熵 payload |
| `goal_changed_from_initial()` | 当前目标偏离初始目标 |

示例：存在目标漂移且准备调用外部工具时转人工审批。

```text
RULE: review_goal_drift_external_call
ON: tool_call.requested
CONDITION: tool.boundary == "external" AND goal_drift_detected()
POLICY: HUMAN_CHECK
Severity: high
Category: goal_drift
```


## TRACE：声明式调用链规则

`TRACE` 用于描述“某个上游调用最终流向某个下游调用”的模式。它比 `upstream_contains_tool(...)` 更强，因为它可以给链上的每个位置命名，并在 `CONDITION` 中读取这些位置的工具名、参数、标签和结果。

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

### TRACE 分隔符

| 语法 | 含义 | 示例 |
| --- | --- | --- |
| `A -> B` | A 后面紧接 B，中间不能有其他调用 | `Fetcher -> Executor` |
| `A -> * -> B` | A 和 B 中间恰好有一次调用 | `Reader -> * -> Writer` |
| `A -> ... -> B` | A 和 B 中间至少有一次调用 | `A -> ... -> B` |
| `A -> ...? -> B` | A 在 B 之前任意位置，中间可以为 0 次或多次调用 | `DbOp -> ...? -> Mailer` |

最常用的是 `-> ...? ->`，表示“上游某个调用最终走到了下游某个调用”。

### TRACE 占位符字段

在 `TRACE: Src -> ...? -> Dst` 中，`Src` 和 `Dst` 是占位符。它们会绑定到会话调用链中的具体工具调用。

| 字段 | 含义 |
| --- | --- |
| `Placeholder.name` | 绑定调用的工具名 |
| `Placeholder.integrity` | 绑定调用的 `label.integrity` |
| `Placeholder.sensitivity` | 绑定调用的 `label.sensitivity` |
| `Placeholder.boundary` | 绑定调用的 `label.boundary` |
| `Placeholder.result` | 绑定调用的返回值 |
| `Placeholder.<param>` | 绑定调用的参数，例如 `Mailer.recipient` |

示例：外部输入最终进入 shell，进入 LLM 审查。

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

示例：数据库查询后紧接邮件发送，直接拒绝。

```text
RULE: deny_adjacent_db_to_email
TRACE: DbOp -> Mailer
CONDITION: DbOp.name IN {"db.query", "database_query"}
           AND Mailer.name IN {"email.send", "send_email_to"}
POLICY: DENY
Severity: critical
Category: data_exfiltration
```

## 元数据

```text
Severity: critical
Category: data_exfiltration
Reason: "Sensitive data sent to non-allowlisted endpoint"
```

建议至少填写：

| 字段 | 建议 |
| --- | --- |
| `Severity` | 用于告警分级，建议取 `critical`、`high`、`medium`、`low`、`info` |
| `Category` | 用于归类，例如 `egress`、`database`、`data_exfiltration` |
| `Reason` | 用一句话说明规则命中的安全原因 |


## 编写策略的建议

### 先限制高风险工具

优先为外发、命令执行、文件写入、数据库写入和敏感数据读取工具写规则。

```text
RULE: deny_db_write_basic
ON: tool_call(db.exec)
CONDITION: tool.sql MATCHES "(?i).*\\b(INSERT|UPDATE|DELETE|DROP|ALTER)\\b.*"
           AND principal.role == "basic"
POLICY: DENY
Severity: critical
Category: database
```

### 优先写窄规则

能写 `ON: tool_call(email.send)` 时，不要写 `ON: tool_call(*)`。能写 `tool.name IN {...}` 时，不要只靠模糊正则。

窄规则更容易审计，也更容易定位误报。

### 把硬拒绝和审批分清楚

明确危险的行为用 `DENY`，不确定但高风险的行为用 `HUMAN_CHECK` 或 `LLM_CHECK`。

### 为跨步骤风险使用 TRACE

单次工具调用规则只能看到当前操作。对于“先读取敏感数据，再外发”的风险，应使用 `TRACE` 或会话历史函数。

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

### 不要依赖规则顺序表达业务意图

策略文件可以包含多条规则。多条规则同时命中时，运行时会合并命中结果并选择相应动作。为了让策略行为可预测，建议把互斥条件写清楚，而不是依赖规则书写顺序。

### 用校验命令检查规则

编写完成后，建议先运行：

```bash
python -m agentguard check rules/my_policy.rules
```

也可以校验一个目录下的所有 `.rules` 文件：

```bash
python -m agentguard check rules/
```

这个命令会解析、编译并做语义检查，能发现常见问题，例如重复规则 ID、缺少元数据、TRACE 分隔符错误、标签枚举值错误等。

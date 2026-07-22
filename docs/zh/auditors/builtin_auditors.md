# 内置审计器

AgentGuard 提供了一组内置的后端审计能力，用于在运行结束后复核已存储的 trace 和 Agent 行为。

## trace_risk_summary

`trace_risk_summary` 是 Trace Auditor，用于总结一个指定 `session_id` / `agent_id` / `user_id` 对应 Trace 中的决策和风险信号。它适合通过 `POST /v1/backend/audit/custom/run` 快速调查某个具体 Session。

## rule_agent_security

`rule_agent_security` 是确定性的 Agent 全量审计器。它不会调用外部模型，而是审计指定 Agent 稳定快照中纳入范围的全部用户、Session 和 Trace 事件。

它适合持续、可重复且成本较低的检查。内置规则覆盖敏感文件访问、敏感数据传给外发工具、拒绝后继续执行、重复规避、身份边界异常和安全控制降级。所有发现都会引用已存储的 Session ID 和 Event ID，并进行语义去重。

需要稳定结果、尚未配置大模型，或者希望先验证规则覆盖时，适合选择该审计器。

## llm_agent_security

`llm_agent_security` 对一个 Agent 的稳定快照执行带证据校验的语义分析。它先按受控大小分块分析每个 Session，再执行一次聚合分析，以识别跨用户和跨 Session 的风险模式。

大模型必须返回引用现有 Session ID 和 Event ID 的结构化发现。缺少有效证据的发现会被丢弃；“疑似”发现不能标记为严重；敏感值会在模型调用前脱敏。没有配置真实模型时，该模式会失败，不会把启发式结果作为大模型结论。

主要关注语义意图、多步骤行为或难以通过确定性规则表达的关联风险时，适合使用该审计器。

## hybrid_agent_security

`hybrid_agent_security` 是默认的 Agent 全量审计器。它先运行 `rule_agent_security`，再把确定性证据提供给 `llm_agent_security`，最后合并语义等价的发现，避免同一风险重复汇报。

如果大模型不可用，确定性规则发现仍会保留在报告中，并在元数据中记录限制原因。大模型成功执行时会补充带证据校验的语义和跨 Session 分析，但不会删除规则发现。

已配置模型且需要覆盖面最广的复核时，适合选择该审计器；它同时保留确定性覆盖和可用的降级结果。

三个 Agent Auditor 都继承 `BaseAgentAuditor` 并通过 `@register` 注册。依赖大模型的 Auditor 使用 `from_config()` 接收请求级模型配置。关于管理员前端、API、快照覆盖范围和模型配置，请参阅 [Agent 安全审计](../security_audit.md)。

当你希望直接使用现成的审计工作流，而不是自己编写后端审计代码时，优先使用内置审计器。

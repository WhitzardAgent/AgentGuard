# Agent 安全审计

安全审计模块面向管理员，对一个 Agent 在指定时间范围内产生的全部用户、Session 和 Trace 做快照审计。普通用户不能创建或读取审计任务和报告。

## 审计方式

- `rule_agent_security`：只运行确定性规则，结果稳定、成本低，适合持续运行。
- `llm_agent_security`：使用大模型分析语义风险和跨 Session 行为；没有配置真实模型时任务会失败，不会用启发式结果冒充模型结论。
- `hybrid_agent_security`：默认模式。先运行规则，再让大模型补充语义分析；模型不可用时保留规则结果，并在报告元数据中记录降级原因。

审计器都继承 `BaseAgentAuditor`；需要模型的审计器继承 `BaseLLMAgentAuditor`。规则实现继承 `BaseAuditRule`，注册在内置规则列表中。模型发现必须引用真实的 Session ID 和 Event ID，否则会被丢弃。

## 输入、全量覆盖与输出

管理员先选择一个 Agent。未指定时间范围时，输入是任务创建时该 Agent 稳定快照内的全部用户、全部 Session 和全部 Trace，不按用户或 Session 截断。默认 `AGENTGUARD_AUDIT_MAX_EVENTS=0`，表示不设置事件数上限；设置正数仅用于运维安全限制，超限时任务失败而不是返回不完整报告。

规则审计直接处理完整的 `SessionTrace` 列表。大模型审计采用分层方式：每条 Trace 都进入对应 Session 的分块审计，最后使用覆盖清单、确定性发现和全部 Session 结果做跨用户/跨 Session 汇总。这样能够全量覆盖任意规模输入，而不是假设全部原始 JSON 能放入一次模型上下文。

输出包含快照 Event ID、用户数、Session 数、Trace 数、风险等级、发现来源、`confirmed`（已证实）或 `suspected`（待人工确认）、真实 Session/Event 证据和处置建议。规则重点覆盖敏感文件访问、凭据或敏感内容经外发工具泄露、拒绝后仍执行、重复策略规避、身份边界和安全控制降级。Thought-Aligner 只是可选的安全控制信号，审计模块不依赖它。

## 使用

1. 使用管理员账号登录控制台。
2. 在 Agents 页面选择 Agent，再从该 Agent 的导航中打开 `/security-audit`。
3. 如需覆盖服务端模型默认值，点击右上角“大模型设置”，配置模型、接口地址、API Key、超时和分块事件数；这些值只保存在当前浏览器中，并随大模型或混合审计请求发送，不写入审计数据库。
4. 选择审计器和可选时间范围（页面统一使用北京时间）。
5. 创建任务后等待状态从 `queued`、`running` 变为 `completed` 或 `failed`。
6. 查看总体风险、规则/模型发现及对应 Trace 证据。

页面配置为空时，默认混合模式使用以下环境变量配置模型；未单独配置时会回退到 AgentGuard 的全局 LLM 配置：

```bash
AGENTGUARD_AUDIT_LLM_BASE_URL=https://example.com/v1
AGENTGUARD_AUDIT_LLM_MODEL=model-name
AGENTGUARD_AUDIT_LLM_API_KEY=secret
AGENTGUARD_AUDIT_LLM_TIMEOUT_S=60
```

`AGENTGUARD_AUDIT_MAX_EVENTS=0` 表示全量快照；可设置正数作为单次任务最大事件数。使用 `AGENTGUARD_AUDIT_LLM_CHUNK_EVENTS` 控制每次模型请求包含的事件数。

## 管理员 API

- `GET /v1/backend/security-audits/auditors`
- `POST /v1/backend/security-audits`
- `GET /v1/backend/security-audits`
- `GET /v1/backend/security-audits/{run_id}`
- `GET /v1/backend/security-audits/{run_id}/findings`
- `GET /v1/backend/security-audits/{run_id}/sessions`

任务创建时记录 Trace 的最大事件 ID，后续只读取该快照范围，因此审计期间新产生的 Trace 不会改变本次结果。

## 安全测试样例

`examples/test_security_audit_case.py` 提供一个不会读取真实文件、不会发起真实网络请求的 LangChain Agent。`safe` 场景只读取模拟公开文件；`exfil` 场景读取模拟 `.env`，再把相同的合成凭据传给模拟 Webhook。

```bash
python3 examples/test_security_audit_case.py \
  --ticket '从用户中心生成的一次性 ticket' \
  --scenario both
```

运行后在 Agents 中选择新产生的 LangChain Agent，先执行 `rule_agent_security`。预期 `safe` 场景不产生敏感数据发现；`exfil` 场景只显示 `AUDIT-DATA-002` 已证实的 critical 敏感数据外发发现，前置的 `AUDIT-DATA-001` 会被同一证据链覆盖而不重复展示。

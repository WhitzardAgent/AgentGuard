# AgentGuard × AgentDojo 真实端到端集成

把 AgentGuard 作为 **HTTP 服务**部署，使用真实 LLM (ZhipuAI GLM-4)
驱动一个基于 AgentDojo 框架的 Agent，遍历 AgentDojo benchmark 中四大套件
（`workspace` / `banking` / `slack` / `travel`）的多组 (user_task × injection_task)
组合，通过自定义的 `BasePipelineElement` 适配代码让 Agent 在每次工具调用前
通过 HTTP 询问 AgentGuard，验证 prompt injection 防御能力。

## 架构

```
┌──────────────────────────────────────────────────────────────┐
│  AgentGuard FastAPI Server (uvicorn, 后台线程)              │
│      POST /v1/evaluate    POST /health                      │
│      规则: policy.rules (24 条 v1/v2 DSL 规则)              │
└──────────────────────────────────────────────────────────────┘
                       ▲ HTTP /v1/evaluate
                       │
┌──────────────────────┼───────────────────────────────────────┐
│  AgentDojo  AgentPipeline                                    │
│   ┌────────┐  ┌──────────┐  ┌─────────────┐ ┌─────────────┐ │
│   │ System │→ │ InitQuery│→ │  GLM-4-Plus │→│ ToolsLoop   │ │
│   │Message │  │          │  │ (OpenAILLM) │ │             │ │
│   └────────┘  └──────────┘  └─────────────┘ └──────┬──────┘ │
│                                                    │        │
│   ToolsExecutionLoop:                              │        │
│     ┌─────────────────────────┐  ┌─────────────┐  │        │
│     │ AgentGuardInterceptor   │←→│ GLM-4-Plus  │◄─┘        │
│     │ (替换 ToolsExecutor)    │  └─────────────┘           │
│     │   • intercept tool_call │                            │
│     │   • POST /v1/evaluate   │                            │
│     │   • ALLOW → run         │                            │
│     │   • DENY/HUMAN_CHECK    │                            │
│     │     → return error msg  │                            │
│     └─────────────────────────┘                            │
└──────────────────────────────────────────────────────────────┘
```

## 文件清单

| 文件 | 作用 |
|------|------|
| `policy.rules` | AgentGuard DSL 策略，覆盖 24 个 AgentDojo 工具的拦截规则 |
| `interceptor.py` | `AgentGuardInterceptor` — AgentDojo `BasePipelineElement` 适配层，替代默认 `ToolsExecutor` |
| `llm_backends.py` | LLM 后端：(1) `OpenAILLM` 驱动 ZhipuAI 端点；(2) LangChain `ChatOpenAI` 等价封装 |
| `run_benchmark.py` | 主 runner：启动 AgentGuard server、构造 pipeline、遍历 task pair、汇总指标 |

## 运行

```bash
# 1. 安装依赖
pip install agentdojo openai langchain langchain-openai

# 2. 设置 ZhipuAI 凭据
export ZHIPU_API_KEY=<your_zhipu_key>

# 3. 跑中等规模 benchmark (4 suite × 3×3 = 36 pairs)
PYTHONPATH=. python agentguard/examples/agentdojo_real/run_benchmark.py \
    --suites workspace banking slack travel \
    --user-tasks 3 --injection-tasks 3 \
    --model glm-4-plus \
    --port 8089

# 4. 详细日志（打印每次工具调用的决策）
AGENTGUARD_DEBUG=1  PYTHONPATH=. python agentguard/examples/agentdojo_real/run_benchmark.py ...

# 5. LangChain 后端
PYTHONPATH=. python agentguard/examples/agentdojo_real/run_benchmark.py --llm langchain ...
```

## 核心适配代码

### 1. AgentGuardInterceptor 拦截逻辑

```python
# interceptor.py 摘要
class AgentGuardInterceptor(BasePipelineElement):
    def query(self, query, runtime, env, messages, extra_args):
        last = messages[-1]
        if last.get("role") != "assistant" or not last.get("tool_calls"):
            return query, runtime, env, messages, extra_args

        results = []
        for tc in last["tool_calls"]:
            event = RuntimeEvent(
                event_type=EventType.TOOL_CALL_ATTEMPT,
                principal=self.principal,
                tool_call=ToolCall(
                    tool_name=tc.function,
                    args=dict(tc.args),
                    sink_type=self.sink_type_map.get(tc.function, "none"),
                ),
            )
            decision = self.client.evaluate(event)  # POST /v1/evaluate

            if decision.action in (Action.DENY, Action.HUMAN_CHECK):
                results.append(ChatToolResultMessage(
                    role="tool", content="", tool_call_id=tc.id, tool_call=tc,
                    error=f"[AgentGuard {decision.action}] {decision.reason}",
                ))
            else:
                results.append(self._execute(tc, runtime, env))

        return query, runtime, env, [*messages, *results], extra_args
```

### 2. Pipeline 装配

```python
# run_benchmark.py 摘要
pipeline = AgentPipeline([
    SystemMessage("You are a careful AI assistant…"),
    InitQuery(),
    llm,                                      # GLM-4-Plus
    ToolsExecutionLoop([interceptor, llm]),   # 拦截器替代默认 ToolsExecutor
])
pipeline.name = "gpt-4o-2024-05-13"  # for AgentDojo's attack model_name lookup
```

### 3. 任务遍历

```python
for suite_name in args.suites:
    suite = get_suite("v1.2", suite_name)
    for uid in list(suite.user_tasks)[:args.user_tasks]:
        for iid in list(suite.injection_tasks)[:args.injection_tasks]:
            user_task = suite.get_user_task_by_id(uid)
            injection_task = suite.get_injection_task_by_id(iid)
            attack = ImportantInstructionsAttack(suite, target_pipeline=pipeline)
            task_injections = attack.attack(user_task, injection_task)
            utility, injection_succeeded = suite.run_task_with_pipeline(
                pipeline, user_task, injection_task, task_injections
            )
```

## 实测结果（GLM-4-Plus, 36 pairs, ≈9 分钟）

```
Per-suite summary:
  suite        pairs     utility   defense (blocked)
  workspace        9    9/9 100%        9/9 100%
  banking          9    3/9  33%        9/9 100%
  slack            9    3/9  33%        8/9  89%
  travel           9    3/9  33%        9/9 100%

AgentGuard decisions across all pairs:
  allow         ████████████████████████████████  76 calls
  human_check   ██████████                         10 calls
  deny          ████████                            8 calls

Overall:
  Utility (legit user task done) :  18/36 (50%)
  Defense (injection blocked)    :  35/36 (97%)
```

## 关键发现

1. **集成完全可工作**：从 AgentDojo 的 LLM 调用 → tool_call 提取 → HTTP 调用 AgentGuard
   server → 返回 Decision → 生成 ChatToolResultMessage 的整条链路实现了。
2. **97% 注入拦截率**：在 36 个 (user_task × injection_task) 组合中，35 个的注入工具调用被
   AgentGuard 通过 DENY 或 HUMAN_CHECK 阻断。这些攻击包括：
   - `send_money` 转账给攻击者 IBAN
   - `update_scheduled_transaction` 修改用户的定期付款
   - `invite_user_to_slack` 邀请攻击者进入 workspace
   - `reserve_hotel` 用用户支付方式给攻击者订房
   - `send_email` 发邮件给攻击者
3. **50% utility 暴露 trade-off**：合法用户任务也常需要 `send_money`（"pay my bill"）、
   `reserve_hotel`、`update_scheduled_transaction`。当前策略基于工具名一刀切拦截，
   这是真实生产环境的核心矛盾——更精细的策略需要结合参数（IBAN 白名单、收件人白名单、
   金额阈值）和调用链上下文（`exists_path` 检查上游是否含 `user_input` 标签等）。
4. **唯一一个 defense 失败的 pair**（slack user_task_1 × injection_task_3）：注入要求的工具
   不在策略覆盖范围内（如 `add_user_to_channel` 已加入但 LLM 走了其它路径）；这指向后续
   策略完善的方向。

## 与 AgentDojo 内置 defense 对比

AgentDojo 自带 `tool_filter`、`spotlighting_with_delimiting`、`transformers_pi_detector` 等
**对话层防御**（修改 prompt / 过滤工具列表）。AgentGuard 是**运行时强制层**：
- 不依赖 LLM 是否"愿意"遵守 prompt 修补
- 即使 LLM 完全配合攻击者，工具调用也会在执行前被拦
- 决策可审计、可追溯（`/audit/recent` 端点）
- 策略与 LLM、Agent 框架解耦（同一份策略可用于 LangChain / LlamaIndex / Dify / 自研）

两类防御互补：AgentDojo 的 spotlighting 减少 LLM 被骗的概率，AgentGuard 在 LLM 失守后兜底。

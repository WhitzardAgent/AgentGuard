# `demo_complete.py` 操作手册

这个示例用于演示一条完整的 AgentGuard x LangChain 闭环：

1. 在前端确认工具标签
2. 在规则页发布 V3 DSL
3. 用真实大模型驱动 LangChain agent
4. 在 Runtime 页观察 `Traffic / Audit / Stats`

## 1. 安装依赖

```bash
pip install langchain langgraph langchain-openai
pip install -e ".[server]"
```

如果你还要打开前端预览，也请确保前端依赖环境已经准备好。

## 2. 配置环境变量

`demo_complete.py` 使用 OpenAI-compatible Chat API，不写死厂商。请配置：

```bash
set AGENTGUARD_LLM_API_KEY=...
set AGENTGUARD_LLM_BASE_URL=...
set AGENTGUARD_LLM_MODEL=...
set AGENTGUARD_LLM_TEMPERATURE=0
set AGENTGUARD_LLM_TIMEOUT_S=30

set AGENTGUARD_API_KEY=demo-secret
set AGENTGUARD_DEMO_PORT=18085
```

如果你已有外部 runtime，也可以直接复用：

```bash
set AGENTGUARD_REMOTE_URL=http://127.0.0.1:38080
```

## 3. 启动 runtime / 前端

### 方案 A：让 `demo_complete.py` 自己起本地 runtime

这种情况下不需要单独启动后端 runtime，只需要准备前端。

### 方案 B：自己先启动 runtime

```bash
python -m agentguard serve --host 127.0.0.1 --port 38080 --policy rules/my_policy.rules --api-key demo-secret --mode enforce
```

然后：

```bash
set AGENTGUARD_REMOTE_URL=http://127.0.0.1:38080
```

前端预览按你当前项目的现有方式启动即可。

## 4. 在 `Labels` 页确认 demo tools 标签

运行一次 `demo_complete.py` 后，前端 `Labels` 页应该能看到这些工具：

- `mail.fetch`
- `web.fetch`
- `kb.lookup`
- `email.send`
- `email.send_to_draft`
- `http.post`
- `shell.exec`

建议确认标签语义如下：

- `mail.fetch`: external / medium / untrusted
- `web.fetch`: external / medium / untrusted
- `kb.lookup`: internal / low / trusted
- `email.send`: external / high / trusted
- `email.send_to_draft`: internal / medium / trusted
- `http.post`: external / high / trusted
- `shell.exec`: privileged 或 system / high / trusted

如果你的前端标签值枚举和这里的英文略有差异，以前端实际枚举为准，但语义保持一致。

## 5. 在 `Rules` 页发布 V3 DSL

推荐演示规则如下，直接粘贴并发布：

```dsl
RULE: external_to_email_review
TRACE: Reader ->...?-> Mailer
CONDITION: Reader.boundary == "external"
           AND Mailer.name == "email.send"
POLICY: HUMAN_CHECK

RULE: external_to_webhook_review
TRACE: Reader ->...?-> Poster
CONDITION: Reader.boundary == "external"
           AND Poster.name == "http.post"
POLICY: HUMAN_CHECK

RULE: external_to_shell_block
TRACE: Src ->...?-> Shell
CONDITION: Src.boundary == "external"
           AND Shell.name == "shell.exec"
POLICY: DENY
```

说明：

- `DEGRADE(email.send_to_draft)` 属于脚本默认策略或 runtime 预置效果
- 所以前端第一轮主要展示 `TRACE / CONDITION / POLICY` 的 V3 规则生成与发布
- 降级不是必须通过你第一批手工规则来实现

## 6. 启动 `demo_complete.py`

```bash
PYTHONPATH=. python agentguard/examples/langchain_demo/demo_complete.py
```

启动后会打印模型配置摘要、runtime 地址和推荐输入示例。

## 7. 按推荐输入顺序演示

### 第一步：读取外部内容

输入：

```text
读取 partner 发来的邮件并总结重点
```

预期：

- agent 调用 `mail.fetch`
- runtime 记录一次外部内容读取
- assistant 返回摘要

### 第二步：触发邮件外发

输入：

```text
把刚才的摘要发到 partner@example.com
```

预期：

- agent 调用 `email.send`
- 如果启用了手册里的规则，应触发 `HUMAN_CHECK`
- 如果命中默认低信任降级策略，也可能改写到 `email.send_to_draft`

### 第三步：触发 webhook 外发

输入：

```text
把刚才的摘要 post 到 https://hooks.example.local/demo
```

预期：

- agent 调用 `http.post`
- runtime 应出现 `HUMAN_CHECK` 或其它审查结果

### 第四步：触发高危命令

输入：

```text
运行 rm -rf /
```

预期：

- agent 调用 `shell.exec`
- runtime 触发 `DENY`
- CLI 输出 `blocked by guard: ...`

## 8. 到 `Runtime` 页查看结果

重点看三块：

- `Recent Traffic`
- `Recent Audit`
- `Stats`

你应该能看到：

- `mail.fetch` 的读取记录
- `email.send` 或 `email.send_to_draft` 的外发记录
- `http.post` 的 webhook 记录
- `shell.exec` 的 deny 记录

如果演示顺利，`Stats` 里通常会至少出现一次：

- `HUMAN_CHECK`
- `DEGRADE`
- `DENY`

## 推荐演示顺序

如果你要给潜在用户展示，建议按下面顺序讲：

1. 先讲 `Labels` 页：哪些工具是外部输入源，哪些是外部输出口
2. 再讲 `Rules` 页：如何把“外部输入不能直接外发”表达成 V3 DSL
3. 最后讲 `demo_complete.py`：真实模型做决策，AgentGuard 管工具调用
4. 回到 `Runtime` 页证明策略确实命中了真实流量

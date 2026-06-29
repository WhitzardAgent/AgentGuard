# 核心概念

本页介绍 AgentGuard 文档和配置中常见的核心概念。AgentGuard 是一套面向 AI Agents 的零信任安全防护基座：它接入已有智能体运行时，观察 LLM 与工具事件，执行配置的安全策略，并返回决策或审计记录，但不会替代智能体自身的规划逻辑。

## 智能体

智能体是接收任务、规划步骤、调用 LLM、并可能调用工具的应用或运行单元。它可以基于 LangChain、LangGraph、LlamaIndex、AutoGen、OpenAI Agents SDK、OpenClaw 构建，也可以是自定义框架。

AgentGuard 不替代智能体本身。智能体仍负责理解任务、推理、编排和选择工具；AgentGuard 则围绕该智能体产生的运行时事件增加安全防护层。

## 运行阶段

AgentGuard 可以检查智能体运行过程中的多个阶段：

- `llm_before`：请求发送给 LLM 之前
- `llm_after`：LLM 返回输出之后
- `tool_before`：工具调用真正执行之前
- `tool_after`：工具返回结果之后

这意味着 AgentGuard 不只用于工具调用访问控制。即使智能体没有调用工具，AgentGuard 依旧可以在 LLM 输入和输出阶段进行安全风险识别与拦截。

## AgentGuard 客户端

AgentGuard 客户端运行在智能体进程内或智能体进程旁边。多数集成场景中，用户直接接触的是 `Guard`。

客户端负责：

- 接入智能体框架或自定义运行时
- 将 LLM 与工具活动规范化为 `RuntimeEvent`
- 在配置后执行本地 plugin
- 在需要时向中控服务发送远端判定请求
- 在智能体进程内执行返回的决策

可以把它理解为 AgentGuard 在智能体侧的运行时探针和执行点。

## AgentGuard Server

AgentGuard Server 是 AgentGuard 的集中式管理和决策组件。

它通常负责：

- 接收 AgentGuard 客户端上报的运行时事件
- 执行配置的远端 plugin 和访问控制策略
- 返回 allow、deny 或 review 决策
- 存储 trace，用于运行时监控和审计
- 支持 Web 控制台中的策略配置、审批等工作流

这种集中式中控架构可以让组织通过统一的策略和审计入口管理多个分布式智能体。

## 身份 (Principal)

身份用于描述运行时事件背后的智能体或调用方的身份与信任属性。

常见身份属性包括：

- 智能体 ID
- 会话 ID
- 用户 ID
- 角色
- 信任级别

策略会使用这些属性表达差异化约束。例如，低信任智能体可能被禁止向外部发送文档，而高权限角色可以被允许或转入审核。

## 会话

会话表示一次智能体任务或运行的上下文范围。它关联同一次运行中的 LLM 事件、工具调用、工具结果、决策和 trace 记录。

会话很重要，因为许多风险不是单步风险，而是跨步骤风险。例如，“读取敏感文件，然后上传到外部端点”需要服务端把同一次运行中的多个事件关联起来判断。

## RuntimeEvent

`RuntimeEvent` 是 client 与 server plugin 共同使用的标准化事件对象。它用统一结构表示一次 LLM 或工具事件。

常见事件类型包括：

- `LLM_INPUT`
- `LLM_OUTPUT`
- `TOOL_INVOKE`
- `TOOL_RESULT`

事件 payload 会按事件阶段使用类型化结构：

- `LLMInput(messages=[{"role": "...", "content": "..."}])`
- `LLMOutput(output="...", thought=None, final_output=None)`
- `ToolInvoke(tool_name="...", arguments={...}, capabilities=[...])`
- `ToolResult(tool_name="...", result="...")`

`LLMOutput` 现在拆成了一个通用字段和两个可选语义字段：

- `output`：标准文本字段，用于兼容旧逻辑和通用扫描。如果存在 `final_output`，`output` 通常会与它保持一致；否则会回退到 `thought` 或原始输出文本。
- `thought`：可选的内部推理文本或中间思考内容，前提是 adapter 能把它识别出来。
- `final_output`：可选的最终对外回答，也就是模型准备返回给调用方的可见内容。

大多数 plugin 和策略直接读取 `payload.output` 就够用了；如果你需要区分“内部思考”和“最终回复”，再单独读取 `payload.thought` 与 `payload.final_output`。

Plugin 和策略会读取这些字段来识别风险并生成决策。

## RuntimeContext

`RuntimeContext` 是跨事件传播的会话级上下文。它包含 `session_id`、`agent_id`、`user_id`、任务信息、策略信息，以及集成方自定义 metadata。

Plugin 和策略会使用运行时上下文理解谁在执行、事件属于哪个任务、当前环境是什么，以及适用哪些 client 或 server 配置。

## 工具

工具是智能体可以调用的操作能力，例如发送邮件、访问 HTTP、执行 Shell 命令、读取文件、写入文件或查询数据库。

工具会影响真实系统和数据，因此是高影响治理对象。AgentGuard 尤其适用于：

- 邮件、HTTP、消息发送等外发工具
- Shell 或系统命令工具
- 文件系统读写工具
- 数据库读写工具
- 不可信输入可能影响后续动作的工作流

## Plugin

Plugin 是 AgentGuard 的模块化运行时检测单元。它可以运行在 client 侧，也可以运行在 server 侧。

Client plugin：

- 运行在智能体进程内
- 接收当前 `RuntimeEvent` 和 `RuntimeContext`
- 适合低延迟本地检查和轻量级过滤

Server plugin：

- 运行在中控服务端
- 接收当前 event 和 context
- 还可以使用 `trajectory_window` 检查同一 session 的近期事件
- 适合跨步骤检测、集中式策略评估和审计分析

Plugin 配置按 phase 组织。每个 phase 可以定义由 client runtime 加载的 `client` plugins，以及由 control server 加载的 `server` plugins。每个 plugin 条目都是一个 spec 对象，例如 `{"name": "rule_based_plugin", "env": {}}`。当前实现里，`client` plugin spec 可以把 `env` 和构造参数传给 client plugin，而 `server` plugin spec 主要按 `name` 或 `class` 解析。实现级细节见 [AgentGuard插件](plugins.md)。

## 策略

策略是用户定义的控制规则。在内置流程中，这些 DSL 策略由服务端 `rule_based_plugin` plugin 消费，用于说明某个运行时动作在什么条件下应该被允许、拒绝，或转交给人工 / LLM 做最终的 allow-or-deny 判断。

AgentGuard 内置访问控制策略能力，并支持通过 DSL 规则定义策略。常见策略包括：

- 低信任身份不能向外部发送敏感文档
- 匹配危险模式的 Shell 命令必须拒绝
- 访问未知目标需要人工审核
- 数据库读取后再外发邮件这类跨步骤序列需要阻断或审核

策略会与 plugin 协同工作：`rule_based_plugin` 负责评估显式访问控制规则，既可以直接返回固定的 `ALLOW` / `DENY`，也可以进入 `HUMAN_CHECK` / `LLM_CHECK`；其他 plugin 则可以附加风险信号或给出额外的候选决策。

## 决策

决策是 AgentGuard 运行时评估的结果。典型结果包括：

- 允许事件继续执行
- 拒绝并阻断执行
- 将操作转入人工或模型审核
- 记录风险信号和 metadata，用于审计

对于工具调用，决策决定工具是否真正执行。对于 LLM 输入和输出事件，决策可以用于在内容继续进入智能体流程前阻断或约束不安全内容。

## 审计与自定义审计器

审计记录运行时事件、决策、plugin 结果和相关 metadata，帮助用户理解发生了什么以及为什么发生。

自定义审计器是面向已存储 trace 的事后分析单元。它适合用于：

- 合规复核
- 事故排查
- 事后风险分析
- 为前端生成汇总风险等级

实现级细节见 [自定义审计器](auditors.md)。

## 数据来源与跨步骤风险

很多智能体风险取决于信息来自哪里，以及后续如何在 session 中流动。AgentGuard 通过存储运行时上下文和 trace window 支持跨步骤推理，例如：

- 之前读取过敏感数据，后续又尝试发送到外部
- 不可信 LLM 输出后来影响了 Shell 命令
- 智能体在被拒绝后反复尝试不同目标

接入 AgentGuard 时，建议清晰标注工具边界、数据敏感度和信任属性。这些标签可以让策略规则和 plugin 检查更精确。

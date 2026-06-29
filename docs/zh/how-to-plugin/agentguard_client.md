# AgentGuard Client

## 这个客户端是干什么的

AgentGuard Client 是部署在智能体进程内，或者智能体进程旁边的运行时探针和执行点。多数集成场景中，用户直接接触的是 `Guard`。

它的目标可以简单理解为：

- 拿到大模型输入
- 拿到大模型输出
- 拿到工具输入
- 拿到工具输出
- 让这些运行时事件经过已配置的 plugins
- 根据 plugins 返回的 decision，最终在智能体进程里决定放行还是拦截

AgentGuard 不替代智能体自身的规划逻辑。智能体仍然负责推理、任务编排和工具选择；客户端负责在这些运行时行为外面加上一层安全决策。

## 客户端实际在观察什么

在不同框架里，客户端会把原生运行时行为统一规范化为 AgentGuard 事件，例如：

- `llm_before`：请求发给模型之前的 prompt 或 message payload
- `llm_after`：模型返回之后的输出
- `tool_before`：工具真正执行之前的工具名和参数
- `tool_after`：工具执行完成之后的返回结果

所以 AgentGuard 保护的不只是工具调用，也包括大模型输入输出本身。

## 决策链路是怎么工作的

一个典型的运行时链路大致如下：

1. `Guard.attach_xxx()` 把 AgentGuard 接到目标框架运行时上。
2. 客户端把框架里的原生调用转换成统一的 `RuntimeEvent`。
3. 已配置的 client plugins 检查这个事件，并可能返回一个 `decision_candidate`。
4. 如果这个 plugin 决策已经是最终决策，客户端就直接在本地执行它。
5. 否则，客户端会继续把事件发给中控服务，由 server plugins 和 policy 继续判断。
6. 客户端拿到最终决策后，再把它落实到智能体进程里。

如果只看最核心的一层，客户端做的事情就是：看见运行时数据、收集 plugin 的判断、并确保智能体最后要么继续执行，要么被拦截。

## 目前已经适配的框架

AgentGuard 目前内置支持以下框架：

| 框架 | 接入方法 | 文档 |
| --- | --- | --- |
| LangChain | `guard.attach_langchain()` | [LangChain](langchain.md) |
| LangGraph | `guard.attach_langgraph()` | [LangGraph](langgraph.md) |
| LlamaIndex | `guard.attach_llamaindex()` | [LlamaIndex](llamaindex.md) |
| AutoGen | `guard.attach_autogen()` | [AutoGen](autogen.md) |
| OpenAI Agents SDK | `guard.attach_openai_agents()` | [OpenAI Agents SDK](openai_agents_sdk.md) |
| Dify Workflow Agent 节点 | 在 Dify `api`/`worker` 进程启动时调用 `install_dify_adapter()` | [Dify Workflow Agent 节点](dify.md) |
| Openclaw | JavaScript 侧集成 | [Openclaw](openclaw_adapter.md) |

如果你的框架不在上面的列表里，也可以通过实现自定义 adapter 的方式接入，详见 [Custom Adapter](custom.md)。

### Dify Workflow Agent 节点

Dify 的 Workflow Agent 节点、LLM model 和 tools 都是在 Dify runtime 内部创建的，所以用户手里没有一个可以传给 `guard.attach_xxx()` 的 agent 对象。Dify 场景需要在 Dify `api` 和 `worker` 进程启动时安装一次 runtime adapter：

```python
from agentguard.adapters.agent.dify import install_dify_adapter

install_dify_adapter()
```

通过环境变量配置客户端，例如 `AGENTGUARD_ENABLED=true`、`AGENTGUARD_SERVER_URL`、`AGENTGUARD_API_KEY` 和 `AGENTGUARD_POLICY`。当前已验证支持 Dify 1.15 本地部署下 `ENABLE_AGENT_V2=false` 的 legacy Workflow Agent 节点路径，用于观察 Agent 节点内部的 LLM 调用和工具调用。详细步骤见 [Dify Workflow Agent 节点](dify.md)。

## 最简理解

如果只想记一句话，可以把 AgentGuard Client 理解成这样一个组件：

- 接到你的智能体框架里
- 捕获模型和工具的输入输出
- 让 plugins 基于这些事件做判断
- 把最终决策执行回运行时

这也是为什么客户端必须离智能体运行进程足够近。

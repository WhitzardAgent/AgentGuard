# AgentGuard Client

## What it is for

The AgentGuard client is the runtime-side probe and enforcement point that runs inside, or alongside, the agent process. In most integrations, users interact with it through `Guard`.

Its goal is straightforward:

- capture the agent's LLM inputs and LLM outputs
- capture the agent's tool inputs and tool outputs
- pass those runtime events through configured plugins
- turn the returned plugin decisions into a final allow-or-block outcome inside the agent process

AgentGuard does not replace the agent's planning logic. The agent still decides how to reason, when to call tools, and how to complete the task. The client adds a security decision layer around that runtime.

## What the client observes

Across supported frameworks, the client normalizes runtime activity into AgentGuard events such as:

- `llm_before`: the prompt or message payload before it is sent to the model
- `llm_after`: the model output after it returns
- `tool_before`: the tool name and arguments before execution
- `tool_after`: the tool result after execution

This is why AgentGuard can protect both model interactions and tool usage, instead of only checking tool calls.

## How the decision flow works

A typical runtime path looks like this:

1. `Guard.attach_xxx()` connects AgentGuard to the target framework runtime.
2. The client converts framework-native calls into normalized `RuntimeEvent` objects.
3. Configured client plugins inspect the event and may return a `decision_candidate`.
4. If that plugin decision is final, the client enforces it locally.
5. Otherwise, the client can forward the event to the control server, where server-side plugins and policies continue evaluation.
6. The client receives the final decision and enforces it in the agent process.

In simple terms, the client is responsible for seeing the runtime data, collecting plugin decisions, and making sure the agent ultimately either proceeds or gets blocked.

## Frameworks currently supported

AgentGuard currently provides built-in adapters for these frameworks:

| Framework | Attach method | Documentation |
| --- | --- | --- |
| LangChain | `guard.attach_langchain()` | [LangChain](langchain.md) |
| LangGraph | `guard.attach_langgraph()` | [LangGraph](langgraph.md) |
| LlamaIndex | `guard.attach_llamaindex()` | [LlamaIndex](llamaindex.md) |
| AutoGen | `guard.attach_autogen()` | [AutoGen](autogen.md) |
| OpenAI Agents SDK | `guard.attach_openai_agents()` | [OpenAI Agents SDK](openai_agents_sdk.md) |
| Dify Workflow Agent node | `install_dify_adapter()` during Dify `api`/`worker` startup | [Dify Workflow Agent Node](dify.md) |
| Openclaw | JavaScript-side integration | [Openclaw](openclaw_adapter.md) |

If your framework is not listed here, you can still integrate AgentGuard by implementing a custom adapter. See [Custom Adapter](custom.md).

### Dify Workflow Agent node

Dify creates Workflow Agent nodes, LLM models, and tools inside the Dify runtime, so there is no user-owned agent object to pass to `guard.attach_xxx()`. For Dify, install the runtime adapter once during Dify `api` and `worker` startup:

```python
from agentguard.adapters.agent.dify import install_dify_adapter

install_dify_adapter()
```

Configure the client with environment variables such as `AGENTGUARD_ENABLED=true`, `AGENTGUARD_SERVER_URL`, `AGENTGUARD_API_KEY`, and `AGENTGUARD_POLICY`. The validated path is the Dify 1.15 local deployment with `ENABLE_AGENT_V2=false`, covering legacy Workflow Agent nodes and observing LLM/tool calls inside those nodes. See [Dify Workflow Agent Node](dify.md).

## Minimal mental model

If you only want the short version, think of the AgentGuard client as the component that:

- hooks into your agent framework
- captures model and tool I/O
- runs plugins on those events
- enforces the final decision back into the runtime

That is the core reason the client must live close to the agent process.

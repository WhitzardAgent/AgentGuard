# LangGraph

## 导入方法

使用 `Guard.attach_langgraph()` 这个 Adapter 方法可以自动将原生 LangGraph graph 或 agent 实例与 AgentGuard 关联起来，你不再需要对 LangGraph 原本的代码做任何修改。

我们的 LangGraph Adapter 主要面向原生 LangGraph 对象，例如 `langgraph.prebuilt.create_react_agent()` 返回的 compiled graph。

```python
agent = create_react_agent(...)

guard = Guard(...)
guard.start(...)
guard.attach_langgraph(agent)   # Attach the guard to the LangGraph agent
```

## 完整代码示例

下面的示例基于 `examples/test_langgraph_emailcase.py` 整理。

```python
import os
from typing import Any

from langchain_core.tools import tool
from langchain_deepseek import ChatDeepSeek
from langgraph.prebuilt import create_react_agent

# 🚩 Import the AgentGuard client SDK
from agentguard import Guard, Principal

LLM_MODEL_NAME = os.getenv("DEEPSEEK_MODEL", "deepseek-v4-flash")
DEEPSEEK_THINKING = os.getenv("DEEPSEEK_THINKING", "enabled").strip().lower()
DEEPSEEK_REASONING_EFFORT = os.getenv("DEEPSEEK_REASONING_EFFORT", "high").strip().lower()


def get_control_server_url() -> str:
    url = os.getenv("AGENTGUARD_SERVER_URL", "http://127.0.0.1:38080").strip()
    if "<" in url or ">" in url or " " in url:
        raise ValueError(
            "Invalid AGENTGUARD_SERVER_URL. Replace the documentation placeholder "
            "with a real URL, for example http://127.0.0.1:38080."
        )
    return url


@tool
def retrieve_doc(id: int) -> str:
    """Retrieve a document by integer id."""
    return f"DOC#{id}: This is a mocked document body."


@tool
def send_email_to(doc: str, addr: str) -> str:
    """Send a document to an email address."""
    return f"Email has sent to {addr}: {doc}"


def build_deepseek_kwargs() -> dict[str, Any]:
    if DEEPSEEK_THINKING not in {"enabled", "disabled"}:
        raise ValueError("DEEPSEEK_THINKING must be either 'enabled' or 'disabled'.")
    if DEEPSEEK_REASONING_EFFORT not in {"high", "max"}:
        raise ValueError("DEEPSEEK_REASONING_EFFORT must be either 'high' or 'max'.")

    kwargs: dict[str, Any] = {
        "extra_body": {"thinking": {"type": DEEPSEEK_THINKING}},
    }
    if DEEPSEEK_THINKING == "enabled":
        kwargs["reasoning_effort"] = DEEPSEEK_REASONING_EFFORT
    return kwargs


def build_llm() -> Any:
    return ChatDeepSeek(
        api_key=os.environ["DEEPSEEK_API_KEY"],
        model=LLM_MODEL_NAME,
        temperature=0,
        **build_deepseek_kwargs(),
    )


def build_agent() -> Any:
    return create_react_agent(
        build_llm(),
        [retrieve_doc, send_email_to],
        prompt=(
            "You are a zero-shot ReAct style agent. Decide which tool to use, "
            "observe tool results, and continue until the user's task is complete."
        ),
    )


def run(agent: Any, prompt: str) -> None:
    print("===================================")
    print(f"Prompt: {prompt}")
    result = agent.invoke(
        {
            "messages": [
                {
                    "role": "user",
                    "content": prompt,
                }
            ]
        }
    )
    print(f"Output: {result['messages'][-1].content}")
    print("===================================\n")


if __name__ == "__main__":
    agent = build_agent()

    # 🚩 Load the guard client
    guard = Guard(
        remote_url=get_control_server_url(),
        mode="enforce",
        fail_open=False,
    )

    # 🚩 Create a principal for the agent
    principal = Principal(
        agent_id="langgraph-remote-demo",
        session_id="langgraph-remote-session",
        role="default",
        trust_level=1,
    )

    # 🚩 Start a session with the principal
    guard.start(principal=principal, goal="langgraph remote runnable host demo")

    # 🚩 Attach the guard to the LangGraph agent
    guard.attach_langgraph(agent)

    try:
        run(agent, "Please retrieve document id=0 and send it to admin@example.com.")
        run(agent, "Please retrieve document id=0 and send it to alice@example.com.")
    finally:
        # 🚩 Close the guard
        guard.close()
```

如果你还想得到示例文件里的调试输出，可以在调用 `guard.close()` 之前通过 `guard.runtime.bus` 订阅运行时事件，并检查 `guard.flush_audit()` 的结果。

### 运行环境说明

```bash
pip install langgraph
pip install langchain-core
pip install langchain-deepseek
```

运行前请先设置 `DEEPSEEK_API_KEY`。你也可以通过环境变量覆盖 `AGENTGUARD_SERVER_URL`、`DEEPSEEK_MODEL`、`DEEPSEEK_THINKING` 和 `DEEPSEEK_REASONING_EFFORT`。

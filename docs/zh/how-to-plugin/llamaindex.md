# LlamaIndex

## 导入方法

使用 `Guard.attach_llamaindex()` 这个 Adapter 方法可以自动将 LlamaIndex 的 workflow agent 实例与 AgentGuard 关联起来，你不再需要对 LlamaIndex 原本的代码做任何修改。

我们的 LlamaIndex Adapter 主要面向 workflow 风格的智能体对象，例如 `llama_index.core.agent.workflow.ReActAgent`。

```python
agent = ReActAgent(...)

guard = Guard(...)
guard.start(...)
guard.attach_llamaindex(agent)   # Attach the guard to the LlamaIndex agent
```

## 完整代码示例

下面的示例基于 `examples/test_llamaindex_emailcase.py` 整理。

```python
import asyncio
import os
from typing import Any

# 🚩 Import the AgentGuard client SDK
from agentguard import Guard, Principal

LLM_MODEL_NAME = os.getenv("DEEPSEEK_MODEL", "deepseek-v4-flash")
LLM_API_BASE = os.getenv("DEEPSEEK_API_BASE", "https://api.deepseek.com/v1")
DEEPSEEK_THINKING = os.getenv("DEEPSEEK_THINKING", "enabled").strip().lower()
DEEPSEEK_REASONING_EFFORT = os.getenv("DEEPSEEK_REASONING_EFFORT", "high").strip().lower()


def retrieve_doc(id: int) -> str:
    """Retrieve a document by integer id."""
    return f"DOC#{id}: This is a mocked document body."


def send_email_to(doc: str, addr: str) -> str:
    """Send a document to an email address."""
    return f"Email has sent to {addr}: {doc}"


def get_control_server_url() -> str:
    url = os.getenv("AGENTGUARD_SERVER_URL", "http://127.0.0.1:38080").strip()
    if "<" in url or ">" in url or " " in url:
        raise ValueError(
            "Invalid AGENTGUARD_SERVER_URL. Replace the documentation placeholder "
            "with a real URL, for example http://127.0.0.1:38080."
        )
    return url


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
    from llama_index.llms.openai_like import OpenAILike

    return OpenAILike(
        api_key=os.environ["DEEPSEEK_API_KEY"],
        model=LLM_MODEL_NAME,
        api_base=LLM_API_BASE,
        temperature=0,
        additional_kwargs=build_deepseek_kwargs(),
        is_chat_model=True,
        is_function_calling_model=False,
    )


def build_agent() -> Any:
    from llama_index.core.agent.workflow import ReActAgent
    from llama_index.core.tools import FunctionTool

    return ReActAgent(
        llm=build_llm(),
        tools=[
            FunctionTool.from_defaults(fn=retrieve_doc),
            FunctionTool.from_defaults(fn=send_email_to),
        ],
        system_prompt=(
            "You are a zero-shot ReAct style agent. Decide which tool to use, "
            "observe tool results, and continue until the user's task is complete."
        ),
    )


async def run(agent: Any, prompt: str) -> None:
    print("===================================")
    print(f"Prompt: {prompt}")
    handler = agent.run(user_msg=prompt)
    result = await handler
    print(f"Output: {result.response}")
    print("===================================\n")


async def main() -> None:
    agent = build_agent()

    # 🚩 Load the guard client
    guard = Guard(
        remote_url=get_control_server_url(),
        mode="enforce",
        fail_open=False,
    )

    # 🚩 Create a principal for the agent
    principal = Principal(
        agent_id="llamaindex-remote-demo",
        session_id="llamaindex-remote-session",
        role="default",
        trust_level=1,
    )

    # 🚩 Start a session with the principal
    guard.start(principal=principal, goal="llamaindex remote runnable host demo")

    # 🚩 Attach the guard to the LlamaIndex agent
    guard.attach_llamaindex(agent)

    try:
        await run(agent, "Please retrieve document id=0 and send it to admin@example.com.")
        await run(agent, "Please retrieve document id=0 and send it to alice@example.com.")
    finally:
        # 🚩 Close the guard
        guard.close()


if __name__ == "__main__":
    asyncio.run(main())
```

如果你还想得到示例文件里的调试输出，可以在调用 `guard.attach_llamaindex()` 之前，通过 `guard.runtime.bus` 订阅运行时事件。

### 运行环境说明

```bash
pip install llama-index-core
pip install llama-index-llms-openai-like
```

运行前请先设置 `DEEPSEEK_API_KEY`。你也可以通过环境变量覆盖 `AGENTGUARD_SERVER_URL`、`DEEPSEEK_API_BASE`、`DEEPSEEK_MODEL`、`DEEPSEEK_THINKING` 和 `DEEPSEEK_REASONING_EFFORT`。

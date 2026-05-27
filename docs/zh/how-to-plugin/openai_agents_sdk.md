# OpenAI Agents SDK

## 导入方法
使用 `Guard.attach_openai_agents()` 这个 Adapter 方法可以自动将 OpenAI Agents 智能体实例与 AgentGuard 关联起来，你不再需要对 OpenAI Agents SDK 原本的代码做任何修改。

我们的 OpenAI Agents Adapter 关联的智能体实例是 `AgentBase` 的子类对象，例如 `Agent`、`SandboxAgent` 等。

```python
agent = Agent(...)

guard = Guard(...)
guard.start(...)
guard.attach_openai_agents(agent)   # Attach the guard to the OpenAI agent
```

## 完整代码示例
下面代码展示了一个导入 AgentGuard 访问控制客户端后的完整代码示例，标 🚩 符号的地方是客户端的插入位置：
```python
import asyncio
import os
from agents import Agent, Runner, function_tool

# 🚩 Import the AgentGuard client SDK
from agentguard import Guard, Principal

os.environ["OPENAI_API_KEY"] = "<YOUR KEY>"         # Fill this manually

@function_tool
def retrieve_doc(id: int) -> str:
    """Retrieve a document by integer id."""
    return f"DOC#{id}: This is a mocked document body."

@function_tool
def send_email_to(doc: str, addr: str) -> str:
    """Send a document to an email address."""
    return f"Email has sent to {addr}: {doc}"

async def run(agent, prompt):
    print("===================================")
    print(f"Prompt: {prompt}")
    result = await Runner.run(agent, prompt)
    print(f"Output: {result.final_output}")
    print("===================================\n")

async def main():
    agent = Agent(
        name="demo",
        instructions=(
            "You are a zero-shot ReAct style agent. Decide which tool to use, "
            "observe tool results, and continue until the user's task is complete."
        ),
        tools=[retrieve_doc, send_email_to],
    )

    # 🚩 Load the guard client
    guard = Guard(
        remote_url="http://<Control Server IP>:38080",         # Replace with your control server IP and port
        mode="enforce",
        fail_open=False,
    )

    # 🚩 Create a principal for the agent
    principal = Principal(
        agent_id="openai-agent-remote-demo",
        session_id="openai-agent-remote-session",
        role="default",
        trust_level=1,
    )

    # 🚩 Start a session with the principal
    guard.start(principal=principal, goal="openai agent remote runnable host demo")

    # 🚩 Attach the guard to the OpenAI agent
    guard.attach_openai_agents(agent)

    try:
        await run(agent, "Please retrieve document id=0 and send it to admin@example.com.")
        await run(agent, "Please retrieve document id=0 and send it to alice@example.com.")
    finally:
        # 🚩 Close the guard
        guard.close()

if __name__ == "__main__":
    asyncio.run(main())
```

### 运行环境说明
以上代码的运行有如下包依赖：
```bash
pip install "openai-agents==0.17.2"
```

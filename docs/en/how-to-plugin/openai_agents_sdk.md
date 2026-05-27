# OpenAI Agents SDK

## Integration

Use `Guard.attach_openai_agents()` to automatically connect an agent instance in OpenAI Agents SDK to AgentGuard. No modifications to the original OpenAI Agents SDK code are required.

The OpenAI Agents adapter targets subclasses of `AgentBase`, such as `Agent`, `SandboxAgent`, etc.

```python
agent = Agent(...)

guard = Guard(...)
guard.start(...)
guard.attach_openai_agents(agent)   # Attach the guard to the OpenAI agent
```

## Full example

Below is a complete code example after integrating the AgentGuard client. Lines marked with 🚩 show where the client is inserted:

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
        remote_url="http://<Control Server IP>:38080",      # Replace with your control server IP and port
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

### Dependencies

```bash
pip install "openai-agents==0.17.2"
```
# AutoGen

## Integration

Use `Guard.attach_autogen()` to automatically connect an AutoGen agent instance to AgentGuard. No modifications to the original AutoGen SDK code are required.

The AutoGen adapter targets `AssistantAgent` objects.

```python
agent = AssistantAgent(...)

guard = Guard(...)
guard.start(...)
guard.attach_autogen(agent)   # Attach the guard to the AutoGen agent
```

## Full example

Below is a complete code example after integrating the AgentGuard client. Lines marked with 🚩 show where the client is inserted:

```python
import asyncio

from autogen_agentchat.agents import AssistantAgent
from autogen_agentchat.messages import TextMessage
from autogen_ext.models.openai import OpenAIChatCompletionClient
from autogen_core.models import ModelFamily
from autogen_core import CancellationToken

# 🚩 Import the AgentGuard client SDK
from agentguard import Guard, Principal

LLM_API_KEY = "<YOUR KEY>"         # Fill this manually
LLM_MODEL_NAME = "gpt-5.4-mini"

def retrieve_doc(id: int) -> str:
    """Retrieve a document by integer id."""
    return f"DOC#{id}: This is a mocked document body."

def send_email_to(doc: str, addr: str) -> str:
    """Send a document to an email address."""
    return f"Email has sent to {addr}: {doc}"

async def run(agent, prompt):
    print("===================================")
    print(f"Prompt: {prompt}")
    result = await agent.on_messages(
        [TextMessage(content=prompt, source="user")],
        cancellation_token=CancellationToken()
    )
    print(f"Output: {result.chat_message.content}")
    print("===================================\n")

async def main():
    model_client = OpenAIChatCompletionClient(
        model=LLM_MODEL_NAME,
        api_key=LLM_API_KEY,
        model_info = {
            "vision": True,
            "function_calling": True,
            "json_output": True,
            "family": ModelFamily.GPT_5,
            "structured_output": True,
            "multiple_system_messages": True,
        }
    )

    agent = AssistantAgent(
        name="assistant",
        model_client=model_client,
        tools=[retrieve_doc, send_email_to],
        system_message=(
            "You are a zero-shot ReAct style agent. Decide which tool to use, "
            "observe tool results, and continue until the user's task is complete."
        )
    )

    # 🚩 Load the guard client
    guard = Guard(
        remote_url="http://<Control Server IP>:38080",      # Replace with your control server IP and port
        mode="enforce",
        fail_open=False,
    )

    # 🚩 Create a principal for the agent
    principal = Principal(
        agent_id="autogen-remote-demo",
        session_id="autogen-remote-session",
        role="default",
        trust_level=1,
    )

    # 🚩 Start a session with the principal
    guard.start(principal=principal, goal="autogen remote runnable host demo")

    # 🚩 Attach the guard to the AutoGen agent
    guard.attach_autogen(agent)

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
pip install "autogen-agentchat==0.7.5"
pip install "autogen-ext[openai]==0.7.5"
```
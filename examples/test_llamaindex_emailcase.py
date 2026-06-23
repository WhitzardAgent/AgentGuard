import asyncio
import os
from typing import Any


LLM_MODEL_NAME = os.getenv("DEEPSEEK_MODEL", "deepseek-v4-flash")
LLM_API_BASE = os.getenv("DEEPSEEK_API_BASE", "https://api.deepseek.com/v1")
# 🚩 Import the AgentGuard client SDK
from agentguard import Guard, Principal

def retrieve_doc(id: int) -> str:
    """Retrieve a document by integer id."""
    print(f"Retrieving document id={id}: This is a mocked document body.")
    return f"DOC#{id}: This is a mocked document body."

def get_control_server_url() -> str:
    url = os.getenv("AGENTGUARD_SERVER_URL", "http://127.0.0.1:38080").strip()
    if "<" in url or ">" in url or " " in url:
        raise ValueError(
            "Invalid AGENTGUARD_SERVER_URL. Replace the documentation placeholder "
            "with a real URL, for example http://127.0.0.1:38080."
        )
    return url
def send_email_to(doc: str, addr: str) -> str:
    """Send a document to an email address."""
    print(f"Email has sent to {addr}: {doc}")
    return f"Email has sent to {addr}: {doc}"


def build_llm() -> Any:
    from llama_index.llms.openai_like import OpenAILike

    return OpenAILike(
        api_key=os.environ["DEEPSEEK_API_KEY"],
        model=LLM_MODEL_NAME,
        api_base=LLM_API_BASE,
        temperature=0,
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

    # 🚩 Attach the guard to the LangChain agent
    guard.attach_llamaindex(agent)
    try:
        await run(agent, "Please retrieve document id=0 and send it to admin@example.com.")
        await run(agent, "Please retrieve document id=0 and send it to alice@example.com.")
    finally:
        guard.close()

if __name__ == "__main__":
    asyncio.run(main())

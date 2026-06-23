import os
from typing import Any


LLM_MODEL_NAME = os.getenv("DEEPSEEK_MODEL", "deepseek-v4-flash")
LLM_API_BASE = os.getenv("DEEPSEEK_API_BASE", "https://api.deepseek.com")
# 🚩 Import the AgentGuard client SDK
from agentguard import Guard, Principal


def retrieve_doc(id: int) -> str:
    """Retrieve a document by integer id."""
    return f"DOC#{id}: This is a mocked document body."


def send_email_to(doc: str, addr: str) -> str:
    """Send a document to an email address."""
    return f"Email has sent to {addr}: {doc}"


def build_llm() -> Any:
    from langchain_openai import ChatOpenAI

    return ChatOpenAI(
        api_key=os.environ["DEEPSEEK_API_KEY"],
        model=LLM_MODEL_NAME,
        base_url=LLM_API_BASE,
        temperature=0,
    )


def build_agent() -> Any:
    from langchain_core.tools import tool
    from langgraph.prebuilt import create_react_agent

    tools = [
        tool(retrieve_doc),
        tool(send_email_to),
    ]
    return create_react_agent(
        build_llm(),
        tools,
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
def get_control_server_url() -> str:
    url = os.getenv("AGENTGUARD_SERVER_URL", "http://127.0.0.1:38080").strip()
    if "<" in url or ">" in url or " " in url:
        raise ValueError(
            "Invalid AGENTGUARD_SERVER_URL. Replace the documentation placeholder "
            "with a real URL, for example http://127.0.0.1:38080."
        )
    return url

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
        agent_id="llamaindex-remote-demo",
        session_id="llamaindex-remote-session",
        role="default",
        trust_level=1,
    )

    # 🚩 Start a session with the principal
    guard.start(principal=principal, goal="llamaindex remote runnable host demo")

    # 🚩 Attach the guard to the LangChain agent
    guard.attach_langgraph(agent)
    try:
        run(agent, "Please retrieve document id=0 and send it to admin@example.com.")
        run(agent, "Please retrieve document id=0 and send it to alice@example.com.")
    finally:
        guard.close()
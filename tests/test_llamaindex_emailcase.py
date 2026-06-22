import asyncio
import os
from typing import Any


LLM_MODEL_NAME = os.getenv("DEEPSEEK_MODEL", "deepseek-v4-flash")
LLM_API_BASE = os.getenv("DEEPSEEK_API_BASE", "https://api.deepseek.com/v1")


def retrieve_doc(id: int) -> str:
    """Retrieve a document by integer id."""
    return f"DOC#{id}: This is a mocked document body."


def send_email_to(doc: str, addr: str) -> str:
    """Send a document to an email address."""
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
    await run(agent, "Please retrieve document id=0 and send it to admin@example.com.")
    await run(agent, "Please retrieve document id=0 and send it to alice@example.com.")


if __name__ == "__main__":
    asyncio.run(main())

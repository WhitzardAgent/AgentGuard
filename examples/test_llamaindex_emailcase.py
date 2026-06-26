import asyncio
import json
import os
import re
from typing import Any


LLM_MODEL_NAME = os.getenv("DEEPSEEK_MODEL", "deepseek-v4-flash")
LLM_API_BASE = os.getenv("DEEPSEEK_API_BASE", "https://api.deepseek.com/v1")
DEEPSEEK_THINKING = os.getenv("DEEPSEEK_THINKING", "enabled").strip().lower()
DEEPSEEK_REASONING_EFFORT = os.getenv(
    "DEEPSEEK_REASONING_EFFORT", "high"
).strip().lower()
# 🚩 Import the AgentGuard client SDK
from agentguard import Guard, Principal
from agentguard.schemas.events import EventType, RuntimeEvent

_SECRET_KEY_HINTS = (
    "password",
    "passwd",
    "secret",
    "token",
    "api_key",
    "apikey",
    "authorization",
    "access_key",
    "private_key",
    "session_key",
)
_SECRET_VALUE_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9]{8,}"),
]


def _redact_for_print(value: Any, key: str | None = None) -> Any:
    if key and any(hint in key.lower() for hint in _SECRET_KEY_HINTS):
        return "[REDACTED]"
    if isinstance(value, dict):
        return {str(k): _redact_for_print(v, str(k)) for k, v in value.items()}
    if isinstance(value, list):
        return [_redact_for_print(item) for item in value]
    if isinstance(value, str):
        redacted = value
        for pattern in _SECRET_VALUE_PATTERNS:
            redacted = pattern.sub("[REDACTED]", redacted)
        return redacted
    return value


def retrieve_doc(id: int) -> str:
    """Retrieve a document by integer id."""
    print(f"Retrieving document id={id}")
    return f"DOC#{id}: This is a  document body."

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


def print_agentguard_event(event: RuntimeEvent) -> None:
    """Print runtime events so the example can verify LLMOutput parsing."""
    redacted = _redact_for_print(event.redacted().to_dict())
    print("\n[AgentGuard Event]")
    print(json.dumps(redacted, ensure_ascii=False, indent=2))

    if event.event_type == EventType.LLM_OUTPUT:
        payload = redacted.get("payload") or {}
        print("[AgentGuard LLMOutput Parsed]")
        print(
            json.dumps(
                {
                    "output": payload.get("output"),
                    "thought": payload.get("thought"),
                    "final_output": payload.get("final_output"),
                },
                ensure_ascii=False,
                indent=2,
            )
        )


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

    # Print every normalized RuntimeEvent captured by AgentGuard. This is useful
    # for checking whether LLMOutput.output is split into thought/final_output.
    guard.runtime.bus.subscribe(None, print_agentguard_event)

    # 🚩 Attach the guard to the LlamaIndex agent
    guard.attach_llamaindex(agent)
    try:
        await run(agent, "Please retrieve document id=0 and send it to admin@example.com.")
        await run(agent, "Please retrieve document id=0 and send it to alice@example.com.")
    finally:
        guard.close()

if __name__ == "__main__":
    asyncio.run(main())

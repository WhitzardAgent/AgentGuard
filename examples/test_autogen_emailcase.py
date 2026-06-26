import asyncio
import json
import os
import re
from typing import Any

from agentguard import Guard, Principal
from agentguard.schemas.events import EventType, RuntimeEvent

LLM_MODEL_NAME = os.getenv("DEEPSEEK_MODEL", "deepseek-v4-flash")
LLM_API_BASE = os.getenv("DEEPSEEK_API_BASE", "https://api.deepseek.com/v1")

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


def get_control_server_url() -> str:
    url = os.getenv("AGENTGUARD_SERVER_URL", "http://127.0.0.1:38080").strip()
    if "<" in url or ">" in url or " " in url:
        raise ValueError(
            "Invalid AGENTGUARD_SERVER_URL. Replace the documentation placeholder "
            "with a real URL, for example http://127.0.0.1:38080."
        )
    return url


def get_deepseek_api_key() -> str:
    api_key = os.getenv("DEEPSEEK_API_KEY", "").strip()
    if not api_key:
        raise ValueError("Missing DEEPSEEK_API_KEY for the AutoGen example.")
    return api_key


def retrieve_doc(id: int) -> str:
    """Retrieve a document by integer id."""
    print(f"Retrieving document id={id}")
    return f"DOC#{id}: This is a document."


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


def _import_autogen() -> tuple[Any, Any, Any, Any]:
    try:
        from autogen_agentchat.agents import AssistantAgent
        from autogen_agentchat.messages import TextMessage
        from autogen_core import CancellationToken
        from autogen_ext.models.openai import OpenAIChatCompletionClient
    except ImportError as exc:
        raise RuntimeError(
            "AutoGen dependencies are missing. Install with:\n"
            '  pip install "autogen-agentchat==0.7.5"\n'
            '  pip install "autogen-ext[openai]==0.7.5"'
        ) from exc
    return AssistantAgent, TextMessage, CancellationToken, OpenAIChatCompletionClient


def build_model_client() -> Any:
    _, _, _, openai_client_cls = _import_autogen()
    return openai_client_cls(
        model=LLM_MODEL_NAME,
        api_key=get_deepseek_api_key(),
        base_url=LLM_API_BASE,
        temperature=0,
        model_info={
            "vision": False,
            "function_calling": True,
            "json_output": True,
            "family": "unknown",
            "structured_output": True,
            "multiple_system_messages": True,
        },
    )


def build_agent() -> Any:
    assistant_agent_cls, _, _, _ = _import_autogen()
    return assistant_agent_cls(
        name="assistant",
        model_client=build_model_client(),
        tools=[retrieve_doc, send_email_to],
        system_message=(
            "You are a zero-shot ReAct style agent. Decide which tool to use, "
            "observe tool results, and continue until the user's task is complete."
        ),
    )


async def run(agent: Any, prompt: str) -> None:
    _, text_message_cls, cancellation_token_cls, _ = _import_autogen()

    print("===================================")
    print(f"Prompt: {prompt}")
    result = await agent.on_messages(
        [text_message_cls(content=prompt, source="user")],
        cancellation_token=cancellation_token_cls(),
    )
    print(f"Output: {result.chat_message.content}")
    print("===================================\n")


async def main() -> None:
    agent = build_agent()

    guard = Guard(
        remote_url=get_control_server_url(),
        mode="enforce",
        fail_open=False,
    )

    principal = Principal(
        agent_id="autogen-remote-demo",
        session_id="autogen-remote-session",
        role="default",
        trust_level=1,
    )

    guard.start(principal=principal, goal="autogen remote runnable host demo")
    guard.runtime.bus.subscribe(None, print_agentguard_event)

    # Attach the guard after the AutoGen AssistantAgent is fully constructed.
    guard.attach_autogen(agent)

    try:
        await run(agent, "告诉我你是谁")
        await run(agent, "Please retrieve document id=0 and send it to admin@example.com.")
        await run(agent, "Please retrieve document id=0 and send it to alice@example.com.")
    finally:
        print("\n[Audit Summary]")
        for rec in guard.flush_audit():
            meta = rec.get("metadata") or {}
            decision_meta = meta.get("decision_metadata") or {}
            plugin_result = decision_meta.get("plugin_result") or {}

            print(
                {
                    "event_type": rec.get("event_type"),
                    "decision_type": rec.get("decision_type"),
                    "policy_id": rec.get("policy_id"),
                    "reason": rec.get("reason"),
                    "risk_signals": rec.get("risk_signals"),
                    "route": decision_meta.get("route"),
                    "plugin_metadata": plugin_result.get("metadata") or {},
                }
            )
        guard.close()


if __name__ == "__main__":
    asyncio.run(main())

import asyncio
import json
import os
import re
from typing import Any

import _bootstrap  # noqa: F401
from agents import Agent, Runner, function_tool, set_default_openai_client, set_tracing_disabled
from openai import AsyncOpenAI

from agentguard import Guard, Principal
from agentguard.schemas.events import EventType, RuntimeEvent

OPENAI_MODEL_NAME = os.getenv("OPENAI_MODEL", "gpt-5.4").strip()
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "").strip()
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()

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


def configure_openai_client() -> None:
    """Allow the example to target OpenAI-compatible endpoints via OPENAI_BASE_URL."""
    if not OPENAI_BASE_URL:
        return
    if "<" in OPENAI_BASE_URL or ">" in OPENAI_BASE_URL or " " in OPENAI_BASE_URL:
        raise ValueError(
            "Invalid OPENAI_BASE_URL. Use a real endpoint, for example "
            "https://api.openai.com/v1 or your compatible provider URL."
        )
    if not OPENAI_API_KEY:
        raise ValueError("OPENAI_API_KEY is required when OPENAI_BASE_URL is set.")

    # Custom OpenAI-compatible providers often do not support OpenAI tracing.
    set_tracing_disabled(True)
    set_default_openai_client(
        AsyncOpenAI(
            api_key=OPENAI_API_KEY,
            base_url=OPENAI_BASE_URL,
        ),
        use_for_tracing=False,
    )


@function_tool
def retrieve_doc(id: int) -> str:
    """Retrieve a document by integer id."""
    print(f"Retrieving document id={id}")
    return f"DOC#{id}: This is a document."


@function_tool
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


def build_agent() -> Agent:
    return Agent(
        name="openai-agentguard-demo",
        model=OPENAI_MODEL_NAME,
        instructions=(
            "You are a zero-shot ReAct style agent. Decide which tool to use, "
            "observe tool results, and continue until the user's task is complete."
        ),
        tools=[retrieve_doc, send_email_to],
    )


async def run(agent: Agent, prompt: str) -> None:
    print("===================================")
    print(f"Prompt: {prompt}")
    result = await Runner.run(agent, prompt)
    print(f"Output: {result.final_output}")
    print("===================================\n")


async def main() -> None:
    configure_openai_client()
    agent = build_agent()

    guard = Guard(
        remote_url=get_control_server_url(),
        mode="enforce",
        fail_open=False,
    )

    principal = Principal(
        agent_id="openai-agents-remote-demo",
        session_id="openai-agents-remote-session",
        role="default",
        trust_level=1,
    )

    guard.start(principal=principal, goal="openai agents remote runnable host demo")
    guard.runtime.bus.subscribe(None, print_agentguard_event)
    guard.attach_openai_agents(agent)

    try:
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

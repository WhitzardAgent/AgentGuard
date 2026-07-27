import json
import os
import re
import argparse
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from langchain.agents import create_agent
from langchain.tools import tool
from langchain_deepseek import ChatDeepSeek
# 🚩 Import the AgentGuard client SDK
from agentguard import Guard, Principal
from agentguard.schemas.events import EventType, RuntimeEvent

LLM_MODEL_NAME = os.getenv("DEEPSEEK_MODEL", "deepseek-v4-flash")
LLM_API_BASE = os.getenv("DEEPSEEK_API_BASE", "https://api.deepseek.com/v1")
DEEPSEEK_THINKING = os.getenv("DEEPSEEK_THINKING", "enabled").strip().lower()
DEEPSEEK_REASONING_EFFORT = os.getenv(
    "DEEPSEEK_REASONING_EFFORT", "high"
).strip().lower()

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


def get_client_plugin_config_path() -> str:
    configured = os.getenv("AGENTGUARD_CLIENT_PLUGIN_CONFIG", "").strip()
    if configured:
        return configured
    return str(Path(__file__).resolve().parents[1] / "config" / "plugins.json")


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer.") from exc


def _env_optional_int(name: str) -> int | None:
    raw = os.getenv(name, "").strip()
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer.") from exc


def _default_client_config_advertise_host(server_url: str) -> str:
    host = (urlparse(server_url).hostname or "").strip().lower()
    if host in {"127.0.0.1", "localhost"}:
        # AgentGuard commonly runs in Docker while this LangChain demo runs on the host.
        return "host.docker.internal"
    return host or "127.0.0.1"


def get_client_config_api_options(server_url: str) -> dict[str, Any]:
    listen_host = os.getenv("AGENTGUARD_CLIENT_CONFIG_API_HOST", "0.0.0.0").strip() or "0.0.0.0"
    listen_port = _env_int("AGENTGUARD_CLIENT_CONFIG_API_PORT", 0)
    advertise_host = (
        os.getenv("AGENTGUARD_CLIENT_CONFIG_API_ADVERTISE_HOST", "").strip()
        or _default_client_config_advertise_host(server_url)
    )
    advertise_port = _env_optional_int("AGENTGUARD_CLIENT_CONFIG_API_ADVERTISE_PORT")
    return {
        "client_config_api_host": listen_host,
        "client_config_api_port": listen_port,
        "client_config_api_advertise_host": advertise_host,
        "client_config_api_advertise_port": advertise_port,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the LangChain AgentGuard remote demo with a user ticket."
    )
    parser.add_argument(
        "--ticket",
        required=True,
        help="One-time AgentGuard user ticket generated from the User Centre.",
    )
    return parser.parse_args()

@tool
def retrieve_doc(id: int) -> str:
    """Retrieve a document by integer id."""
    print(f"Retrieving document id={id}")
    return f"DOC#{id}: This is a document which can only be sent to admin."

@tool
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
    from langchain_openai import ChatOpenAI

    return ChatDeepSeek(
    api_key=os.environ["DEEPSEEK_API_KEY"],
    model=LLM_MODEL_NAME,
    temperature=0,
    **build_deepseek_kwargs(),
)


def build_agent() -> Any:
    return create_agent(
        model=build_llm(),
        tools=[retrieve_doc, send_email_to],
        system_prompt=(
            "You are a zero-shot ReAct style agent. Decide which tool to use, "
            "observe tool results, and continue until the user's task is complete."
        ),
    )

def run(agent, prompt):
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

if __name__ == "__main__":
    args = parse_args()
    agent = build_agent()
    control_server_url = get_control_server_url()

    # 🚩 Load the guard client
    guard = Guard(
        remote_url=control_server_url,
        ticket=args.ticket,
        mode="enforce",
        fail_open=False,
        plugin_config=get_client_plugin_config_path(),
        **get_client_config_api_options(control_server_url),
    )

    # 🚩 Create a principal for the agent
    principal = Principal(
        agent_id="langchain-remote-demo",
        session_id="langchain-remote-session",
        role="default",
        trust_level=1,
    )

    # 🚩 Start a session with the principal
    guard.start(principal=principal, goal="langchain remote runnable host demo")
    client_config_url = guard.start_config_api(sync_remote=True)
    print(f"[AgentGuard Client Config API] {client_config_url}")
    print(f"[AgentGuard Client Plugin List] {guard.context.metadata.get('client_plugin_list_url')}")

    # Print every normalized RuntimeEvent captured by AgentGuard. This is useful
    # for checking whether LLMOutput.output is split into thought/final_output.
    guard.runtime.bus.subscribe(None, print_agentguard_event)

    # 🚩 Attach the guard to the LangChain agent
    guard.attach_langchain(agent)

    try:
        run(agent, "Please retrieve document id=0 and send it to admin@example.com.")
        import time; time.sleep(20)  # Wait for the guard to flush events
        run(agent, "Please retrieve document id=0 and send it to alice@example.com.")
    finally:
        # 🚩 Close the guard
        print("\n[Audit Summary]")
        for rec in guard.flush_audit():
            meta = rec.get("metadata") or {}
            decision_meta = meta.get("decision_metadata") or {}
            plugin_result = decision_meta.get("plugin_result") or {}

            print({
                "event_type": rec.get("event_type"),
                "decision_type": rec.get("decision_type"),
                "policy_id": rec.get("policy_id"),
                "reason": rec.get("reason"),
                "risk_signals": rec.get("risk_signals"),
                "route": decision_meta.get("route"),
                "plugin_metadata": plugin_result.get("metadata") or {},
            })
        guard.close()

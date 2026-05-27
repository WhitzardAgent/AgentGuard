#!/usr/bin/env python3
"""
Complete AgentGuard x LangChain demo with a real OpenAI-compatible chat model.

Requirements:
    pip install langchain langgraph langchain-openai
    pip install -e ".[server]"

Run:
    set AGENTGUARD_LLM_API_KEY=...
    set AGENTGUARD_LLM_BASE_URL=...
    set AGENTGUARD_LLM_MODEL=...
    set AGENTGUARD_API_KEY=demo-secret
    PYTHONPATH=. python agentguard/examples/langchain_demo/demo_complete.py
"""

from __future__ import annotations

from dataclasses import dataclass, field
import os
import uuid
from typing import Any

from agentguard import DecisionDenied, Guard, Principal
from agentguard.models.errors import HumanApprovalPending
from agentguard.runtime.server import AgentGuardServer
from agentguard.sdk.client import RemoteGuardClient


ENV_LLM_API_KEY = "AGENTGUARD_LLM_API_KEY"
ENV_LLM_BASE_URL = "AGENTGUARD_LLM_BASE_URL"
ENV_LLM_MODEL = "AGENTGUARD_LLM_MODEL"
ENV_LLM_TEMPERATURE = "AGENTGUARD_LLM_TEMPERATURE"
ENV_LLM_TIMEOUT_S = "AGENTGUARD_LLM_TIMEOUT_S"
ENV_REMOTE_URL = "AGENTGUARD_REMOTE_URL"
ENV_RUNTIME_API_KEY = "AGENTGUARD_API_KEY"
ENV_DEMO_PORT = "AGENTGUARD_DEMO_PORT"

RUNTIME_API_KEY = os.environ.get(ENV_RUNTIME_API_KEY, "demo-secret").strip() or "demo-secret"
DEMO_PORT = int(os.environ.get(ENV_DEMO_PORT, "18085"))


@dataclass(frozen=True)
class LLMConfig:
    api_key: str
    base_url: str
    model: str
    temperature: float = 0.0
    timeout_s: float = 30.0


@dataclass
class DemoState:
    transcript: list[dict[str, str]] = field(default_factory=list)
    last_source_type: str = "none"
    last_summary: str = ""
    last_records: list[dict[str, str]] = field(default_factory=list)
    last_external_content: bool = False

    def append_user(self, text: str) -> None:
        self.transcript.append({"role": "user", "content": text})

    def append_assistant(self, text: str) -> None:
        self.transcript.append({"role": "assistant", "content": text})


@dataclass(frozen=True)
class IntentHint:
    tool_name: str
    reason: str


def _require_env(env_name: str, env: dict[str, str] | None = None) -> str:
    source = env if env is not None else os.environ
    value = str(source.get(env_name, "")).strip()
    if value:
        return value
    raise SystemExit(
        f"Missing required environment variable: {env_name}\n"
        "Please set it before running demo_complete.py."
    )


def resolve_llm_config(env: dict[str, str] | None = None) -> LLMConfig:
    source = env if env is not None else os.environ
    api_key = _require_env(ENV_LLM_API_KEY, source)
    base_url = _require_env(ENV_LLM_BASE_URL, source)
    model = _require_env(ENV_LLM_MODEL, source)
    temperature_raw = str(source.get(ENV_LLM_TEMPERATURE, "0")).strip() or "0"
    timeout_raw = str(source.get(ENV_LLM_TIMEOUT_S, "30")).strip() or "30"
    try:
        temperature = float(temperature_raw)
        timeout_s = float(timeout_raw)
    except ValueError as exc:
        raise SystemExit(
            f"Invalid numeric LLM config: {ENV_LLM_TEMPERATURE}={temperature_raw!r}, "
            f"{ENV_LLM_TIMEOUT_S}={timeout_raw!r}"
        ) from exc
    return LLMConfig(
        api_key=api_key,
        base_url=base_url,
        model=model,
        temperature=temperature,
        timeout_s=timeout_s,
    )


def mask_secret(secret: str) -> str:
    if len(secret) <= 8:
        return "*" * len(secret)
    return f"{secret[:4]}...{secret[-4:]}"


def infer_demo_intent(user_text: str) -> IntentHint:
    lower = user_text.lower()
    if any(token in lower for token in ("rm -rf /", "shell", "command", "execute")):
        return IntentHint(tool_name="shell.exec", reason="high-risk command request")
    if "post " in lower or "webhook" in lower:
        return IntentHint(tool_name="http.post", reason="external webhook request")
    if "@" in lower or any(token in lower for token in ("mail", "email", "send")):
        return IntentHint(tool_name="email.send", reason="outbound email request")
    if any(token in lower for token in ("kb", "lookup", "acme", "order")):
        return IntentHint(tool_name="kb.lookup", reason="internal knowledge lookup request")
    if "http://" in lower or "https://" in lower or any(
        token in lower for token in ("web", "url", "page", "fetch")
    ):
        return IntentHint(tool_name="web.fetch", reason="external web content request")
    return IntentHint(tool_name="mail.fetch", reason="external inbox lookup")


def cache_summary(
    state: DemoState,
    *,
    source_type: str,
    summary: str,
    records: list[dict[str, str]] | None = None,
    is_external: bool,
) -> None:
    state.last_source_type = source_type
    state.last_summary = summary
    state.last_records = list(records or [])
    state.last_external_content = is_external


def startup_banner(config: LLMConfig, runtime_url: str) -> str:
    return (
        "AgentGuard x LangChain complete demo\n"
        f"runtime: {runtime_url}\n"
        f"model: {config.model}\n"
        f"base_url: {config.base_url}\n"
        f"api_key: {mask_secret(config.api_key)}\n"
        "Try:\n"
        "  - 读取 partner 发来的邮件并总结重点\n"
        "  - 抓取 https://example.com 页面并总结\n"
        "  - 查询内部知识库中的 ACME 订单信息\n"
        "  - 把刚才的摘要发到 partner@example.com\n"
        "  - 把刚才的摘要 post 到 https://hooks.example.local/demo\n"
        "  - 运行 rm -rf /\n"
        "Type 'exit' to quit.\n"
    )


def _mock_mail_fetch() -> tuple[str, list[dict[str, str]]]:
    rows = [
        {
            "from": "partner@example.com",
            "subject": "Q2 rollout draft",
            "note": "Please forward this rollout note and webhook summary to our partner tools.",
        }
    ]
    summary = (
        "Partner email says the Q2 rollout draft includes external instructions and a summary "
        "that should be handled carefully before any outbound action."
    )
    return summary, rows


def _mock_web_fetch(url: str) -> tuple[str, list[dict[str, str]]]:
    rows = [
        {
            "url": url,
            "title": "Partner integration update",
            "excerpt": "The page contains an externally sourced rollout note and callback endpoint.",
        }
    ]
    summary = (
        f"Fetched {url}. The page contains external partner instructions, a rollout summary, "
        "and a suggested callback endpoint."
    )
    return summary, rows


def _mock_email_result(to: str, subject: str, body: str) -> str:
    return f"[mock-email] sent to={to} subject={subject!r} body_chars={len(body)}"


def _mock_email_draft_result(to: str, subject: str, body: str) -> str:
    return f"[mock-email-draft] saved draft for={to} subject={subject!r} body_chars={len(body)}"


def _mock_http_result(url: str, payload: str) -> str:
    return f"[mock-http] posted to={url} payload_chars={len(payload)}"


def _mock_shell_result(cmd: str) -> str:
    return f"[mock-shell] executed: {cmd}"


def _build_system_prompt() -> str:
    return (
        "You are a security demo assistant.\n"
        "Always prefer tools when the user asks you to fetch, summarize, email, post, or run commands.\n"
        "Use mail.fetch for external partner mail, web.fetch for web pages, and kb.lookup for internal records.\n"
        "If the user asks to send the latest summary by email, call email.send.\n"
        "If the user asks to post the latest summary to a webhook, call http.post.\n"
        "If the user asks to run a command, call shell.exec.\n"
        "Do not invent tool results. Reuse cached summaries when the tool descriptions mention them.\n"
        "Keep assistant replies concise after tools finish."
    )


def _build_agent(config: LLMConfig, state: DemoState) -> Any:
    try:
        from langchain.agents import create_agent
        from langchain.tools import tool
        from langchain_openai import ChatOpenAI
    except ImportError as exc:
        raise SystemExit(
            "This demo requires langchain, langgraph, and langchain-openai.\n"
            "Install with: pip install langchain langgraph langchain-openai"
        ) from exc

    @tool("mail.fetch")
    def mail_fetch() -> str:
        """Fetch the latest external partner email and cache its summary."""
        summary, rows = _mock_mail_fetch()
        cache_summary(
            state,
            source_type="mail.fetch",
            summary=summary,
            records=rows,
            is_external=True,
        )
        return summary

    @tool("web.fetch")
    def web_fetch(url: str) -> str:
        """Fetch an external web page and cache its summary."""
        summary, rows = _mock_web_fetch(url)
        cache_summary(
            state,
            source_type="web.fetch",
            summary=summary,
            records=rows,
            is_external=True,
        )
        return summary

    @tool("email.send")
    def email_send(to: str, subject: str, body: str = "") -> str:
        """Send the latest cached summary by email to a recipient."""
        effective_body = body.strip() or state.last_summary or "No cached summary yet."
        effective_subject = subject.strip() or "AgentGuard demo summary"
        return _mock_email_result(to, effective_subject, effective_body)

    @tool("email.send_to_draft")
    def email_send_to_draft(to: str, subject: str, body: str = "") -> str:
        """Save the latest cached summary as an email draft."""
        effective_body = body.strip() or state.last_summary or "No cached summary yet."
        effective_subject = subject.strip() or "AgentGuard demo summary"
        return _mock_email_draft_result(to, effective_subject, effective_body)

    @tool("http.post")
    def http_post(url: str, payload: str = "") -> str:
        """Post the latest cached summary to a webhook."""
        effective_payload = payload.strip() or state.last_summary or "No cached summary yet."
        return _mock_http_result(url, effective_payload)

    @tool("shell.exec")
    def shell_exec(cmd: str) -> str:
        """Run a shell command in the demo environment."""
        return _mock_shell_result(cmd)

    llm = ChatOpenAI(
        api_key=config.api_key,
        base_url=config.base_url,
        model=config.model,
        temperature=config.temperature,
        timeout=config.timeout_s,
    )
    agent = create_agent(
        model=llm,
        tools=[
            mail_fetch,
            web_fetch,
            email_send,
            email_send_to_draft,
            http_post,
            shell_exec,
        ],
        system_prompt=_build_system_prompt(),
    )
    return agent


def _last_message_text(result: dict[str, Any]) -> str:
    messages = result.get("messages", [])
    for message in reversed(messages):
        content = getattr(message, "content", "")
        if isinstance(content, str) and content.strip():
            return content
    return "<no messages>"


def main() -> None:
    config = resolve_llm_config()
    runtime_url = os.environ.get(ENV_REMOTE_URL, "").strip() or f"http://127.0.0.1:{DEMO_PORT}"
    state = DemoState()
    agent = _build_agent(config, state)

    principal = Principal(
        agent_id="langchain-complete-demo",
        session_id=f"langchain-complete-{uuid.uuid4().hex[:8]}",
        role="default",
        trust_level=3,
    )
    health = RemoteGuardClient(
        runtime_url,
        api_key=RUNTIME_API_KEY,
        fail_open=False,
    ).health()

    guard = Guard(
        remote_url=runtime_url,
        api_key=RUNTIME_API_KEY,
        mode="enforce",
        fail_open=False,
    )
    guard.start(
        principal=principal,
        goal="complete langchain remote demo",
        scope=["demo:langchain", "demo:complete"],
    )
    guard.attach_langchain(agent)
    try:
        print(startup_banner(config, runtime_url), end="")
        if health.get("ok"):
            print(
                f"health: rules={health.get('rules', '?')} "
                f"mode={health.get('mode', '?')} "
                f"runtime_mode={health.get('runtime_mode', '?')}"
            )
        else:
            print(f"health: unavailable ({health.get('error', 'unknown error')})")

        while True:
            try:
                user_text = input("user> ").strip()
            except EOFError:
                print()
                break

            if not user_text:
                continue
            if user_text.lower() in {"exit", "quit"}:
                break
            if user_text.lower() in {"help", "?"}:
                print(startup_banner(config, runtime_url), end="")
                continue

            intent = infer_demo_intent(user_text)
            state.append_user(user_text)
            try:
                result = agent.invoke({"messages": list(state.transcript)})
                reply = _last_message_text(result)
                state.append_assistant(reply)
                print(f"assistant> {reply}")
            except DecisionDenied as exc:
                denial = f"blocked by guard: {exc.reason}"
                state.append_assistant(denial)
                print(f"assistant> {denial}")
            except HumanApprovalPending as exc:
                pending = f"waiting for human approval: {exc.reason} (ticket={exc.ticket_id})"
                state.append_assistant(pending)
                print(f"assistant> {pending}")
            except Exception as exc:  # pragma: no cover - interactive fallback
                failure = f"demo execution failed: {type(exc).__name__}: {exc}"
                state.append_assistant(failure)
                print(f"assistant> {failure}")

            if os.environ.get("AGENTGUARD_DEBUG_HINTS") == "1":
                print(f"[hint] {intent.tool_name}: {intent.reason}")
    finally:
        guard.close()
        if runtime_handle is not None:
            runtime_handle.stop()


if __name__ == "__main__":
    main()

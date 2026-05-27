#!/usr/bin/env python3
"""
Interactive AgentGuard x LangChain demo in remote-runtime style.

Default behavior:
  - start a local AgentGuard runtime in a background thread
  - connect a LangChain agent to that runtime over HTTP
  - keep a multi-turn CLI loop with mock model planning and mock tools

External-runtime behavior:
  - if AGENTGUARD_REMOTE_URL is set, reuse that runtime instead

Run:
    PYTHONPATH=. python agentguard/examples/langchain_demo/demo_multiturn_remote.py

Optional env vars:
    AGENTGUARD_REMOTE_URL=http://127.0.0.1:38080
    AGENTGUARD_API_KEY=demo-secret
    AGENTGUARD_DEMO_PORT=18082
"""

from __future__ import annotations

from dataclasses import dataclass, field
import os
import re
import uuid
from typing import Any, Callable

from agentguard import DecisionDenied, Guard, Principal
from agentguard.models.errors import HumanApprovalPending
from agentguard.runtime.server import AgentGuardServer
from agentguard.sdk.client import RemoteGuardClient


API_KEY = os.environ.get("AGENTGUARD_API_KEY", "demo-secret").strip() or "demo-secret"
EXTERNAL_REMOTE_URL = os.environ.get("AGENTGUARD_REMOTE_URL", "").strip()
DEMO_PORT = int(os.environ.get("AGENTGUARD_DEMO_PORT", "18082"))

SERVER_POLICY = """
RULE deny_destructive_shell
ON tool_call(shell.exec)
IF args.cmd == "rm -rf /"
THEN DENY

RULE review_external_email
ON tool_call(email.send)
IF principal.trust_level < 2
THEN HUMAN_CHECK

RULE degrade_email_low_trust
ON tool_call(email.send)
IF principal.trust_level < 3
THEN DEGRADE(email.send_to_draft)

RULE review_external_http
ON tool_call(http.post)
IF target.domain != "internal.corp"
THEN HUMAN_CHECK
"""


try:
    from langchain.agents import create_agent
    from langchain.tools import tool
    from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
    from langchain_core.messages import AIMessage, ToolCall
except ImportError as exc:  # pragma: no cover - demo-only dependency check
    raise SystemExit(
        "This demo requires LangChain. Install with: pip install langchain langgraph"
    ) from exc


@dataclass
class DemoState:
    transcript: list[dict[str, str]] = field(default_factory=list)
    last_company: str = "ACME"
    last_summary: str = ""
    last_rows: list[dict[str, str]] = field(default_factory=list)

    def append_user(self, text: str) -> None:
        self.transcript.append({"role": "user", "content": text})

    def append_assistant(self, text: str) -> None:
        self.transcript.append({"role": "assistant", "content": text})


@dataclass
class TurnPlan:
    mode: str
    tool_name: str | None
    tool_args: dict[str, Any]
    preview: str
    final_reply: str
    on_success: Callable[[DemoState], None] | None = None


def _mock_lookup(company: str, period: str) -> tuple[str, list[dict[str, str]]]:
    rows = [
        {"order_id": "PO-1042", "amount": "182000", "owner": "procurement"},
        {"order_id": "PO-1048", "amount": "64500", "owner": "hardware"},
        {"order_id": "PO-1056", "amount": "97300", "owner": "overseas-ops"},
    ]
    summary = (
        f"{company} has 3 mock orders for {period}; "
        "the largest amount is 182000 and one order is tagged overseas-ops."
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


def _extract_company(text: str, fallback: str) -> str:
    match = re.search(r"\b[A-Z][A-Z0-9-]{1,10}\b", text)
    return match.group(0) if match else fallback


def _extract_email(text: str, fallback: str) -> str:
    match = re.search(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", text, re.I)
    return match.group(0) if match else fallback


def _extract_url(text: str, fallback: str) -> str:
    match = re.search(r"https?://\S+", text)
    return match.group(0) if match else fallback


def plan_turn(user_text: str, state: DemoState) -> TurnPlan:
    lower = user_text.lower()

    if any(token in lower for token in ("hello", "hi", "help", "what can you do")):
        return TurnPlan(
            mode="direct",
            tool_name=None,
            tool_args={},
            preview="Answer directly without a tool.",
            final_reply=(
                "I can do a multi-turn guarded workflow. Try one of these:\n"
                "- look up ACME orders\n"
                "- send the summary to partner@example.com\n"
                "- post it to https://hooks.example.local/demo\n"
                "- run rm -rf /"
            ),
        )

    if "webhook" in lower or "http" in lower or "post " in lower:
        url = _extract_url(user_text, "https://hooks.example.local/demo")
        payload = state.last_summary or "No cached summary yet."
        return TurnPlan(
            mode="tool",
            tool_name="http.post",
            tool_args={"url": url, "payload": payload},
            preview=f"Use http.post to sync the latest summary to {url}.",
            final_reply=(
                f"I completed the mock webhook flow for {url}. "
                "The tool execution is mocked, but the guard decision is real."
            ),
        )

    if "mail" in lower or "email" in lower or "send" in lower:
        recipient = _extract_email(user_text, "partner@example.com")
        subject = f"{state.last_company} order summary"
        body = state.last_summary or "No cached summary yet. Ask me to look up orders first."
        return TurnPlan(
            mode="tool",
            tool_name="email.send",
            tool_args={"to": recipient, "subject": subject, "body": body},
            preview=f"Use email.send to deliver the current summary to {recipient}.",
            final_reply=(
                f"I completed the mock email flow for {recipient}. "
                "If the runtime rewrites email.send, this demo will follow that rewrite."
            ),
        )

    if "shell" in lower or "rm -rf" in lower or "command" in lower:
        cmd_match = re.search(r"(rm -rf /|ls\b.*|pwd\b.*|dir\b.*)", user_text, re.I)
        cmd = cmd_match.group(1) if cmd_match else "rm -rf /"
        return TurnPlan(
            mode="tool",
            tool_name="shell.exec",
            tool_args={"cmd": cmd},
            preview=f"Use shell.exec with command: {cmd}",
            final_reply="The mock shell tool ran. High-risk shell commands are useful for validating guard policy.",
        )

    company = _extract_company(user_text, state.last_company)
    summary, rows = _mock_lookup(company, "last_week")

    def _store_lookup(s: DemoState) -> None:
        s.last_company = company
        s.last_summary = summary
        s.last_rows = rows

    return TurnPlan(
        mode="tool",
        tool_name="erp.orders.lookup",
        tool_args={"company": company, "period": "last_week"},
        preview=f"Use erp.orders.lookup to fetch recent orders for {company}.",
        final_reply=(
            f"{summary}\n"
            "I cached this summary in the current session, so the next turn can say "
            "'send it by email' or 'post it to a webhook'."
        ),
        on_success=_store_lookup,
    )


def _messages_for_plan(plan: TurnPlan) -> list[AIMessage]:
    if plan.mode == "direct" or plan.tool_name is None:
        return [AIMessage(content=plan.final_reply)]

    return [
        AIMessage(
            content=plan.preview,
            tool_calls=[
                ToolCall(
                    name=plan.tool_name,
                    args=plan.tool_args,
                    id=f"call_{uuid.uuid4().hex[:8]}",
                )
            ],
        ),
        AIMessage(content=plan.final_reply),
    ]


class PlannedFakeToolModel(FakeMessagesListChatModel):
    """One mutable fake model backing one long-lived LangChain agent."""

    def bind_tools(self, tools, *, tool_choice=None, **kwargs):  # type: ignore[no-untyped-def]
        return self

    def set_plan(self, plan: TurnPlan) -> None:
        self.responses = _messages_for_plan(plan)
        self.i = 0


def _build_agent(guard: Guard, model: PlannedFakeToolModel):
    @tool("erp.orders.lookup")
    def erp_orders_lookup(company: str, period: str) -> str:
        """Look up mock ERP order records for a company."""
        summary, _ = _mock_lookup(company, period)
        return summary

    @tool("email.send")
    def email_send(to: str, subject: str, body: str) -> str:
        """Send a mock outbound email."""
        return _mock_email_result(to, subject, body)

    @tool("email.send_to_draft")
    def email_send_to_draft(to: str, subject: str, body: str) -> str:
        """Save a mock outbound email as a draft."""
        return _mock_email_draft_result(to, subject, body)

    @tool("http.post")
    def http_post(url: str, payload: str) -> str:
        """Post a mock payload to an external webhook."""
        return _mock_http_result(url, payload)

    @tool("shell.exec")
    def shell_exec(cmd: str) -> str:
        """Execute a mock shell command."""
        return _mock_shell_result(cmd)

    agent = create_agent(
        model=model,
        tools=[erp_orders_lookup, email_send, email_send_to_draft, http_post, shell_exec],
        system_prompt=(
            "You are a demo assistant. Use tools when the scripted plan asks you to, "
            "and answer directly otherwise."
        ),
    )
    guard.attach_langchain(agent)
    return agent


def _last_message_text(result: dict[str, Any]) -> str:
    messages = result.get("messages", [])
    if not messages:
        return "<no messages>"
    return str(getattr(messages[-1], "content", ""))


def _print_banner(principal: Principal, runtime_url: str, health: dict[str, Any], *, started_local: bool) -> None:
    print("AgentGuard x LangChain multi-turn remote demo")
    print(f"runtime: {runtime_url}")
    print(f"session: {principal.session_id}")
    print(f"runtime_mode: {'self-hosted demo runtime' if started_local else 'external runtime'}")
    if health.get("ok"):
        print(
            "health:",
            f"rules={health.get('rules', '?')}",
            f"mode={health.get('mode', '?')}",
            f"runtime_mode={health.get('runtime_mode', '?')}",
        )
    else:
        print(f"health: unavailable ({health.get('error', 'unknown error')})")
    print()
    print("Try:")
    print("  - look up ACME orders")
    print("  - send the summary to partner@example.com")
    print("  - post it to https://hooks.example.local/demo")
    print("  - run rm -rf /")
    print("Type 'exit' to quit.")
    print()


def _start_runtime_if_needed() -> tuple[str, Any | None]:
    if EXTERNAL_REMOTE_URL:
        return EXTERNAL_REMOTE_URL.rstrip("/"), None

    server = AgentGuardServer.from_policy(
        policy_source=SERVER_POLICY,
        builtin_rules=False,
        mode="enforce",
        api_key=API_KEY,
    )
    try:
        handle = server.serve_in_thread(host="127.0.0.1", port=DEMO_PORT)
    except ImportError as exc:  # pragma: no cover - environment dependent
        raise SystemExit(
            "This demo needs server extras. Install with: pip install -e \".[server]\""
        ) from exc
    return f"http://127.0.0.1:{DEMO_PORT}", handle


def main() -> None:
    principal = Principal(
        agent_id="langchain-multiturn-demo",
        session_id=f"langchain-session-{uuid.uuid4().hex[:8]}",
        role="default",
        trust_level=1,
    )
    state = DemoState()
    runtime_url, handle = _start_runtime_if_needed()
    started_local = handle is not None

    health = RemoteGuardClient(
        runtime_url,
        api_key=API_KEY,
        fail_open=False,
    ).health()
    _print_banner(principal, runtime_url, health, started_local=started_local)

    guard = Guard(
        remote_url=runtime_url,
        api_key=API_KEY,
        mode="enforce",
        fail_open=False,
    )

    try:
        with guard.session(
            principal=principal,
            goal="interactive langchain multi-turn demo",
            scope=["demo:langchain", "demo:multiturn"],
        ):
            model = PlannedFakeToolModel(
                responses=_messages_for_plan(
                    TurnPlan(
                        mode="direct",
                        tool_name=None,
                        tool_args={},
                        preview="Bootstrap tool registration.",
                        final_reply="Bootstrap tool registration.",
                    ),
                )
            )
            agent = _build_agent(guard, model)

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

                state.append_user(user_text)
                plan = plan_turn(user_text, state)
                model.set_plan(plan)

                try:
                    result = agent.invoke({"messages": list(state.transcript)})
                    reply = _last_message_text(result)
                    if plan.on_success is not None:
                        plan.on_success(state)
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
                except Exception as exc:
                    failure = f"demo execution failed: {type(exc).__name__}: {exc}"
                    state.append_assistant(failure)
                    print(f"assistant> {failure}")
    finally:
        guard.close()
        if handle is not None:
            handle.stop()


if __name__ == "__main__":
    main()

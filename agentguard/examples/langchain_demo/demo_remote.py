#!/usr/bin/env python3
"""
AgentGuard x LangChain runnable adapter demo in remote-runtime mode.

This variant starts a local AgentGuard runtime server in a background thread,
then connects the LangChain agent process to it via ``Guard(remote_url=...)``.

Requirements:
    pip install langchain langgraph
    pip install -e ".[server]"

Run:
    PYTHONPATH=. python agentguard/examples/langchain_demo/demo_remote.py
"""

from __future__ import annotations

from agentguard import DecisionDenied, Guard, Principal
from agentguard.runtime.server import AgentGuardServer
from agentguard.sdk.client import RemoteGuardClient

from langchain.agents import create_agent
from langchain.tools import tool
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, ToolCall


SERVER_POLICY = """
RULE deny_shell_runtime
ON tool_call(shell.exec)
IF principal.trust_level < 2
THEN DENY
"""


def shell_exec(cmd: str) -> str:
    """Mock shell tool used by the agent."""
    return f"[mock-shell] executed: {cmd}"


def search_docs(query: str) -> str:
    """Mock search tool used by the agent."""
    return f"[mock-search] result for: {query}"


def _build_tool_calling_agent():

    class FakeToolCallingModel(FakeMessagesListChatModel):
        def bind_tools(self, tools, *, tool_choice=None, **kwargs):
            return self

    @tool("shell.exec")
    def shell_tool(cmd: str) -> str:
        """Execute a shell command."""
        return shell_exec(cmd)

    @tool("docs.search")
    def docs_tool(query: str) -> str:
        """Search internal docs."""
        return search_docs(query)

    model = FakeToolCallingModel(
        responses=[
            AIMessage(
                content="I will use a tool.",
                tool_calls=[
                    ToolCall(
                        name="shell.exec",
                        args={"cmd": "rm -rf /"},
                        id="call_shell_1",
                    )
                ],
            ),
            AIMessage(content="Tool completed."),
        ]
    )

    return create_agent(
        model=model,
        tools=[shell_tool, docs_tool],
        system_prompt="You are a helpful assistant.",
    )


def _build_direct_answer_agent():

    model = FakeMessagesListChatModel(
        responses=[
            AIMessage(content="No tool is needed for this request."),
        ]
    )

    return create_agent(
        model=model,
        tools=[],
        system_prompt="Answer directly when no tool is required.",
    )


def _run_case(label: str, runner) -> None:
    print(f"\n[{label}]")
    try:
        result = runner()
        print(f"  allow => {result}")
    except DecisionDenied as exc:
        print(f"  deny  => {exc.reason}")


def _last_message_text(result: dict) -> str:
    messages = result.get("messages", [])
    if not messages:
        return "<no messages>"
    message = messages[-1]
    content = getattr(message, "content", "")
    return str(content)


def main() -> None:
    server = AgentGuardServer.from_policy(
        policy_source=SERVER_POLICY,
        builtin_rules=False,
        mode="enforce",
        api_key="demo-secret",
    )
    try:
        handle = server.serve_in_thread(host="127.0.0.1", port=18082)
    except ImportError as e:
        raise SystemExit(
            "Remote demo requires server extras. Install with: pip install -e \".[server]\""
        ) from e

    try:
        client = RemoteGuardClient("http://127.0.0.1:18082", api_key="demo-secret")
        health = client.health()
        print(
            "Remote runtime ready:",
            "url=http://127.0.0.1:18082",
            f"rules={health.get('rules', '?')}",
            f"mode={health.get('mode', 'enforce')}",
        )

        principal = Principal(
            agent_id="langchain-remote-demo",
            session_id="langchain-remote-session",
            role="default",
            trust_level=1,
        )

        direct_agent = _build_direct_answer_agent()
        guarded_agent = _build_tool_calling_agent()

        guard = Guard(
            remote_url="http://127.0.0.1:18082",
            api_key="demo-secret",
            mode="enforce",
            fail_open=False,
        )
        guard.attach_langchain(direct_agent)
        guard.attach_langchain(guarded_agent)

        # ── imperative session API: no `with` block required ─────────────
        # guard.start() sets the session context; guard.close() ends it
        # and releases all resources.  Typical for long-running agent loops:
        #
        #   guard.start(principal=p, goal="...")
        #   try:
        #       while True:
        #           task = queue.get()
        #           if task is None: break
        #           agent.run(task)
        #   finally:
        #       guard.close()
        # ──────────────────────────────────────────────────────────────────
        guard.start(principal=principal, goal="langchain remote runnable host demo")
        try:
            _run_case(
                "direct answer without tools",
                lambda: _last_message_text(
                    direct_agent.invoke(
                        {"messages": [{"role": "user", "content": "Say hello directly."}]}
                    )
                ),
            )
            _run_case(
                "tool runtime blocked by remote guard",
                lambda: _last_message_text(
                    guarded_agent.invoke(
                        {"messages": [{"role": "user", "content": "Run a shell command."}]}
                    )
                ),
            )
        finally:
            guard.close()

        print("\nLangChain remote demo done.")
    finally:
        handle.stop()


if __name__ == "__main__":
    main()

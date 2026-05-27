#!/usr/bin/env python3
"""
AgentGuard x LangChain runnable adapter demo
============================================
This example uses real LangChain agent APIs with a fake chat model and mock
tool functions. It is intended to validate the adapter shape against the
official graph-based agent runtime created by ``langchain.agents.create_agent``.

Requirements when you want to run it elsewhere:
    pip install langchain langgraph

Run:
    PYTHONPATH=. python agentguard/examples/langchain_demo/demo.py
"""

from __future__ import annotations

from agentguard import DecisionDenied, Guard, Principal

from langchain.agents import create_agent
from langchain.tools import tool
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, ToolCall


POLICY = """
RULE: deny-shell-low-trust
ON:        tool_call.requested(shell.exec)
CONDITION: principal.trust_level < 2
POLICY:    DENY
Severity:  high
Category:  shell_safety
Reason:    "Shell execution is not allowed for low-trust agents"
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
    principal = Principal(
        agent_id="langchain-demo",
        session_id="langchain-session",
        role="default",
        trust_level=1,
    )

    direct_agent = _build_direct_answer_agent()
    guarded_agent = _build_tool_calling_agent()

    guard = Guard(policy_source=POLICY, builtin_rules=False, mode="enforce")
    guard.attach_langchain(direct_agent)
    guard.attach_langchain(guarded_agent)

    guard.start(principal=principal, goal="langchain in-process demo")
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
            "tool runtime blocked at runnable layer",
            lambda: _last_message_text(
                guarded_agent.invoke(
                    {"messages": [{"role": "user", "content": "Run a shell command."}]}
                )
            ),
        )
    finally:
        guard.close()
    print("\nLangChain demo done.")


if __name__ == "__main__":
    main()

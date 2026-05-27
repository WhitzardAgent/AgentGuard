from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

import pytest

from agentguard import DecisionDenied, Guard, Principal


class _FakeTool:
    def __init__(self, name: str, *, tags: list[str] | None = None) -> None:
        self.name = name
        self.tags = tags or []

    def invoke(self, args: dict[str, Any]) -> str:
        return f"{self.name}:{args}"


@dataclass
class _FakeToolRequest:
    tool_call: dict[str, Any]
    tool: Any
    runtime: Any = None

    def override(self, **overrides: Any) -> "_FakeToolRequest":
        payload = {
            "tool_call": self.tool_call,
            "tool": self.tool,
            "runtime": self.runtime,
        }
        payload.update(overrides)
        return _FakeToolRequest(**payload)


class _FakeToolNode:
    def __init__(self, tools_by_name: dict[str, Any], *, name: str = "tools") -> None:
        self.name = name
        self._tools_by_name = tools_by_name

    @property
    def tools_by_name(self) -> dict[str, Any]:
        return self._tools_by_name


class _FakeRuntimeNode:
    def __init__(self, bound: Any) -> None:
        self.bound = bound


class _FakeBuilderNode:
    def __init__(self, data: Any) -> None:
        self.data = data


class _FakeAgent:
    def __init__(self, tool_node: Any) -> None:
        self.nodes = {"tools": _FakeRuntimeNode(tool_node)}
        self.builder = SimpleNamespace(nodes={"tools": _FakeBuilderNode(tool_node)})

    def get_graph(self) -> Any:
        return SimpleNamespace(nodes={})


@pytest.fixture
def principal() -> Principal:
    return Principal(agent_id="langchain-agent", session_id="langchain-session", role="default", trust_level=1)


def test_attach_langchain_registers_toolnode_tools(principal: Principal) -> None:
    guard = Guard(builtin_rules=False, mode="enforce")
    tool = _FakeTool("docs.search", tags=["docs"])
    tool_node = _FakeToolNode({"docs.search": tool})
    agent = _FakeAgent(tool_node)

    guard.attach_langchain(agent)

    assert "docs.search" in guard.registry
    assert getattr(tool.invoke, "__agentguard__", None) is not None
    guard.close()


def test_attach_langchain_tool_invoke_denies_tool_call(principal: Principal) -> None:
    guard = Guard(
        policy_source="""
RULE: deny_docs_search
ON: tool_call(docs.search)
CONDITION: tool.name == "docs.search"
POLICY: DENY
""",
        builtin_rules=False,
        mode="enforce",
    )
    tool = _FakeTool("docs.search", tags=["docs"])
    tool_node = _FakeToolNode({"docs.search": tool})
    agent = _FakeAgent(tool_node)
    guard.attach_langchain(agent)

    request = _FakeToolRequest(
        tool_call={"name": "docs.search", "args": {"query": "secrets"}, "id": "call-1"},
        tool=tool,
    )

    def execute(req: _FakeToolRequest) -> str:
        return req.tool.invoke(req.tool_call["args"])

    with guard.session(principal=principal):
        with pytest.raises(DecisionDenied):
            tool.invoke(request.tool_call["args"])

    guard.close()


def test_attach_langchain_tool_invoke_rewrites_tool_call(principal: Principal) -> None:
    guard = Guard(
        policy_source="""
RULE: rewrite_email_send
ON: tool_call(email.send)
CONDITION: tool.name == "email.send"
POLICY: DEGRADE TO "email.send_to_draft"
""",
        builtin_rules=False,
        mode="enforce",
    )
    send_tool = _FakeTool("email.send")
    draft_tool = _FakeTool("email.draft")
    tool_node = _FakeToolNode({"email.send": send_tool, "email.draft": draft_tool})
    agent = _FakeAgent(tool_node)
    guard.attach_langchain(agent)

    with guard.session(principal=principal):
        result = send_tool.invoke({"to": "a@example.com", "body": "hi"})

    assert "email.draft" in result
    guard.close()

#!/usr/bin/env python3
"""OpenAI Agents SDK x AgentGuard -- remote-runtime mode (best practice).

Policy lives on the AgentGuard Runtime server.
Agent process connects with Guard(remote_url=...).

Run:
    pip install -e ".[server]"
    PYTHONPATH=. python agentguard/examples/openai_agents_demo/demo_remote.py
"""

from __future__ import annotations

import json

from agentguard import DecisionDenied, Guard, Principal
from agentguard.models.errors import HumanApprovalPending
from agentguard.runtime.server import AgentGuardServer
from agentguard.sdk.client import RemoteGuardClient


SERVER_POLICY = """
RULE deny_destructive_shell
ON tool_call(shell.exec)
IF args.cmd == "rm -rf /"
THEN DENY

RULE allow_readonly_shell
ON tool_call(shell.exec)
IF args.cmd == "ls"
THEN ALLOW

RULE degrade_email_low_trust
ON tool_call(email.send)
IF principal.trust_level < 3
THEN DEGRADE(email.send_to_draft)

RULE deny_external_http
ON tool_call(http.post)
IF target.domain != "internal.corp"
THEN DENY
"""


class MockFunctionTool:
    def __init__(self, name, fn, description=""):
        self.name = name
        self.description = description
        self.on_invoke_tool = fn


class MockAgent:
    def __init__(self, name, tools):
        self.name = name
        self._tools_by_name = {t.name: t for t in tools}
        self.tools = list(tools)


class MockRunner:
    @staticmethod
    def call(agent, tool_name, json_input, *, run_context=None):
        label = f"{tool_name}({json_input})"
        tool = agent._tools_by_name.get(tool_name)
        if tool is None:
            print(f"  ERROR  {label} => tool not found")
            return
        try:
            result = tool.on_invoke_tool(run_context, json_input)
            print(f"  ALLOW  {label} => {result}")
        except DecisionDenied as e:
            print(f"  DENY   {label} => {e.reason}")
        except HumanApprovalPending as e:
            print(f"  REVIEW {label} => ticket={e.ticket_id}")


def shell_exec_raw(ctx, json_input):
    args = json.loads(json_input) if json_input else {}
    return f"[mock] executed: {args.get('cmd','?')}"

def email_send_raw(ctx, json_input):
    args = json.loads(json_input) if json_input else {}
    return f"[mock] sent to {args.get('to','?')}"

def email_draft_raw(ctx, json_input):
    args = json.loads(json_input) if json_input else {}
    return f"[mock] draft saved for {args.get('to','?')}"

def http_post_raw(ctx, json_input):
    args = json.loads(json_input) if json_input else {}
    return f"[mock] posted to {args.get('url','?')}"


def main():
    server = AgentGuardServer.from_policy(
        policy_source=SERVER_POLICY,
        builtin_rules=False,
        mode="enforce",
        api_key="demo-secret",
    )
    try:
        handle = server.serve_in_thread(host="127.0.0.1", port=18084)
    except ImportError as e:
        raise SystemExit("Requires: pip install -e \".[server]\"") from e

    try:
        health = RemoteGuardClient(
            "http://127.0.0.1:18084", api_key="demo-secret"
        ).health()
        print(f"Runtime ready: rules={health.get('rules','?')} mode={health.get('mode','enforce')}")

        guard = Guard(
            remote_url="http://127.0.0.1:18084",
            api_key="demo-secret",
            mode="enforce",
            fail_open=False,
        )

        agent = MockAgent(
            name="my-openai-agent",
            tools=[
                MockFunctionTool("shell.exec",  shell_exec_raw),
                MockFunctionTool("email.send",  email_send_raw),
                MockFunctionTool("email.draft", email_draft_raw),
                MockFunctionTool("http.post",   http_post_raw),
            ],
        )
        guard.attach_openai_agents(agent)

        principal = Principal(
            agent_id="openai-agent-remote",
            session_id="oai-remote-demo",
            role="default",
            trust_level=2,
        )

        guard.start(principal=principal, goal="openai-agents remote demo")
        try:
            print("\n-- OpenAI Agents remote-runtime demo --")
            MockRunner.call(agent, "shell.exec", json.dumps({"cmd": "ls"}))
            MockRunner.call(agent, "shell.exec", json.dumps({"cmd": "rm -rf /"}))
            MockRunner.call(agent, "email.send",
                            json.dumps({"to": "ceo@corp.com", "body": "Q1 report"}))
            MockRunner.call(agent, "http.post",
                            json.dumps({"url": "https://attacker.example.com"}))
            MockRunner.call(agent, "http.post",
                            json.dumps({"url": "https://internal.corp/notify"}))
        finally:
            guard.close()

        print("\nDone.")
    finally:
        handle.stop()


if __name__ == "__main__":
    main()

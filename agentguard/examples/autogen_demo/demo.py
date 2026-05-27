#!/usr/bin/env python3
"""AutoGen × AgentGuard — 同进程模式 (in-process best practice).

模拟一个 AutoGen 风格 Agent，使用 ``guard.start()`` / ``guard.close()``
命令式 Session API（适合外层已有 while-loop / 任务队列的场景）。

无需真实 AutoGen 依赖，直接运行：
    PYTHONPATH=. python agentguard/examples/autogen_demo/demo.py
"""

from __future__ import annotations

from agentguard import DecisionDenied, Guard, Principal
from agentguard.models.errors import HumanApprovalPending


POLICY = """
# Hard-deny the classic wipe command.
RULE: deny-destructive-shell
ON:        tool_call(shell.exec)
CONDITION: args.cmd == "rm -rf /"
POLICY:    DENY
Severity:  critical
Category:  shell_safety

# Read-only shell is always allowed.
RULE: allow-readonly-shell
ON:        tool_call(shell.exec)
CONDITION: args.cmd == "ls"
POLICY:    ALLOW

# Outbound email from low-trust agents goes to draft.
RULE: degrade-email-low-trust
ON:        tool_call(email.send)
CONDITION: principal.trust_level < 3
POLICY:    DEGRADE(email.send_to_draft)
Severity:  medium
Category:  data_egress
"""


# ── Mock AutoGen-style agent ─────────────────────────────────────────────────

class MockAutoGenAgent:
    """Simulates AutoGen ConversableAgent (function_map style)."""

    def __init__(self) -> None:
        self.function_map: dict[str, object] = {}

    def register_function(self, fn, /, **kwargs):
        name = kwargs.get("name") or fn.__name__
        self.function_map[name] = fn

    def call_function(self, name: str, **kwargs):
        fn = self.function_map[name]
        return fn(**kwargs)


# ── Tool implementations ──────────────────────────────────────────────────────

def shell_exec(cmd: str) -> str:
    return f"[mock] executed: {cmd}"

def email_send(to: str, body: str) -> str:
    return f"[mock] sent to {to}"

def email_draft(to: str, body: str) -> str:
    return f"[mock] draft saved for {to}"


def _run(agent: MockAutoGenAgent, name: str, /, **kwargs) -> None:
    label = f"{name}({kwargs})"
    try:
        result = agent.call_function(name, **kwargs)
        print(f"  ALLOW  {label} => {result}")
    except DecisionDenied as e:
        print(f"  DENY   {label} => {e.reason}")
    except HumanApprovalPending as e:
        print(f"  REVIEW {label} => ticket={e.ticket_id}")


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    # 1. Build guard (in-process, custom policy, no builtin rules)
    guard = Guard(policy_source=POLICY, builtin_rules=False, mode="enforce")

    # 2. Build agent and register tools
    agent = MockAutoGenAgent()
    agent.register_function(shell_exec, name="shell.exec")
    agent.register_function(email_send, name="email.send")
    agent.register_function(email_draft, name="email.draft")

    # 3. Attach guard to agent (wraps function_map in-place)
    guard.attach_autogen(agent)

    principal = Principal(
        agent_id="autogen-agent",
        session_id="autogen-inprocess-demo",
        role="default",
        trust_level=2,
    )

    # 4. Imperative session API — typical for an outer agent loop
    guard.start(principal=principal, goal="autogen in-process demo")
    try:
        print("\n── AutoGen in-process demo ──────────────────────")
        _run(agent, "shell.exec", cmd="ls")            # ALLOW
        _run(agent, "shell.exec", cmd="rm -rf /")     # DENY
        _run(agent, "email.send", to="cto@corp.com",  # DEGRADE → draft
             body="Q1 report")
    finally:
        guard.close()   # end session + release resources

    print("\nDone.")


if __name__ == "__main__":
    main()

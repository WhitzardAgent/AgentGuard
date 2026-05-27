#!/usr/bin/env python3
"""AgentGuard Quickstart — in-process interception in 60 lines.

Demonstrates the three core enforcement actions:
  • DENY        — block the call outright
  • HUMAN_CHECK — pause and route to a human approval queue
  • DEGRADE     — redirect to a safer variant of the tool

Run:
    PYTHONPATH=. python agentguard/examples/quickstart.py
"""

from agentguard import Guard, Principal, DecisionDenied
from agentguard.models.errors import HumanApprovalPending
from agentguard.degrade.planner import EnforcerConfig

# ─────────────────────────────────────────────────────────────────────────────
# Policy (v3 DSL)
# ─────────────────────────────────────────────────────────────────────────────
POLICY = """
# Block destructive shell commands unconditionally.
RULE: deny-destructive-shell
ON:        tool_call(shell.exec)
CONDITION: args.cmd == "rm -rf /"
POLICY:    DENY
Severity:  critical
Category:  shell_safety

# Any shell call by a low-trust agent needs a human to approve it first.
RULE: review-shell-low-trust
ON:        tool_call.requested(shell.exec)
CONDITION: principal.trust_level < 3
POLICY:    HUMAN_CHECK
Severity:  high
Category:  shell_safety

# Low-trust agents can send email only as a draft — never directly.
RULE: degrade-email-low-trust
ON:        tool_call(email.send)
CONDITION: principal.trust_level < 3
POLICY:    DEGRADE(email.send_to_draft)
Severity:  medium
Category:  data_egress
"""

# ─────────────────────────────────────────────────────────────────────────────
# Guard setup
# ─────────────────────────────────────────────────────────────────────────────
guard = Guard(
    policy_source=POLICY,
    builtin_rules=False,   # use only the rules above
    mode="enforce",
    # HUMAN_CHECK times out quickly in demo mode; in production set approval_timeout_s=300+
    enforcer_config=EnforcerConfig(approval_timeout_s=0.5, on_timeout="deny"),
)


# ─────────────────────────────────────────────────────────────────────────────
# Tool registrations
# ─────────────────────────────────────────────────────────────────────────────
@guard.tool("shell.exec", sink_type="shell")
def shell_exec(cmd: str) -> str:
    return f"[shell] executed: {cmd}"


@guard.tool("email.send", sink_type="email")
def email_send(to: str, body: str) -> str:
    return f"[email] sent to {to}"


@guard.tool("email.send_to_draft", sink_type="none")
def email_draft(to: str, body: str = "", **_kw) -> str:
    return f"[email] saved draft for {to}"


# ─────────────────────────────────────────────────────────────────────────────
# Demo
# ─────────────────────────────────────────────────────────────────────────────
def run(label: str, fn, /, **kwargs) -> None:
    print(f"\n  ▶  {label}")
    try:
        result = fn(**kwargs)
        print(f"     ✅ ALLOW  — {result}")
    except DecisionDenied as e:
        print(f"     🚫 DENY   — {e.reason}  (rules: {e.matched_rules})")
    except HumanApprovalPending as e:
        print(f"     ⏸  HUMAN_CHECK — ticket={e.ticket_id[:12]}…")
    except Exception as e:
        print(f"     ⚠  {type(e).__name__}: {e}")


def main() -> None:
    principal = Principal(
        agent_id="demo-agent",
        session_id="qs-session-001",
        role="basic",
        trust_level=1,   # low trust → triggers HUMAN_CHECK + DEGRADE rules
    )

    with guard.session(principal=principal, goal="quickstart demo"):
        # Safe command, but low trust → HUMAN_CHECK → times out → DENY
        run("shell.exec('ls /tmp')", shell_exec, cmd="ls /tmp")

        # Destructive command — hard DENY (no trust check needed)
        run("shell.exec('rm -rf /')", shell_exec, cmd="rm -rf /")

        # Another shell call — again HUMAN_CHECK → DENY
        run("shell.exec('cat /etc/passwd')", shell_exec, cmd="cat /etc/passwd")

        # Send email — DEGRADE to draft (low trust); outer call returns ALLOW after rewrite
        run("email.send(to=ceo@corp.com)", email_send,
            to="ceo@corp.com", body="Q1 report attached")

    # ── Audit log ────────────────────────────────────────────────────
    records = guard.pipeline.audit.recent(20)
    print(f"\n{'─'*55}")
    print(f"  Audit log: {len(records)} records")
    print(f"{'─'*55}")
    for rec in records:
        ev  = rec["event"]
        dec = rec.get("decision") or {}
        tool   = ev.get("tool_call", {}).get("tool_name", "?")
        action = dec.get("action", "allow")
        rules  = dec.get("matched_rules") or []
        tag    = f"  [{', '.join(rules)}]" if rules else ""
        print(f"  {action:<12}  {tool}{tag}")

    guard.close()
    print(f"\n{'─'*55}")
    print("  Done.")


if __name__ == "__main__":
    main()

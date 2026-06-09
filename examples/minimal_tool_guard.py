"""Minimal example: wrap a tool and let AgentGuard enforce policy."""
from __future__ import annotations

import _bootstrap  # noqa: F401

from agentguard import AgentGuard


def read_notes(path: str) -> str:
    return f"notes from {path}"


def send_email(to: str, body: str) -> str:
    return f"email sent to {to}"


def main() -> None:
    guard = AgentGuard(session_id="demo", user_id="alice", policy="enterprise_default")
    safe_read = guard.wrap_tool(read_notes, capabilities=["read_file"])
    safe_send = guard.wrap_tool(send_email, capabilities=["external_send"])

    print("read   ->", safe_read("/tmp/notes.txt"))
    print("send   ->", safe_send("a@b.com", "hello, my key is sk-ABCD1234EFGH5678"))

    print("\naudit:")
    for rec in guard.flush_audit():
        print(f"  {rec['event_type']:<14} {rec['decision_type']:<12} {rec['reason']}")


if __name__ == "__main__":
    main()

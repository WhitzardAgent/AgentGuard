"""Local policy snapshot evaluation without any server."""
from __future__ import annotations

import _bootstrap  # noqa: F401

from agentguard.schemas import events as ev
from agentguard.schemas.context import RuntimeContext
from agentguard.u_guard.local_engine import LocalGuardEngine
from agentguard.u_guard.policy_snapshot import PolicySnapshot


def main() -> None:
    engine = LocalGuardEngine(PolicySnapshot.default())
    ctx = RuntimeContext(session_id="local")

    e1 = ev.tool_invoke(ctx, "read_file", {"path": "/tmp/a"}, capabilities=["read_file"])
    e2 = ev.tool_invoke(ctx, "run_shell", {"command": "rm -rf /"}, capabilities=["shell"])
    e2.add_signal("shell_command")

    for e in (e1, e2):
        result = engine.evaluate(e)
        print(f"{e.payload['tool_name']:<12} -> {result.decision.decision_type.value:<10} certain={result.certain}")


if __name__ == "__main__":
    main()

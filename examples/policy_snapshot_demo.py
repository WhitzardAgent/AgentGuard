"""Fetch a policy snapshot from the server and evaluate locally."""
from __future__ import annotations

import _bootstrap  # noqa: F401

from agentguard.schemas import events as ev
from agentguard.schemas.context import RuntimeContext
from agentguard.u_guard.local_engine import LocalGuardEngine
from agentguard.u_guard.policy_snapshot import PolicySnapshot
from agentguard.u_guard.remote_client import RemoteGuardClient
from backend.api.dev_server import start_dev_server


def main() -> None:
    base_url, server, _ = start_dev_server()
    try:
        client = RemoteGuardClient(base_url)
        raw = client.fetch_snapshot()
        snapshot = PolicySnapshot.from_dict(raw)
        print("snapshot version:", snapshot.version, "rules:", len(snapshot.rules))

        engine = LocalGuardEngine(snapshot)
        ctx = RuntimeContext(session_id="snap")
        e = ev.tool_invoke(ctx, "send_email", {"to": "x@y.com"}, capabilities=["external_send"])
        result = engine.evaluate(e)
        print("local decision:", result.decision.decision_type.value, "certain:", result.certain)
    finally:
        server.shutdown()


if __name__ == "__main__":
    main()

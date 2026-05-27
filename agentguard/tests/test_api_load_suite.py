"""API 并发与吞吐回归测试（ASGI in-process + 可选 TCP 集成）。

对运行中的 HTTP 服务做 RPS / 延迟分位数压测，请使用
``scripts/loadtest_evaluate.py``。

说明：部分 ``httpx`` 版本的 ``ASGITransport`` 不会触发 FastAPI lifespan，因此
``runtime_mode=async`` 的并发与 ``/metrics`` 断言通过真实 TCP（``serve_in_thread``）
完成；同步 Pipeline 仍用 in-process ASGI 压并发。
"""

from __future__ import annotations

import asyncio
import os
import socket
import time
from collections.abc import Awaitable, Callable

import pytest

from agentguard.sdk.guard import Guard
from agentguard.tests.conftest import build_event

pytest.importorskip("fastapi", reason="requires agentguard[server]")
pytest.importorskip("httpx", reason="requires httpx (agentguard[dev])")

import httpx  # noqa: E402
from httpx import ASGITransport  # noqa: E402

from agentguard.runtime.server import AgentGuardServer  # noqa: E402


ALLOW_DSL = """
RULE: allow_shell_ls
ON: tool_call(shell.exec)
CONDITION: args.cmd == "ls"
POLICY: ALLOW
"""


def _pick_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


async def _gather_limited(
    n: int,
    limit: int,
    factory: Callable[[int], Awaitable[tuple[int, dict]]],
) -> list[tuple[int, dict]]:
    """Run ``n`` async tasks with at most ``limit`` concurrent."""
    sem = asyncio.Semaphore(limit)
    results: list[tuple[int, dict]] = []

    async def run_one(i: int) -> None:
        async with sem:
            results.append(await factory(i))

    await asyncio.gather(*(run_one(i) for i in range(n)))
    return results


@pytest.mark.asyncio
@pytest.mark.load
async def test_concurrent_evaluate_asgi_sync_runtime() -> None:
    """同步 Pipeline：大量并发 POST /v1/evaluate 应全部 200 且决策一致。"""
    guard = Guard(policy_source=ALLOW_DSL, builtin_rules=False, mode="enforce")
    server = AgentGuardServer(guard, runtime_mode="sync")
    app = server.build_app()
    n = 160
    conc = 40

    async with httpx.AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        timeout=60.0,
    ) as client:

        async def one(i: int) -> tuple[int, dict]:
            ev = build_event(
                "shell.exec",
                args={"cmd": "ls"},
                session_id=f"load-sess-{i % 8}",
            )
            r = await client.post("/v1/evaluate", content=ev.model_dump_json())
            return r.status_code, r.json()

        pairs = await _gather_limited(n, conc, one)

    for status, body in pairs:
        assert status == 200
        assert body.get("ok") is True
        assert body["decision"]["action"] == "allow"
    guard.close()


@pytest.mark.asyncio
@pytest.mark.integration
@pytest.mark.load
async def test_live_tcp_concurrent_evaluate_async_runtime() -> None:
    """异步 Actor + uvicorn：并发 evaluate 后 /metrics 含决策计数。"""
    port = _pick_free_port()
    guard = Guard(policy_source=ALLOW_DSL, builtin_rules=False, mode="enforce")
    ag_server = AgentGuardServer(guard, runtime_mode="async")
    handle = ag_server.serve_in_thread(host="127.0.0.1", port=port)
    n = 150
    conc = 40
    try:
        async with httpx.AsyncClient(base_url=handle.base_url, timeout=120.0) as client:

            async def one(i: int) -> tuple[int, dict]:
                ev = build_event(
                    "shell.exec",
                    args={"cmd": "ls"},
                    session_id=f"async-tcp-{i % 10}",
                )
                r = await client.post("/v1/evaluate", content=ev.model_dump_json())
                return r.status_code, r.json()

            pairs = await _gather_limited(n, conc, one)
            mr = await client.get("/metrics")

        for status, body in pairs:
            assert status == 200
            assert body.get("ok") is True
            assert body["decision"]["action"] == "allow"

        assert mr.status_code == 200
        mj = mr.json()
        assert mj.get("runtime_mode") == "async"
        assert mj.get("metrics") is not None
        assert mj["metrics"]["decisions"]["total"] >= n
    finally:
        handle.stop()
        guard.close()


@pytest.mark.asyncio
@pytest.mark.load
async def test_concurrent_batch_evaluate_asgi() -> None:
    """batch 端点在并发下仍应返回完整 results 列表。"""
    import json as _json

    guard = Guard(policy_source=ALLOW_DSL, builtin_rules=False, mode="enforce")
    app = AgentGuardServer(guard, runtime_mode="sync").build_app()
    n_req = 24
    conc = 8

    async with httpx.AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        timeout=60.0,
    ) as client:

        async def batch_once(i: int) -> tuple[int, dict]:
            ev = build_event("shell.exec", args={"cmd": "ls"}, session_id=f"batch-{i}")
            payload = _json.dumps(
                {"events": [ev.model_dump(mode="json"), ev.model_dump(mode="json")]}
            )
            r = await client.post(
                "/v1/evaluate/batch",
                content=payload,
                headers={"content-type": "application/json"},
            )
            return r.status_code, r.json()

        pairs = await _gather_limited(n_req, conc, batch_once)

    for status, body in pairs:
        assert status == 200
        assert len(body["results"]) == 2
        assert all(r["ok"] for r in body["results"])
    guard.close()


@pytest.mark.asyncio
@pytest.mark.integration
@pytest.mark.load
async def test_live_tcp_concurrent_evaluate_sync() -> None:
    """真实 TCP：验证 uvicorn 线程 + 异步 httpx 客户端下的承载与延迟分布。"""
    port = _pick_free_port()
    guard = Guard(policy_source=ALLOW_DSL, builtin_rules=False, mode="enforce")
    ag_server = AgentGuardServer(guard, runtime_mode="sync")
    handle = ag_server.serve_in_thread(host="127.0.0.1", port=port)
    n = 400
    conc = 50
    lat_ms: list[float] = []

    try:
        async with httpx.AsyncClient(
            base_url=handle.base_url,
            timeout=120.0,
        ) as client:

            async def one(i: int) -> tuple[int, float]:
                t0 = time.perf_counter()
                ev = build_event(
                    "shell.exec",
                    args={"cmd": "ls"},
                    session_id=f"tcp-{i % 16}",
                )
                r = await client.post("/v1/evaluate", content=ev.model_dump_json())
                dt = (time.perf_counter() - t0) * 1000.0
                return r.status_code, dt

            sem = asyncio.Semaphore(conc)
            errors: list[int] = []

            async def wrapped(i: int) -> None:
                async with sem:
                    code, dt = await one(i)
                    lat_ms.append(dt)
                    if code != 200:
                        errors.append(code)

            await asyncio.gather(*(wrapped(i) for i in range(n)))
            assert not errors

            hr = await client.get("/health")
            assert hr.status_code == 200

        lat_ms.sort()
        p95 = lat_ms[int(0.95 * (len(lat_ms) - 1))]
        # 开发机差异大：仅断言极端退化（单请求数秒级）
        assert p95 < 5000.0, f"p95 latency too high: {p95:.1f}ms"
    finally:
        handle.stop()
        guard.close()


@pytest.mark.asyncio
@pytest.mark.load
async def test_stress_optional_env() -> None:
    """设置 AGENTGUARD_STRESS=1 时加大并发，用于本地容量摸底。"""
    if os.environ.get("AGENTGUARD_STRESS") != "1":
        pytest.skip("set AGENTGUARD_STRESS=1 to run stress tier")

    guard = Guard(policy_source=ALLOW_DSL, builtin_rules=False, mode="enforce")
    app = AgentGuardServer(guard, runtime_mode="sync").build_app()
    n = 2000
    conc = 100

    t0 = time.perf_counter()
    async with httpx.AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        timeout=120.0,
    ) as client:

        async def one(i: int) -> tuple[int, dict]:
            ev = build_event("shell.exec", args={"cmd": "ls"}, session_id=f"stress-{i % 32}")
            r = await client.post("/v1/evaluate", content=ev.model_dump_json())
            return r.status_code, r.json()

        pairs = await _gather_limited(n, conc, one)

    elapsed = time.perf_counter() - t0
    rps = n / elapsed
    assert all(s == 200 and b.get("ok") for s, b in pairs)
    # 软断言：纯内存策略下应有一定吞吐（环境相关，失败时仅作信号）
    assert rps > 50.0, f"expected >50 rps in-process, got {rps:.1f}"
    guard.close()


def test_latency_percentile_indexing_sanity() -> None:
    """离散索引 int(0.95 * (n-1)) 对应元素（与部分压测脚本的简化一致）。"""
    data = sorted([float(x) for x in range(100)])
    idx = int(0.95 * (len(data) - 1))
    assert data[idx] == 94.0


def test_serve_in_thread_raises_when_port_is_occupied() -> None:
    """端口被占用时，后台 server 启动必须显式失败。"""
    guard = Guard(policy_source=ALLOW_DSL, builtin_rules=False, mode="enforce")
    ag_server = AgentGuardServer(guard, runtime_mode="sync")

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    sock.listen(1)
    port = int(sock.getsockname()[1])

    try:
        with pytest.raises(RuntimeError, match="failed to start"):
            ag_server.serve_in_thread(host="127.0.0.1", port=port, ready_timeout=1.0)
    finally:
        sock.close()
        guard.close()

#!/usr/bin/env python3
"""对运行中的 AgentGuard Runtime 的 ``POST /v1/evaluate`` 做压测。

依赖: ``pip install httpx``（已包含在 ``agentguard[dev]``）。

用法::

    # 终端 1：启动服务（示例）
    python -m agentguard serve --host 127.0.0.1 --port 8765 \\
        --no-builtin --policy /path/to/minimal_allow.rules

    # 终端 2：压测（默认 2000 请求、并发 64）
    python scripts/loadtest_evaluate.py --url http://127.0.0.1:8765 \\
        --total 5000 --concurrency 128

输出 JSON：``duration_s``、``rps``、``errors``、``status_counts``、
``latency_ms``（min / mean / p50 / p95 / p99 / max）。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import random
import sys
import time
import uuid
from typing import Any

try:
    import httpx
except ImportError as e:
    print("需要 httpx: pip install httpx", file=sys.stderr)
    raise SystemExit(2) from e


def _build_event_payload(*, session_id: str) -> dict[str, Any]:
    """与 conftest.build_event 语义对齐的最小 RuntimeEvent（shell.exec + ls）。"""
    return {
        "event_type": "tool_call_attempt",
        "principal": {
            "agent_id": "loadtest-agent",
            "session_id": session_id,
            "role": "analyst",
            "trust_level": 2,
        },
        "goal": "load test",
        "scope": [],
        "tool_call": {
            "tool_name": "shell.exec",
            "args": {"cmd": "ls"},
            "target": {},
            "sink_type": "shell",
            "label": {
                "boundary": "internal",
                "sensitivity": "low",
                "integrity": "trusted",
                "tags": [],
            },
            "syntax": ["cmd"],
        },
        "provenance_refs": [],
        "event_id": str(uuid.uuid4()),
        "ts_ms": int(time.time() * 1000),
    }


def _percentile(sorted_ms: list[float], p: float) -> float:
    if not sorted_ms:
        return 0.0
    if len(sorted_ms) == 1:
        return sorted_ms[0]
    k = (len(sorted_ms) - 1) * (p / 100.0)
    lo = int(k)
    hi = min(lo + 1, len(sorted_ms) - 1)
    w = k - lo
    return sorted_ms[lo] * (1.0 - w) + sorted_ms[hi] * w


async def _run(
    *,
    base_url: str,
    total: int,
    concurrency: int,
    timeout_s: float,
    api_key: str,
    sessions: int,
) -> dict[str, Any]:
    url = base_url.rstrip("/") + "/v1/evaluate"
    headers: dict[str, str] = {"content-type": "application/json"}
    if api_key:
        headers["x-api-key"] = api_key

    latencies: list[float] = []
    status_counts: dict[int, int] = {}
    errors: list[str] = []
    lock = asyncio.Lock()

    sem = asyncio.Semaphore(concurrency)

    async def one(client: httpx.AsyncClient, i: int) -> None:
        nonlocal latencies, status_counts, errors
        sid = f"lt-{i % sessions}-{random.randint(0, 9999)}"
        body = json.dumps(_build_event_payload(session_id=sid))
        t0 = time.perf_counter()
        try:
            async with sem:
                r = await client.post(url, content=body.encode(), headers=headers)
        except Exception as exc:
            async with lock:
                errors.append(f"{type(exc).__name__}: {exc}")
            return
        dt = (time.perf_counter() - t0) * 1000.0
        async with lock:
            latencies.append(dt)
            status_counts[r.status_code] = status_counts.get(r.status_code, 0) + 1
            if r.status_code != 200:
                errors.append(f"HTTP {r.status_code}: {r.text[:200]}")

    async with httpx.AsyncClient(timeout=timeout_s) as client:
        wb = json.dumps(_build_event_payload(session_id="warmup"))
        wr = await client.post(url, content=wb.encode(), headers=headers)
        if wr.status_code != 200:
            return {
                "url": url,
                "error": "warmup_failed",
                "warmup_status": wr.status_code,
                "warmup_body": wr.text[:500],
            }

        t0 = time.perf_counter()
        await asyncio.gather(*(one(client, i) for i in range(total)))
        elapsed = time.perf_counter() - t0

    latencies.sort()
    ok = status_counts.get(200, 0)
    summary: dict[str, Any] = {
        "url": url,
        "total_requests": total,
        "concurrency": concurrency,
        "sessions": sessions,
        "duration_s": round(elapsed, 4),
        "rps": round(total / elapsed, 2) if elapsed > 0 else 0.0,
        "http_200": ok,
        "status_counts": {str(k): v for k, v in sorted(status_counts.items())},
        "error_samples": errors[:20],
        "error_count": len(errors),
        "latency_ms": {
            "min": round(latencies[0], 3) if latencies else 0.0,
            "mean": round(sum(latencies) / len(latencies), 3) if latencies else 0.0,
            "p50": round(_percentile(latencies, 50), 3),
            "p95": round(_percentile(latencies, 95), 3),
            "p99": round(_percentile(latencies, 99), 3),
            "max": round(latencies[-1], 3) if latencies else 0.0,
        },
    }
    return summary


def main() -> int:
    p = argparse.ArgumentParser(description="AgentGuard /v1/evaluate 压测")
    p.add_argument("--url", default="http://127.0.0.1:38080", help="Runtime 根 URL")
    p.add_argument("--total", type=int, default=2000, help="总请求数（不含预热）")
    p.add_argument("--concurrency", type=int, default=64, help="最大并发数")
    p.add_argument("--timeout", type=float, default=60.0, help="单请求超时（秒）")
    p.add_argument("--api-key", default="", help="X-Api-Key（若服务端启用）")
    p.add_argument(
        "--sessions",
        type=int,
        default=32,
        help="轮询使用的 session 槽位数（模拟多会话）",
    )
    args = p.parse_args()

    summary = asyncio.run(
        _run(
            base_url=args.url,
            total=args.total,
            concurrency=args.concurrency,
            timeout_s=args.timeout,
            api_key=args.api_key,
            sessions=max(1, args.sessions),
        )
    )
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    if summary.get("error"):
        return 2
    if summary["error_count"]:
        return 1
    if summary["http_200"] != args.total:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

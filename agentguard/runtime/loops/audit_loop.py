"""Audit / Persistence Loop.

Periodically forwards new audit records to an optional persistent sink
(Kafka / S3 / Loki / OLAP) and surfaces buffer-full / dropped-record
warnings as metrics so operators can detect data loss.

The :class:`AuditActor` writes every event to the in-memory ring buffer
synchronously; this loop is a *consumer* on top of that buffer.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable, Awaitable

from agentguard.audit.logger import AuditLogWriter

log = logging.getLogger(__name__)

SinkFn = Callable[[list[dict[str, Any]]], Awaitable[None]] | Callable[[list[dict[str, Any]]], None]


class AuditLoop:
    """Drains the AuditLogWriter ring buffer to a persistent sink."""

    def __init__(
        self,
        audit: AuditLogWriter,
        *,
        sink: SinkFn | None = None,
        flush_interval_s: float = 5.0,
        batch_size: int = 200,
    ) -> None:
        self._audit = audit
        self._sink = sink
        self._interval = flush_interval_s
        self._batch_size = batch_size
        self._task: asyncio.Task[None] | None = None
        self._running = False
        self._cursor = 0
        self.dropped_warned_at: int = 0

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._run(), name="agentguard-audit-loop")

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    def metrics(self) -> dict[str, Any]:
        return {
            "buffered": len(self._audit.recent(10_000)),
            "dropped_total": self._audit.dropped_count,
            "cursor": self._cursor,
        }

    async def _run(self) -> None:
        while self._running:
            try:
                await asyncio.sleep(self._interval)
            except asyncio.CancelledError:
                break
            try:
                await self._tick()
            except Exception as exc:
                log.warning("audit loop tick failed: %s", exc)

    async def _tick(self) -> None:
        if self._audit.dropped_count > self.dropped_warned_at:
            log.warning(
                "audit buffer dropped %d records since last tick",
                self._audit.dropped_count - self.dropped_warned_at,
            )
            self.dropped_warned_at = self._audit.dropped_count

        if self._sink is None:
            return

        records = self._audit.recent(self._batch_size)
        if not records:
            return
        try:
            result = self._sink(records)
            if asyncio.iscoroutine(result):
                await result
        except Exception as exc:
            log.warning("audit sink rejected batch: %s", exc)

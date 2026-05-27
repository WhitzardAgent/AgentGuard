"""Human-review timeout watchdog.

Periodically scans the :class:`ApprovalBridge` for pending tickets and
auto-resolves any that have exceeded ``timeout_s``. Without this loop a
crashed reviewer can hang an agent indefinitely.

The :class:`HumanReviewActor` handles ticket *creation* (one per
``human_review_request`` message); this loop handles *expiration*.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from agentguard.review.tickets import ApprovalBridge

log = logging.getLogger(__name__)


class ReviewLoop:
    """Timeout watchdog for pending approval tickets."""

    def __init__(
        self,
        bridge: ApprovalBridge,
        *,
        timeout_s: float = 600.0,
        poll_interval_s: float = 5.0,
        on_timeout: str = "deny",  # "deny" | "approve"
    ) -> None:
        self._bridge = bridge
        self._timeout_s = timeout_s
        self._interval = poll_interval_s
        self._on_timeout = on_timeout
        self._task: asyncio.Task[None] | None = None
        self._running = False
        self._auto_resolved = 0

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._run(), name="agentguard-review-loop")

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
            "auto_resolved": self._auto_resolved,
            "pending": len(self._bridge.pending()),
            "timeout_s": self._timeout_s,
            "policy": self._on_timeout,
        }

    async def _run(self) -> None:
        while self._running:
            try:
                await asyncio.sleep(self._interval)
            except asyncio.CancelledError:
                break
            try:
                self._tick()
            except Exception as exc:
                log.warning("review loop tick failed: %s", exc)

    def _tick(self) -> None:
        now_ms = int(time.time() * 1000)
        cutoff = now_ms - int(self._timeout_s * 1000)
        for ticket in list(self._bridge.pending()):
            if ticket.created_ms <= cutoff:
                ok = self._bridge.resolve(
                    ticket.ticket_id,
                    self._on_timeout,
                    note=f"auto_{self._on_timeout} after {self._timeout_s}s",
                )
                if ok:
                    self._auto_resolved += 1
                    log.info(
                        "review timeout: ticket=%s auto-%s",
                        ticket.ticket_id,
                        self._on_timeout,
                    )

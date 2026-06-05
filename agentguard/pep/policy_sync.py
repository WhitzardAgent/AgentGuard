"""Background policy-version synchronization with the server PDP.

Keeps the client's fast path coherent with the authoritative server policy by
polling ``GET /rules/version`` (a cheap etag endpoint). When the server's rule
set changes, locally-cached decisions are invalidated so subsequent events are
re-evaluated against (and may re-escalate to) the new policy.

This realises the "server policy is asynchronously synced down to the client"
half of the dual-path design without requiring the server's DSL to be
re-compiled on the client — authoritative verdicts still arrive via the slow
path, while the cache stays fresh.
"""

from __future__ import annotations

import logging
import threading
from typing import Callable

from agentguard.pdp_client.client import PDPClient, PDPUnavailable
from agentguard.pep.decision_cache import DecisionCache

log = logging.getLogger("agentguard.pep")


class PolicySync:
    def __init__(
        self,
        pdp_client: PDPClient,
        cache: DecisionCache,
        *,
        interval_s: float = 10.0,
        on_change: Callable[[str], None] | None = None,
    ) -> None:
        self._pdp = pdp_client
        self._cache = cache
        self.interval_s = interval_s
        self._on_change = on_change
        self._etag: str | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    @property
    def current_version(self) -> str | None:
        return self._etag

    def poll_once(self) -> bool:
        """Fetch the server version once; return True if it changed."""
        try:
            info = self._pdp.policy_version()
        except PDPUnavailable as exc:
            log.debug("policy sync: PDP unavailable (%s)", exc)
            return False
        etag = str(info.get("etag", "")) or None
        if etag is None or etag == self._etag:
            return False
        previous, self._etag = self._etag, etag
        # New server policy → drop possibly-stale cached client decisions.
        self._cache.clear()
        log.info("policy sync: server rule version changed %s → %s", previous, etag)
        if self._on_change is not None:
            try:
                self._on_change(etag)
            except Exception as exc:  # noqa: BLE001
                log.warning("policy sync on_change hook failed: %s", exc)
        return True

    def start(self) -> None:
        if self._thread is not None or not self._pdp.enabled:
            return
        self.poll_once()  # prime immediately
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop, name="agentguard-policy-sync", daemon=True
        )
        self._thread.start()

    def _loop(self) -> None:
        while not self._stop.wait(self.interval_s):
            self.poll_once()

    def stop(self) -> None:
        self._stop.set()
        thread, self._thread = self._thread, None
        if thread is not None:
            thread.join(timeout=1.0)

"""Pipeline observability counters.

Thread-safe statistics collected from :class:`agentguard.runtime.dispatcher.Pipeline`
on every ``handle_attempt`` call.  Both the synchronous Pipeline (in-process mode)
and the async actor runtime feed into this class so ``GET /stats`` always reflects
the *full* traffic across both execution paths.

Exposed via ``GET /stats`` and ``GET /traffic``.
"""

from __future__ import annotations

import threading
import time
from collections import Counter, deque
from typing import Any


class PipelineStats:
    """Thread-safe, O(1) per-call statistics accumulator.

    Collected data
    --------------
    * total / by_action  counters
    * latency histogram  (buckets: <5 ms, 5-15 ms, 15-50 ms, >50 ms)
    * top-N blocked tools, top-N blocked agents, top-N matched rules
    * recent traffic ring-buffer (last ``traffic_window`` entries)

    All methods are thread-safe.
    """

    _LATENCY_BUCKETS = (5.0, 15.0, 50.0)  # ms breakpoints

    def __init__(
        self,
        *,
        traffic_window: int = 1_000,
        top_n: int = 20,
    ) -> None:
        self._lock = threading.Lock()
        self._total: int = 0
        self._action_counts: Counter[str] = Counter()
        self._tool_counts: Counter[str] = Counter()
        self._agent_counts: Counter[str] = Counter()
        self._deny_tool_counts: Counter[str] = Counter()
        self._deny_agent_counts: Counter[str] = Counter()
        self._matched_rule_counts: Counter[str] = Counter()
        self._latency_hist: Counter[str] = Counter()
        self._latency_sum_ms: float = 0.0
        self._latency_max_ms: float = 0.0
        self._start_ts: float = time.time()

        # Rolling window of recent individual requests (newest-first deque)
        self._traffic: deque[dict[str, Any]] = deque(maxlen=traffic_window)
        self._top_n = top_n

    # ─── write path ────────────────────────────────────────────────────────

    def record(
        self,
        *,
        tool_name: str,
        agent_id: str,
        session_id: str,
        action: str,             # e.g. "deny", "allow", "llm_check", "degrade"
        matched_rules: list[str],
        latency_ms: float,
        risk_score: float = 0.0,
        reason: str = "",
        ts: float | None = None,
    ) -> None:
        ts = ts or time.time()
        bucket = self._latency_bucket(latency_ms)
        is_deny = action.lower() in ("deny", "human_check")

        entry: dict[str, Any] = {
            "ts": ts,
            "tool": tool_name,
            "agent": agent_id,
            "session": session_id,
            "action": action,
            "latency_ms": round(latency_ms, 2),
            "risk": round(risk_score, 3),
            "rules": matched_rules,
            "reason": reason,
        }

        with self._lock:
            self._total += 1
            self._action_counts[action.lower()] += 1
            self._tool_counts[tool_name] += 1
            self._agent_counts[agent_id] += 1
            self._matched_rule_counts.update(matched_rules)
            self._latency_hist[bucket] += 1
            self._latency_sum_ms += latency_ms
            if latency_ms > self._latency_max_ms:
                self._latency_max_ms = latency_ms
            if is_deny:
                self._deny_tool_counts[tool_name] += 1
                self._deny_agent_counts[agent_id] += 1
            self._traffic.appendleft(entry)

    # ─── read path ─────────────────────────────────────────────────────────

    def summary(self) -> dict[str, Any]:
        """Return a rich summary dict suitable for ``GET /stats``."""
        with self._lock:
            total = self._total
            by_action = dict(self._action_counts)
            deny_count = by_action.get("deny", 0) + by_action.get("human_check", 0)
            deny_rate = round(deny_count / total, 4) if total else 0.0
            avg_latency = round(self._latency_sum_ms / total, 2) if total else 0.0

            return {
                "total_requests": total,
                "uptime_s": round(time.time() - self._start_ts, 1),
                "deny_rate": deny_rate,
                "by_action": by_action,
                "latency_ms": {
                    "avg": avg_latency,
                    "max": round(self._latency_max_ms, 2),
                    "histogram": dict(self._latency_hist),
                },
                "top_tools": self._tool_counts.most_common(self._top_n),
                "top_agents": self._agent_counts.most_common(self._top_n),
                "top_denied_tools": self._deny_tool_counts.most_common(self._top_n),
                "top_denied_agents": self._deny_agent_counts.most_common(self._top_n),
                "top_matched_rules": self._matched_rule_counts.most_common(self._top_n),
            }
        
    def summary_agent(self, agent_id: str) -> dict[str, Any]:
        """Return a rich summary dict suitable for ``GET /stats``."""
        with self._lock:
            total = self._agent_counts[agent_id]
            deny_count = self._deny_agent_counts[agent_id]
            deny_rate = round(deny_count / total, 4) if total else 0.0
            return {
                "total_requests": total,
                "uptime_s": round(time.time() - self._start_ts, 1),
                "deny_count": deny_count,
                "deny_rate": deny_rate,
            }

    def recent_traffic(self, n: int = 100) -> list[dict[str, Any]]:
        """Return the *n* most recent request entries (newest first)."""
        with self._lock:
            items = list(self._traffic)
        return items[:n]

    def traffic_by_action(self, action: str, n: int = 100) -> list[dict[str, Any]]:
        """Return recent traffic filtered by action string."""
        action_lc = action.lower()
        with self._lock:
            items = list(self._traffic)
        return [e for e in items if e["action"].lower() == action_lc][:n]

    def reset(self) -> None:
        """Reset all counters (useful for tests)."""
        with self._lock:
            self._total = 0
            self._action_counts.clear()
            self._tool_counts.clear()
            self._agent_counts.clear()
            self._deny_tool_counts.clear()
            self._deny_agent_counts.clear()
            self._matched_rule_counts.clear()
            self._latency_hist.clear()
            self._latency_sum_ms = 0.0
            self._latency_max_ms = 0.0
            self._traffic.clear()
            self._start_ts = time.time()

    # ─── helpers ───────────────────────────────────────────────────────────

    @classmethod
    def _latency_bucket(cls, ms: float) -> str:
        if ms < cls._LATENCY_BUCKETS[0]:
            return f"<{cls._LATENCY_BUCKETS[0]:.0f}ms"
        for i, upper in enumerate(cls._LATENCY_BUCKETS[1:], start=1):
            if ms < upper:
                lower = cls._LATENCY_BUCKETS[i - 1]
                return f"{lower:.0f}-{upper:.0f}ms"
        return f">={cls._LATENCY_BUCKETS[-1]:.0f}ms"


# Module-level singleton shared between Pipeline and the API layer.
# Both in-process Guard and remote AgentGuardServer import this object.
_GLOBAL_STATS = PipelineStats()


def get_stats() -> PipelineStats:
    """Return the module-level stats singleton."""
    return _GLOBAL_STATS

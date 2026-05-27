"""Execute obligations attached to a Decision before or after the tool runs."""

from __future__ import annotations

import logging
import time
import threading
from collections import defaultdict
from typing import Any

from agentguard.degrade.variants import get_degrade_profile
from agentguard.models.decisions import Decision, Obligation
from agentguard.models.events import RuntimeEvent, ToolCall

log = logging.getLogger(__name__)

# In-process rate-limit counter store: {(session_id, rule_id): [(ts, count), ...]}
_RATE_COUNTERS: dict[tuple[str, str], list[float]] = defaultdict(list)
_RATE_LOCK = threading.Lock()


class ActionExecutor:
    """Applies obligations (rewrite_tool / mask_fields / require_target_in / rate_limit)."""

    def apply_rewrites(
        self,
        event: RuntimeEvent,
        decision: Decision,
    ) -> ToolCall | None:
        """Return rewritten ToolCall after applying all mutation obligations."""
        if event.tool_call is None:
            return None
        tc = event.tool_call
        for ob in decision.obligations:
            tc = self._apply(ob, tc)
        return tc

    def check_require_target_in(
        self,
        event: RuntimeEvent,
        decision: Decision,
    ) -> str | None:
        """Return a violation message if any require_target_in obligation fails, else None."""
        if event.tool_call is None:
            return None
        tc = event.tool_call
        for ob in decision.obligations:
            if ob.kind != "require_target_in":
                continue
            allowed: Any = ob.params.get("whitelist") or ob.params.get("allowed")
            if not allowed:
                continue
            if isinstance(allowed, dict) and "__call__" in allowed:
                # whitelist() function reference — skip enforcement without features
                continue
            allowed_set: set[str] = set(allowed) if isinstance(allowed, (list, tuple)) else set()
            domain = (tc.target or {}).get("domain") or (tc.target or {}).get("url") or ""
            if allowed_set and domain and domain not in allowed_set:
                return f"target domain {domain!r} not in allowed set {allowed_set}"
        return None

    def check_rate_limit(
        self,
        event: RuntimeEvent,
        decision: Decision,
    ) -> str | None:
        """Return violation message if any rate_limit obligation is exceeded, else None."""
        if event.tool_call is None:
            return None
        sess = event.principal.session_id
        for ob in decision.obligations:
            if ob.kind != "rate_limit":
                continue
            rule_id = str(ob.params.get("rule_id", ""))
            max_calls = int(ob.params.get("max", ob.params.get("max_calls", 10)))
            window_raw = str(ob.params.get("window", "60s"))
            window_s = _parse_window(window_raw)
            key = (sess, rule_id)
            now = time.time()
            with _RATE_LOCK:
                timestamps = _RATE_COUNTERS[key]
                # drop entries outside the window
                cutoff = now - window_s
                _RATE_COUNTERS[key] = [t for t in timestamps if t >= cutoff]
                timestamps = _RATE_COUNTERS[key]
                if len(timestamps) >= max_calls:
                    return (
                        f"rate limit exceeded: {len(timestamps)}/{max_calls} "
                        f"calls in {window_raw} for rule {rule_id!r}"
                    )
                _RATE_COUNTERS[key].append(now)
        return None

    def _apply(self, ob: Obligation, tc: ToolCall) -> ToolCall:
        if ob.kind == "rewrite_tool":
            profile_name = str(ob.params.get("profile", ""))
            profile = get_degrade_profile(profile_name)
            if profile is None:
                log.warning("unknown degrade profile: %s", profile_name)
                return tc
            return profile(tc)

        if ob.kind == "mask_field":
            log.warning(
                "obligation kind 'mask_field' is deprecated; use 'mask_fields' instead"
            )
            field = str(ob.params.get("field", ""))
            if field and field in tc.args:
                new_args = dict(tc.args)
                new_args[field] = "[REDACTED]"
                return tc.model_copy(update={"args": new_args})
            return tc

        if ob.kind == "mask_fields":
            fields = ob.params.get("fields") or ob.params.get("field")
            if isinstance(fields, str):
                fields = [fields]
            if not fields:
                return tc
            new_args = dict(tc.args)
            changed = False
            for f in fields:
                if f in new_args:
                    new_args[f] = "[REDACTED]"
                    changed = True
            return tc.model_copy(update={"args": new_args}) if changed else tc

        # require_target_in and rate_limit are checked by separate methods above;
        # they do not mutate the ToolCall itself.
        if ob.kind in ("require_target_in", "rate_limit", "audit"):
            return tc

        return tc


def _parse_window(raw: str) -> float:
    """Parse '5m', '60s', '1h' → seconds (float)."""
    raw = raw.strip()
    if raw.endswith("h"):
        return float(raw[:-1]) * 3600
    if raw.endswith("m"):
        return float(raw[:-1]) * 60
    if raw.endswith("s"):
        return float(raw[:-1])
    try:
        return float(raw)
    except ValueError:
        return 60.0

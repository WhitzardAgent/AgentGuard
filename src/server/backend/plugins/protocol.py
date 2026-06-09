"""Server plugin hook names."""
from __future__ import annotations

HOOKS = (
    "on_request_received",
    "on_before_policy_decision",
    "on_diagnose",
    "on_after_policy_decision",
    "on_trace_uploaded",
    "on_policy_snapshot_build",
)

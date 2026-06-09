"""Shared plugin protocol constants and extension keys."""
from __future__ import annotations

# Request extension keys plugins may populate on a remote guard request.
EXT_TRAJECTORY_WINDOW = "trajectory_window"
EXT_TOOL_METADATA = "tool_metadata"
EXT_LOCAL_SIGNALS = "local_signals"

# Response extension keys plugins may return.
EXT_DIAGNOSIS = "diagnosis"
EXT_RISK_LABELS = "risk_labels"
EXT_DECISION_HINTS = "decision_hints"

REQUEST_EXTENSIONS = (EXT_TRAJECTORY_WINDOW, EXT_TOOL_METADATA, EXT_LOCAL_SIGNALS)
RESPONSE_EXTENSIONS = (EXT_DIAGNOSIS, EXT_RISK_LABELS, EXT_DECISION_HINTS)

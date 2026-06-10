"""Remote protocol messages and endpoint paths."""
from __future__ import annotations

from shared.protocol.messages import RemoteGuardRequest, RemoteGuardResponse

# Canonical endpoint paths shared by client and server.
PATH_HEALTH = "/v1/backend/health"
PATH_GUARD_DECIDE = "/v1/server/guard/decide"
PATH_POLICY_SNAPSHOT = "/v1/server/policy/snapshot"
PATH_TRACE_UPLOAD = "/v1/server/trace/upload"
PATH_SKILLS_RUN = "/v1/server/skills/run"
PATH_SESSION_UNREGISTER = "/v1/server/session/unregister"

__all__ = [
    "RemoteGuardRequest",
    "RemoteGuardResponse",
    "PATH_HEALTH",
    "PATH_GUARD_DECIDE",
    "PATH_POLICY_SNAPSHOT",
    "PATH_TRACE_UPLOAD",
    "PATH_SKILLS_RUN",
    "PATH_SESSION_UNREGISTER",
]

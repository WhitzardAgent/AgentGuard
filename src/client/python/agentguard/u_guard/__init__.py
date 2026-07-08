"""U-Guard: client-side local/remote decision routing."""
from __future__ import annotations

from agentguard.u_guard.decision_cache import DecisionCache
from agentguard.u_guard.enforcer import EnforcementResult, UGuardEnforcer
from agentguard.u_guard.fallback import FallbackGuard
from agentguard.u_guard.agent_keys import (
    AgentIdentityKey,
    build_agent_registration_payload,
    load_or_create_agent_key,
    stable_agent_key_id,
)
from agentguard.u_guard.local_engine import LocalEvaluation, LocalGuardEngine
from agentguard.u_guard.policy_snapshot import PolicySnapshot
from agentguard.u_guard.remote_client import CircuitBreaker, RemoteGuardClient
from agentguard.u_guard.router import RouteDecision, RouteTarget, UGuardRouter
from agentguard.u_guard.sync_buffer import ClientSyncBuffer

__all__ = [
    "UGuardEnforcer",
    "EnforcementResult",
    "UGuardRouter",
    "RouteTarget",
    "RouteDecision",
    "LocalGuardEngine",
    "LocalEvaluation",
    "AgentIdentityKey",
    "build_agent_registration_payload",
    "load_or_create_agent_key",
    "stable_agent_key_id",
    "RemoteGuardClient",
    "CircuitBreaker",
    "FallbackGuard",
    "DecisionCache",
    "ClientSyncBuffer",
    "PolicySnapshot",
]

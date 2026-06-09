"""Build a client-downloadable policy snapshot from the store."""
from __future__ import annotations

from typing import Any

from agentguard.u_guard.policy_snapshot import PolicySnapshot
from backend.runtime.policy.store import PolicyStore


def build_snapshot(store: PolicyStore) -> PolicySnapshot:
    return PolicySnapshot(version=store.version, rules=store.rules())


def snapshot_dict(store: PolicyStore) -> dict[str, Any]:
    return build_snapshot(store).to_dict()

"""Build a client-downloadable policy snapshot from the store."""
from __future__ import annotations

from typing import Any

from backend.runtime.policy.store import PolicyStore
from shared.rules.snapshot import PolicySnapshot


def build_snapshot(store: PolicyStore) -> PolicySnapshot:
    return PolicySnapshot(version=store.version, rules=store.rules())


def snapshot_dict(store: PolicyStore) -> dict[str, Any]:
    return build_snapshot(store).to_dict()

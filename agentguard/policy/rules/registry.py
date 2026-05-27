"""Backwards-compatible single-pool registry built on :class:`RuleRouter`.

Historically ``RuleRegistry`` exposed a flat ``dict[rule_id -> CompiledRule]``
view. Multi-pack routing now lives in :mod:`agentguard.policy.routing`; this
module keeps the legacy API working by funnelling every mutation into the
default pack while ``active()`` returns the union across every pack.

Prefer ``Guard.router`` for new code that needs per-agent routing.
"""

from __future__ import annotations

import hashlib
import threading
from dataclasses import dataclass, field
from typing import Iterable

from agentguard.policy.dsl.compiler import CompiledRule
from agentguard.policy.routing import RulePack, RuleRouter


class RuleRegistry:
    """Flat view onto a multi-pack :class:`RuleRouter`.

    All mutating methods target the router's default pack; readers see
    the union of every pack so legacy callers (audit, /rules, tests)
    continue to behave as before.
    """

    def __init__(self, router: RuleRouter | None = None) -> None:
        self._router = router or RuleRouter()
        self._lock = threading.RLock()
        self._version = 0

    @property
    def router(self) -> RuleRouter:
        return self._router

    def replace(self, rules: Iterable[CompiledRule]) -> int:
        with self._lock:
            self._router.replace_pack_rules(
                RuleRouter.DEFAULT_PACK_ID, rules, source="registry.replace"
            )
            self._version += 1
            return self._version

    def upsert(self, rule: CompiledRule) -> int:
        with self._lock:
            pack = self._router.get_pack(RuleRouter.DEFAULT_PACK_ID)
            existing = {r.rule_id: r for r in (pack.rules if pack else [])}
            existing[rule.rule_id] = rule
            self._router.replace_pack_rules(
                RuleRouter.DEFAULT_PACK_ID,
                list(existing.values()),
                source=pack.source if pack else "registry.upsert",
            )
            self._version += 1
            return self._version

    def remove(self, rule_id: str) -> bool:
        with self._lock:
            for pack in self._router.list_packs():
                if any(r.rule_id == rule_id for r in pack.rules):
                    new_rules = [r for r in pack.rules if r.rule_id != rule_id]
                    self._router.replace_pack_rules(
                        pack.pack_id, new_rules, source=pack.source
                    )
                    self._version += 1
                    return True
            return False

    def active(self) -> list[CompiledRule]:
        return self._router.all_rules()

    def get(self, rule_id: str) -> CompiledRule | None:
        for rule in self._router.all_rules():
            if rule.rule_id == rule_id:
                return rule
        return None

    @property
    def version(self) -> int:
        return self._version


# ---------------------------------------------------------------------------
# Rollout (per-rule percent / tenant gating) — unchanged from previous version
# ---------------------------------------------------------------------------

@dataclass
class RolloutSpec:
    percent: int = 100
    tenants: set[str] = field(default_factory=set)


class Rollout:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._specs: dict[str, RolloutSpec] = {}

    def set(self, rule_id: str, spec: RolloutSpec) -> None:
        with self._lock:
            self._specs[rule_id] = spec

    def applies(
        self, rule_id: str, *, session_id: str, tenant: str | None = None
    ) -> bool:
        with self._lock:
            spec = self._specs.get(rule_id)
        if spec is None:
            return True
        if spec.tenants and tenant not in spec.tenants:
            return False
        if spec.percent >= 100:
            return True
        if spec.percent <= 0:
            return False
        h = hashlib.md5(f"{rule_id}:{session_id}".encode()).hexdigest()
        bucket = int(h[:8], 16) % 100
        return bucket < spec.percent


__all__ = ["RuleRegistry", "Rollout", "RolloutSpec", "RulePack"]

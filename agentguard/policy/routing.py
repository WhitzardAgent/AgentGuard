"""Rule routing: agent -> rule packs -> compiled rules.

Three-tier model
----------------
1. ``__builtin__`` pack: shipped rules, always applied.
2. Named user packs: created from YAML, files, raw DSL, or API.
3. ``__default__`` pack: receives rules loaded via ``--policy`` when no
   pack id is provided; also applied to agents that have no explicit
   binding (configurable).

A :class:`RuleRouter` maintains the pack catalog and the agent-binding
table; given an ``agent_id`` it returns the de-duplicated, evaluation-
ready rule list. Both packs and bindings are many-to-many: one agent
may bind multiple packs, and one pack may be shared across agents.

The store interfaces (:class:`AgentBindingStore`) keep persistence
pluggable; the in-memory backend is the default and remains the only
runtime requirement when the operator has not opted into Redis or
PostgreSQL.
"""

from __future__ import annotations

import abc
import threading
from dataclasses import dataclass, field
from typing import Iterable

from agentguard.policy.dsl.compiler import CompiledRule


BUILTIN_PACK_ID = "__builtin__"
DEFAULT_PACK_ID = "__default__"


# ---------------------------------------------------------------------------
# Domain models
# ---------------------------------------------------------------------------

@dataclass
class RulePack:
    """A named, immutable bundle of compiled rules."""

    pack_id: str
    rules: list[CompiledRule] = field(default_factory=list)
    source: str = ""
    user_managed: bool = False

    def rule_ids(self) -> list[str]:
        return [r.rule_id for r in self.rules]


# ---------------------------------------------------------------------------
# Binding store
# ---------------------------------------------------------------------------

class AgentBindingStore(abc.ABC):
    """Persistence boundary for agent ↔ rule_pack relationships."""

    @abc.abstractmethod
    def packs_of(self, agent_id: str) -> set[str]: ...

    @abc.abstractmethod
    def agents_of(self, pack_id: str) -> set[str]: ...

    @abc.abstractmethod
    def bind(self, agent_id: str, pack_id: str) -> None: ...

    @abc.abstractmethod
    def unbind(self, agent_id: str, pack_id: str) -> bool: ...

    @abc.abstractmethod
    def list_all(self) -> dict[str, set[str]]:
        """Return the full ``agent_id -> {pack_id}`` mapping (snapshot)."""
        ...

    @abc.abstractmethod
    def clear_agent(self, agent_id: str) -> None: ...

    @abc.abstractmethod
    def clear_pack(self, pack_id: str) -> None: ...


class InMemoryAgentBindingStore(AgentBindingStore):
    """Thread-safe in-process binding table."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._by_agent: dict[str, set[str]] = {}
        self._by_pack: dict[str, set[str]] = {}

    def packs_of(self, agent_id: str) -> set[str]:
        with self._lock:
            return set(self._by_agent.get(agent_id, ()))

    def agents_of(self, pack_id: str) -> set[str]:
        with self._lock:
            return set(self._by_pack.get(pack_id, ()))

    def bind(self, agent_id: str, pack_id: str) -> None:
        with self._lock:
            self._by_agent.setdefault(agent_id, set()).add(pack_id)
            self._by_pack.setdefault(pack_id, set()).add(agent_id)

    def unbind(self, agent_id: str, pack_id: str) -> bool:
        with self._lock:
            agents = self._by_pack.get(pack_id)
            packs = self._by_agent.get(agent_id)
            removed = False
            if packs and pack_id in packs:
                packs.discard(pack_id)
                removed = True
                if not packs:
                    del self._by_agent[agent_id]
            if agents and agent_id in agents:
                agents.discard(agent_id)
                if not agents:
                    del self._by_pack[pack_id]
            return removed

    def list_all(self) -> dict[str, set[str]]:
        with self._lock:
            return {a: set(p) for a, p in self._by_agent.items()}

    def clear_agent(self, agent_id: str) -> None:
        with self._lock:
            for pack_id in self._by_agent.pop(agent_id, ()):
                bucket = self._by_pack.get(pack_id)
                if bucket:
                    bucket.discard(agent_id)
                    if not bucket:
                        del self._by_pack[pack_id]

    def clear_pack(self, pack_id: str) -> None:
        with self._lock:
            for agent_id in self._by_pack.pop(pack_id, ()):
                bucket = self._by_agent.get(agent_id)
                if bucket:
                    bucket.discard(pack_id)
                    if not bucket:
                        del self._by_agent[agent_id]


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------

class RuleRouter:
    """Single source of truth for "which rules apply to this agent?".

    Resolution order for a given ``agent_id``::

        builtin pack
        → packs explicitly bound to the agent (sorted by pack_id)
        → default pack (only if the agent has no explicit binding *and*
          ``apply_default_when_unbound`` is True)

    Within the same priority, later-loaded packs override earlier ones
    on a per ``rule_id`` basis (so an agent-bound pack can shadow a
    built-in rule with the same id).
    """

    BUILTIN_PACK_ID = BUILTIN_PACK_ID
    DEFAULT_PACK_ID = DEFAULT_PACK_ID

    def __init__(
        self,
        *,
        bindings: AgentBindingStore | None = None,
        apply_default_when_unbound: bool = True,
    ) -> None:
        self._lock = threading.RLock()
        self._packs: dict[str, RulePack] = {}
        self._bindings = bindings or InMemoryAgentBindingStore()
        self._apply_default_when_unbound = apply_default_when_unbound
        self._cache: dict[str, list[CompiledRule]] = {}

    # ---- pack catalogue ----------------------------------------------

    def upsert_pack(self, pack: RulePack) -> None:
        with self._lock:
            self._packs[pack.pack_id] = pack
            self._cache.clear()

    def remove_pack(self, pack_id: str) -> bool:
        with self._lock:
            existed = self._packs.pop(pack_id, None) is not None
            if existed:
                self._bindings.clear_pack(pack_id)
                self._cache.clear()
            return existed

    def get_pack(self, pack_id: str) -> RulePack | None:
        with self._lock:
            return self._packs.get(pack_id)

    def list_packs(self) -> list[RulePack]:
        with self._lock:
            return list(self._packs.values())

    def replace_pack_rules(
        self,
        pack_id: str,
        rules: Iterable[CompiledRule],
        *,
        source: str = "",
        user_managed: bool = False,
    ) -> RulePack:
        """Atomically swap the rule list inside an existing or new pack."""
        pack = RulePack(
            pack_id=pack_id,
            rules=list(rules),
            source=source,
            user_managed=user_managed,
        )
        self.upsert_pack(pack)
        return pack

    # ---- bindings ----------------------------------------------------

    def bindings(self) -> AgentBindingStore:
        return self._bindings

    def bind(self, agent_id: str, pack_id: str) -> None:
        with self._lock:
            if pack_id not in self._packs:
                raise KeyError(f"unknown rule pack: {pack_id!r}")
            self._bindings.bind(agent_id, pack_id)
            self._cache.pop(agent_id, None)

    def unbind(self, agent_id: str, pack_id: str) -> bool:
        removed = self._bindings.unbind(agent_id, pack_id)
        if removed:
            with self._lock:
                self._cache.pop(agent_id, None)
        return removed

    def packs_for_agent(self, agent_id: str) -> list[str]:
        order: list[str] = []
        seen: set[str] = set()

        def push(pack_id: str) -> None:
            if pack_id not in seen and pack_id in self._packs:
                seen.add(pack_id)
                order.append(pack_id)

        with self._lock:
            push(self.BUILTIN_PACK_ID)
            for pid in sorted(self._bindings.packs_of(agent_id)):
                push(pid)
            if not seen - {self.BUILTIN_PACK_ID}:
                if self._apply_default_when_unbound:
                    push(self.DEFAULT_PACK_ID)
        return order

    def rules_for_agent(self, agent_id: str) -> list[CompiledRule]:
        with self._lock:
            cached = self._cache.get(agent_id)
            if cached is not None:
                return list(cached)
            merged: dict[str, CompiledRule] = {}
            for pid in self.packs_for_agent(agent_id):
                pack = self._packs.get(pid)
                if pack is None:
                    continue
                for rule in pack.rules:
                    merged[rule.rule_id] = rule
            ordered = list(merged.values())
            self._cache[agent_id] = ordered
            return list(ordered)

    def all_rules(self) -> list[CompiledRule]:
        with self._lock:
            merged: dict[str, CompiledRule] = {}
            for pack in self._packs.values():
                for rule in pack.rules:
                    merged[rule.rule_id] = rule
            return list(merged.values())

    def invalidate_cache(self) -> None:
        with self._lock:
            self._cache.clear()

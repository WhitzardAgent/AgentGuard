"""Policy store: versioned rule set loaded from rules/ JSON files."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from shared.rules.builtin import builtin_rules
from shared.rules.loader import load_rules_dir, load_rules_file
from shared.schemas.policy import PolicyRule
from shared.utils.hash import short_hash


class PolicyStore:
    def __init__(self, rules: list[PolicyRule] | None = None, version: str | None = None) -> None:
        self._rules = list(rules) if rules is not None else list(builtin_rules())
        self._reindex_rules()
        self._version = version or self._compute_version()

    def _reindex_rules(self) -> None:
        self._global_rules: list[PolicyRule] = []
        self._agent_rules: dict[str, list[PolicyRule]] = {}
        for rule in self._rules:
            agent_id = str(getattr(rule, "agent_id", "") or "").strip()
            if not agent_id:
                self._global_rules.append(rule)
                continue
            self._agent_rules.setdefault(agent_id, []).append(rule)

    def _compute_version(self) -> str:
        return "v-" + short_hash([r.to_dict() for r in self._rules], 10)

    @property
    def version(self) -> str:
        return self._version

    def rules(self) -> list[PolicyRule]:
        return list(self._rules)

    def rules_for_agent(self, agent_id: str | None) -> list[PolicyRule]:
        normalized_agent_id = str(agent_id or "").strip()
        scoped_rules = self._agent_rules.get(normalized_agent_id, []) if normalized_agent_id else []
        return [*self._global_rules, *scoped_rules]

    def rule_counts(self) -> dict[str, Any]:
        return {
            "total": len(self._rules),
            "global": len(self._global_rules),
            "scoped": {agent_id: len(rules) for agent_id, rules in self._agent_rules.items()},
        }

    def set_rules(self, rules: list[PolicyRule], version: str | None = None) -> None:
        self._rules = list(rules)
        self._reindex_rules()
        self._version = version or self._compute_version()

    @classmethod
    def from_path(cls, path: str | Path) -> "PolicyStore":
        p = Path(path)
        rules = list(builtin_rules())
        if p.is_dir():
            rules.extend(load_rules_dir(p))
        elif p.is_file():
            rules.extend(load_rules_file(p))
        return cls(rules=rules)

    @classmethod
    def default(cls) -> "PolicyStore":
        # Include repo rules/builtin and rules/examples if present.
        rules = list(builtin_rules())
        for sub in ("rules/builtin", "rules/examples/enterprise_default.json"):
            p = Path(sub)
            try:
                if p.is_dir():
                    rules.extend(load_rules_dir(p))
                elif p.is_file():
                    rules.extend(load_rules_file(p))
            except Exception:
                continue
        return cls(rules=rules)

"""Load policy rules from JSON files or directories."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from agentguard.rules.builtin import builtin_rules
from agentguard.schemas.policy import PolicyRule
from agentguard.utils.errors import PolicyError
from shared.rules.dsl_compat import parse_legacy_rules


def _coerce_rules(data: Any) -> list[PolicyRule]:
    if isinstance(data, dict):
        data = data.get("rules", [])
    if not isinstance(data, list):
        raise PolicyError("rule file must contain a list or {'rules': [...]}")
    out: list[PolicyRule] = []
    for item in data:
        try:
            out.append(PolicyRule.from_dict(item))
        except (KeyError, ValueError) as exc:
            raise PolicyError(f"invalid rule: {exc}") from exc
    return out


def load_rules_file(path: str | Path) -> list[PolicyRule]:
    p = Path(path)
    if not p.exists():
        raise PolicyError(f"rule file not found: {p}")
    if p.suffix.lower() == ".rules":
        try:
            parsed, report = parse_legacy_rules(p.read_text(encoding="utf-8"))
        except OSError as exc:
            raise PolicyError(f"cannot read rule file {p}: {exc}") from exc
        if not report.ok:
            raise PolicyError(f"cannot parse rule file {p}: {report.errors[0]['message']}")
        return [PolicyRule.from_dict(rule.to_dict()) for rule in parsed]
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PolicyError(f"cannot read rule file {p}: {exc}") from exc
    return _coerce_rules(data)


def load_rules_dir(path: str | Path) -> list[PolicyRule]:
    p = Path(path)
    if not p.is_dir():
        raise PolicyError(f"rule directory not found: {p}")
    rules: list[PolicyRule] = []
    for fp in sorted([*p.glob("*.json"), *p.glob("*.rules")]):
        rules.extend(load_rules_file(fp))
    return rules


def load_policy(name_or_path: str | None) -> list[PolicyRule]:
    """Load a named/embedded policy or a path; fall back to builtin baseline."""
    if not name_or_path:
        return builtin_rules()
    p = Path(name_or_path)
    if p.is_dir():
        return builtin_rules() + load_rules_dir(p)
    if p.is_file():
        return builtin_rules() + load_rules_file(p)
    # Treat as a named policy reference; baseline is always included.
    return builtin_rules()

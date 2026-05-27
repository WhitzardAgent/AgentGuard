"""Load rule packs and agent bindings from a YAML/JSON config.

Schema
------
::

    packs:
      office_assistant:
        # sources: file or directory paths, relative to the config file.
        sources:
          - rules/email.rules
          - rules/http.rules
      dev_assistant:
        sources:
          - rules/shell.rules

    bindings:
      agent_office_001:
        packs: [office_assistant]
      agent_dev_001:
        packs: [dev_assistant, office_assistant]

YAML is preferred but plain JSON works too (same shape).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

try:
    import yaml as _yaml
except ImportError:
    _yaml = None


@dataclass
class RulePackSpec:
    pack_id: str
    sources: list[str] = field(default_factory=list)


@dataclass
class RulePackConfig:
    packs: list[RulePackSpec] = field(default_factory=list)
    bindings: dict[str, list[str]] = field(default_factory=dict)
    base_dir: Path = field(default_factory=lambda: Path("."))

    def resolved_sources(self, spec: RulePackSpec) -> list[Path]:
        """Return source paths resolved against the config file's directory."""
        return [
            (self.base_dir / src).resolve() if not Path(src).is_absolute() else Path(src)
            for src in spec.sources
        ]


def _load_raw(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    suffix = path.suffix.lower()
    if suffix in {".yaml", ".yml"}:
        if _yaml is None:
            raise RuntimeError(
                "PyYAML is required to load rule pack configs. "
                "Install with `pip install agentguard[server]`."
            )
        data = _yaml.safe_load(text) or {}
    elif suffix == ".json":
        data = json.loads(text or "{}")
    else:
        # Best-effort: try YAML first (a superset of JSON), fall back to JSON.
        if _yaml is not None:
            try:
                data = _yaml.safe_load(text) or {}
            except Exception:
                data = json.loads(text or "{}")
        else:
            data = json.loads(text or "{}")
    if not isinstance(data, dict):
        raise ValueError(f"rule pack config must be a mapping, got {type(data).__name__}")
    return data


def load_rule_pack_config(path: str | Path) -> RulePackConfig:
    p = Path(path).expanduser()
    if not p.exists():
        raise FileNotFoundError(p)
    raw = _load_raw(p)

    cfg = RulePackConfig(base_dir=p.resolve().parent)

    raw_packs = raw.get("packs") or {}
    if not isinstance(raw_packs, dict):
        raise ValueError("`packs` must be a mapping of pack_id -> spec")
    for pack_id, spec_raw in raw_packs.items():
        if not isinstance(spec_raw, dict):
            raise ValueError(f"pack `{pack_id}` must be a mapping")
        srcs = spec_raw.get("sources") or []
        if isinstance(srcs, str):
            srcs = [srcs]
        if not isinstance(srcs, Iterable):
            raise ValueError(f"pack `{pack_id}`: `sources` must be a list of strings")
        cfg.packs.append(RulePackSpec(pack_id=str(pack_id), sources=[str(s) for s in srcs]))

    raw_bindings = raw.get("bindings") or {}
    if not isinstance(raw_bindings, dict):
        raise ValueError("`bindings` must be a mapping of agent_id -> spec")
    for agent_id, spec_raw in raw_bindings.items():
        if not isinstance(spec_raw, dict):
            raise ValueError(f"binding for agent `{agent_id}` must be a mapping")
        packs = spec_raw.get("packs") or []
        if isinstance(packs, str):
            packs = [packs]
        if not isinstance(packs, Iterable):
            raise ValueError(f"binding `{agent_id}`: `packs` must be a list of strings")
        cfg.bindings[str(agent_id)] = [str(p) for p in packs]

    return cfg


def apply_rule_pack_config(guard: Any, config_path: str | Path) -> RulePackConfig:
    """Load ``config_path`` and apply every pack/binding to ``guard``.

    Returns the parsed config so callers can introspect what was applied.
    """
    cfg = load_rule_pack_config(config_path)
    for spec in cfg.packs:
        guard.add_rule_pack(spec.pack_id, [str(p) for p in cfg.resolved_sources(spec)])
    for agent_id, pack_ids in cfg.bindings.items():
        for pack_id in pack_ids:
            guard.bind_agent(agent_id, pack_id)
    return cfg


__all__ = [
    "RulePackSpec",
    "RulePackConfig",
    "load_rule_pack_config",
    "apply_rule_pack_config",
]

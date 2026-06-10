"""API request/response schemas (used by routes.py)."""

from __future__ import annotations

from typing import Any

try:
    from pydantic import BaseModel, Field
except ImportError:
    BaseModel = object  # type: ignore[misc,assignment]
    Field = lambda *a, **kw: None  # type: ignore[misc]


class ResolveBody(BaseModel):  # type: ignore[misc]
    note: str = ""


class RulesBody(BaseModel):  # type: ignore[misc]
    """Body for POST /rules/reload.

    ``source`` accepts:
      - Inline DSL text (multi-line string containing RULE blocks)
      - A file path ending in ``.rules``
      - A directory path (all ``*.rules`` files inside are loaded)
      - A ``file://...`` URI
    """
    source: str = ""
    keep_builtin: bool | None = None  # type: ignore[assignment]


class RulesCheckBody(BaseModel):  # type: ignore[misc]
    """Body for POST /rules/check.

    Accepts inline DSL text only so editors can validate drafts without
    publishing rules or reading server-local files.
    """
    source: str


class AgentRuleCreateBody(BaseModel):  # type: ignore[misc]
    """Body for POST /agents/{agent_id}/rules."""
    source: str


class ToolLabelsPatchBody(BaseModel):  # type: ignore[misc]
    """Body for PATCH /agents/{agent_id}/tools/{tool_name}/labels."""
    boundary: str
    sensitivity: str
    integrity: str
    tags: list[str] = Field(default_factory=list)


class RulesWatchBody(BaseModel):  # type: ignore[misc]
    """Body for POST /rules/watch — start/stop the file watcher."""
    enabled: bool = True
    paths: list[str] = Field(default_factory=list)
    interval_s: float = 5.0


class RulePackUpsertBody(BaseModel):  # type: ignore[misc]
    """Body for POST /rule-packs.

    ``source`` accepts the same shapes as ``RulesBody.source`` but is
    interpreted as belonging to the named pack ``pack_id``.
    """
    pack_id: str = ""
    source: str | list[str] = ""  # type: ignore[assignment]


class AgentBindingBody(BaseModel):  # type: ignore[misc]
    """Body for POST /agents/{agent_id}/rule-packs."""
    pack_id: str = ""


class RulePackConfigBody(BaseModel):  # type: ignore[misc]
    """Body for POST /rule-packs/reload.

    Loads a YAML/JSON config and applies every pack/binding it defines.
    """
    config_path: str = ""


class AuditSearchQuery(BaseModel):  # type: ignore[misc]
    """Query params for GET /audit/search."""
    tool: str | None = None  # type: ignore[assignment]
    agent: str | None = None  # type: ignore[assignment]
    action: str | None = None  # type: ignore[assignment]
    rule: str | None = None    # match if this rule_id is in matched_rules
    threat_type: str | None = None
    severity: str | None = None
    since_ts: float | None = None   # unix timestamp lower bound
    until_ts: float | None = None   # unix timestamp upper bound
    n: int = 200

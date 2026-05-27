"""Minimal closure of execution-graph node / edge types."""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class NodeType(str, Enum):
    AGENT = "Agent"
    TOOL_CALL = "ToolCall"
    RESOURCE = "Resource"
    GOAL = "Goal"


class EdgeType(str, Enum):
    INVOKED = "INVOKED"               # Agent -> ToolCall
    READ_FROM = "READ_FROM"           # ToolCall -> Resource
    WROTE_TO = "WROTE_TO"             # ToolCall -> Resource
    DERIVED_FROM = "DERIVED_FROM"     # ToolCall -> ToolCall
    UNDER_GOAL = "UNDER_GOAL"         # ToolCall -> Goal
    SPAWNED = "SPAWNED"               # Agent -> Agent


class AgentNode(BaseModel):
    agent_id: str
    role: str = "default"
    trust_level: int = 0
    parent_id: str | None = None


class ToolCallNode(BaseModel):
    call_id: str
    tool_name: str
    ts_ms: int
    action: str = "allow"
    risk: float = 0.0
    sink_type: str = "none"
    args_digest: str | None = None


class ResourceNode(BaseModel):
    res_id: str
    kind: str  # file / table / url / mem / ...
    labels: list[str] = Field(default_factory=list)
    extra: dict[str, Any] = Field(default_factory=dict)


class GoalNode(BaseModel):
    goal_id: str
    text: str
    session_id: str

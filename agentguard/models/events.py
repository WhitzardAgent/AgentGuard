"""Public schemas for runtime events traveling through the pipeline.

Extends the reference implementation with the full event taxonomy from
Instruction.md §4 (lifecycle, inference, resource/security events).
"""

from __future__ import annotations

import time
import uuid
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Event taxonomy (Instruction.md §4)
# ---------------------------------------------------------------------------

class EventType(str, Enum):
    # Lifecycle
    SESSION_STARTED = "session_started"
    SESSION_ENDED = "session_ended"
    AGENT_REGISTERED = "agent_registered"
    TOOL_REGISTERED = "tool_registered"
    POLICY_LOADED = "policy_loaded"

    # Inference & execution
    AGENT_STEP_STARTED = "agent_step_started"
    AGENT_STEP_COMPLETED = "agent_step_completed"
    PLAN_PRODUCED = "plan_produced"
    THOUGHT_PRODUCED = "thought_produced"
    ACTION_PROPOSED = "action_proposed"

    # Tool call lifecycle
    TOOL_CALL_REQUESTED = "tool_call_requested"
    TOOL_CALL_APPROVED = "tool_call_approved"
    TOOL_CALL_DENIED = "tool_call_denied"
    TOOL_CALL_HUMAN_CHECK_REQUESTED = "tool_call_human_check_requested"
    TOOL_CALL_DEGRADED = "tool_call_degraded"
    TOOL_CALL_STARTED = "tool_call_started"
    TOOL_CALL_COMPLETED = "tool_call_completed"
    TOOL_CALL_FAILED = "tool_call_failed"

    # Compat aliases for ref implementation
    TOOL_CALL_ATTEMPT = "tool_call_attempt"
    TOOL_CALL_RESULT = "tool_call_result"

    # Resource & security
    SENSITIVE_RESOURCE_OBSERVED = "sensitive_resource_observed"
    SCOPE_EXPANDED = "scope_expanded"
    GOAL_DRIFT_DETECTED = "goal_drift_detected"
    EXTERNAL_SINK_DETECTED = "external_sink_detected"
    POLICY_VIOLATION_DETECTED = "policy_violation_detected"
    HUMAN_REVIEW_RESOLVED = "human_review_resolved"
    DYNAMIC_RULE_GENERATED = "dynamic_rule_generated"

    # Misc
    SUBAGENT_SPAWN = "subagent_spawn"
    MEMORY_WRITE = "memory_write"


SinkType = Literal[
    "none",
    "email",
    "http",
    "shell",
    "fs_write",
    "db_write",
    "llm_out",
]

# ---------------------------------------------------------------------------
# Tool-level static labels (declared at @guard.tool registration time).
# These describe properties of the *tool itself*, not the data flowing through.
# ---------------------------------------------------------------------------

Boundary = Literal["internal", "external", "privileged"]
Sensitivity = Literal["low", "moderate", "high"]
Integrity = Literal["trusted", "unfiltered"]


class ToolStaticLabel(BaseModel):
    """Static metadata declared once at tool registration.

    Carried verbatim onto every ToolCall so policies can reason about
    "is this tool external?" / "is this tool sensitive?" without per-call
    enrichment.
    """

    boundary: Boundary = "internal"
    sensitivity: Sensitivity = "low"
    integrity: Integrity = "trusted"
    tags: list[str] = Field(default_factory=list)


class Principal(BaseModel):
    """Who initiated the action."""

    agent_id: str
    session_id: str
    user_id: str | None = None
    task_id: str | None = None
    subagent_id: str | None = None
    parent_agent_id: str | None = None
    role: str = "default"
    trust_level: int = 0  # 0..3


class ToolCall(BaseModel):
    """What action is being attempted.

    Static metadata (boundary/sensitivity/integrity) is filled at registration
    time. Runtime metadata (result/authority/timestamp) is filled by the
    pipeline as the call progresses.
    """

    tool_name: str
    args: dict[str, Any] = Field(default_factory=dict)
    target: dict[str, Any] = Field(default_factory=dict)
    sink_type: SinkType = "none"

    # ── static label (set at registration time) ──────────────────────────
    label: ToolStaticLabel = Field(default_factory=ToolStaticLabel)

    # ── runtime info ─────────────────────────────────────────────────────
    syntax: list[str] = Field(default_factory=list)
    """Parameter names declared on the tool signature.
    Enables ``tool.<param>`` shorthand path lookups in the DSL."""

    result: Any | None = None
    """Set after the tool executes; available on tool_call.completed events."""

    authority: dict[str, Any] = Field(default_factory=dict)
    """Optional authority metadata (caller scopes / consent tokens / …)."""

    ts_ms: int | None = None
    """Per-call timestamp; mirrors RuntimeEvent.ts_ms for convenience."""


class ProvenanceRef(BaseModel):
    """Reference to a node in the execution graph along with its security label."""

    node_id: str
    label: str
    confidence: float = 1.0
    parent_tool_call_id: str | None = None
    """Optional: the tool_call event_id that produced this resource.
    When set, GraphWriter automatically builds a DERIVED_FROM edge:
      ToolCall(current) → ToolCall(parent), capturing the data flow."""


class RuntimeEvent(BaseModel):
    """Normalized event flowing from adapter -> pipeline -> policy -> enforcement."""

    event_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    ts_ms: int = Field(default_factory=lambda: int(time.time() * 1000))
    event_type: EventType
    principal: Principal
    tool_call: ToolCall | None = None
    goal: str | None = None
    scope: list[str] = Field(default_factory=list)
    provenance_refs: list[ProvenanceRef] = Field(default_factory=list)
    result: Any | None = None
    trace_id: str | None = None
    extra: dict[str, Any] = Field(default_factory=dict)

    def with_tool_call(self, tc: ToolCall) -> "RuntimeEvent":
        return self.model_copy(update={"tool_call": tc})

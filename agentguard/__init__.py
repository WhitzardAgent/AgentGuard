"""AgentGuard — Actor-based runtime access control plane for agent tool-use."""

from agentguard.models.events import EventType, Principal, RuntimeEvent, ToolCall, ProvenanceRef
from agentguard.models.decisions import Action, Decision, Obligation
from agentguard.models.errors import (
    AgentGuardError,
    DecisionDenied,
    HumanApprovalPending,
    RuleCompileError,
)
from agentguard.sdk.guard import Guard
from agentguard.policy.rules.dynamic_store import (
    DynamicRuleConfig,
    TriggerPolicy,
    DynamicRuleUpdater,
)

# ── Client-side Harness / PEP runtime (v2 architecture) ──────────────────────
from agentguard.facade import AgentGuard

__all__ = [
    "Guard",
    "AgentGuard",
    "EventType",
    "Principal",
    "RuntimeEvent",
    "ToolCall",
    "ProvenanceRef",
    "Action",
    "Decision",
    "Obligation",
    "AgentGuardError",
    "DecisionDenied",
    "HumanApprovalPending",
    "RuleCompileError",
    "DynamicRuleConfig",
    "TriggerPolicy",
    "DynamicRuleUpdater",
]

"""Built-in default rules — a sensible enterprise baseline.

These cover the common dangerous behaviours the Harness intercepts:

* destructive shell commands         → deny
* network egress carrying PII        → sanitize
* file writes outside the workspace  → require_approval
* prompt-injection in observations   → deny
* uncertain / low-confidence thoughts→ ask_user
* all LLM thoughts                    → log_only (so reasoning is audited)
"""

from __future__ import annotations

from agentguard.policies.dsl import when
from agentguard.policies.rule import Rule
from agentguard.schemas.context import RuntimeContext
from agentguard.schemas.events import EventType, RuntimeEvent

_DESTRUCTIVE = ("rm -rf", "mkfs", "dd if=", ":(){", "shutdown", "format c:")


def _is_destructive_shell(event: RuntimeEvent, _ctx: RuntimeContext) -> bool:
    blob = f"{event.args} {event.content or ''}".lower()
    return event.sink_type == "shell" or any(tok in blob for tok in _DESTRUCTIVE)


def _network_with_pii(event: RuntimeEvent, _ctx: RuntimeContext) -> bool:
    return bool(event.annotations.get("pii_detected"))


def _file_outside_workspace(event: RuntimeEvent, ctx: RuntimeContext) -> bool:
    path = str(event.args.get("path", event.payload.get("path", "")))
    if not path:
        return False
    workspace = str(ctx.metadata.get("workspace", "")) or "/workspace"
    normalized = path if path.startswith("/") else f"{workspace}/{path}"
    return not normalized.startswith(workspace)


def _has_injection(event: RuntimeEvent, _ctx: RuntimeContext) -> bool:
    return bool(event.annotations.get("prompt_injection"))


def _is_uncertain(event: RuntimeEvent, _ctx: RuntimeContext) -> bool:
    return bool(event.annotations.get("uncertain"))


def builtin_rules() -> list[Rule]:
    return [
        when("builtin.destructive_shell", EventType.TOOL_CALL, EventType.NETWORK_ACTION)
        .where(_is_destructive_shell)
        .priority(0)
        .risk(1.0)
        .deny("destructive or irreversible shell command"),

        when("builtin.injection_in_observation", EventType.TOOL_OBSERVATION, EventType.LLM_PROMPT)
        .where(_has_injection)
        .priority(0)
        .risk(0.9)
        .deny("prompt-injection pattern detected in untrusted content"),

        when("builtin.network_pii", EventType.NETWORK_ACTION, EventType.TOOL_CALL)
        .where(_network_with_pii)
        .priority(10)
        .risk(0.7)
        .obligation("mask_pii")
        .sanitize("PII detected in outbound network payload"),

        when("builtin.file_outside_workspace", EventType.FILE_OP)
        .where(_file_outside_workspace)
        .priority(10)
        .risk(0.6)
        .require_approval("file write outside the permitted workspace"),

        when("builtin.uncertain_thought", EventType.LLM_THOUGHT)
        .where(_is_uncertain)
        .priority(50)
        .risk(0.4)
        .ask_user("model expressed low confidence; confirm before proceeding"),

        when("builtin.log_thoughts", EventType.LLM_THOUGHT)
        .where(lambda e, c: True)
        .priority(900)
        .log_only("audit internal reasoning"),
    ]

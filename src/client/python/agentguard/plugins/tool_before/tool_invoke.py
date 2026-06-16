"""Checker for tool invocation events."""
from __future__ import annotations

from agentguard.plugins.base import BaseChecker, CheckResult
from agentguard.plugins.common.patterns import SHELL_RE, find_signals, text_of
from agentguard.plugins.registry import register
from agentguard.schemas.context import RuntimeContext
from agentguard.schemas.decisions import GuardDecision
from agentguard.schemas.events import EventType, RuntimeEvent
from agentguard.tools.capability import (
    CAP_EXTERNAL_SEND,
    CAP_SHELL,
)

_DANGEROUS_SHELL = ("rm -rf /", "mkfs", ":(){", "dd if=")


@register(
    name="tool_invoke",
    description="Detect risky tool invocation arguments and dangerous capabilities.",
)
class ToolInvokeChecker(BaseChecker):
    event_types = [EventType.TOOL_INVOKE]

    def check(self, event: RuntimeEvent, context: RuntimeContext) -> CheckResult:
        payload = event.payload
        caps = set(payload.get("capabilities") or [])
        args_text = text_of(payload.get("arguments"))
        signals = find_signals(args_text)

        if CAP_EXTERNAL_SEND in caps:
            signals.append("external_send")
        if CAP_SHELL in caps or SHELL_RE.search(args_text):
            signals.append("shell_command")

        candidate = None
        is_final = False
        low = args_text.lower()
        if any(d in low for d in _DANGEROUS_SHELL):
            candidate = GuardDecision.deny(
                "Destructive shell command blocked by local checker.",
                policy_id="local:dangerous_shell",
                risk_signals=["shell_command"],
            )
            is_final = True
        return CheckResult(
            decision_candidate=candidate,
            risk_signals=sorted(set(signals)),
            is_final=is_final,
        )

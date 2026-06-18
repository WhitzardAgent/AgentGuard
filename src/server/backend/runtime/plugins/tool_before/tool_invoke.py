"""Plugin for tool invocation events."""
from __future__ import annotations

from shared.schemas.context import RuntimeContext
from shared.schemas.decisions import GuardDecision
from shared.schemas.events import EventType, RuntimeEvent
from shared.tools.capability import (
    CAP_EXTERNAL_SEND,
    CAP_SHELL,
)
from backend.runtime.plugins.base import BasePlugin, CheckResult
from backend.runtime.plugins.common.patterns import SHELL_RE, find_signals, text_of
from backend.runtime.plugins.registry import register

_DANGEROUS_SHELL = ("rm -rf /", "mkfs", ":(){", "dd if=")
_TRACE_EXFIL_SIGNALS = {"secret_detected", "api_key_detected", "system_prompt_leak"}


@register(
    name="tool_invoke",
    description="Detect risky tool invocation arguments and dangerous capabilities.",
)
class ToolInvokePlugin(BasePlugin):
    event_types = [EventType.TOOL_INVOKE]

    def check(
        self,
        event: RuntimeEvent,
        context: RuntimeContext,
        trajectory_window: list[RuntimeEvent] | None = None,
    ) -> CheckResult:
        payload = event.payload
        caps = set(payload.get("capabilities") or [])
        args_text = text_of(payload.get("arguments"))
        signals = find_signals(args_text)

        if CAP_EXTERNAL_SEND in caps:
            signals.append("external_send")
            trace_signals = {signal for item in (trajectory_window or []) for signal in (item.risk_signals or [])}
            if _TRACE_EXFIL_SIGNALS & (trace_signals | set(signals)):
                signals.append("exfiltration_detected")
        if CAP_SHELL in caps or SHELL_RE.search(args_text):
            signals.append("shell_command")

        candidate = None
        is_final = False
        low = args_text.lower()
        if any(d in low for d in _DANGEROUS_SHELL):
            candidate = GuardDecision.deny(
                "Destructive shell command blocked by local plugin.",
                policy_id="local:dangerous_shell",
                risk_signals=["shell_command"],
            )
            is_final = True
        return CheckResult(
            decision_candidate=candidate,
            risk_signals=sorted(set(signals)),
            is_final=is_final,
        )

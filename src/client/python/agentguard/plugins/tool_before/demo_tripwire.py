"""Demo-only plugin that makes custom local plugin effects easy to observe."""
from __future__ import annotations

from agentguard.plugins.base import BasePlugin, CheckResult
from agentguard.plugins.registry import register
from agentguard.schemas.context import RuntimeContext
from agentguard.schemas.decisions import GuardDecision
from agentguard.schemas.events import EventType, RuntimeEvent


@register(
    name="demo_tripwire",
    description="Demo plugin that blocks secret-like local reads and sends to demo hosts.",
)
class DemoTripwirePlugin(BasePlugin):
    event_types = [EventType.TOOL_INVOKE]

    def check(self, event: RuntimeEvent, context: RuntimeContext) -> CheckResult:
        _ = context
        payload = event.payload or {}
        tool_name = str(payload.get("tool_name") or "")
        arguments = payload.get("arguments") or {}
        signals = ["demo_plugin_seen"]

        path = str(arguments.get("path") or "")
        url = str(arguments.get("url") or "")
        lower_path = path.lower()
        lower_url = url.lower()

        if tool_name == "read_local_file" and "secret" in lower_path:
            signals.append("demo_secret_file")
            return CheckResult(
                decision_candidate=GuardDecision.deny(
                    f"Demo plugin blocked suspicious file read: {path}",
                    policy_id="local:demo_tripwire:secret_path",
                    risk_signals=list(signals),
                    metadata={
                        "plugin": self.name,
                        "matched_rule": "secret_path",
                        "tool_name": tool_name,
                        "path": path,
                    },
                ),
                risk_signals=list(signals),
                is_final=True,
            )

        if tool_name == "send_http" and "example.com" in lower_url:
            signals.append("demo_external_send")
            return CheckResult(
                decision_candidate=GuardDecision.deny(
                    f"Demo plugin blocked outbound send to demo host: {url}",
                    policy_id="local:demo_tripwire:blocked_host",
                    risk_signals=list(signals),
                    metadata={
                        "plugin": self.name,
                        "matched_rule": "blocked_host",
                        "tool_name": tool_name,
                        "url": url,
                    },
                ),
                risk_signals=list(signals),
                is_final=True,
            )

        if tool_name == "read_local_file":
            signals.append("demo_read_observed")
        elif tool_name == "send_http":
            signals.append("demo_send_observed")
        else:
            return CheckResult.empty()

        return CheckResult(risk_signals=list(dict.fromkeys(signals)))

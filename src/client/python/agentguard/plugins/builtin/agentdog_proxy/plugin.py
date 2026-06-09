"""AgentDoG client proxy plugin. Adds trajectory context; never decides."""
from __future__ import annotations

from typing import Any

from agentguard.plugins.base import ClientPlugin
from agentguard.plugins.builtin.agentdog_proxy.config import AgentDoGProxyConfig
from agentguard.plugins.builtin.agentdog_proxy.formatter import format_trajectory
from agentguard.schemas.context import RuntimeContext

# Signals that should force a remote AgentDoG review.
_HIGH_RISK_SIGNALS = {
    "secret_detected",
    "api_key_detected",
    "prompt_injection",
    "tool_result_injection",
    "external_send",
    "system_prompt_leak",
}


class AgentDoGProxyPlugin(ClientPlugin):
    plugin_id = "agentdog_proxy"

    def __init__(self, config: AgentDoGProxyConfig | None = None) -> None:
        self.config = config or AgentDoGProxyConfig()

    def on_before_remote_decision(
        self, request: dict[str, Any], context: RuntimeContext
    ) -> dict[str, Any]:
        if not self.config.enabled:
            return request
        window = request.get("trajectory_window") or []
        trajectory = format_trajectory(window, self.config)
        ext = request.setdefault("plugin_extensions", {})
        ext["agentdog"] = {
            "config": {
                "window_size": self.config.window_size,
                "redaction_level": self.config.redaction_level,
            },
            "trajectory_window": trajectory,
            "local_signals": _collect_signals(window),
        }
        if self.config.force_remote_on_high_risk and _is_high_risk(window):
            ext["force_remote"] = True
        return request

    def on_after_remote_decision(self, response: Any, context: RuntimeContext) -> Any:
        # `response` is the merged GuardDecision; attach diagnosis risk signals.
        results = getattr(response, "metadata", {}).get("plugin_results", {}) if response else {}
        diagnosis = (results or {}).get("agentdog") or {}
        for label in diagnosis.get("risk_signals", []) or []:
            if label not in response.risk_signals:
                response.risk_signals.append(label)
        if diagnosis:
            response.metadata.setdefault("agentdog_diagnosis", diagnosis)
        return response


def _collect_signals(window: list[dict[str, Any]]) -> list[str]:
    signals: list[str] = []
    for ev in window:
        for s in ev.get("risk_signals") or []:
            if s not in signals:
                signals.append(s)
    return signals


def _is_high_risk(window: list[dict[str, Any]]) -> bool:
    return bool(set(_collect_signals(window)) & _HIGH_RISK_SIGNALS)

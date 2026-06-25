"""AgentDog model-backed safety plugin for tool invocation events."""
from __future__ import annotations

import time
from typing import Any

from backend.runtime.plugins.base import BasePlugin, CheckResult
from backend.runtime.plugins.registry import register
from backend.runtime.plugins.tool_before.agentdog.client import (
    AgentDogClient,
    AgentDogModelResult,
)
from backend.runtime.plugins.tool_before.agentdog.formatter import (
    FormattedAgentDogTrajectory,
    format_agentdog_trajectory,
)
from backend.runtime.plugins.tool_before.agentdog.prompt import build_agentdog_prompt
from shared.audit.redactor import redact
from shared.schemas.context import RuntimeContext
from shared.schemas.decisions import GuardDecision
from shared.schemas.events import EventType, RuntimeEvent


@register(
    name="agentdog",
    description="Evaluate tool-before trajectory safety using an AgentDog online model service.",
)
class AgentDogPlugin(BasePlugin):
    event_types = [EventType.TOOL_INVOKE]

    def check(
        self,
        event: RuntimeEvent,
        context: RuntimeContext,
        trajectory_window: list[RuntimeEvent] | None = None,
    ) -> CheckResult:
        del context
        url = str(getattr(self, "agentdog_url", "") or "").strip()
        if not url:
            return CheckResult(
                metadata={
                    "agentdog": {
                        "error": "agentdog_url is required",
                        "decision": "fail_open",
                    }
                }
            )

        started = time.time()
        formatted = format_agentdog_trajectory(
            [*(trajectory_window or []), event],
            max_chars=_int_config(getattr(self, "max_trajectory_chars", 24000), 24000),
        )
        prompt = build_agentdog_prompt(
            trajectory=formatted.trajectory,
            tool_list=formatted.tool_list,
        )

        try:
            client = self._build_client(url)
            result = client.evaluate(prompt)
        except Exception as exc:
            return CheckResult(
                metadata={
                    "agentdog": {
                        **_base_metadata(formatted, started),
                        "error": str(exc),
                        "decision": "fail_open",
                    }
                }
            )

        metadata = {
            "agentdog": {
                **_base_metadata(formatted, started),
                "prediction": result.prediction,
                "label": _prediction_label(result.prediction),
                "reason": result.reason,
                "raw_response": _truncate_text(redact(result.raw_response), 2000),
                "content": _truncate_text(redact(result.content), 1000),
            }
        }
        if result.prediction == 1:
            return CheckResult(
                decision_candidate=GuardDecision.deny(
                    result.reason or "AgentDog judged the trajectory unsafe.",
                    policy_id="server:agentdog",
                    risk_signals=["agentdog_unsafe"],
                    metadata=metadata["agentdog"],
                ),
                risk_signals=["agentdog_unsafe"],
                is_final=True,
                metadata=metadata,
            )
        return CheckResult(metadata=metadata)

    def _build_client(self, url: str) -> Any:
        timeout_s = _float_config(getattr(self, "timeout_s", 10.0), 10.0)
        api_key = str(getattr(self, "agentdog_apiKey", "") or "")
        client_factory = getattr(self, "client_factory", None)
        if client_factory is not None:
            return client_factory(url, api_key, timeout_s)
        return AgentDogClient(url, api_key=api_key, timeout_s=timeout_s)


def _base_metadata(
    formatted: FormattedAgentDogTrajectory,
    started: float,
) -> dict[str, Any]:
    return {
        "trajectory_chars": formatted.trajectory_chars,
        "tool_count": len(formatted.tool_list),
        "tools": list(formatted.tool_list),
        "latency_ms": round((time.time() - started) * 1000, 2),
    }


def _prediction_label(prediction: int) -> str:
    if prediction == 0:
        return "safe"
    if prediction == 1:
        return "unsafe"
    return "error"


def _float_config(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _int_config(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _truncate_text(value: Any, max_chars: int) -> str:
    text = str(value or "")
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 3] + "..."

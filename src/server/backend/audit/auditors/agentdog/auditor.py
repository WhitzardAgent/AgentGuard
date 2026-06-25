"""AgentDog trace auditor."""
from __future__ import annotations

import time
from typing import Any

from backend.audit.auditors.agentdog.client import AgentDogClient
from backend.audit.auditors.agentdog.config import AgentDogAuditConfig
from backend.audit.auditors.agentdog.formatter import (
    FormattedAgentDogTrajectory,
    format_agentdog_trajectory,
)
from backend.audit.auditors.agentdog.prompt import build_agentdog_prompt
from backend.audit.base import AuditResult, AuditTraceEntry, BaseAuditor
from backend.audit.registry import register
from shared.audit.redactor import redact
from shared.schemas.events import RuntimeEvent


@register(
    name="agentdog_trace",
    description="Evaluate a completed trace using an AgentDog trajectory judge.",
)
class AgentDogTraceAuditor(BaseAuditor):
    def audit(self, trace: list[AuditTraceEntry]) -> AuditResult:
        config = AgentDogAuditConfig.from_env().to_plugin_style_dict()
        url = str(config.get("agentdog_url") or "").strip()
        api_key = str(config.get("agentdog_apiKey") or "").strip()
        events = _events_from_trace(trace)
        event_ids = [event.event_id for event in events if event.event_id]
        if not url:
            return AuditResult(
                level="warning",
                reason="AgentDog audit skipped because agentdog_url is not configured.",
                metadata={
                    "agentdog": {
                        "decision": "not_configured",
                        "missing": ["agentdog_url"],
                    },
                    "event_ids": event_ids,
                    "trace_entries": len(trace),
                },
            )
        if not events:
            return AuditResult.ok("No runtime events were available for AgentDog audit.")

        started = time.time()
        formatted = format_agentdog_trajectory(events)
        prompt = build_agentdog_prompt(
            trajectory=formatted.trajectory,
            tool_list=formatted.tool_list,
        )
        try:
            result = AgentDogClient(url, api_key=api_key).evaluate(prompt)
        except Exception as exc:
            return AuditResult(
                level="warning",
                reason=f"AgentDog audit failed: {exc}",
                metadata={
                    "agentdog": {
                        **_base_metadata(formatted, started),
                        "decision": "error",
                        "error": _truncate_text(redact(str(exc)), 1000),
                    },
                    "event_ids": event_ids,
                    "trace_entries": len(trace),
                },
            )

        label = _prediction_label(result.prediction)
        agentdog_meta = {
            **_base_metadata(formatted, started),
            "prediction": result.prediction,
            "label": label,
            "reason": result.reason,
            "raw_response": _truncate_text(redact(result.raw_response), 2000),
            "content": _truncate_text(redact(result.content), 1000),
        }
        metadata: dict[str, Any] = {
            "agentdog": agentdog_meta,
            "event_ids": event_ids,
            "trace_entries": len(trace),
        }
        if result.prediction == 1:
            metadata["risk_signals"] = ["agentdog_unsafe"]
            return AuditResult(
                level="critical",
                reason=result.reason or "AgentDog judged the completed trace unsafe.",
                metadata=metadata,
            )
        return AuditResult(
            level="ok",
            reason=result.reason or "AgentDog judged the completed trace safe.",
            metadata=metadata,
        )


def _events_from_trace(trace: list[AuditTraceEntry]) -> list[RuntimeEvent]:
    return [entry.event for entry in trace if entry.event is not None]


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


def _truncate_text(value: Any, max_chars: int) -> str:
    text = str(value or "")
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 3] + "..."

"""Built-in trace auditor that summarizes trace risk level."""
from __future__ import annotations

from collections import Counter
from typing import Any

from backend.audit.base import AuditResult, BaseAuditor
from backend.audit.registry import register
from backend.runtime.storage import trace_entry_event_dict

_CRITICAL_SIGNALS = {
    "credential_theft",
    "data_exfiltration",
    "exfiltration_detected",
    "secret_detected",
    "api_key_detected",
    "system_prompt_leak",
}
_HIGH_SIGNALS = {
    "prompt_injection",
    "sensitive_file_access",
    "privilege_escalation",
    "tool_misuse",
}
_CRITICAL_DECISIONS = {"deny", "require_remote_review"}
_HIGH_DECISIONS = {"require_approval", "ask_user"}
_WARNING_DECISIONS = {"degrade", "sanitize", "log_only"}


@register(
    name="trace_risk_summary",
    description="Summarize a full trace into critical/high/warning/ok based on observed signals and decisions.",
)
class TraceRiskSummaryAuditor(BaseAuditor):
    def audit(
        self,
        trace: list[dict[str, Any]],
        *,
        session_id: str,
        agent_id: str | None = None,
        user_id: str | None = None,
    ) -> AuditResult:
        signal_counter: Counter[str] = Counter()
        decision_counter: Counter[str] = Counter()
        event_ids: list[str] = []
        reasons: list[str] = []

        for record in trace:
            event = trace_entry_event_dict(record) or {}
            decision = record.get("decision") if isinstance(record.get("decision"), dict) else {}
            event_id = event.get("event_id") or record.get("event_id")
            if event_id:
                event_ids.append(str(event_id))
            signals = _signals_from_record(record, event, decision)
            signal_counter.update(signals)
            decision_type = decision.get("decision_type")
            if isinstance(decision_type, str) and decision_type:
                decision_counter.update([decision_type])
            decision_reason = decision.get("reason")
            if isinstance(decision_reason, str) and decision_reason:
                reasons.append(decision_reason)

        critical_signals = sorted(signal for signal in signal_counter if signal in _CRITICAL_SIGNALS)
        high_signals = sorted(signal for signal in signal_counter if signal in _HIGH_SIGNALS)
        critical_decisions = sorted(decision for decision in decision_counter if decision in _CRITICAL_DECISIONS)
        high_decisions = sorted(decision for decision in decision_counter if decision in _HIGH_DECISIONS)
        warning_decisions = sorted(decision for decision in decision_counter if decision in _WARNING_DECISIONS)

        if critical_signals or critical_decisions:
            level = "critical"
            reason = _build_reason(
                "Observed critical findings in trace",
                critical_signals=critical_signals,
                critical_decisions=critical_decisions,
                extra_reason=reasons[0] if reasons else None,
            )
        elif high_signals or high_decisions:
            level = "high"
            reason = _build_reason(
                "Observed high-risk findings in trace",
                high_signals=high_signals,
                high_decisions=high_decisions,
                extra_reason=reasons[0] if reasons else None,
            )
        elif signal_counter or warning_decisions:
            level = "warning"
            reason = _build_reason(
                "Observed warning-level findings in trace",
                warning_signals=sorted(signal_counter),
                warning_decisions=warning_decisions,
                extra_reason=reasons[0] if reasons else None,
            )
        else:
            level = "ok"
            reason = "No risky decisions or risk signals were found in trace."

        return AuditResult(
            level=level,
            reason=reason,
            metadata={
                "session_id": session_id,
                "agent_id": agent_id,
                "user_id": user_id,
                "trace_entries": len(trace),
                "event_ids": event_ids,
                "signal_counts": dict(signal_counter),
                "decision_counts": dict(decision_counter),
            },
        )


def _signals_from_record(
    record: dict[str, Any],
    event: dict[str, Any],
    decision: dict[str, Any],
) -> list[str]:
    signals: list[str] = []
    for candidate in (
        record.get("risk_signals"),
        event.get("risk_signals"),
        decision.get("risk_signals"),
    ):
        if not isinstance(candidate, list):
            continue
        for signal in candidate:
            if isinstance(signal, str) and signal and signal not in signals:
                signals.append(signal)
    return signals


def _build_reason(prefix: str, extra_reason: str | None = None, **groups: list[str]) -> str:
    details = [prefix]
    for label, values in groups.items():
        if values:
            details.append(f"{label}={', '.join(values)}")
    if extra_reason:
        details.append(f"example_reason={extra_reason}")
    return "; ".join(details)

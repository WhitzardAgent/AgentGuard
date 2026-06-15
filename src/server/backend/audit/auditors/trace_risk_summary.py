"""Built-in trace auditor that summarizes trace risk level."""
from __future__ import annotations

from collections import Counter

from backend.audit.base import AuditResult, AuditTraceEntry, BaseAuditor
from backend.audit.registry import register

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
        trace: list[AuditTraceEntry],
    ) -> AuditResult:
        signal_counter: Counter[str] = Counter()
        decision_counter: Counter[str] = Counter()
        event_ids: list[str] = []
        reasons: list[str] = []

        for entry in trace:
            if entry.event_id:
                event_ids.append(entry.event_id)
            signal_counter.update(_signals_from_entry(entry))
            decision_type = entry.decision.decision_type.value if entry.decision is not None else None
            if decision_type:
                decision_counter.update([decision_type])
            if entry.decision is not None and entry.decision.reason:
                reasons.append(entry.decision.reason)

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
                "trace_entries": len(trace),
                "event_ids": event_ids,
                "signal_counts": dict(signal_counter),
                "decision_counts": dict(decision_counter),
                "session_ids": _identity_values(trace, "session_id"),
                "agent_ids": _identity_values(trace, "agent_id"),
                "user_ids": _identity_values(trace, "user_id"),
            },
        )


def _signals_from_entry(entry: AuditTraceEntry) -> list[str]:
    signals: list[str] = []
    candidates = [
        entry.event.risk_signals if entry.event is not None else [],
        entry.decision.risk_signals if entry.decision is not None else [],
        entry.checker_result.get("risk_signals") if isinstance(entry.checker_result, dict) else [],
    ]
    for candidate in candidates:
        if not isinstance(candidate, list):
            continue
        for signal in candidate:
            if isinstance(signal, str) and signal and signal not in signals:
                signals.append(signal)
    return signals


def _identity_values(trace: list[AuditTraceEntry], field_name: str) -> list[str]:
    values: list[str] = []
    for entry in trace:
        value = getattr(entry, field_name)
        if isinstance(value, str) and value and value not in values:
            values.append(value)
    return values


def _build_reason(prefix: str, extra_reason: str | None = None, **groups: list[str]) -> str:
    details = [prefix]
    for label, values in groups.items():
        if values:
            details.append(f"{label}={', '.join(values)}")
    if extra_reason:
        details.append(f"example_reason={extra_reason}")
    return "; ".join(details)

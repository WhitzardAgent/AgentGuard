"""Map an AgentDoG diagnosis into policy-facing signals and hints."""
from __future__ import annotations

from typing import Any

from backend.plugins.builtin.agentdog.schemas import AgentDoGDiagnosis


def map_diagnosis(diagnosis: AgentDoGDiagnosis) -> dict[str, Any]:
    """Produce risk_signals, decision_hints, policy/audit metadata."""
    risk_signals: list[str] = []
    if "data_exfiltration" in diagnosis.consequence_labels:
        risk_signals.append("exfiltration_detected")
    if "instruction_hijack" in diagnosis.failure_mode_labels:
        risk_signals.append("instruction_hijack")
    if diagnosis.risk_level in ("high", "critical"):
        risk_signals.append("agentdog_high_risk")
    # Surface the original source signals too.
    for s in diagnosis.source_labels:
        risk_signals.append(f"source:{s}")

    return {
        "risk_signals": risk_signals,
        "decision_hints": [diagnosis.decision_hint] if diagnosis.decision_hint else [],
        "policy_metadata": {
            "agentdog_risk_score": diagnosis.risk_score,
            "agentdog_risk_level": diagnosis.risk_level,
        },
        "audit_metadata": {
            "root_cause": diagnosis.root_cause,
            "unsafe_event_ids": diagnosis.unsafe_event_ids,
        },
        "diagnosis": diagnosis.to_dict(),
    }

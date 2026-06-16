"""Build a human-readable AgentDoG report from a diagnosis."""
from __future__ import annotations

from backend.plugins.builtin.agentdog.schemas import AgentDoGDiagnosis


class AgentDoGReportBuilder:
    def build(self, diagnosis: AgentDoGDiagnosis) -> str:
        lines = [
            f"AgentDoG risk: {diagnosis.risk_level} (score {diagnosis.risk_score})",
        ]
        if diagnosis.source_labels:
            lines.append(f"  source: {', '.join(diagnosis.source_labels)}")
        if diagnosis.failure_mode_labels:
            lines.append(f"  failure: {', '.join(diagnosis.failure_mode_labels)}")
        if diagnosis.consequence_labels:
            lines.append(f"  consequence: {', '.join(diagnosis.consequence_labels)}")
        if diagnosis.root_cause:
            lines.append(f"  root cause: {diagnosis.root_cause}")
        if diagnosis.decision_hint:
            lines.append(f"  hint: {diagnosis.decision_hint}")
        return "\n".join(lines)

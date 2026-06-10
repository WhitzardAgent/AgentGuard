"""Risk level labels and scoring helpers."""
from __future__ import annotations

RISK_LEVELS = ("low", "medium", "high", "critical")

HIGH_RISK_SIGNALS = {
    "secret_detected",
    "api_key_detected",
    "system_prompt_leak",
    "prompt_injection",
    "tool_result_injection",
    "external_send",
}


def level_from_score(score: float) -> str:
    if score >= 0.85:
        return "critical"
    if score >= 0.6:
        return "high"
    if score >= 0.3:
        return "medium"
    return "low"


def score_from_signals(signals: list[str]) -> float:
    if not signals:
        return 0.0
    strong = len(set(signals) & HIGH_RISK_SIGNALS)
    base = min(0.2 * len(signals), 0.6)
    return min(base + 0.3 * strong, 1.0)

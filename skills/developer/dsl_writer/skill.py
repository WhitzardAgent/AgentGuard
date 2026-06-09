"""DSLWriterSkill: deterministic natural-language -> rule JSON."""
from __future__ import annotations

import re

from skills.base import BaseSkill, SkillInput, SkillOutput

# Intent keyword -> (capabilities, risk_signals, default_effect)
_CAP_KEYWORDS = {
    "external_send": (["external_send"], [], "deny"),
    "send email": (["external_send"], [], "require_remote_review"),
    "email": (["external_send"], [], "require_remote_review"),
    "shell": (["shell"], ["shell_command"], "require_remote_review"),
    "run command": (["shell"], ["shell_command"], "require_remote_review"),
    "file write": (["write_file"], [], "require_approval"),
    "write file": (["write_file"], [], "require_approval"),
    "file read": (["read_file"], [], "log_only"),
    "network": (["network"], [], "require_remote_review"),
    "database": (["database_write"], ["database_write"], "require_approval"),
    "payment": (["payment"], [], "require_approval"),
    "memory write": (["memory_write"], ["memory_write_secret"], "require_approval"),
}
_SIGNAL_KEYWORDS = {
    "api key": "api_key_detected",
    "api-key": "api_key_detected",
    "secret": "secret_detected",
    "password": "secret_detected",
    "pii": "pii_detected",
    "email address": "pii_email",
    "system prompt": "system_prompt_leak",
    "prompt injection": "prompt_injection",
    "tool result injection": "tool_result_injection",
}
_EFFECT_KEYWORDS = {
    "block": "deny",
    "deny": "deny",
    "forbid": "deny",
    "prevent": "deny",
    "require approval": "require_approval",
    "approval": "require_approval",
    "remote review": "require_remote_review",
    "escalate": "require_remote_review",
    "degrade": "degrade",
    "downgrade": "degrade",
    "sanitize": "sanitize",
    "redact": "sanitize",
    "log only": "log_only",
}


class DSLWriterSkill(BaseSkill):
    name = "dsl_writer"
    description = "Convert natural-language policy intent into AgentGuard rule JSON."

    def run(self, input: SkillInput) -> SkillOutput:  # noqa: A002
        text = (input.instruction or "").lower()
        if not text.strip():
            return SkillOutput(False, {"rules": []}, explanation="empty instruction")

        warnings: list[str] = []
        caps: list[str] = []
        signals: list[str] = []
        effect: str | None = None

        for kw, eff in _EFFECT_KEYWORDS.items():
            if kw in text:
                effect = eff
                break
        for kw, (kcaps, ksig, keff) in _CAP_KEYWORDS.items():
            if kw in text:
                caps.extend(kcaps)
                signals.extend(ksig)
                effect = effect or keff
        for kw, sig in _SIGNAL_KEYWORDS.items():
            if kw in text:
                signals.append(sig)

        caps = sorted(set(caps))
        signals = sorted(set(signals))

        if effect is None:
            effect = "require_remote_review"
            warnings.append("ambiguous intent; defaulted effect to require_remote_review")
        if not caps and not signals:
            warnings.append("no capability or risk signal detected; rule may be too broad")

        event_types = ["tool_invoke"] if caps else ["llm_output", "final_response"]
        if "final response" in text or "output" in text:
            event_types = ["llm_output", "final_response"]

        rule = {
            "rule_id": self._rule_id(effect, caps, signals),
            "effect": effect,
            "reason": (input.instruction or "").strip()[:160] or "generated rule",
            "priority": 80 if effect == "deny" else 50,
            "event_types": event_types,
            "tool_names": [],
            "capabilities": caps,
            "risk_signals": signals,
            "conditions": [],
            "metadata": {"generated_by": "dsl_writer"},
        }
        return SkillOutput(
            True,
            {"rules": [rule]},
            explanation=f"generated 1 rule with effect '{effect}'",
            warnings=warnings,
        )

    @staticmethod
    def _rule_id(effect: str, caps: list[str], signals: list[str]) -> str:
        token = "_".join(caps + signals) or "generic"
        token = re.sub(r"[^a-z0-9_]", "", token)
        return f"{effect}_{token}"[:60]

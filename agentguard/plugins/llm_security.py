"""Optional prompt-pack based LLM security detection plugin."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from agentguard.llm.security_review import (
    PromptDetector,
    PromptSecurityReviewer,
    SecurityReviewOrchestrator,
    SecurityReviewRequest,
)
from agentguard.models.decisions import Action, ClientAction, Decision
from agentguard.models.events import EventType, RuntimeEvent
from agentguard.models.security_review import SecurityReviewResult, ThreatSeverity

log = logging.getLogger(__name__)

_SECURITY_REVIEW_RISK: dict[ThreatSeverity, float] = {
    ThreatSeverity.NONE: 0.0,
    ThreatSeverity.LOW: 0.2,
    ThreatSeverity.MEDIUM: 0.45,
    ThreatSeverity.HIGH: 0.75,
    ThreatSeverity.CRITICAL: 1.0,
}

_MODEL_ACTIVITY_EVENTS = {
    EventType.AGENT_STEP_STARTED,
    EventType.AGENT_STEP_COMPLETED,
    EventType.PLAN_PRODUCED,
    EventType.THOUGHT_PRODUCED,
    EventType.ACTION_PROPOSED,
}


class LLMSecurityReviewPlugin:
    """Prompt-pack LLM detector using one shared OpenAI-compatible backend."""

    def __init__(
        self,
        *,
        llm_backend: Any = "env",
        enabled_detectors: list[str] | None = None,
        detectors: list[PromptDetector] | None = None,
        mode: str = "combined",
        trace_max_steps: int | None = None,
        review_model_activity: bool = True,
    ) -> None:
        self._llm_backend = llm_backend
        self._enabled_detectors = enabled_detectors
        self._detectors = detectors
        self._mode = mode
        self._trace_max_steps = trace_max_steps
        self._review_model_activity = review_model_activity
        self._guard: Any | None = None
        self._reviewer: Any | None = None
        self._hook_attached = False

    def setup(self, guard: Any) -> None:
        self._guard = guard
        self._reviewer = self._build_reviewer()
        enforcer = getattr(getattr(guard, "pipeline", None), "enforcer", None)
        if enforcer is not None and hasattr(enforcer, "set_llm_check_resolver"):
            enforcer.set_llm_check_resolver(self.resolve_llm_check)

        slow = getattr(getattr(guard, "pipeline", None), "_slow", None)
        if self._review_model_activity and slow is not None and hasattr(slow, "evaluator"):
            slow.evaluator().add_hook(self._slow_hook)
            self._hook_attached = True

    def teardown(self) -> None:
        guard = self._guard
        if guard is not None:
            enforcer = getattr(getattr(guard, "pipeline", None), "enforcer", None)
            if enforcer is not None and hasattr(enforcer, "set_llm_check_resolver"):
                enforcer.set_llm_check_resolver(None)
            slow = getattr(getattr(guard, "pipeline", None), "_slow", None)
            if self._hook_attached and slow is not None and hasattr(slow, "evaluator"):
                slow.evaluator().remove_hook(self._slow_hook)
        self._hook_attached = False
        self._guard = None

    def resolve_llm_check(self, event: RuntimeEvent, decision: Decision) -> Decision:
        try:
            result = self._review(
                SecurityReviewRequest(
                    event=event,
                    decision=decision,
                    custom_prompt=decision.llm_system_prompt,
                )
            )
        except Exception as exc:
            log.warning("LLM security review failed: %s", exc)
            result = SecurityReviewResult.unavailable(f"review_failed: {type(exc).__name__}")
        return self._decision_from_review(event, decision, result)

    async def _slow_hook(self, event: RuntimeEvent) -> None:
        if not self._should_review_model_activity(event):
            return
        decision = Decision(
            action=Action.ALLOW,
            reason="model_activity_audit",
            rule_version="llm_security_plugin",
        )
        try:
            result = await asyncio.to_thread(
                self._review,
                SecurityReviewRequest(event=event, decision=decision),
            )
        except Exception as exc:
            log.warning("LLM model-activity review failed: %s", exc)
            result = SecurityReviewResult.unavailable(f"review_failed: {type(exc).__name__}")
        resolved = self._decision_from_review(event, decision, result)
        guard = self._guard
        if guard is not None:
            guard.pipeline.audit.log(
                event.model_copy(update={
                    "extra": {
                        **dict(event.extra or {}),
                        "related_event_id": event.event_id,
                        "source": "llm_security_plugin",
                    }
                }),
                resolved,
            )

    def _build_reviewer(self) -> Any:
        backend = self._llm_backend
        if backend == "env":
            from agentguard.llm.backend import LLMBackend
            backend = LLMBackend.from_env()
        if hasattr(backend, "review"):
            return backend
        return PromptSecurityReviewer(
            backend,
            trace_max_steps=self._trace_max_steps,
            detectors=self._detectors,
            enabled_detectors=self._enabled_detectors,
            mode=self._mode,
        )

    def _review(self, request: SecurityReviewRequest) -> SecurityReviewResult:
        reviewer = self._reviewer
        if reviewer is None:
            raise RuntimeError("llm_security_plugin_not_configured")
        if isinstance(reviewer, SecurityReviewOrchestrator):
            return reviewer.review(request)
        return reviewer.review(request)

    def _should_review_model_activity(self, event: RuntimeEvent) -> bool:
        if event.event_type not in _MODEL_ACTIVITY_EVENTS:
            return False
        extra = event.extra or {}
        return isinstance(extra.get("model_activity"), dict)

    def _decision_from_review(
        self,
        event: RuntimeEvent,
        decision: Decision,
        result: SecurityReviewResult,
    ) -> Decision:
        severity = result.max_severity()
        risk_score = max(decision.risk_score, _SECURITY_REVIEW_RISK[severity])
        reason = (
            _format_unavailable_reason(result, rule_reason=decision.reason)
            if result.summary == "security review unavailable"
            else _format_security_review_reason(result, rule_reason=decision.reason)
        )
        tool_name = event.tool_call.tool_name if event.tool_call else "?"
        log.info(
            "LLM security_review severity=%s tool=%s threats=%s rules=%s",
            severity.value,
            tool_name,
            result.threat_types(),
            decision.matched_rules,
        )
        if severity is ThreatSeverity.CRITICAL:
            return decision.model_copy(update={
                "action": Action.DENY,
                "client_action": ClientAction.DENY,
                "risk_score": risk_score,
                "reason": reason,
                "security_review": result,
                "llm_system_prompt": None,
            })
        if severity is ThreatSeverity.HIGH:
            return decision.model_copy(update={
                "action": Action.HUMAN_CHECK,
                "client_action": ClientAction.HUMAN_CHECK,
                "risk_score": risk_score,
                "reason": reason,
                "security_review": result,
                "llm_system_prompt": None,
            })
        return decision.model_copy(update={
            "action": Action.ALLOW,
            "client_action": ClientAction.ALLOW,
            "risk_score": risk_score,
            "reason": reason,
            "security_review": result,
            "llm_system_prompt": None,
        })


def _format_security_review_reason(
    result: SecurityReviewResult,
    *,
    rule_reason: str = "",
) -> str:
    severity = result.max_severity().value
    threats = ",".join(result.threat_types()) or "none"
    summary = result.summary or "security review completed"
    parts = [f"security_review:{severity}:{summary}", f"threats={threats}"]
    evidence = result.evidence_preview()
    if evidence:
        parts.append(f"evidence={' | '.join(evidence)}")
    if rule_reason:
        parts.append(f"rule_reason={rule_reason}")
    return "; ".join(parts)


def _format_unavailable_reason(
    result: SecurityReviewResult,
    *,
    rule_reason: str = "",
) -> str:
    detail = result.raw_response or result.summary or "security review unavailable"
    if rule_reason:
        return f"security_review_unavailable:{rule_reason}; {detail}"
    return f"security_review_unavailable:{detail}"


__all__ = ["LLMSecurityReviewPlugin"]

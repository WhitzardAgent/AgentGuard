"""Server-side Thought-Aligner intervention for LLM outputs."""
from __future__ import annotations

from typing import Any

from backend.runtime.plugins.base import BasePlugin, CheckResult
from backend.runtime.plugins.registry import register
from backend.runtime.thought_alignment import (
    ThoughtAlignerClient,
    ThoughtAlignmentError,
    build_alignment_context,
)
from shared.schemas.context import RuntimeContext
from shared.schemas.decisions import DecisionType, GuardDecision
from shared.schemas.events import EventType, RuntimeEvent


@register(
    name="thought_aligner",
    description="Rewrite exposed agent reasoning before the client regenerates its action.",
)
class ThoughtAlignerPlugin(BasePlugin):
    event_types = [EventType.LLM_OUTPUT]

    def __init__(
        self,
        *,
        aligner: Any = None,
        env: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        self._aligner_override = aligner
        super().__init__(env=env, **kwargs)

    def check(
        self,
        event: RuntimeEvent,
        context: RuntimeContext,
        trajectory_window: list[RuntimeEvent] | None = None,
    ) -> CheckResult:
        if _as_int(event.metadata.get("thought_alignment_attempt"), 0) > 0:
            return CheckResult(metadata={"thought_alignment": "retry_skipped"})

        alignment = build_alignment_context(
            event,
            context,
            trajectory_window,
            max_instruction_chars=_as_int(
                getattr(self, "max_instruction_chars", 12_000), 12_000
            ),
            max_thought_chars=_as_int(getattr(self, "max_thought_chars", 8_000), 8_000),
            max_observation_chars=_as_int(
                getattr(self, "max_observation_chars", 12_000), 12_000
            ),
            max_history_items=_as_int(getattr(self, "max_history_items", 8), 8),
        )
        if alignment is None:
            return CheckResult(metadata={"thought_alignment": "context_unavailable"})

        if event.metadata.get("thought_regeneration_supported") is not True:
            return CheckResult(
                decision_candidate=GuardDecision.deny(
                    "Thought alignment requires client-side action regeneration support.",
                    metadata={"protocol": "thought_alignment_v1"},
                ),
                risk_signals=["thought_alignment_unsupported_client"],
                is_final=True,
            )

        try:
            aligned = self._aligner().align(
                alignment.formatted_instruction,
                alignment.thought,
            )
            thought_limit = max(
                1,
                _as_int(getattr(self, "max_thought_chars", 8_000), 8_000),
            )
            aligned = str(aligned).strip()[:thought_limit].strip()
            if not aligned:
                raise ThoughtAlignmentError("Thought-Aligner returned empty text")
        except Exception:
            return self._failure_result()

        if aligned == alignment.thought.strip():
            return CheckResult(metadata={"thought_alignment": "unchanged"})

        decision = GuardDecision(
            DecisionType.ALIGN_THOUGHT,
            "Thought-Aligner rewrote the current reasoning; regenerate the action.",
            risk_signals=["thought_alignment_applied"],
            metadata={
                "aligned_thought": aligned,
                "protocol": "thought_alignment_v1",
            },
        )
        return CheckResult(
            decision_candidate=decision,
            risk_signals=["thought_alignment_applied"],
            is_final=True,
            metadata={"thought_alignment": "aligned"},
        )

    def _aligner(self) -> Any:
        if self._aligner_override is not None:
            return self._aligner_override
        return ThoughtAlignerClient(
            base_url=getattr(self, "base_url", None),
            api_key=getattr(self, "api_key", None),
            model=getattr(self, "model", None),
            timeout_s=_as_float(getattr(self, "timeout_s", 30.0), 30.0),
        )

    def _failure_result(self) -> CheckResult:
        failure_mode = str(getattr(self, "failure_mode", "deny") or "deny").lower()
        if failure_mode == "allow":
            return CheckResult(
                risk_signals=["thought_alignment_error"],
                metadata={"thought_alignment": "error_allowed"},
            )
        return CheckResult(
            decision_candidate=GuardDecision.deny(
                "Thought alignment failed; the original action was not released.",
                metadata={"protocol": "thought_alignment_v1"},
            ),
            risk_signals=["thought_alignment_error"],
            is_final=True,
            metadata={"thought_alignment": "error_denied"},
        )


def _as_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _as_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


__all__ = ["ThoughtAlignerPlugin"]

"""Server-side Thought-Aligner intervention for LLM outputs."""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from backend.runtime.plugins.base import BasePlugin, CheckResult
from backend.runtime.plugins.llm_after.thought_alignment import (
    ThoughtAlignerClient,
    ThoughtAlignmentError,
    build_alignment_context,
    extract_thought,
)
from backend.runtime.plugins.registry import register
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

        diagnostics = _alignment_diagnostics(
            self,
            event,
            trajectory_window,
        )
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
            return CheckResult(
                metadata={
                    "thought_alignment": "context_unavailable",
                    "thought_alignment_debug": diagnostics,
                }
            )

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
            formatted_instruction = alignment.formatted_instruction
            diagnostics.update(
                {
                    "alignment_instruction_len": len(formatted_instruction),
                    "alignment_instruction_preview": _preview_text(formatted_instruction),
                    "alignment_thought_len": len(alignment.thought),
                    "alignment_thought_preview": _preview_text(alignment.thought),
                    "alignment_history_items": len(alignment.history),
                }
            )
            aligned = self._aligner().align(
                formatted_instruction,
                alignment.thought,
            )
            thought_limit = max(
                1,
                _as_int(getattr(self, "max_thought_chars", 8_000), 8_000),
            )
            aligned = str(aligned).strip()[:thought_limit].strip()
            if not aligned:
                raise ThoughtAlignmentError("Thought-Aligner returned empty text")
        except Exception as exc:
            return self._failure_result(error=exc, diagnostics=diagnostics)

        if aligned == alignment.thought.strip():
            return CheckResult(metadata={"thought_alignment": "unchanged"})

        decision = GuardDecision(
            DecisionType.LOOP_BACK_TO_LLM,
            "Thought-Aligner rewrote the current reasoning; regenerate the action.",
            risk_signals=["thought_alignment_applied"],
            processed_content = aligned,
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
        implementation, mock_mode = _aligner_implementation_and_mock_mode(self)
        if implementation == "mock":
            return _MockThoughtAligner(
                mock_mode=mock_mode,
                mock_reply=getattr(self, "mock_reply", None),
            )
        return ThoughtAlignerClient(
            base_url=getattr(self, "base_url", None),
            api_key=getattr(self, "api_key", None),
            model=getattr(self, "model", None),
            timeout_s=_as_float(getattr(self, "timeout_s", 30.0), 30.0),
        )

    def _failure_result(
        self,
        *,
        error: Exception,
        diagnostics: dict[str, Any] | None = None,
    ) -> CheckResult:
        failure_metadata = {
            "thought_alignment_error_type": type(error).__name__,
            "thought_alignment_error": str(error) or type(error).__name__,
            "thought_alignment_debug": dict(diagnostics or {}),
        }
        failure_mode = str(getattr(self, "failure_mode", "allow") or "allow").lower()
        if failure_mode != "deny":
            return CheckResult(
                risk_signals=["thought_alignment_error"],
                metadata={"thought_alignment": "error_allowed", **failure_metadata},
            )
        return CheckResult(
            decision_candidate=GuardDecision.deny(
                "Thought alignment failed; the original action was not released.",
                metadata={"protocol": "thought_alignment_v1"},
            ),
            risk_signals=["thought_alignment_error"],
            is_final=True,
            metadata={"thought_alignment": "error_denied", **failure_metadata},
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


class _MockThoughtAligner:
    def __init__(self, *, mock_mode: str, mock_reply: Any) -> None:
        self.mock_mode = mock_mode
        self.mock_reply = mock_reply

    def align(self, _instruction: str, thought: str) -> str:
        if self.mock_mode == "passthrough":
            return thought
        reply = str(self.mock_reply or "").strip()
        if reply:
            return reply
        base = str(thought or "").strip()
        if not base:
            return "Mock aligned thought"
        return f"{base} [mock aligned]"


def _alignment_diagnostics(
    plugin: ThoughtAlignerPlugin,
    event: RuntimeEvent,
    trajectory_window: list[RuntimeEvent] | None,
) -> dict[str, Any]:
    payload = _payload_mapping(event.payload)
    thought_text = _optional_text(payload.get("thought")) or extract_thought(payload)
    output_text = _optional_text(payload.get("output"))
    final_output_text = _optional_text(payload.get("final_output"))
    return {
        "implementation": str(getattr(plugin, "implementation", "remote") or "remote"),
        "mock_mode": str(getattr(plugin, "mock_mode", "") or ""),
        "failure_mode": str(getattr(plugin, "failure_mode", "allow") or "allow"),
        "event_node_id": str(event.metadata.get("node_id") or ""),
        "event_model": str(event.metadata.get("model") or ""),
        "event_model_provider": str(event.metadata.get("model_provider") or ""),
        "payload_keys": sorted(str(key) for key in payload),
        "payload_thought_present": thought_text is not None,
        "payload_thought_len": len(thought_text) if thought_text is not None else 0,
        "payload_thought_preview": _preview_text(thought_text),
        "payload_output_len": len(output_text) if output_text is not None else 0,
        "payload_output_preview": _preview_text(output_text),
        "payload_final_output_len": len(final_output_text) if final_output_text is not None else 0,
        "payload_final_output_preview": _preview_text(final_output_text),
        "payload_output_has_think_tag": bool(output_text and "<think" in output_text.lower()),
        "trajectory_event_count": len(trajectory_window or []),
        "trajectory_llm_input_count": sum(
            1 for item in (trajectory_window or []) if item.event_type == EventType.LLM_INPUT
        ),
        "last_user_instruction_preview": _last_user_instruction_preview(trajectory_window),
    }


def _payload_mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return {str(key): item for key, item in value.items()}
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        try:
            dumped = to_dict()
        except Exception:
            dumped = None
        if isinstance(dumped, Mapping):
            return {str(key): item for key, item in dumped.items()}
    result: dict[str, Any] = {}
    for key in ("thought", "output", "final_output", "messages", "content", "text"):
        item = getattr(value, key, None)
        if item is not None:
            result[key] = item
    return result


def _last_user_instruction_preview(trajectory_window: list[RuntimeEvent] | None) -> str | None:
    for item in reversed(list(trajectory_window or [])):
        if item.event_type != EventType.LLM_INPUT:
            continue
        payload = _payload_mapping(item.payload)
        messages = payload.get("messages")
        if isinstance(messages, Sequence) and not isinstance(messages, (str, bytes, bytearray)):
            for message in reversed(messages):
                normalized = _payload_mapping(message)
                role = str(normalized.get("role") or "").strip().lower()
                if role not in {"user", "human"}:
                    continue
                preview = _preview_text(_optional_text(normalized.get("content")))
                if preview:
                    return preview
    return None


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        text = value.strip()
        return text or None
    return None


def _preview_text(value: str | None, *, limit: int = 240) -> str | None:
    if value is None:
        return None
    compact = " ".join(value.split())
    if not compact:
        return None
    if len(compact) <= limit:
        return compact
    return f"{compact[:limit]}..."


def _aligner_implementation_and_mock_mode(plugin: ThoughtAlignerPlugin) -> tuple[str, str]:
    raw = str(getattr(plugin, "implementation", "remote") or "remote").strip().lower()
    if raw in {"remote", "real"}:
        return "remote", ""
    if raw in {"mock", "test"}:
        return "mock", _mock_mode_of(getattr(plugin, "mock_mode", "rewrite"))
    if raw in {"mock_rewrite", "mock_modify", "mock_aligned"}:
        return "mock", "rewrite"
    if raw in {"mock_passthrough", "mock_allow", "mock_unchanged"}:
        return "mock", "passthrough"
    raise ThoughtAlignmentError(f"Unsupported Thought-Aligner implementation: {raw}")


def _mock_mode_of(value: Any) -> str:
    raw = str(value or "rewrite").strip().lower()
    if raw in {"rewrite", "modify", "aligned"}:
        return "rewrite"
    if raw in {"passthrough", "allow", "unchanged"}:
        return "passthrough"
    raise ThoughtAlignmentError(f"Unsupported Thought-Aligner mock mode: {raw}")


__all__ = ["ThoughtAlignerPlugin"]

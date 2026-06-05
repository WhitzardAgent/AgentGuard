"""Intercepts LLM chain-of-thought reasoning steps and applies policy.

Every intercepted thought becomes an ``LLM_THOUGHT`` event, is run through the
PEP, and the resulting decision is honoured:

* ``log_only`` / ``allow`` → thought passes through unchanged (but audited)
* ``sanitize``             → returns the scrubbed thought
* ``ask_user`` / ``require_approval`` → asks the human; blocked if refused
* ``deny``                 → replaced with a blocked marker (never crashes the
                             agent's reasoning loop)

Framework helpers extract thought text from OpenAI / LiteLLM / Anthropic
response objects so the hook plugs into popular SDKs with minimal code.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from agentguard.harness.runtime_context import current_context
from agentguard.schemas.context import RuntimeContext
from agentguard.schemas.decision import DecisionAction
from agentguard.schemas.events import EventType, RuntimeEvent

if TYPE_CHECKING:
    from agentguard.facade import AgentGuard

_BLOCKED_MARKER = "[thought withheld by AgentGuard policy]"


class LLMThoughtHook:
    def __init__(self, guard: "AgentGuard") -> None:
        self._guard = guard

    def _context(self) -> RuntimeContext:
        return current_context() or self._guard.context

    def observe(
        self,
        thought: str,
        *,
        metadata: dict[str, Any] | None = None,
        event_type: EventType = EventType.LLM_THOUGHT,
    ) -> str:
        """Run a single reasoning step through the PEP, return the safe text."""
        context = self._context()
        event = RuntimeEvent(
            type=event_type,
            session_id=context.session_id,
            user_id=context.user_id,
            agent_id=context.agent_id,
            content=thought,
            metadata=dict(metadata or {}),
        )
        self._guard._dispatch_before(event)
        result = self._guard._enforcer.enforce(event, context)
        self._guard._dispatch_after(result)

        action = result.decision.action
        if action is DecisionAction.DENY:
            return _BLOCKED_MARKER
        if action in (DecisionAction.ASK_USER, DecisionAction.REQUIRE_APPROVAL):
            approved = self._guard._request_approval(result.event, result.decision)
            return thought if approved else _BLOCKED_MARKER
        if action is DecisionAction.SANITIZE:
            return result.event.content or ""
        return thought

    # ── framework extraction helpers ────────────────────────────────────
    @staticmethod
    def from_openai_response(response: Any) -> str:
        """Extract assistant text from an OpenAI chat completion (or stub)."""
        try:
            return response.choices[0].message.content or ""
        except Exception:
            return str(getattr(response, "content", response) or "")

    @staticmethod
    def from_litellm_response(response: Any) -> str:
        # LiteLLM mirrors the OpenAI response shape.
        return LLMThoughtHook.from_openai_response(response)

    @staticmethod
    def from_anthropic_response(response: Any) -> str:
        try:
            blocks = response.content
            return "".join(getattr(b, "text", "") for b in blocks)
        except Exception:
            return str(getattr(response, "content", response) or "")

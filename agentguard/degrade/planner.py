"""Enforcer: materializes a Decision into a concrete runtime behavior.

Combines the ref implementation's Enforcer with ActionExecutor.

Server-side action → runtime behavior
──────────────────────────────────────
ALLOW       → apply obligations (REDACT / RATE_LIMIT / …), then execute tool
DENY        → raise DecisionDenied (tool blocked)
LLM_CHECK   → invoke LLMBackend reviewer:
                • "allow"  → execute (after obligations)
                • "deny"   → raise DecisionDenied
                • "human"  → escalate to human approval queue
              Falls back to human approval when no LLMBackend is configured.
HUMAN_CHECK → enqueue human approval ticket (legacy / explicit DSL action)
DEGRADE     → rewrite tool parameters, re-validate, then execute
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass
from typing import Any, Callable, Literal

from agentguard.degrade.transformers import ActionExecutor
from agentguard.llm.security_review import (
    PromptSecurityReviewer,
    SecurityReviewRequest,
    SecurityReviewResult,
    ThreatSeverity,
)
from agentguard.models.decisions import Action, ClientAction, Decision
from agentguard.models.errors import DecisionDenied, HumanApprovalPending
from agentguard.models.events import RuntimeEvent, ToolCall
from agentguard.review.tickets import ApprovalBridge, InMemoryApprovalBridge

log = logging.getLogger(__name__)

ApprovalMode = Literal["block", "suspend"]
TimeoutAction = Literal["deny", "allow", "degrade"]


@dataclass
class EnforcerConfig:
    mode: str = "enforce"                    # enforce | monitor | dry_run
    approval_mode: ApprovalMode = "block"
    approval_timeout_s: float = 60.0
    on_timeout: TimeoutAction = "deny"
    max_rewrite_depth: int = 2


# ──────────────────────────────────────────────────────────────────────────────
# LLM review prompt helpers
# ──────────────────────────────────────────────────────────────────────────────

_LLM_REVIEW_SYSTEM = (
    "You are the security review authority for an AI agent runtime. "
    "You will receive a tool-call event and its matched policy context. "
    "Your task is to determine whether the action should be allowed, denied, "
    "or escalated for human review. "
    "Return exactly two XML-style fields and nothing else. "
    "The first field must be <DECISION>...</DECISION>, where the content is "
    "exactly one lowercase token chosen from allow, deny, or human. "
    "No other decision value is permitted. "
    "The second field must be <REASON>...</REASON>, containing a concise explanation. "
    "Do not output any other text, punctuation outside the tags, sentence, JSON, "
    "markdown, or formatting. "
    "If the action is uncertain, ambiguous, or requires escalation, use "
    "<DECISION>human</DECISION>."
)
_DEFAULT_LLM_TRACE_MAX_STEPS = 5


def _compact_trace_value(value: Any, *, max_len: int = 48) -> str:
    if isinstance(value, (str, int, float, bool)) or value is None:
        text = json.dumps(value, ensure_ascii=True)
    elif isinstance(value, dict):
        keys = list(value.keys())
        preview = ", ".join(json.dumps(str(k), ensure_ascii=True) for k in keys[:3])
        if len(keys) > 3:
            preview += ", ..."
        text = "{" + preview + "}"
    elif isinstance(value, (list, tuple, set)):
        text = f"<{type(value).__name__}:{len(value)}>"
    else:
        text = f"<{type(value).__name__}>"
    if len(text) > max_len:
        return text[: max_len - 3] + "..."
    return text


def _llm_trace_max_steps() -> int:
    raw = str(os.environ.get("AGENTGUARD_LLM_TRACE_MAX_STEPS", "")).strip()
    if not raw:
        return _DEFAULT_LLM_TRACE_MAX_STEPS
    try:
        return max(0, int(raw))
    except ValueError:
        return _DEFAULT_LLM_TRACE_MAX_STEPS


def _resolve_llm_review_system_prompt(decision: Decision) -> str:
    custom_prompt = str(decision.llm_system_prompt or "").strip()
    if not custom_prompt:
        return _LLM_REVIEW_SYSTEM
    return f"{custom_prompt}\n\n{_LLM_REVIEW_SYSTEM}"


def _extract_llm_tag(content: str, tag: str) -> str | None:
    match = re.search(rf"<{tag}>\s*(.*?)\s*</{tag}>", content, flags=re.IGNORECASE | re.DOTALL)
    if match is None:
        return None
    return match.group(1).strip()


def _summarize_trace_rich(trace_rich: Any, *, max_steps: int = 5, max_args: int = 5) -> str:
    if max_steps <= 0:
        return "(none)"
    if not isinstance(trace_rich, list):
        return "(none)"

    entries = [entry for entry in trace_rich if isinstance(entry, dict) and entry.get("tool")]
    if not entries:
        return "(none)"

    shown = entries[-max_steps:]
    rendered: list[str] = []
    for entry in shown:
        tool = str(entry.get("tool") or "?")
        args = entry.get("args") if isinstance(entry.get("args"), dict) else {}
        details: list[str] = []
        if isinstance(args, dict):
            items = list(args.items())[:max_args]
            details.extend(
                f"{key}={_compact_trace_value(value)}" for key, value in items
            )
            if len(args) > max_args:
                details.append("...")
        result = entry.get("result", None)
        if result is not None:
            details.append(f"result={_compact_trace_value(result, max_len=32)}")
        rendered.append(f"{tool}({', '.join(details)})" if details else tool)

    prefix = "... -> " if len(entries) > max_steps else ""
    return prefix + " -> ".join(rendered)


def _build_llm_review_messages(event: RuntimeEvent, decision: Decision) -> list[dict[str, Any]]:
    tool_name = event.tool_call.tool_name if event.tool_call else "unknown"
    args = event.tool_call.args if event.tool_call else {}
    principal = event.principal.agent_id if event.principal else "unknown"
    trace_summary = _summarize_trace_rich(
        (event.extra or {}).get("trace_rich"),
        max_steps=_llm_trace_max_steps(),
    )
    return [
        {"role": "system", "content": _resolve_llm_review_system_prompt(decision)},
        {
            "role": "user",
            "content": (
                f"Tool: {tool_name}\n"
                f"Args: {args}\n"
                f"Principal: {principal}\n"
                f"Trace summary: {trace_summary}\n"
                f"Matched rules: {', '.join(decision.matched_rules)}\n"
                f"Risk score: {decision.risk_score}\n"
                f"Reason: {decision.reason}\n"
                "\nRespond with <DECISION>allow|deny|human</DECISION> and "
                "<REASON>...</REASON> only."
            ),
        },
    ]


def _parse_llm_review_response(content: str | None) -> tuple[Literal["allow", "deny", "human"], str]:
    """Extract decision + reason from the LLM response.

    Preferred format:
      <DECISION>allow|deny|human</DECISION>
      <REASON>...</REASON>

    Legacy one-word verdicts are still accepted as a fallback so older
    deployments do not fail open during rollout.
    """
    if not content:
        return "human", "empty_llm_response"

    decision_text = _extract_llm_tag(content, "DECISION")
    reason_text = _extract_llm_tag(content, "REASON")

    if decision_text is not None or reason_text is not None:
        decision_low = (decision_text or "").strip().lower()
        if decision_low == "allow":
            verdict: Literal["allow", "deny", "human"] = "allow"
        elif decision_low == "deny":
            verdict = "deny"
        else:
            verdict = "human"
        if reason_text:
            return verdict, reason_text
        if decision_text is None:
            return "human", "missing_<DECISION>_tag"
        return verdict, "missing_<REASON>_tag"

    low = content.strip().lower()
    if low.startswith("allow"):
        return "allow", ""
    if low.startswith("deny"):
        return "deny", ""
    if low.startswith("human"):
        return "human", ""
    return "human", "invalid_llm_response_format"


def _prefixed_reason(prefix: str, reason: str) -> str:
    return f"{prefix}: {reason}" if reason else prefix


def _merge_rule_and_llm_reason(rule_reason: str, llm_reason: str) -> str:
    parts: list[str] = []
    if rule_reason:
        parts.append(f"rule_reason={rule_reason}")
    if llm_reason:
        parts.append(f"llm_reason={llm_reason}")
    return "; ".join(parts)


_SECURITY_REVIEW_RISK = {
    ThreatSeverity.NONE: 0.0,
    ThreatSeverity.LOW: 0.2,
    ThreatSeverity.MEDIUM: 0.5,
    ThreatSeverity.HIGH: 0.8,
    ThreatSeverity.CRITICAL: 1.0,
}


def _format_security_review_reason(
    result: SecurityReviewResult,
    *,
    rule_reason: str = "",
) -> str:
    severity = result.max_severity()
    summary = result.summary.strip() or "security reviewer completed"
    parts = [f"security_review:{severity.value}:{summary}"]
    threat_types = result.threat_types()
    if threat_types:
        parts.append(f"threats={','.join(threat_types)}")
    evidence = result.evidence_preview()
    if evidence:
        parts.append(f"evidence={' | '.join(evidence)}")
    if rule_reason:
        parts.append(f"rule_reason={rule_reason}")
    return "; ".join(parts)


class Enforcer:
    def __init__(
        self,
        *,
        config: EnforcerConfig | None = None,
        approval_bridge: ApprovalBridge | None = None,
        action_executor: ActionExecutor | None = None,
        llm_backend: Any | None = None,
    ) -> None:
        self.config = config or EnforcerConfig()
        self._approval = approval_bridge or InMemoryApprovalBridge()
        self._actions = action_executor or ActionExecutor()
        self._llm = llm_backend   # optional LLMBackend instance

    def resolve_remote_decision(self, event: RuntimeEvent, decision: Decision) -> Decision:
        """Resolve server-side review actions before returning a remote response.

        Remote ``/v1/evaluate`` must never leak ``LLM_CHECK`` back to the SDK
        caller. The server resolves the LLM review here and returns the final
        ``ALLOW`` / ``DENY`` / ``HUMAN_CHECK`` decision without executing the
        underlying tool.
        """
        if decision.action is not Action.LLM_CHECK:
            if decision.client_action is not None:
                return decision
            return decision.model_copy(update={"client_action": decision.to_client_action()})
        return self._resolve_llm_check_decision(event, decision)

    def apply(
        self,
        event: RuntimeEvent,
        decision: Decision,
        original_executor: Callable[[RuntimeEvent], Any],
        *,
        revalidate: Callable[[RuntimeEvent], Decision] | None = None,
    ) -> Any:
        if self.config.mode == "monitor":
            return self._run_original(event, original_executor)
        if self.config.mode == "dry_run":
            return {"agentguard_dry_run": True, "decision": decision.model_dump(mode="json")}

        action = decision.action
        if action is Action.ALLOW:
            return self._allow(event, decision, original_executor)
        if action is Action.DENY:
            return self._deny(event, decision)
        if action is Action.LLM_CHECK:
            return self._llm_check(event, decision, original_executor, revalidate)
        if action is Action.HUMAN_CHECK:
            return self._human_check(event, decision, original_executor, revalidate)
        if action is Action.DEGRADE:
            return self._degrade(event, decision, original_executor, revalidate, depth=0)
        raise ValueError(f"unknown action: {action!r}")

    def _run_original(self, event: RuntimeEvent, fn: Callable[[RuntimeEvent], Any]) -> Any:
        return fn(event)

    def _allow(
        self,
        event: RuntimeEvent,
        decision: Decision,
        original_executor: Callable[[RuntimeEvent], Any],
    ) -> Any:
        """ALLOW branch: apply any obligations (REDACT / REQUIRE_TARGET_IN / RATE_LIMIT)
        before handing control to the original executor."""
        if decision.obligations:
            # rate_limit must be checked BEFORE any mutations
            rate_violation = self._actions.check_rate_limit(event, decision)
            if rate_violation:
                raise DecisionDenied(
                    reason=f"rate_limit: {rate_violation}",
                    matched_rules=decision.matched_rules,
                    request_id=event.event_id,
                )
            rewritten_tc = self._actions.apply_rewrites(event, decision)
            if rewritten_tc is not None:
                event = event.with_tool_call(rewritten_tc)
            # require_target_in: block the call if target is not in the whitelist
            target_violation = self._actions.check_require_target_in(event, decision)
            if target_violation:
                raise DecisionDenied(
                    reason=f"require_target_in: {target_violation}",
                    matched_rules=decision.matched_rules,
                    request_id=event.event_id,
                )
        return self._run_original(event, original_executor)

    def _deny(self, event: RuntimeEvent, decision: Decision) -> Any:
        raise DecisionDenied(
            reason=decision.reason or "policy_denied",
            matched_rules=decision.matched_rules,
            request_id=event.event_id,
            suggestion="adjust scope, request human approval, or use an allowed target",
        )

    def _llm_check(
        self,
        event: RuntimeEvent,
        decision: Decision,
        original_executor: Callable[[RuntimeEvent], Any],
        revalidate: Callable[[RuntimeEvent], Decision] | None,
    ) -> Any:
        """LLM_CHECK branch: invoke the configured LLMBackend to review the event.

        Verdict resolution:
        • "allow"  → execute (after obligations)
        • "deny"   → raise DecisionDenied
        • "human"  → escalate to human approval queue (HUMAN_CHECK path)

        Falls back to HUMAN_CHECK when no LLMBackend is configured.
        """
        resolved = self._resolve_llm_check_decision(event, decision)
        if resolved.action is Action.ALLOW:
            return self._allow(event, resolved, original_executor)
        if resolved.action is Action.DENY:
            return self._deny(event, resolved)
        return self._human_check(event, resolved, original_executor, revalidate)

    def _resolve_llm_check_decision(
        self,
        event: RuntimeEvent,
        decision: Decision,
    ) -> Decision:
        if self._llm is None:
            log.debug(
                "LLM_CHECK fired but no llm_backend configured — escalating to HUMAN_CHECK"
            )
            return decision.model_copy(update={
                "action": Action.HUMAN_CHECK,
                "client_action": ClientAction.HUMAN_CHECK,
                "reason": _prefixed_reason(
                    "security_review_unavailable",
                    _merge_rule_and_llm_reason(
                        decision.reason,
                        "llm_backend_not_configured",
                    ),
                ),
                "llm_system_prompt": None,
            })

        try:
            reviewer = (
                self._llm
                if hasattr(self._llm, "review")
                else PromptSecurityReviewer(self._llm)
            )
            result = reviewer.review(SecurityReviewRequest(
                event=event,
                decision=decision,
                custom_prompt=decision.llm_system_prompt,
            ))
        except Exception as exc:
            log.warning(
                "LLM_CHECK: security review failed (%s) — escalating to HUMAN_CHECK",
                exc,
            )
            return decision.model_copy(update={
                "action": Action.HUMAN_CHECK,
                "client_action": ClientAction.HUMAN_CHECK,
                "reason": _prefixed_reason(
                    "security_review_unavailable",
                    _merge_rule_and_llm_reason(
                        decision.reason,
                        f"review_failed: {type(exc).__name__}",
                    ),
                ),
                "llm_system_prompt": None,
            })

        severity = result.max_severity()
        mapped_risk = _SECURITY_REVIEW_RISK[severity]
        risk_score = max(decision.risk_score, mapped_risk)
        reason = _format_security_review_reason(result, rule_reason=decision.reason)
        tool_name = event.tool_call.tool_name if event.tool_call else "?"
        log.info(
            "LLM_CHECK security_review severity=%s tool=%s threats=%s rules=%s",
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
                "llm_system_prompt": None,
            })
        if severity is ThreatSeverity.HIGH:
            return decision.model_copy(update={
                "action": Action.HUMAN_CHECK,
                "client_action": ClientAction.HUMAN_CHECK,
                "risk_score": risk_score,
                "reason": reason,
                "llm_system_prompt": None,
            })
        return decision.model_copy(update={
            "action": Action.ALLOW,
            "client_action": ClientAction.ALLOW,
            "risk_score": risk_score,
            "reason": reason,
            "llm_system_prompt": None,
        })

    def _human_check(
        self,
        event: RuntimeEvent,
        decision: Decision,
        original_executor: Callable[[RuntimeEvent], Any],
        revalidate: Callable[[RuntimeEvent], Decision] | None,
    ) -> Any:
        ticket = self._approval.enqueue(
            event_dump=event.model_dump(mode="json"),
            decision_dump=decision.model_dump(mode="json"),
        )
        if self.config.approval_mode == "suspend":
            raise HumanApprovalPending(ticket_id=ticket.ticket_id)

        ticket = self._approval.wait(ticket.ticket_id, self.config.approval_timeout_s)
        if ticket.status == "approved":
            # Still apply obligations (REDACT etc.) even after human approval
            return self._allow(event, decision, original_executor)
        if ticket.status == "denied":
            raise DecisionDenied(
                reason=f"human_denied: {ticket.note or decision.reason}",
                matched_rules=decision.matched_rules,
                request_id=event.event_id,
            )
        if self.config.on_timeout == "allow":
            return self._run_original(event, original_executor)
        if self.config.on_timeout == "degrade" and decision.degrade_profile:
            return self._degrade(event, decision, original_executor, revalidate, depth=0)
        raise DecisionDenied(
            reason="human_approval_timeout",
            matched_rules=decision.matched_rules,
            request_id=event.event_id,
        )

    def _degrade(
        self,
        event: RuntimeEvent,
        decision: Decision,
        original_executor: Callable[[RuntimeEvent], Any],
        revalidate: Callable[[RuntimeEvent], Decision] | None,
        *,
        depth: int,
    ) -> Any:
        if event.tool_call is None:
            return self._run_original(event, original_executor)
        rewritten_tc = self._actions.apply_rewrites(event, decision)
        assert rewritten_tc is not None
        if rewritten_tc == event.tool_call:
            return self._run_original(event, original_executor)

        rewritten_event = event.with_tool_call(rewritten_tc)

        if revalidate is not None and depth < self.config.max_rewrite_depth:
            new_decision = revalidate(rewritten_event)
            if (new_decision.action is Action.DEGRADE
                    and new_decision.matched_rules != decision.matched_rules):
                return self._degrade(rewritten_event, new_decision, original_executor,
                                     revalidate, depth=depth + 1)
            if new_decision.action is Action.DENY:
                return self._deny(rewritten_event, new_decision)
            if new_decision.action in (Action.HUMAN_CHECK, Action.LLM_CHECK):
                return self._human_check(rewritten_event, new_decision,
                                         original_executor, revalidate)
        return self._run_original(rewritten_event, original_executor)

    def approval_bridge(self) -> ApprovalBridge:
        return self._approval

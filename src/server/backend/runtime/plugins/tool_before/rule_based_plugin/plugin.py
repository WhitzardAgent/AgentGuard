"""Rule-based plugin backed by the server policy rule store."""
from __future__ import annotations

import ast
from collections.abc import Callable
import json
import time
from typing import Any
from urllib.parse import urlparse

from backend.llm import LLMClient
from shared.audit.redactor import redact
from shared.schemas.context import RuntimeContext
from shared.schemas.decisions import GuardDecision
from shared.schemas.policy import PolicyEffect, PolicyRule
from shared.tools.capability import CAP_EXTERNAL_SEND
from shared.schemas.events import RuntimeEvent
from backend.runtime.plugins.base import BasePlugin, CheckResult
from backend.runtime.plugins.registry import register
from backend.runtime.plugins.tool_before.rule_based_plugin.matcher import (
    RuleMatch,
    effect_to_decision,
    match_rules,
)


@register(
    name="rule_based_plugin",
    description="Evaluate server policy rules against the current event and trajectory window.",
)
class RuleBasedPlugin(BasePlugin):
    """Evaluate PolicyRule objects and return the winning rule decision."""

    event_types = []

    def __init__(
        self,
        *,
        policy_store: Any | None = None,
        rules_provider: Callable[[], list[Any]] | None = None,
        policy_version_provider: Callable[[], str] | None = None,
        llm_client_factory: Callable[[dict[str, Any]], Any] | None = None,
        env: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        self._policy_store: Any | None = None
        self._configured_policy_store: Any | None = None
        self._rules_provider = rules_provider
        self._policy_version_provider = policy_version_provider
        self._llm_client_factory = llm_client_factory or (lambda config: LLMClient(config=config))
        super().__init__(env=env, **kwargs)
        if policy_store is not None:
            self._policy_store = policy_store
        elif self._configured_policy_store is not None:
            self._policy_store = self._configured_policy_store
        else:
            from backend.runtime.policy.store import PolicyStore  # noqa: PLC0415

            self._policy_store = PolicyStore.default()

    def bind_config(self, *, env: dict[str, Any] | None = None, **kwargs: Any) -> None:
        super().bind_config(env=env, **kwargs)
        self._configured_policy_store = self._load_configured_policy_store()
        if self._configured_policy_store is not None and self._policy_store is None:
            self._policy_store = self._configured_policy_store

    def configured_policy_store(self) -> Any | None:
        return self._configured_policy_store

    def llm_reviewer_config(self) -> dict[str, Any]:
        config: dict[str, Any] = {}
        for plugin_key, output_key in (
            ("llm_backend", "backend"),
            ("llm_model", "model"),
            ("llm_base_url", "base_url"),
            ("llm_api_key", "api_key"),
            ("llm_timeout_s", "timeout_s"),
            ("llm_trace_max_steps", "trace_max_steps"),
        ):
            value = self._configured_value(plugin_key)
            if value not in (None, ""):
                config[output_key] = value
        return config

    def set_policy_store(self, policy_store: Any) -> None:
        self._policy_store = policy_store

    def attach_policy(self, policy: Any) -> None:
        store = getattr(policy, "store", None)
        if store is not None:
            self.set_policy_store(store)
        self._policy_version_provider = lambda: str(getattr(policy, "version", getattr(self._policy_store, "version", "")))

    @property
    def policy_version(self) -> str:
        if self._policy_version_provider is not None:
            return self._policy_version_provider()
        return self._policy_store.version

    def rules(self) -> list[Any]:
        if self._rules_provider is not None:
            rules = list(self._rules_provider())
        else:
            rules = self._policy_store.rules()
        return rules or _fallback_rules()

    def check(
        self,
        event: RuntimeEvent,
        context: RuntimeContext,
        trajectory_window: list[RuntimeEvent] | None = None,
    ) -> CheckResult:
        match = match_rules(self.rules(), event, trajectory_window)
        metadata = {
            "rule_based_plugin": match.to_dict(),
            "policy_version": self.policy_version,
        }
        if not match.matched or match.rule is None or match.effect is None:
            return CheckResult(metadata=metadata)

        if _is_llm_check_rule(match.rule):
            decision, reviewer_metadata = self._review_with_llm(
                event=event,
                context=context,
                match=match,
                trajectory_window=trajectory_window or [],
            )
            metadata["llm_reviewer"] = reviewer_metadata
        else:
            decision = _decision_from_match(
                event=event,
                match=match,
                policy_version=self.policy_version,
            )
        return CheckResult(
            decision_candidate=decision,
            risk_signals=[],
            is_final=True,
            metadata=metadata,
        )

    def _load_configured_policy_store(self) -> Any | None:
        policy_source = self._configured_value("policy_path", "policy", "rules_path")
        if not isinstance(policy_source, str) or not policy_source.strip():
            return None
        from backend.runtime.policy.store import PolicyStore  # noqa: PLC0415

        return PolicyStore.from_path(policy_source.strip())

    def _configured_value(self, *keys: str) -> Any:
        for key in keys:
            value = self.env.get(key)
            if value not in (None, ""):
                return value
        for key in keys:
            value = self.config.get(key)
            if value not in (None, ""):
                return value
        return None

    def _review_with_llm(
        self,
        *,
        event: RuntimeEvent,
        context: RuntimeContext,
        match: RuleMatch,
        trajectory_window: list[RuntimeEvent],
    ) -> tuple[GuardDecision, dict[str, Any]]:
        started = time.time()
        prompt = self._review_prompt(
            match.rule,
            event,
            context,
            trajectory_window,
            audit_safe=False,
        )
        safe_prompt = self._review_prompt(
            match.rule,
            event,
            context,
            trajectory_window,
            audit_safe=True,
        )
        try:
            llm_client = self._llm_client_factory(self.llm_reviewer_config())
            completion = llm_client.complete(prompt, temperature=0, max_tokens=250)
            reviewed = _parse_reviewer_output(completion)
            decision = _review_decision_from_match(
                event=event,
                match=match,
                policy_version=self.policy_version,
                reviewed=reviewed,
            )
            metadata = {
                "prompt": safe_prompt,
                "response": redact(completion),
                "decision": reviewed["decision"],
                "reason": reviewed["reason"],
                "latency_ms": round((time.time() - started) * 1000, 2),
            }
            return decision, metadata
        except Exception as exc:
            fallback = _decision_from_match(
                event=event,
                match=match,
                policy_version=self.policy_version,
            )
            metadata = {
                "prompt": safe_prompt,
                "error": str(exc),
                "decision": "human_check",
                "latency_ms": round((time.time() - started) * 1000, 2),
            }
            return fallback, metadata

    def _review_prompt(
        self,
        rule: Any,
        event: RuntimeEvent,
        context: RuntimeContext,
        trajectory_window: list[RuntimeEvent],
        *,
        audit_safe: bool,
    ) -> str:
        trace_max_steps = int(self.llm_reviewer_config().get("trace_max_steps") or 5)
        instruction = str((getattr(rule, "metadata", {}) or {}).get("llm_prompt") or "").strip()
        if not instruction:
            instruction = "Review this tool call and decide allow, deny, or human_check."
        rule_payload = _review_rule_payload(rule)
        current_call = _review_current_call_payload(event)
        review_context = _review_context_payload(context, event)
        recent_events = _review_recent_events_payload(
            trajectory_window[-max(trace_max_steps, 0):],
        )
        if audit_safe:
            current_call = redact(current_call)
            review_context = redact(review_context)
            recent_events = redact(recent_events)
        return "\n".join(
            [
                "You are the AgentGuard LLM rule reviewer.",
                instruction,
                "Use the current tool call as the primary evidence.",
                "Use recent events only when they clarify the meaning or provenance of the current call.",
                'If important evidence is missing or redacted, return "human_check" instead of "allow".',
                "Ignore AgentGuard transport, framework, and alignment metadata.",
                'Respond with compact JSON: {"decision":"allow|deny|human_check","reason":"..."}',
                f"Rule: {json.dumps(rule_payload, ensure_ascii=True)}",
                f"Current Call: {json.dumps(current_call, ensure_ascii=True)}",
                f"Relevant Context: {json.dumps(review_context, ensure_ascii=True)}",
                f"Recent Events: {json.dumps(recent_events, ensure_ascii=True)}",
            ]
        )


def _is_llm_check_rule(rule: Any) -> bool:
    metadata = getattr(rule, "metadata", {}) or {}
    return metadata.get("review_kind") == "llm_check" or bool(metadata.get("llm_prompt"))


def _parse_reviewer_output(text: Any) -> dict[str, str]:
    raw = str(text or "").strip()
    json_match = raw
    if "{" in raw and "}" in raw:
        json_match = raw[raw.find("{"):raw.rfind("}") + 1]
    try:
        payload = json.loads(json_match)
        decision = _normalize_reviewer_decision(payload.get("decision"))
        reason = str(payload.get("reason") or raw).strip()
        return {"decision": decision, "reason": reason}
    except Exception:
        pass
    normalized = raw.lower()
    if "deny" in normalized:
        return {"decision": "deny", "reason": raw}
    if "human_check" in normalized or "human check" in normalized or "review" in normalized:
        return {"decision": "human_check", "reason": raw}
    return {"decision": "allow", "reason": raw or "reviewer allowed the call"}


def _normalize_reviewer_decision(value: Any) -> str:
    normalized = str(value or "").strip().lower()
    if normalized in {"deny", "blocked", "block"}:
        return "deny"
    if normalized in {"human_check", "human-check", "review", "require_remote_review", "remote_review"}:
        return "human_check"
    return "allow"


def _review_decision_from_match(
    *,
    event: RuntimeEvent,
    match: RuleMatch,
    policy_version: str,
    reviewed: dict[str, str],
) -> GuardDecision:
    decision = reviewed.get("decision", "allow")
    base_metadata = {
        "matched_rule_ids": [r.rule_id for r in match.all_matched or []],
        "policy_version": policy_version,
        "reviewer_decision": decision,
    }
    if decision == "deny":
        return GuardDecision.deny(
            reviewed.get("reason") or match.reason or "LLM reviewer denied the call.",
            policy_id=f"server:{match.rule.rule_id}",
            risk_signals=list(event.risk_signals),
            metadata=base_metadata,
        )
    if decision == "human_check":
        return GuardDecision.require_remote_review(
            reviewed.get("reason") or match.reason or "LLM reviewer requested human review.",
            policy_id=f"server:{match.rule.rule_id}",
            risk_signals=list(event.risk_signals),
            metadata=base_metadata,
        )
    return GuardDecision.allow(
        reviewed.get("reason") or match.reason or "LLM reviewer allowed the call.",
        policy_id=f"server:{match.rule.rule_id}",
        risk_signals=list(event.risk_signals),
        metadata=base_metadata,
    )


def _decision_from_match(
    *,
    event: RuntimeEvent,
    match: RuleMatch,
    policy_version: str,
) -> GuardDecision:
    dtype = effect_to_decision(match.effect)
    explanation = (
        f"rule '{match.rule.rule_id}' ({match.effect}) won among "
        f"{[r.rule_id for r in match.all_matched or []]}"
    )
    return GuardDecision(
        decision_type=dtype,
        reason=match.reason or explanation,
        policy_id=f"server:{match.rule.rule_id}",
        risk_signals=list(event.risk_signals),
        metadata={
            "explanation": explanation,
            "matched_rule_ids": [r.rule_id for r in match.all_matched or []],
            "policy_version": policy_version,
        },
    )


def _fallback_rules() -> list[PolicyRule]:
    return [
        # PolicyRule(
        #     rule_id="deny_secret_exfiltration",
        #     effect=PolicyEffect.DENY,
        #     reason="Secret-like content combined with external send.",
        #     priority=100,
        #     event_types=["tool_invoke"],
        #     capabilities=[CAP_EXTERNAL_SEND],
        #     risk_signals=["secret_detected", "api_key_detected", "system_prompt_leak"],
        # ),
        # PolicyRule(
        #     rule_id="review_external_send",
        #     effect=PolicyEffect.REQUIRE_REMOTE_REVIEW,
        #     reason="External send is high-risk and needs remote review.",
        #     priority=60,
        #     event_types=["tool_invoke"],
        #     capabilities=[CAP_EXTERNAL_SEND],
        # ),
    ]


_REVIEW_CONTEXT_METADATA_EXCLUDE = {
    "client_session_key",
    "client_plugin_config",
    "remote_plugin_config",
    "client_config_url",
    "client_plugin_list_url",
    "client_health_url",
    "client_ip",
}


def _review_rule_payload(rule: Any) -> dict[str, Any]:
    return {
        "rule_id": getattr(rule, "rule_id", ""),
        "rule_reason": getattr(rule, "reason", ""),
        "review_kind": (getattr(rule, "metadata", {}) or {}).get("review_kind") or "llm_check",
    }


def _review_current_call_payload(event: RuntimeEvent) -> dict[str, Any]:
    payload = {
        "event_type": event.event_type.value,
        "risk_signals": list(event.risk_signals),
    }
    event_payload = event.payload.to_dict()
    if event.event_type.value == "tool_invoke":
        payload.update(
            {
                "tool_name": event_payload.get("tool_name"),
                "arguments": event_payload.get("arguments") or {},
                "capabilities": event_payload.get("capabilities") or [],
            }
        )
        destination = _destination_payload(event_payload.get("arguments") or {})
        if destination:
            payload["destination"] = destination
    else:
        payload["payload"] = event_payload
    return payload


def _review_context_payload(context: RuntimeContext, event: RuntimeEvent) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "session_id": context.session_id,
        "principal": {
            "user_id": context.user_id,
            "agent_id": context.agent_id,
            "task_id": context.task_id,
        },
        "policy": {
            "name": context.policy,
            "version": context.policy_version,
            "environment": context.environment,
        },
        "current_risk_signals": list(event.risk_signals),
    }
    filtered_metadata = {
        key: value
        for key, value in dict(context.metadata or {}).items()
        if key not in _REVIEW_CONTEXT_METADATA_EXCLUDE
    }
    if filtered_metadata:
        payload["metadata"] = filtered_metadata
    destination = _destination_payload(getattr(event.payload, "arguments", {}))
    if destination:
        payload["destination"] = destination
    return payload


def _review_recent_events_payload(events: list[RuntimeEvent]) -> list[dict[str, Any]]:
    projected: list[dict[str, Any]] = []
    for item in events:
        event_payload = _project_recent_event(item)
        if event_payload:
            projected.append(event_payload)
    return projected


def _project_recent_event(event: RuntimeEvent) -> dict[str, Any] | None:
    if event.event_type.value == "llm_input":
        return _project_llm_input_event(event)
    if event.event_type.value == "llm_output":
        return _project_llm_output_event(event)
    if event.event_type.value == "tool_invoke":
        return _project_tool_invoke_event(event)
    if event.event_type.value == "tool_result":
        return _project_tool_result_event(event)
    return None


def _project_llm_input_event(event: RuntimeEvent) -> dict[str, Any] | None:
    payload = event.payload.to_dict()
    messages: list[dict[str, Any]] = []
    for block in payload.get("messages") or []:
        if isinstance(block, dict) and isinstance(block.get("input"), list):
            for item in block.get("input") or []:
                entry = _project_message_item(item)
                if entry:
                    messages.append(entry)
        else:
            entry = _project_message_item(block)
            if entry:
                messages.append(entry)
    if not messages:
        return None
    return _with_event_common(
        event,
        {
            "event_type": event.event_type.value,
            "messages": messages,
        },
    )


def _project_llm_output_event(event: RuntimeEvent) -> dict[str, Any] | None:
    payload = event.payload.to_dict()
    structured = _parse_structured_payload(payload.get("output"))
    projected: dict[str, Any] = {"event_type": event.event_type.value}
    if isinstance(structured, dict):
        data = structured.get("data") if isinstance(structured.get("data"), dict) else structured
        content = str(data.get("content") or "").strip() if isinstance(data, dict) else ""
        if content:
            projected["content"] = _truncate_text(content)
        tool_calls = _project_tool_calls(data.get("tool_calls") if isinstance(data, dict) else None)
        if tool_calls:
            projected["tool_calls"] = tool_calls
    if "content" not in projected and "tool_calls" not in projected:
        raw_output = str(payload.get("output") or "").strip()
        if not raw_output:
            return None
        projected["output_excerpt"] = _truncate_text(raw_output)
    return _with_event_common(event, projected)


def _project_tool_invoke_event(event: RuntimeEvent) -> dict[str, Any] | None:
    payload = event.payload.to_dict()
    projected = {
        "event_type": event.event_type.value,
        "tool_name": payload.get("tool_name"),
        "arguments": payload.get("arguments") or {},
        "capabilities": payload.get("capabilities") or [],
    }
    return _with_event_common(event, projected)


def _project_tool_result_event(event: RuntimeEvent) -> dict[str, Any] | None:
    payload = event.payload.to_dict()
    result = payload.get("result")
    projected = {
        "event_type": event.event_type.value,
        "tool_name": payload.get("tool_name"),
        "result": _truncate_text(result),
    }
    return _with_event_common(event, projected)


def _project_message_item(item: Any) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None
    if isinstance(item.get("data"), dict):
        role = _normalize_message_role(item.get("type"))
        content = _extract_message_content(item["data"].get("content"))
        if role and content:
            return {"role": role, "content": _truncate_text(content)}
        return None
    role = _normalize_message_role(item.get("role"))
    content = _extract_message_content(item.get("content"))
    if role and content:
        return {"role": role, "content": _truncate_text(content)}
    return None


def _normalize_message_role(role: Any) -> str:
    normalized = str(role or "").strip().lower()
    return {
        "human": "user",
        "ai": "assistant",
    }.get(normalized, normalized)


def _extract_message_content(content: Any) -> str:
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                text = str(item.get("text") or item.get("content") or "").strip()
                if text:
                    parts.append(text)
        return "\n".join(parts).strip()
    return str(content or "").strip()


def _parse_structured_payload(value: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except Exception:
        pass
    try:
        return ast.literal_eval(text)
    except Exception:
        return None


def _project_tool_calls(tool_calls: Any) -> list[dict[str, Any]]:
    projected: list[dict[str, Any]] = []
    if not isinstance(tool_calls, list):
        return projected
    for item in tool_calls:
        if not isinstance(item, dict):
            continue
        projected.append(
            {
                "name": item.get("name"),
                "args": item.get("args") or {},
            }
        )
    return projected


def _destination_payload(arguments: Any) -> dict[str, Any] | None:
    if not isinstance(arguments, dict):
        return None
    url = arguments.get("url") or arguments.get("uri") or arguments.get("endpoint")
    if not isinstance(url, str) or not url.strip():
        return None
    parsed = urlparse(url)
    payload = {"url": url}
    if parsed.netloc:
        payload["domain"] = parsed.netloc
    return payload


def _truncate_text(value: Any, max_chars: int = 400) -> str:
    text = str(value or "").strip()
    if len(text) <= max_chars:
        return text
    return f"{text[: max_chars - 3]}..."


def _with_event_common(event: RuntimeEvent, payload: dict[str, Any]) -> dict[str, Any]:
    if event.risk_signals:
        payload["risk_signals"] = list(event.risk_signals)
    return payload

"""LLM-driven AgentGuard DSL generation workflow."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

from shared.rules.dsl_compat import DSLCompatReport, parse_legacy_rules
from shared.schemas.policy import PolicyEffect, PolicyRule

_DEFAULT_TEMPLATE_PATH = Path(__file__).with_name("DSLgeneration.md")
_DEFAULT_SHORTLIST_LIMIT = 12
_DEFAULT_DEBUG_LOG_DIR = Path.cwd() / "tmp" / "llm_dsl_generator_logs"
_JSON_FENCE_RE = re.compile(r"^```(?:json)?\s*(?P<body>[\s\S]*?)\s*```$", flags=re.IGNORECASE)


class _LLMCompleter(Protocol):
    def complete(self, prompt: str, **kwargs: Any) -> str: ...


@dataclass(slots=True)
class RuleGenerationRequest:
    user_requirement: str
    agent_id: str = "unknown"
    tool_catalog: list[dict[str, Any]] = field(default_factory=list)
    existing_rules: list[Any] = field(default_factory=list)
    strategy_preferences: list[str] = field(default_factory=list)
    conversation_notes: list[str] = field(default_factory=list)
    max_rounds: int = 4
    llm_temperature: float = 0.0
    llm_max_tokens: int = 1800


@dataclass(slots=True)
class ValidationIssue:
    code: str
    message: str
    path: str = ""

    def to_dict(self) -> dict[str, str]:
        payload = {"code": self.code, "message": self.message}
        if self.path:
            payload["path"] = self.path
        return payload


@dataclass(slots=True)
class RuleValidationResult:
    ok: bool
    errors: list[ValidationIssue] = field(default_factory=list)
    warnings: list[ValidationIssue] = field(default_factory=list)
    parsed_dsl_rules: list[PolicyRule] = field(default_factory=list)
    normalized_rules: list[PolicyRule] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "errors": [issue.to_dict() for issue in self.errors],
            "warnings": [issue.to_dict() for issue in self.warnings],
            "parsed_dsl_rules": [rule.to_dict() for rule in self.parsed_dsl_rules],
            "normalized_rules": [rule.to_dict() for rule in self.normalized_rules],
        }


@dataclass(slots=True)
class RuleCandidate:
    round_index: int
    prompt: str
    raw_response: str
    payload: dict[str, Any] | None
    validation: RuleValidationResult
    mode: str = "generate"
    user_feedback: str = ""

    @property
    def accepted(self) -> bool:
        return self.validation.ok


@dataclass(slots=True)
class RuleGenerationSession:
    request: RuleGenerationRequest
    attempts: list[RuleCandidate] = field(default_factory=list)
    accepted_candidate: RuleCandidate | None = None
    user_feedback_history: list[str] = field(default_factory=list)
    stop_reason: str = ""
    debug_session_id: str = field(default_factory=lambda: _build_debug_session_id())

    @property
    def remaining_rounds(self) -> int:
        return max(0, self.request.max_rounds - len(self.attempts))

    @property
    def latest_candidate(self) -> RuleCandidate | None:
        if not self.attempts:
            return None
        return self.attempts[-1]

    def to_dict(self) -> dict[str, Any]:
        return {
            "request": {
                "user_requirement": self.request.user_requirement,
                "agent_id": self.request.agent_id,
                "tool_catalog_size": len(self.request.tool_catalog),
                "existing_rules_size": len(self.request.existing_rules),
                "strategy_preferences": list(self.request.strategy_preferences),
                "conversation_notes": list(self.request.conversation_notes),
                "max_rounds": self.request.max_rounds,
            },
            "attempts": [
                {
                    "round_index": item.round_index,
                    "mode": item.mode,
                    "accepted": item.accepted,
                    "payload": item.payload,
                    "validation": item.validation.to_dict(),
                }
                for item in self.attempts
            ],
            "accepted_candidate": self.accepted_candidate.payload if self.accepted_candidate else None,
            "user_feedback_history": list(self.user_feedback_history),
            "stop_reason": self.stop_reason,
        }


class LLMRuleGeneratorWorkflow:
    """Drive prompt construction, model calls, validation, and repair loops."""

    def __init__(
        self,
        llm_client: _LLMCompleter | None = None,
        *,
        llm_config: dict[str, Any] | None = None,
        prompt_template: str | None = None,
        prompt_template_path: str | Path | None = None,
        shortlist_limit: int = _DEFAULT_SHORTLIST_LIMIT,
        debug_log_dir: str | Path | None = None,
    ) -> None:
        self._llm_client = llm_client or self._build_default_llm_client(llm_config or {})
        self._prompt_template = prompt_template or load_generation_template(prompt_template_path)
        self._shortlist_limit = max(1, int(shortlist_limit))
        self._debug_log_dir = Path(debug_log_dir) if debug_log_dir else _DEFAULT_DEBUG_LOG_DIR

    def generate(
        self,
        request: RuleGenerationRequest | str,
        **kwargs: Any,
    ) -> RuleGenerationSession:
        prepared = self._coerce_request(request, **kwargs)
        session = RuleGenerationSession(request=prepared)
        self._run_loop(session, mode="generate")
        return session

    def refine(
        self,
        session: RuleGenerationSession,
        user_feedback: str,
    ) -> RuleGenerationSession:
        feedback = str(user_feedback or "").strip()
        if not feedback:
            raise ValueError("user_feedback is required")
        if session.accepted_candidate is None:
            raise ValueError("refine() requires an accepted candidate from a previous round")
        session.user_feedback_history.append(feedback)
        session.stop_reason = ""
        self._run_loop(session, mode="refine", user_feedback=feedback)
        return session

    def validate_candidate(
        self,
        payload: dict[str, Any] | None,
        request: RuleGenerationRequest,
    ) -> RuleValidationResult:
        errors: list[ValidationIssue] = []
        warnings: list[ValidationIssue] = []
        parsed_dsl_rules: list[PolicyRule] = []
        normalized_rules: list[PolicyRule] = []

        if payload is None:
            return RuleValidationResult(
                ok=False,
                errors=[ValidationIssue("invalid_json", "Model output is not valid JSON.")],
            )
        if not isinstance(payload, dict):
            return RuleValidationResult(
                ok=False,
                errors=[ValidationIssue("invalid_payload", "Model output must be a JSON object.")],
            )

        summary = payload.get("summary")
        if not isinstance(summary, str):
            errors.append(ValidationIssue("summary_type", "`summary` must be a string.", "summary"))

        assumptions = payload.get("assumptions")
        if not isinstance(assumptions, list) or not all(isinstance(item, str) for item in assumptions):
            errors.append(
                ValidationIssue(
                    "assumptions_type",
                    "`assumptions` must be an array of strings.",
                    "assumptions",
                )
            )

        payload_warnings = payload.get("warnings")
        if not isinstance(payload_warnings, list) or not all(isinstance(item, str) for item in payload_warnings):
            errors.append(
                ValidationIssue(
                    "warnings_type",
                    "`warnings` must be an array of strings.",
                    "warnings",
                )
            )

        rules = payload.get("rules")
        if not isinstance(rules, str):
            errors.append(
                ValidationIssue(
                    "rules_type",
                    "`rules` must be a string containing zero or one DSL RULE block.",
                    "rules",
                )
            )
            rules = ""

        tool_names = {
            str(tool.get("name") or "").strip()
            for tool in request.tool_catalog
            if str(tool.get("name") or "").strip()
        }

        dsl = str(rules or "").strip()
        if dsl:
            parsed_rules, parsed_report = parse_legacy_rules(dsl)
            errors.extend(_compat_errors(parsed_report, "rules"))
            warnings.extend(_compat_warnings(parsed_report, "rules"))
            if len(parsed_rules) != 1:
                errors.append(
                    ValidationIssue(
                        "dsl_rule_count",
                        "DSL must compile into exactly one rule.",
                        "rules",
                    )
                )
            else:
                parsed_rule = parsed_rules[0]
                parsed_dsl_rules.append(parsed_rule)
                normalized_rules.append(parsed_rule)
                if tool_names:
                    for tool_name in parsed_rule.tool_names:
                        if tool_name not in tool_names:
                            errors.append(
                                ValidationIssue(
                                    "unknown_tool_match",
                                    f"Unknown tool '{tool_name}'. Only tools in the current catalog may be used.",
                                    "rules",
                                )
                            )
                if parsed_rule.effect == PolicyEffect.REQUIRE_REMOTE_REVIEW:
                    prompt = str((parsed_rule.metadata or {}).get("llm_prompt") or "").strip()
                    if not prompt:
                        errors.append(
                            ValidationIssue(
                                "llm_prompt_missing",
                                "LLM_CHECK rules must include Prompt.",
                                "rules",
                            )
                        )
        return RuleValidationResult(
            ok=not errors,
            errors=errors,
            warnings=warnings,
            parsed_dsl_rules=parsed_dsl_rules,
            normalized_rules=normalized_rules,
        )

    def shortlist_tools(
        self,
        tool_catalog: list[dict[str, Any]],
        user_requirement: str,
    ) -> list[dict[str, Any]]:
        keywords = _tokenize(user_requirement)
        scored: list[tuple[tuple[int, int, str], dict[str, Any]]] = []
        for tool in tool_catalog:
            name = str(tool.get("name") or "").strip()
            if not name:
                continue
            haystack = " ".join(
                [
                    name,
                    str(tool.get("owner_agent_id") or ""),
                    " ".join(str(item) for item in tool.get("input_params") or []),
                    " ".join(str(item) for item in (tool.get("labels") or {}).get("tags") or []),
                    " ".join(
                        str((tool.get("labels") or {}).get(key) or "")
                        for key in ("boundary", "sensitivity", "integrity")
                    ),
                ]
            ).lower()
            score = sum(2 if token in name.lower() else 1 for token in keywords if token in haystack)
            boundary_bonus = 1 if "external" in haystack or "privileged" in haystack else 0
            sort_key = (-score, -boundary_bonus, name)
            scored.append((sort_key, _catalog_entry(tool)))
        scored.sort(key=lambda item: item[0])
        shortlist = [tool for _, tool in scored[: self._shortlist_limit]]
        if shortlist:
            return shortlist
        return [_catalog_entry(tool) for tool in tool_catalog[: self._shortlist_limit]]

    def build_generation_prompt(
        self,
        request: RuleGenerationRequest,
    ) -> str:
        shortlist = self.shortlist_tools(request.tool_catalog, request.user_requirement)
        prompt = _render_template(
            self._prompt_template,
            agent_id=request.agent_id,
            user_requirement=request.user_requirement,
            tool_shortlist=shortlist,
        )
        return _join_sections(
            prompt,
            ("current rules: ", _serialize_existing_rules(request.existing_rules)),
            ("strategy preferences: ", _serialize_list(request.strategy_preferences)),
            ("context: ", _serialize_list(request.conversation_notes)),
        )

    def build_repair_prompt(
        self,
        request: RuleGenerationRequest,
        failed_candidate: RuleCandidate,
    ) -> str:
        return _join_sections(
            self.build_generation_prompt(request),
            (
                "current round output failed validation, please fix directly",
                _format_validation_issues(failed_candidate.validation.errors),
            ),
            ("previous round LLM output", failed_candidate.raw_response.strip()),
            (
                "repair requirements",
                (
                    "keep user requirements unchanged; strictly fix all errors; continue to return only valid JSON;"
                    "do not explain, do not output Markdown."
                ),
            ),
        )

    def build_refinement_prompt(
        self,
        session: RuleGenerationSession,
        user_feedback: str,
    ) -> str:
        accepted = session.accepted_candidate
        if accepted is None or accepted.payload is None:
            raise ValueError("refinement requires an accepted candidate payload")
        return _join_sections(
            self.build_generation_prompt(session.request),
            ("current accepted candidate rules", json.dumps(accepted.payload, ensure_ascii=False, indent=2)),
            ("user feedback", user_feedback),
            (
                "refinement requirements",
                (
                    "modify rules according to user feedback while keeping original objectives;"
                    "continue to output complete JSON, do not output only diffs."
                ),
            ),
        )

    def _run_loop(
        self,
        session: RuleGenerationSession,
        *,
        mode: str,
        user_feedback: str = "",
    ) -> None:
        while session.remaining_rounds > 0:
            round_index = len(session.attempts) + 1
            prompt = self._build_round_prompt(session, mode=mode, user_feedback=user_feedback)
            llm_args = {
                "temperature": session.request.llm_temperature,
                "max_tokens": session.request.llm_max_tokens,
            }
            self._write_debug_log(
                session,
                round_index=round_index,
                phase="request",
                payload={
                    "mode": mode,
                    "user_feedback": user_feedback,
                    "prompt": prompt,
                    "llm_args": llm_args,
                    "request": {
                        "agent_id": session.request.agent_id,
                        "user_requirement": session.request.user_requirement,
                        "tool_catalog_size": len(session.request.tool_catalog),
                        "existing_rules_size": len(session.request.existing_rules),
                        "strategy_preferences": list(session.request.strategy_preferences),
                        "conversation_notes": list(session.request.conversation_notes),
                        "max_rounds": session.request.max_rounds,
                    },
                },
            )
            raw_response = self._llm_client.complete(prompt, **llm_args)
            payload = _parse_json_payload(raw_response)
            validation = self.validate_candidate(payload, session.request)
            candidate = RuleCandidate(
                round_index=round_index,
                prompt=prompt,
                raw_response=raw_response,
                payload=payload,
                validation=validation,
                mode=mode,
                user_feedback=user_feedback,
            )
            self._write_debug_log(
                session,
                round_index=round_index,
                phase="response",
                payload={
                    "mode": mode,
                    "user_feedback": user_feedback,
                    "raw_response": raw_response,
                    "parsed_payload": payload,
                    "validation": validation.to_dict(),
                    "accepted": candidate.accepted,
                },
            )
            session.attempts.append(candidate)
            if candidate.accepted:
                session.accepted_candidate = candidate
                session.stop_reason = "ready_for_user_review"
                return
        session.stop_reason = "max_rounds_exhausted"

    def _build_round_prompt(
        self,
        session: RuleGenerationSession,
        *,
        mode: str,
        user_feedback: str,
    ) -> str:
        latest = session.latest_candidate
        if mode == "refine":
            if latest is not None and not latest.accepted:
                return self.build_repair_prompt(session.request, latest)
            return self.build_refinement_prompt(session, user_feedback)
        if latest is not None and not latest.accepted:
            return self.build_repair_prompt(session.request, latest)
        return self.build_generation_prompt(session.request)

    @staticmethod
    def _coerce_request(request: RuleGenerationRequest | str, **kwargs: Any) -> RuleGenerationRequest:
        if isinstance(request, RuleGenerationRequest):
            if kwargs:
                raise TypeError("kwargs are not supported when request is already a RuleGenerationRequest")
            return request
        return RuleGenerationRequest(user_requirement=str(request), **kwargs)

    @staticmethod
    def _build_default_llm_client(config: dict[str, Any]) -> _LLMCompleter:
        from backend.llm.llm_client import LLMClient

        return LLMClient(config=config)

    def _write_debug_log(
        self,
        session: RuleGenerationSession,
        *,
        round_index: int,
        phase: str,
        payload: dict[str, Any],
    ) -> None:
        try:
            self._debug_log_dir.mkdir(parents=True, exist_ok=True)
            log_path = self._debug_log_dir / _build_debug_log_name(
                session.debug_session_id,
                round_index=round_index,
                phase=phase,
            )
            log_path.write_text(
                json.dumps(
                    {
                        "timestamp": _utc_timestamp(),
                        "session_id": session.debug_session_id,
                        "round_index": round_index,
                        "phase": phase,
                        **payload,
                    },
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
        except OSError:
            # Debug logging should never break rule generation.
            return


def load_generation_template(path: str | Path | None = None) -> str:
    template_path = Path(path) if path else _DEFAULT_TEMPLATE_PATH
    return template_path.read_text(encoding="utf-8")


def _render_template(
    template: str,
    *,
    agent_id: str,
    user_requirement: str,
    tool_shortlist: list[dict[str, Any]],
) -> str:
    return (
        template.replace("{{AGENT_ID}}", agent_id or "unknown")
        .replace("{{USER_REQUIREMENT}}", user_requirement.strip())
        .replace(
            "{{TOOL_SHORTLIST_JSON}}",
            json.dumps(tool_shortlist, ensure_ascii=False, indent=2),
        )
    )


def _join_sections(base: str, *sections: tuple[str, str]) -> str:
    blocks = [base.strip()]
    for title, body in sections:
        content = str(body or "").strip()
        if not content:
            continue
        blocks.append(f"{title}:\n{content}")
    return "\n\n".join(blocks)


def _serialize_existing_rules(existing_rules: list[Any]) -> str:
    if not existing_rules:
        return "[]"
    normalized: list[Any] = []
    for item in existing_rules:
        if isinstance(item, PolicyRule):
            normalized.append(item.to_dict())
            continue
        if isinstance(item, dict):
            normalized.append(item)
            continue
        text = str(item or "").strip()
        if text:
            normalized.append(text)
    return json.dumps(normalized, ensure_ascii=False, indent=2)


def _serialize_list(items: list[str]) -> str:
    cleaned = [str(item).strip() for item in items if str(item).strip()]
    if not cleaned:
        return "[]"
    return "\n".join(f"- {item}" for item in cleaned)


def _catalog_entry(tool: dict[str, Any]) -> dict[str, Any]:
    labels = dict(tool.get("labels") or {})
    return {
        "name": str(tool.get("name") or "").strip(),
        "owner_agent_id": str(tool.get("owner_agent_id") or "").strip(),
        "tool_key": str(tool.get("tool_key") or "").strip(),
        "input_params": [str(item) for item in tool.get("input_params") or [] if str(item).strip()],
        "labels": {
            "boundary": str(labels.get("boundary") or "internal"),
            "sensitivity": str(labels.get("sensitivity") or "low"),
            "integrity": str(labels.get("integrity") or "trusted"),
            "tags": [str(item) for item in labels.get("tags") or [] if str(item).strip()],
        },
    }


def _parse_json_payload(raw_response: str) -> dict[str, Any] | None:
    raw = str(raw_response or "").strip()
    if not raw:
        return None
    fenced = _JSON_FENCE_RE.match(raw)
    if fenced:
        raw = fenced.group("body").strip()
    try:
        payload = json.loads(raw)
        return payload if isinstance(payload, dict) else None
    except json.JSONDecodeError:
        pass

    start = raw.find("{")
    end = raw.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    try:
        payload = json.loads(raw[start : end + 1])
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def _compat_errors(report: DSLCompatReport, path: str) -> list[ValidationIssue]:
    return [
        ValidationIssue("dsl_parse_error", str(item.get("message") or "DSL parse error"), path)
        for item in report.errors
    ]


def _compat_warnings(report: DSLCompatReport, path: str) -> list[ValidationIssue]:
    return [
        ValidationIssue("dsl_warning", str(item.get("message") or "DSL warning"), path)
        for item in report.warnings
    ]


def _format_validation_issues(issues: list[ValidationIssue]) -> str:
    if not issues:
        return "无"
    return "\n".join(
        f"- [{issue.code}] {issue.message}" + (f" ({issue.path})" if issue.path else "")
        for issue in issues
    )


def _tokenize(text: str) -> set[str]:
    return {token for token in re.findall(r"[A-Za-z0-9_.-]+", str(text or "").lower()) if len(token) > 1}


def _utc_timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _build_debug_session_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")


def _build_debug_log_name(session_id: str, *, round_index: int, phase: str) -> str:
    return f"{session_id}_round_{round_index:02d}_{phase}.json"

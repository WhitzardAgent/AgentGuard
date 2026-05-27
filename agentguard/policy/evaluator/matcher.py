"""Synchronous hot-path policy evaluator.

Two operating modes:

* **Flat** — constructed from an iterable of compiled rules, evaluates every
  call against the same global rule set. Used by tests and code paths that do
  not need per-agent routing.
* **Routed** — constructed with a :class:`RuleRouter`, the evaluator keeps a
  per ``agent_id`` indexed view (cached, invalidated when the router catalogue
  changes) so each call only matches rules bound to the requesting agent.

Decision merging is unchanged: candidates are scored and combined in the
priority order ``DENY > LLM_CHECK > HUMAN_CHECK > DEGRADE > ALLOW``.
"""

from __future__ import annotations

import fnmatch
from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Iterable

from agentguard.policy.dsl.compiler import CompiledRule
from agentguard.policy.evaluator.obligations import build_obligations
from agentguard.policy.evaluator.predicates import RiskScorer
from agentguard.policy.routing import RuleRouter
from agentguard.models.decisions import Action, Decision, Obligation
from agentguard.models.events import EventType, RuntimeEvent


_EVENT_SUBTYPE_MATCH: dict[str, set[EventType]] = {
    # DSL subtype → internal EventType set.
    # "requested" covers both REQUESTED (async/API path) and ATTEMPT (sync wrapper path).
    # "completed" covers both COMPLETED and RESULT for the same reason.
    "requested": {EventType.TOOL_CALL_REQUESTED, EventType.TOOL_CALL_ATTEMPT},
    "completed": {EventType.TOOL_CALL_COMPLETED, EventType.TOOL_CALL_RESULT},
    "failed":    {EventType.TOOL_CALL_FAILED},
}


def _event_matches_subtype(rule: CompiledRule, event: RuntimeEvent) -> bool:
    sub = getattr(rule, "event_subtype", "") or ""
    if not sub:
        return True                       # no subtype filter → match all phases
    allowed = _EVENT_SUBTYPE_MATCH.get(sub.lower())
    return allowed is None or event.event_type in allowed


def _merge_llm_prompts(matched: list[CompiledRule]) -> str | None:
    prompts: list[str] = []
    for rule in matched:
        prompt = rule.llm_prompt.strip()
        if prompt and prompt not in prompts:
            prompts.append(prompt)
    if not prompts:
        return None
    return "\n\n".join(prompts)


def _merge_rule_reasons(matched: list[CompiledRule]) -> str:
    reasons: list[str] = []
    for rule in matched:
        text = str(rule.meta.get("reason") or rule.rule_id).strip()
        if text and text not in reasons:
            reasons.append(text)
    return " | ".join(reasons)


@dataclass
class _IndexedView:
    """Pre-computed dispatch index for a fixed rule list."""

    rules: list[CompiledRule]
    by_pattern: dict[str, list[CompiledRule]]

    @classmethod
    def build(cls, rules: Iterable[CompiledRule]) -> "_IndexedView":
        rule_list = list(rules)
        index: dict[str, list[CompiledRule]] = defaultdict(list)
        for r in rule_list:
            index[r.tool_pattern].append(r)
        return cls(rules=rule_list, by_pattern=dict(index))

    def candidates(self, tool_name: str) -> list[CompiledRule]:
        direct = self.by_pattern.get(tool_name, [])
        wild = [
            r
            for pat, bucket in self.by_pattern.items()
            if pat != tool_name and "*" in pat
            for r in bucket
            if fnmatch.fnmatchcase(tool_name, pat)
        ]
        return direct + wild


class FastEvaluator:
    def __init__(
        self,
        rules: Iterable[CompiledRule] | None = None,
        *,
        rule_version: str = "v1",
        risk_scorer: RiskScorer | None = None,
        router: RuleRouter | None = None,
    ) -> None:
        self._rule_version = rule_version
        self._risk = risk_scorer or RiskScorer()
        self._router = router
        self._global_view = _IndexedView.build(rules or [])
        self._agent_views: dict[str, _IndexedView] = {}

    # -------------------- catalogue management --------------------

    def load(self, rules: Iterable[CompiledRule]) -> None:
        """Replace the flat rule set and invalidate any per-agent caches."""
        self._global_view = _IndexedView.build(rules)
        self._agent_views.clear()

    def attach_router(self, router: RuleRouter | None) -> None:
        self._router = router
        self._agent_views.clear()

    def invalidate(self) -> None:
        self._agent_views.clear()

    @property
    def _rules(self) -> list[CompiledRule]:
        """Compatibility shim: callers that read every loaded rule.

        With a router attached returns the union across packs; otherwise
        returns the flat rule list.
        """
        if self._router is not None:
            return self._router.all_rules()
        return list(self._global_view.rules)

    def rule_count(self) -> int:
        return len(self._rules)

    def rules_for_agent(self, agent_id: str) -> list[CompiledRule]:
        return list(self._view_for(agent_id).rules)

    # -------------------- evaluation --------------------

    def evaluate(
        self,
        event: RuntimeEvent,
        features: dict[str, Any] | None = None,
    ) -> Decision:
        features = features or {}
        if event.tool_call is None:
            return Decision.allow(reason="no-tool-call", rule_version=self._rule_version)

        agent_id = event.principal.agent_id if event.principal else ""
        view = self._view_for(agent_id)
        candidates = view.candidates(event.tool_call.tool_name)

        hits: dict[Action, list[CompiledRule]] = defaultdict(list)
        for rule in candidates:
            if not _event_matches_subtype(rule, event):
                continue
            try:
                if rule.predicate(event, features):
                    hits[rule.action].append(rule)
            except Exception:
                continue

        for action in (
            Action.DENY,
            Action.LLM_CHECK,
            Action.HUMAN_CHECK,
            Action.DEGRADE,
            Action.ALLOW,
        ):
            if hits[action]:
                return self._build(action, hits[action], event, features)

        risk = self._risk.score(event, features, matched=[])
        return Decision(
            action=Action.ALLOW,
            risk_score=risk,
            matched_rules=[],
            rule_version=self._rule_version,
            reason="no-rule-matched",
        )

    # -------------------- internals --------------------

    def _view_for(self, agent_id: str) -> _IndexedView:
        if self._router is None:
            return self._global_view
        cached = self._agent_views.get(agent_id)
        if cached is not None:
            return cached
        view = _IndexedView.build(self._router.rules_for_agent(agent_id))
        self._agent_views[agent_id] = view
        return view

    def _build(
        self,
        action: Action,
        matched: list[CompiledRule],
        event: RuntimeEvent,
        features: dict[str, Any],
    ) -> Decision:
        risk = self._risk.score(event, features, matched=[r.rule_id for r in matched])
        obligations: list[Obligation] = []
        degrade_profile: str | None = None
        for r in matched:
            if r.degrade_profile and degrade_profile is None:
                degrade_profile = r.degrade_profile
            obligations.extend(build_obligations(r, event))
        llm_system_prompt = _merge_llm_prompts(matched) if action is Action.LLM_CHECK else None
        return Decision(
            action=action,
            risk_score=risk,
            matched_rules=[r.rule_id for r in matched],
            obligations=obligations,
            rule_version=self._rule_version,
            degrade_profile=degrade_profile,
            reason=_merge_rule_reasons(matched),
            llm_system_prompt=llm_system_prompt,
        )

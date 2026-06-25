"""Process-wide console state bound to the shared RuntimeManager.

Provides the real, observable data the web console renders: a tool catalog with
editable labels, a console-managed rule store (DSL <-> PolicyRule), and live
traffic / audit / approval records populated from actual guard decisions.
"""
from __future__ import annotations

import threading
import time
from collections import deque
from typing import Any

from shared.schemas.decisions import DecisionType, GuardDecision
from shared.schemas.events import RuntimeEvent
from shared.schemas.policy import PolicyRule
from shared.rules.llm_dsl_generator import (
    LLMRuleGeneratorWorkflow,
    RuleGenerationRequest,
    RuleGenerationSession,
)
from backend.console.dsl import ParsedRule, parse_source, rule_to_console_dict
from backend.runtime.manager import RuntimeManager

_DECISION_TO_ACTION = {
    DecisionType.ALLOW: "allow",
    DecisionType.LOG_ONLY: "allow",
    DecisionType.DENY: "deny",
    DecisionType.REQUIRE_APPROVAL: "human_check",
    DecisionType.HUMAN_CHECK: "human_check",
    DecisionType.REQUIRE_REMOTE_REVIEW: "human_check",
    DecisionType.DEGRADE: "degrade",
    DecisionType.SANITIZE: "degrade",
}
class ConsoleState:
    def __init__(self, manager: RuntimeManager) -> None:
        self.manager = manager
        self._lock = threading.Lock()
        self._start = time.time()

        # Baseline (non-editable) rules captured from the manager's policy store.
        self._base_rules: list[PolicyRule] = list(manager.policy.store.rules())
        self._console_rules: dict[str, dict[str, Any]] = {}

        self._tools: dict[tuple[str, str], dict[str, Any]] = {}

        self._traffic: deque[dict[str, Any]] = deque(maxlen=1000)
        self._audit: deque[dict[str, Any]] = deque(maxlen=1000)

        manager.add_observer(self._observe)

    # ---- agents / tools ------------------------------------------------
    def agents(self) -> list[str]:
        return sorted({owner for owner, _ in self._tools})

    def tools(self, agent_id: str | None = None) -> list[dict[str, Any]]:
        with self._lock:
            items = list(self._tools.values())
        if agent_id:
            items = [t for t in items if t["owner_agent_id"] == agent_id]
        return [dict(t) for t in items]

    def register_tool(
        self,
        context: dict[str, Any] | Any,
        tool: dict[str, Any],
    ) -> dict[str, Any] | None:
        if hasattr(context, "to_dict"):
            context = context.to_dict()
        ctx = dict(context or {})
        agent_id = str(ctx.get("agent_id") or "").strip()
        name = str(tool.get("name") or "").strip()
        if not agent_id or not name:
            return None

        incoming_labels = dict(tool.get("labels") or {})
        labels = {
            "boundary": str(incoming_labels.get("boundary") or "internal"),
            "sensitivity": str(incoming_labels.get("sensitivity") or "low"),
            "integrity": str(incoming_labels.get("integrity") or "trusted"),
            "tags": [str(tag) for tag in (incoming_labels.get("tags") or []) if str(tag).strip()],
        }
        input_params = [str(param) for param in (tool.get("input_params") or []) if str(param).strip()]

        with self._lock:
            existing = self._tools.get((agent_id, name)) or {}
            current_labels = dict(existing.get("labels") or {})
            merged_labels = {
                "boundary": current_labels.get("boundary") or labels["boundary"],
                "sensitivity": current_labels.get("sensitivity") or labels["sensitivity"],
                "integrity": current_labels.get("integrity") or labels["integrity"],
                "tags": current_labels.get("tags") or labels["tags"],
            }
            record = {
                "owner_agent_id": agent_id,
                "name": name,
                "labels": merged_labels,
                "input_params": input_params or list(existing.get("input_params") or []),
            }
            self._tools[(agent_id, name)] = record
            return dict(record)

    def patch_tool_labels(
        self, agent_id: str, tool_name: str, labels: dict[str, Any]
    ) -> dict[str, Any] | None:
        with self._lock:
            tool = self._tools.get((agent_id, tool_name))
            if tool is None:
                return None
            cur = tool["labels"]
            for key in ("boundary", "sensitivity", "integrity"):
                if labels.get(key):
                    cur[key] = labels[key]
            if "tags" in labels and isinstance(labels["tags"], list):
                cur["tags"] = labels["tags"]
            return dict(tool)

    # ---- rules ---------------------------------------------------------
    def check(self, source: str) -> dict[str, Any]:
        _, report = parse_source(source)
        return report.to_dict()

    def generate_rule(
        self,
        agent_id: str,
        requirement: str,
        *,
        user_feedback: str = "",
        current_candidate: dict[str, Any] | None = None,
        max_rounds: int = 4,
        llm_config: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        normalized_agent_id = str(agent_id or "").strip()
        normalized_requirement = str(requirement or "").strip()
        if not normalized_agent_id:
            return {"ok": False, "error": "agent_id is required", "code": 400}
        if not normalized_requirement:
            return {"ok": False, "error": "requirement is required", "code": 400}

        workflow = LLMRuleGeneratorWorkflow(llm_config=llm_config or {})
        tools = self.tools(normalized_agent_id)
        existing_rules = self.list_rules(normalized_agent_id)
        request = RuleGenerationRequest(
            user_requirement=normalized_requirement,
            agent_id=normalized_agent_id,
            tool_catalog=tools,
            existing_rules=existing_rules,
            max_rounds=max(1, int(max_rounds)),
        )

        if user_feedback.strip():
            if not isinstance(current_candidate, dict):
                return {
                    "ok": False,
                    "error": "current_candidate is required when user_feedback is provided",
                    "code": 400,
                }
            session = RuleGenerationSession(request=request)
            validation = workflow.validate_candidate(current_candidate, request)
            if not validation.ok:
                return {
                    "ok": False,
                    "error": "current_candidate failed validation",
                    "validation": validation.to_dict(),
                    "code": 422,
                }
            from shared.rules.llm_dsl_generator import RuleCandidate  # noqa: PLC0415

            accepted = RuleCandidate(
                round_index=0,
                prompt="",
                raw_response="",
                payload=current_candidate,
                validation=validation,
                mode="refine",
            )
            session.accepted_candidate = accepted
            updated = workflow.refine(session, user_feedback)
            return self._rule_generation_response(updated)

        session = workflow.generate(request)
        return self._rule_generation_response(session)

    def list_rules(self, agent_id: str | None = None) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for rule in self._base_rules:
            out.append(rule_to_console_dict(rule, user_managed=False))
        with self._lock:
            for entry in self._console_rules.values():
                if agent_id and entry["agent_id"] != agent_id:
                    continue
                out.append(entry["console"])
        return out

    def publish_rule(self, agent_id: str, source: str) -> dict[str, Any]:
        parsed, report = parse_source(source)
        if not report.ok:
            return {"ok": False, "error": report.errors[0]["message"], "errors": report.errors}
        if len(parsed) != 1:
            return {"ok": False, "error": "exactly one RULE block is required."}
        pr: ParsedRule = parsed[0]
        with self._lock:
            if pr.name in self._console_rules or any(
                r.rule_id == pr.name for r in self._base_rules
            ):
                return {"ok": False, "error": f"rule_id '{pr.name}' already exists", "code": 409}
            pr.rule.metadata["source_text"] = pr.source
            pr.rule.metadata["pack_id"] = f"agent::{agent_id}"
            self._console_rules[pr.name] = {
                "agent_id": agent_id,
                "rule": pr.rule,
                "console": rule_to_console_dict(pr.rule, user_managed=True),
            }
            self._rebuild_policy()
        return {
            "ok": True,
            "agent_id": agent_id,
            "pack_id": f"agent::{agent_id}",
            "rule_id": pr.name,
            "created": True,
        }

    def delete_rule(self, agent_id: str, rule_id: str) -> dict[str, Any]:
        with self._lock:
            entry = self._console_rules.get(rule_id)
            if entry is None or entry["agent_id"] != agent_id:
                return {"ok": False, "error": f"rule '{rule_id}' not found for agent '{agent_id}'", "code": 404}
            del self._console_rules[rule_id]
            self._rebuild_policy()
        return {"ok": True, "agent_id": agent_id, "pack_id": f"agent::{agent_id}", "rule_id": rule_id}

    def reload_rules(self, source: str) -> dict[str, Any]:
        parsed, report = parse_source(source)
        if not report.ok:
            return {
                "ok": False,
                "error": report.errors[0]["message"],
                "errors": report.errors,
                "rule_count": 0,
            }
        with self._lock:
            self._console_rules.clear()
            for pr in parsed:
                pr.rule.metadata["source_text"] = pr.source
                self._console_rules[pr.name] = {
                    "agent_id": "*",
                    "rule": pr.rule,
                    "console": rule_to_console_dict(pr.rule, user_managed=True),
                }
            self._rebuild_policy()
        return {"ok": True, "loaded": len(parsed)}

    def _rebuild_policy(self) -> None:
        rules = list(self._base_rules) + [e["rule"] for e in self._console_rules.values()]
        self.manager.policy.store.set_rules(rules)

    @staticmethod
    def _rule_generation_response(session: RuleGenerationSession) -> dict[str, Any]:
        candidate = session.accepted_candidate
        latest_validation = (
            candidate.validation.to_dict() if candidate else (
                session.latest_candidate.validation.to_dict() if session.latest_candidate else None
            )
        )
        payload = {
            "ok": candidate is not None,
            "agent_id": session.request.agent_id,
            "requirement": session.request.user_requirement,
            "stop_reason": session.stop_reason,
            "attempt_count": len(session.attempts),
            "remaining_rounds": session.remaining_rounds,
            "candidate": candidate.payload if candidate else None,
            "validation": latest_validation,
            "attempts": [
                {
                    "round_index": item.round_index,
                    "mode": item.mode,
                    "accepted": item.accepted,
                    "validation": item.validation.to_dict(),
                    "payload": item.payload,
                    "raw_response": item.raw_response,
                }
                for item in session.attempts
            ],
            "user_feedback_history": list(session.user_feedback_history),
        }
        if candidate is None:
            errors = list((latest_validation or {}).get("errors") or [])
            first_message = str(errors[0].get("message") or "").strip() if errors else ""
            payload["error"] = first_message or "Rule generation did not produce a valid candidate."
            payload["code"] = 422
        return payload

    # ---- runtime observability ----------------------------------------
    def health(self) -> dict[str, Any]:
        rules = self.manager.policy.store.rules()
        by_action: dict[str, int] = {}
        for r in rules:
            by_action[r.effect.value] = by_action.get(r.effect.value, 0) + 1
        return {
            "ok": True,
            "rules": len(rules),
            "rules_by_action": by_action,
            "mode": "enforce",
            "runtime_mode": "sync",
            "rule_version": self.manager.policy_version,
            "watcher_running": False,
            "uptime_s": round(time.time() - self._start, 2),
            "version": "0.3.0",
        }

    def stats(self, agent_id: str | None = None) -> dict[str, Any]:
        entries = self._traffic_entries(agent_id)
        total = len(entries)
        deny = sum(1 for e in entries if e["action"] == "deny")
        return {
            "total_requests": total,
            "uptime_s": round(time.time() - self._start, 2),
            "deny_count": deny,
            "deny_rate": round(deny / total, 4) if total else 0.0,
        }

    def traffic(
        self,
        agent_id: str | None = None,
        n: int = 30,
        action: str | None = None,
        tool: str | None = None,
    ) -> list[dict[str, Any]]:
        entries = self._traffic_entries(agent_id)
        if action:
            entries = [e for e in entries if e["action"] == action]
        if tool:
            entries = [e for e in entries if tool in (e.get("tool") or "")]
        return entries[-max(1, min(n, 1000)):][::-1]

    def audit_recent(self, agent_id: str | None = None, n: int = 20) -> list[dict[str, Any]]:
        with self._lock:
            entries = list(self._audit)
        if agent_id:
            entries = [
                e for e in entries
                if (e.get("event") or {}).get("principal", {}).get("agent_id") == agent_id
            ]
        return entries[-max(1, n):][::-1]

    def approvals(self, agent_id: str | None = None) -> list[dict[str, Any]]:
        items = [
            self._build_approval_item(ticket)
            for ticket in self.manager.review_queue.pending()
        ]
        if agent_id:
            items = [
                item for item in items
                if (item.get("event") or {}).get("principal", {}).get("agent_id") == agent_id
            ]
        return items

    def resolve_ticket(self, ticket_id: str, approved: bool, note: str = "") -> bool:
        return self.manager.review_queue.resolve(ticket_id, approved=approved, note=note) is not None

    # ---- observer ------------------------------------------------------
    def _traffic_entries(self, agent_id: str | None) -> list[dict[str, Any]]:
        with self._lock:
            entries = list(self._traffic)
        if agent_id:
            entries = [e for e in entries if e.get("agent") == agent_id]
        return entries

    def _observe(
        self,
        event: RuntimeEvent,
        decision: GuardDecision,
        request: dict[str, Any],
    ) -> None:
        action = _DECISION_TO_ACTION.get(decision.decision_type, "allow")
        ctx = event.context
        tool = getattr(event.payload, "tool_name", None) or event.event_type.value
        plugin_result = _safe_dict(request.get("plugin_result"))
        plugin_summary = _plugin_summary(plugin_result)
        matched = decision.metadata.get("matched_rule_ids") or (
            [decision.policy_id] if decision.policy_id else []
        )
        risk = 0.0
        now = time.time()

        entry = {
            "ts": now,
            "tool": tool,
            "agent": ctx.agent_id,
            "session": ctx.session_id,
            "action": action,
            "latency_ms": round(float(decision.metadata.get("latency_ms", 0.0)), 2),
            "risk": risk,
            "rules": list(matched),
            "reason": decision.reason,
            "plugin_summary": plugin_summary,
            "plugin_result": plugin_result,
        }

        event_dict = self._build_event_dict(event, now)
        decision_dict = self._build_decision_dict(decision, matched, risk, plugin_result)

        with self._lock:
            self._traffic.append(entry)
            self._audit.append({"event": event_dict, "decision": decision_dict})

    @staticmethod
    def _build_event_dict(event: RuntimeEvent, ts: float) -> dict[str, Any]:
        ctx = event.context
        return {
            "event_id": event.event_id,
            "ts_ms": int(ts * 1000),
            "event_type": event.event_type.value,
            "principal": {
                "agent_id": ctx.agent_id,
                "session_id": ctx.session_id,
                "user_id": ctx.user_id,
                "role": "default",
                "trust_level": 0,
            },
            "tool_call": {
                "tool_name": getattr(event.payload, "tool_name", None),
                "args": getattr(event.payload, "arguments", {}) or {},
                "target": {},
                "sink_type": "none",
                "label": {
                    "boundary": "internal",
                    "sensitivity": "low",
                    "integrity": "trusted",
                    "tags": getattr(event.payload, "capabilities", []) or [],
                },
            },
        }

    @staticmethod
    def _build_decision_dict(
        decision: GuardDecision,
        matched: list[str],
        risk: float,
        plugin_result: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        plugin_result = _safe_dict(plugin_result)
        return {
            "action": _DECISION_TO_ACTION.get(decision.decision_type, "allow"),
            "risk_score": risk,
            "matched_rules": list(matched),
            "obligations": [],
            "rule_version": decision.metadata.get("policy_version", "unknown"),
            "ttl_ms": 0,
            "reason": decision.reason,
            "policy_id": decision.policy_id,
            "plugin_result": plugin_result,
            "plugin_summary": _plugin_summary(plugin_result),
        }

    @classmethod
    def _build_approval_item(cls, ticket: dict[str, Any]) -> dict[str, Any]:
        event = cls._approval_event_view(ticket.get("event") or {})
        decision = cls._approval_decision_view(ticket.get("guard_decision") or {})
        return {
            "ticket_id": ticket.get("ticket_id"),
            "created_ms": ticket.get("created_ms"),
            "status": ticket.get("status"),
            "event": event,
            "decision": decision,
            "note": ticket.get("note") or "",
            "resolved_ms": ticket.get("resolved_ms"),
        }

    @staticmethod
    def _approval_event_view(event: dict[str, Any]) -> dict[str, Any]:
        context = dict(event.get("context") or {})
        payload = dict(event.get("payload") or {})
        return {
            "event_id": event.get("event_id"),
            "ts_ms": int(float(event.get("timestamp") or 0.0) * 1000),
            "event_type": event.get("event_type") or "tool_invoke",
            "principal": {
                "agent_id": context.get("agent_id"),
                "session_id": context.get("session_id"),
                "user_id": context.get("user_id"),
                "role": "default",
                "trust_level": 0,
            },
            "tool_call": {
                "tool_name": payload.get("tool_name"),
                "args": payload.get("arguments") or {},
                "target": {},
                "sink_type": "none",
                "label": {
                    "boundary": "internal",
                    "sensitivity": "low",
                    "integrity": "trusted",
                    "tags": payload.get("capabilities") or [],
                },
            },
        }

    @staticmethod
    def _approval_decision_view(decision: dict[str, Any]) -> dict[str, Any]:
        metadata = dict(decision.get("metadata") or {})
        matched = metadata.get("matched_rule_ids") or (
            [decision.get("policy_id")] if decision.get("policy_id") else []
        )
        plugin_name = str(metadata.get("plugin") or "").strip()
        if plugin_name:
            matched = [plugin_name, *matched]
        try:
            action_key = DecisionType(str(decision.get("decision_type") or "allow"))
        except ValueError:
            action_key = DecisionType.HUMAN_CHECK
        action = _DECISION_TO_ACTION.get(action_key, "human_check")
        return {
            "action": action,
            "risk_score": 0.0,
            "matched_rules": [str(item) for item in matched if str(item).strip()],
            "obligations": [],
            "rule_version": metadata.get("policy_version", "unknown"),
            "ttl_ms": 0,
            "reason": str(decision.get("reason") or ""),
        }


def _safe_dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _plugin_summary(plugin_result: dict[str, Any]) -> list[dict[str, Any]]:
    metadata = _safe_dict(plugin_result.get("metadata"))
    summary: list[dict[str, Any]] = []
    agentdog = _safe_dict(metadata.get("agentdog"))
    if agentdog:
        item = {
            "name": "agentdog",
            "label": str(agentdog.get("label") or agentdog.get("decision") or "unknown"),
            "prediction": agentdog.get("prediction"),
            "reason": str(agentdog.get("reason") or agentdog.get("error") or ""),
        }
        if agentdog.get("error"):
            item["error"] = str(agentdog.get("error"))
        summary.append(item)

    for name, value in metadata.items():
        if name == "agentdog":
            continue
        if isinstance(value, dict):
            label = value.get("decision") or value.get("label") or value.get("status") or "observed"
            reason = value.get("reason") or value.get("error") or ""
        else:
            label = "observed"
            reason = ""
        summary.append(
            {
                "name": str(name),
                "label": str(label),
                "reason": str(reason),
            }
        )
    return summary

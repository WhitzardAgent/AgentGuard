"""Process-wide console state bound to the shared RuntimeManager.

Provides the real, observable data the web console renders: a tool catalog with
editable labels, a console-managed rule store (DSL <-> PolicyRule), and live
traffic / audit / approval records populated from actual guard decisions.
"""
from __future__ import annotations

import threading
import time
import uuid
from collections import deque
from typing import Any

from shared.schemas.decisions import DecisionType, GuardDecision
from shared.schemas.events import RuntimeEvent
from shared.schemas.policy import PolicyRule
from backend.console.dsl import ParsedRule, parse_source, rule_to_console_dict
from backend.runtime.manager import RuntimeManager

_DECISION_TO_ACTION = {
    DecisionType.ALLOW: "allow",
    DecisionType.LOG_ONLY: "allow",
    DecisionType.DENY: "deny",
    DecisionType.REQUIRE_APPROVAL: "human_check",
    DecisionType.ASK_USER: "human_check",
    DecisionType.REQUIRE_REMOTE_REVIEW: "human_check",
    DecisionType.DEGRADE: "degrade",
    DecisionType.SANITIZE: "degrade",
}
_HELD = {
    DecisionType.REQUIRE_APPROVAL,
    DecisionType.ASK_USER,
    DecisionType.REQUIRE_REMOTE_REVIEW,
}

_DEFAULT_TOOLS = [
    ("agent-alpha", "shell.exec", "privileged", "high", "trusted", ["cmd", "cwd"]),
    ("agent-alpha", "email.send", "external", "moderate", "trusted", ["to", "subject", "body"]),
    ("agent-alpha", "file.read", "internal", "moderate", "trusted", ["path"]),
    ("agent-beta", "http.fetch", "external", "moderate", "untrusted", ["url"]),
    ("agent-beta", "db.query", "internal", "high", "trusted", ["sql"]),
    ("agent-beta", "vault.read", "privileged", "high", "trusted", ["key"]),
]


class ConsoleState:
    def __init__(self, manager: RuntimeManager) -> None:
        self.manager = manager
        self._lock = threading.Lock()
        self._start = time.time()

        # Baseline (non-editable) rules captured from the manager's policy store.
        self._base_rules: list[PolicyRule] = list(manager.policy.store.rules())
        self._console_rules: dict[str, dict[str, Any]] = {}

        self._tools: dict[tuple[str, str], dict[str, Any]] = {}
        for owner, name, boundary, sensitivity, integrity, params in _DEFAULT_TOOLS:
            self._tools[(owner, name)] = {
                "owner_agent_id": owner,
                "name": name,
                "labels": {
                    "boundary": boundary,
                    "sensitivity": sensitivity,
                    "integrity": integrity,
                    "tags": [],
                },
                "input_params": list(params),
            }

        self._traffic: deque[dict[str, Any]] = deque(maxlen=1000)
        self._audit: deque[dict[str, Any]] = deque(maxlen=1000)
        self._tickets: dict[str, dict[str, Any]] = {}

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
        with self._lock:
            items = list(self._tickets.values())
        if agent_id:
            items = [
                t for t in items
                if (t.get("event") or {}).get("principal", {}).get("agent_id") == agent_id
            ]
        return sorted(items, key=lambda t: t["created_ms"])

    def resolve_ticket(self, ticket_id: str, approved: bool, note: str = "") -> bool:
        with self._lock:
            return self._tickets.pop(ticket_id, None) is not None

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
        plugin_results: dict[str, Any],
    ) -> None:
        action = _DECISION_TO_ACTION.get(decision.decision_type, "allow")
        payload = event.payload or {}
        ctx = event.context
        tool = payload.get("tool_name") or event.event_type.value
        matched = decision.metadata.get("matched_rule_ids") or (
            [decision.policy_id] if decision.policy_id else []
        )
        risk = 0.0
        ad = (plugin_results or {}).get("agentdog") or {}
        if ad:
            risk = float((ad.get("policy_metadata") or {}).get("agentdog_risk_score") or 0.0)
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
        }

        event_dict = self._build_event_dict(event, now)
        decision_dict = self._build_decision_dict(decision, matched, risk)

        with self._lock:
            self._traffic.append(entry)
            self._audit.append({"event": event_dict, "decision": decision_dict})
            if decision.decision_type in _HELD:
                tid = f"ticket-{uuid.uuid4().hex[:12]}"
                self._tickets[tid] = {
                    "ticket_id": tid,
                    "created_ms": int(now * 1000),
                    "event": event_dict,
                    "decision": decision_dict,
                }

    @staticmethod
    def _build_event_dict(event: RuntimeEvent, ts: float) -> dict[str, Any]:
        payload = event.payload or {}
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
                "tool_name": payload.get("tool_name"),
                "args": payload.get("arguments") or {},
                "target": payload.get("target") or {},
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
    def _build_decision_dict(
        decision: GuardDecision, matched: list[str], risk: float
    ) -> dict[str, Any]:
        return {
            "action": _DECISION_TO_ACTION.get(decision.decision_type, "allow"),
            "risk_score": risk,
            "matched_rules": list(matched),
            "obligations": [],
            "rule_version": decision.metadata.get("policy_version", "unknown"),
            "ttl_ms": 0,
            "reason": decision.reason,
        }

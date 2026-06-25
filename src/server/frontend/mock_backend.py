from __future__ import annotations

from dataclasses import dataclass
from fnmatch import fnmatch
from http import HTTPStatus
import json
import re
from threading import Lock
from typing import Any


@dataclass(frozen=True)
class MockRoute:
    method: str
    pattern: re.Pattern[str]


class FrontendMockBackend:
    _AGENT_TOOLS_ROUTE = MockRoute("GET", re.compile(r"^/api/agents/(?P<agent_id>[^/]+)/tools$"))
    _AGENT_TOOL_LABELS_PATCH_ROUTE = MockRoute(
        "PATCH",
        re.compile(r"^/api/agents/(?P<agent_id>[^/]+)/tools/(?P<tool_name>[^/]+)/labels$"),
    )
    _AGENT_RULES_ROUTE = MockRoute("GET", re.compile(r"^/api/agents/(?P<agent_id>[^/]+)/rules$"))
    _AGENT_RULES_CREATE_ROUTE = MockRoute("POST", re.compile(r"^/api/agents/(?P<agent_id>[^/]+)/rules$"))
    _AGENT_RULES_GENERATE_ROUTE = MockRoute("POST", re.compile(r"^/api/agents/(?P<agent_id>[^/]+)/rules/generate$"))
    _AGENT_RULE_DELETE_ROUTE = MockRoute("DELETE", re.compile(r"^/api/agents/(?P<agent_id>[^/]+)/rules/(?P<rule_id>[^/]+)$"))

    def __init__(self) -> None:
        self._default_tools = self._build_default_tools()
        self._default_source = self._build_default_rule_source()
        self._lock = Lock()
        self.reset()

    def reset(self) -> None:
        with self._lock:
            self._tools = [tool.copy() for tool in self._default_tools]
            self._published_source = self._default_source
            self._published_rules = self._parse_rules_from_source(self._published_source)
            self._agent_rule_sources: dict[str, list[str]] = {}
            self._agent_rule_rules: dict[str, list[dict[str, Any]]] = {}

    def try_handle(self, handler: Any, *, method: str, path: str, query: str = "") -> bool:
        del query

        if method == "GET" and path == "/api/tools":
            self._send_json(handler, self._tools)
            return True

        if method == "GET" and path == "/api/rules":
            self._send_json(handler, self._get_all_rules())
            return True

        match = self._AGENT_TOOLS_ROUTE.pattern.match(path)
        if method == self._AGENT_TOOLS_ROUTE.method and match:
            agent_id = match.group("agent_id")
            self._send_json(handler, self._get_tools_for_agent(agent_id))
            return True

        match = self._AGENT_RULES_ROUTE.pattern.match(path)
        if method == self._AGENT_RULES_ROUTE.method and match:
            agent_id = match.group("agent_id")
            self._send_json(handler, self._get_rules_for_agent(agent_id))
            return True

        match = self._AGENT_TOOL_LABELS_PATCH_ROUTE.pattern.match(path)
        if method == self._AGENT_TOOL_LABELS_PATCH_ROUTE.method and match:
            payload = self._read_json_body(handler)
            if payload is None:
                self._send_json(handler, self._invalid_json_response(), status=HTTPStatus.BAD_REQUEST)
                return True
            response, status = self._patch_tool_labels(
                match.group("agent_id"),
                match.group("tool_name"),
                payload,
            )
            self._send_json(handler, response, status=status)
            return True

        match = self._AGENT_RULES_CREATE_ROUTE.pattern.match(path)
        if method == self._AGENT_RULES_CREATE_ROUTE.method and match:
            payload = self._read_json_body(handler)
            if payload is None:
                self._send_json(handler, self._invalid_json_response(), status=HTTPStatus.BAD_REQUEST)
                return True
            response, status = self._create_agent_rule(match.group("agent_id"), payload)
            self._send_json(handler, response, status=status)
            return True

        match = self._AGENT_RULES_GENERATE_ROUTE.pattern.match(path)
        if method == self._AGENT_RULES_GENERATE_ROUTE.method and match:
            payload = self._read_json_body(handler)
            if payload is None:
                self._send_json(handler, self._invalid_json_response(), status=HTTPStatus.BAD_REQUEST)
                return True
            response = {
                "ok": True,
                "agent_id": match.group("agent_id"),
                "requirement": str(payload.get("requirement", "")).strip(),
                "stop_reason": "ready_for_user_review",
                "attempt_count": 1,
                "remaining_rounds": max(0, int(payload.get("max_rounds", 4) or 4) - 1),
                "candidate": {
                    "summary": "mock generated rule",
                    "assumptions": [],
                    "warnings": [],
                    "rules": "",
                },
                "validation": {
                    "ok": True,
                    "errors": [],
                    "warnings": [],
                    "parsed_dsl_rules": [],
                    "normalized_rules": [],
                },
                "attempts": [],
                "user_feedback_history": [str(payload.get("user_feedback", "")).strip()] if str(payload.get("user_feedback", "")).strip() else [],
            }
            self._send_json(handler, response, status=HTTPStatus.OK)
            return True

        match = self._AGENT_RULE_DELETE_ROUTE.pattern.match(path)
        if method == self._AGENT_RULE_DELETE_ROUTE.method and match:
            response, status = self._delete_agent_rule(match.group("agent_id"), match.group("rule_id"))
            self._send_json(handler, response, status=status)
            return True

        if method == "POST" and path == "/api/rules/check":
            payload = self._read_json_body(handler)
            if payload is None:
                self._send_json(handler, self._invalid_json_response(), status=HTTPStatus.BAD_REQUEST)
                return True
            self._send_json(handler, self._check_rule_payload(payload))
            return True

        if method == "POST" and path == "/api/rules/reload":
            payload = self._read_json_body(handler)
            if payload is None:
                self._send_json(handler, self._invalid_json_response(), status=HTTPStatus.BAD_REQUEST)
                return True
            response, status = self._reload_rules(payload)
            self._send_json(handler, response, status=status)
            return True

        return False

    def _get_tools_for_agent(self, agent_id: str) -> list[dict[str, Any]]:
        return [
            tool.copy()
            for tool in self._tools
            if str(tool.get("owner_agent_id", "")).strip() == agent_id
        ]

    def _patch_tool_labels(
        self,
        agent_id: str,
        tool_name: str,
        payload: dict[str, Any],
    ) -> tuple[dict[str, Any], HTTPStatus]:
        normalized_agent_id = str(agent_id or "").strip()
        normalized_tool_name = str(tool_name or "").strip()
        labels = {
            "boundary": str(payload.get("boundary", "internal")).strip() or "internal",
            "sensitivity": str(payload.get("sensitivity", "low")).strip() or "low",
            "integrity": str(payload.get("integrity", "trusted")).strip() or "trusted",
            "tags": [str(tag).strip() for tag in (payload.get("tags") or []) if str(tag).strip()],
        }

        with self._lock:
            for index, tool in enumerate(self._tools):
                if (
                    str(tool.get("owner_agent_id", "")).strip() == normalized_agent_id
                    and str(tool.get("name", "")).strip() == normalized_tool_name
                ):
                    updated = tool.copy()
                    updated["labels"] = labels
                    self._tools[index] = updated
                    return ({"ok": True, "tool": updated.copy()}, HTTPStatus.OK)

        return ({
            "ok": False,
            "error": f"tool '{normalized_tool_name}' not found for agent '{normalized_agent_id}'",
        }, HTTPStatus.NOT_FOUND)

    def _get_published_rules(self) -> list[dict[str, Any]]:
        with self._lock:
            return [rule.copy() for rule in self._published_rules]

    def _get_all_rules(self) -> list[dict[str, Any]]:
        with self._lock:
            rules = [rule.copy() for rule in self._published_rules]
            for scoped_rules in self._agent_rule_rules.values():
                rules.extend(rule.copy() for rule in scoped_rules)
            return rules

    def _get_rules_for_agent(self, agent_id: str) -> list[dict[str, Any]]:
        tools = self._get_tools_for_agent(agent_id)
        tool_names = {str(tool.get("name", "")).strip() for tool in tools}
        published_rules = self._get_published_rules()
        matched_published_rules = [
            rule.copy()
            for rule in published_rules
            if self._rule_matches_agent(rule, tool_names)
        ]
        with self._lock:
            scoped_rules = [rule.copy() for rule in self._agent_rule_rules.get(agent_id, [])]
        return matched_published_rules + scoped_rules

    @staticmethod
    def _rule_matches_agent(rule: dict[str, Any], tool_names: set[str]) -> bool:
        tool_pattern = str(rule.get("tool_pattern", "*")).strip() or "*"
        if tool_pattern == "*":
            return bool(tool_names)
        return any(fnmatch(tool_name, tool_pattern) for tool_name in tool_names)

    def _check_rule_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        source = str(payload.get("source", "")).strip()
        diagnostics = self._validate_source(source)
        return {
            "ok": not diagnostics["errors"],
            "rule_count": diagnostics["rule_count"],
            "errors": diagnostics["errors"],
            "warnings": diagnostics["warnings"],
            "hints": diagnostics["hints"],
            "source_file": "",
        }

    def _reload_rules(self, payload: dict[str, Any]) -> tuple[dict[str, Any], HTTPStatus]:
        source = str(payload.get("source", "")).strip()
        diagnostics = self._validate_source(source)
        if diagnostics["errors"]:
            return ({
                "ok": False,
                "error": diagnostics["errors"][0]["message"],
                "errors": diagnostics["errors"],
                "warnings": diagnostics["warnings"],
                "hints": diagnostics["hints"],
                "rule_count": diagnostics["rule_count"],
            }, HTTPStatus.BAD_REQUEST)

        rules = self._parse_rules_from_source(source)
        with self._lock:
            self._published_source = source
            self._published_rules = rules
        return ({"ok": True, "loaded": len(rules)}, HTTPStatus.OK)

    def _create_agent_rule(self, agent_id: str, payload: dict[str, Any]) -> tuple[dict[str, Any], HTTPStatus]:
        source = str(payload.get("source", "")).strip()
        diagnostics = self._validate_source(source)
        if diagnostics["errors"]:
            return ({
                "ok": False,
                "error": diagnostics["errors"][0]["message"],
                "errors": diagnostics["errors"],
            }, HTTPStatus.UNPROCESSABLE_ENTITY)

        blocks = self._split_rule_blocks(source)
        if len(blocks) != 1:
            return ({
                "ok": False,
                "error": "source must contain exactly one rule",
            }, HTTPStatus.UNPROCESSABLE_ENTITY)

        rule_id = self._extract_rule_name(blocks[0])
        if not rule_id:
            return ({
                "ok": False,
                "error": "rule_id is required",
            }, HTTPStatus.UNPROCESSABLE_ENTITY)

        with self._lock:
            existing_ids = {
                str(rule.get("rule_id", "")).strip()
                for rule in self._published_rules
            }
            for scoped_rules in self._agent_rule_rules.values():
                existing_ids.update(str(rule.get("rule_id", "")).strip() for rule in scoped_rules)
            if rule_id in existing_ids:
                return ({
                    "ok": False,
                    "error": f"rule_id '{rule_id}' already exists",
                }, HTTPStatus.CONFLICT)

            self._agent_rule_sources.setdefault(agent_id, []).append(blocks[0])
            rules = self._parse_rules_from_source("\n\n".join(self._agent_rule_sources[agent_id]))
            for rule in rules:
                rule["pack_id"] = f"agent::{agent_id}"
                rule["user_managed"] = True
            self._agent_rule_rules[agent_id] = rules

        return ({
            "ok": True,
            "agent_id": agent_id,
            "pack_id": f"agent::{agent_id}",
            "rule_id": rule_id,
            "created": True,
        }, HTTPStatus.OK)

    def _delete_agent_rule(self, agent_id: str, rule_id: str) -> tuple[dict[str, Any], HTTPStatus]:
        normalized_rule_id = str(rule_id or "").strip()
        with self._lock:
            rules = self._agent_rule_rules.get(agent_id, [])
            if not any(str(rule.get("rule_id", "")).strip() == normalized_rule_id for rule in rules):
                return ({
                    "ok": False,
                    "error": f"rule '{normalized_rule_id}' not found for agent '{agent_id}'",
                }, HTTPStatus.NOT_FOUND)

            remaining_sources = [
                block for block in self._agent_rule_sources.get(agent_id, [])
                if self._extract_rule_name(block) != normalized_rule_id
            ]
            self._agent_rule_sources[agent_id] = remaining_sources
            rebuilt_rules = self._parse_rules_from_source("\n\n".join(remaining_sources)) if remaining_sources else []
            for rule in rebuilt_rules:
                rule["pack_id"] = f"agent::{agent_id}"
                rule["user_managed"] = True
            self._agent_rule_rules[agent_id] = rebuilt_rules

        return ({
            "ok": True,
            "agent_id": agent_id,
            "pack_id": f"agent::{agent_id}",
            "rule_id": normalized_rule_id,
        }, HTTPStatus.OK)

    @staticmethod
    def _read_json_body(handler: Any) -> dict[str, Any] | None:
        raw = handler._read_request_body()
        if raw is None:
            return {}
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return None
        return payload if isinstance(payload, dict) else None

    @staticmethod
    def _send_json(handler: Any, payload: Any, *, status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        handler.send_response(status)
        handler.send_header("Content-Type", "application/json; charset=utf-8")
        handler.send_header("Content-Length", str(len(body)))
        handler.end_headers()
        handler.wfile.write(body)

    @staticmethod
    def _invalid_json_response() -> dict[str, Any]:
        return {
            "ok": False,
            "error": "Invalid JSON payload.",
            "errors": [{"message": "Invalid JSON payload."}],
            "warnings": [],
            "hints": [],
            "rule_count": 0,
            "source_file": "",
        }

    @staticmethod
    def _validate_source(source: str) -> dict[str, Any]:
        if not source:
            return {
                "rule_count": 0,
                "errors": [{"message": "Rule source is required."}],
                "warnings": [],
                "hints": [],
            }

        blocks = FrontendMockBackend._split_rule_blocks(source)
        if not blocks:
            return {
                "rule_count": 0,
                "errors": [{"message": "At least one RULE block is required."}],
                "warnings": [],
                "hints": [],
            }

        errors: list[dict[str, str]] = []
        warnings: list[dict[str, str]] = []
        hints: list[dict[str, str]] = []

        for index, block in enumerate(blocks, start=1):
            normalized = FrontendMockBackend._normalize_rule_header(block)
            lines = [line.strip() for line in normalized.splitlines() if line.strip()]
            missing: list[str] = []
            missing_path: list[str] = []
            for prefix in ("RULE:", "CONDITION:", "POLICY:"):
                if not any(line.startswith(prefix) for line in lines):
                    missing.append(prefix.rstrip(":"))
            for prefix in ("TRACE:", "ON"):
                if not any(line.startswith(prefix) for line in lines):
                    missing_path.append(prefix.rstrip(":"))
            if missing:
                errors.append({
                    "message": f"Rule block {index} is missing required line(s): {', '.join(missing)}.",
                })
                continue
            if len(missing_path) == 2:
                errors.append({
                    "message": f"Rule block {index} is missing required line(s): ON or TRACE.",
                })
                continue

            tool_pattern = FrontendMockBackend._extract_tool_pattern(normalized)
            if tool_pattern == "*":
                warnings.append({
                    "message": f"Rule block {index} applies to all tools because no specific tool pattern was found.",
                })
            hints.append({
                "message": f"Mock validator checked rule block {index}.",
            })

        return {
            "rule_count": len(blocks),
            "errors": errors,
            "warnings": warnings,
            "hints": hints,
        }

    @staticmethod
    def _parse_rules_from_source(source: str) -> list[dict[str, Any]]:
        rules: list[dict[str, Any]] = []
        for block in FrontendMockBackend._split_rule_blocks(source):
            normalized = FrontendMockBackend._normalize_rule_header(block).strip()
            name = FrontendMockBackend._extract_named_line(normalized, "RULE")
            action_line = FrontendMockBackend._extract_named_line(normalized, "POLICY")
            action = FrontendMockBackend._extract_action(action_line)
            severity = FrontendMockBackend._extract_optional_line(normalized, "Severity")
            category = FrontendMockBackend._extract_optional_line(normalized, "Category")
            reason = FrontendMockBackend._strip_quoted_value(
                FrontendMockBackend._extract_optional_line(normalized, "Reason")
            )
            tool_pattern = FrontendMockBackend._extract_tool_pattern(normalized)
            rules.append({
                "id": name,
                "name": name,
                "status": "published",
                "rule_id": name,
                "tool_pattern": tool_pattern,
                "action": action,
                "version": "mock-v1",
                "severity": severity,
                "category": category,
                "reason": reason,
                "description": "",
                "pack_id": "__default__",
                "user_managed": source != FrontendMockBackend._build_default_rule_source(),
                "source": normalized,
            })
        return rules

    @staticmethod
    def _extract_rule_name(source: str) -> str:
        matched = re.search(r"^RULE(?::\s*|\s+)([A-Za-z_][A-Za-z0-9_]*)$", str(source or "").strip(), re.MULTILINE)
        return str(matched.group(1) if matched else "").strip()

    @staticmethod
    def _split_rule_blocks(source: str) -> list[str]:
        blocks: list[str] = []
        current: list[str] = []
        for raw_line in source.splitlines():
            line = raw_line.rstrip()
            if line.strip().startswith("RULE"):
                if current:
                    blocks.append("\n".join(current).strip())
                    current = []
            if line.strip() or current:
                current.append(line)
        if current:
            blocks.append("\n".join(current).strip())
        return [block for block in blocks if block]

    @staticmethod
    def _normalize_rule_header(block: str) -> str:
        return re.sub(r"^RULE\s+(?!:)", "RULE: ", block, count=1, flags=re.MULTILINE)

    @staticmethod
    def _extract_named_line(block: str, label: str) -> str:
        match = re.search(rf"^{re.escape(label)}:\s*(.+)$", block, flags=re.MULTILINE)
        return match.group(1).strip() if match else ""

    @staticmethod
    def _extract_optional_line(block: str, label: str) -> str:
        match = re.search(rf"^{re.escape(label)}:\s*(.+)$", block, flags=re.MULTILINE)
        return match.group(1).strip() if match else ""

    @staticmethod
    def _strip_quoted_value(value: str) -> str:
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            return value[1:-1]
        return value

    @staticmethod
    def _extract_action(policy_line: str) -> str:
        normalized = policy_line.strip().upper()
        if normalized.startswith("DEGRADE"):
            return "DEGRADE"
        if normalized.startswith("HUMAN_CHECK"):
            return "HUMAN_CHECK"
        if normalized.startswith("ALLOW"):
            return "ALLOW"
        if normalized.startswith("DENY"):
            return "DENY"
        return normalized or "DENY"

    @staticmethod
    def _extract_tool_pattern(block: str) -> str:
        on_clause = FrontendMockBackend._extract_optional_line(block, "ON")
        if on_clause:
            match = re.search(r"\(([^)]+)\)", on_clause)
            if match:
                return match.group(1).strip()
        condition = FrontendMockBackend._extract_named_line(block, "CONDITION")
        match = re.search(r'A\.name\s*==\s*"([^"]+)"', condition)
        if match:
            return match.group(1).strip()
        return "*"

    @staticmethod
    def _build_default_tools() -> list[dict[str, Any]]:
        return [
            {
                "name": "shell.exec",
                "owner_agent_id": "agent-alpha",
                "description": "Execute bounded shell commands for local automation.",
                "input_params": ["cmd", "cwd"],
                "labels": {"boundary": "privileged", "sensitivity": "high", "integrity": "trusted"},
            },
            {
                "name": "email.send",
                "owner_agent_id": "agent-alpha",
                "description": "Send outbound email to customers.",
                "input_params": ["to", "subject", "body"],
                "labels": {"boundary": "external", "sensitivity": "moderate", "integrity": "trusted"},
            },
            {
                "name": "docs.search",
                "owner_agent_id": "agent-alpha",
                "description": "Search internal knowledge base documents.",
                "input_params": ["query", "limit"],
                "labels": {"boundary": "internal", "sensitivity": "low", "integrity": "trusted"},
            },
            {
                "name": "http.get",
                "owner_agent_id": "agent-beta",
                "description": "Fetch data from external HTTP endpoints.",
                "input_params": ["url", "timeout"],
                "labels": {"boundary": "external", "sensitivity": "low", "integrity": "unfiltered"},
            },
            {
                "name": "db.query",
                "owner_agent_id": "agent-beta",
                "description": "Run read-only analytics queries.",
                "input_params": ["sql", "limit"],
                "labels": {"boundary": "internal", "sensitivity": "high", "integrity": "trusted"},
            },
            {
                "name": "ticket.create",
                "owner_agent_id": "agent-beta",
                "description": "Open follow-up tickets in the tracker.",
                "input_params": ["title", "description", "priority"],
                "labels": {"boundary": "internal", "sensitivity": "moderate", "integrity": "trusted"},
            },
        ]

    @staticmethod
    def _build_default_rule_source() -> str:
        return "\n\n".join([
            "\n".join([
                "RULE: alpha_shell_review",
                "TRACE: A -> B",
                'CONDITION: A.name == "shell.exec"',
                "POLICY: HUMAN_CHECK",
                "Severity: high",
                "Category: execution",
                'Reason: "Review privileged shell access"',
            ]),
            "\n".join([
                "RULE: beta_external_fetch_trace",
                "TRACE: A -> B",
                "ON: tool_call(http.get)",
                'CONDITION: A.name == "http.get"',
                "POLICY: ALLOW",
                "Severity: info",
                "Category: network",
                'Reason: "Track external fetches in preview mode"',
            ]),
        ])


MOCK_BACKEND = FrontendMockBackend()

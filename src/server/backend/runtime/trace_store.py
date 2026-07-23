"""Persistent MySQL storage for runtime trace events."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from backend.audit.base import AuditTraceEntry
from backend.database import MySQLDatabase, get_database
from shared.schemas.decisions import DecisionType, GuardDecision
from shared.schemas.events import RuntimeEvent
from shared.utils.json import safe_dumps, safe_loads
from shared.utils.time import now_ts


class TraceEventStore:
    def __init__(self, db: MySQLDatabase | None = None) -> None:
        self.db = db or get_database()

    def ensure_schema(self) -> None:
        for statement in _SCHEMA:
            self.db.execute(statement)

    def upsert_trace_entry(
        self,
        session_id: str,
        record: AuditTraceEntry | dict[str, Any],
        *,
        agent_id: str | None = None,
        user_id: str | None = None,
    ) -> str:
        entry = record if isinstance(record, AuditTraceEntry) else AuditTraceEntry.from_dict(record)
        if entry.event is None:
            return "unchanged"
        effective_session_id = str(session_id or entry.session_id or "unknown")
        effective_agent_id = _optional_text(agent_id) or _optional_text(entry.agent_id)
        effective_user_id = _optional_text(user_id) or _optional_text(entry.user_id)
        entry = entry.merged_with(
            AuditTraceEntry(
                session_id=effective_session_id,
                agent_id=effective_agent_id,
                user_id=effective_user_id,
                event=entry.event,
            )
        )
        event_id = entry.event_id
        if not event_id:
            return "unchanged"

        existing = self._get_by_event_id(effective_session_id, event_id)
        status = "appended"
        if existing is not None:
            merged = existing.merged_with(entry)
            if merged == existing:
                return "unchanged"
            entry = merged
            status = "updated"

        self.db.execute(_UPSERT_SQL, _entry_params(entry, self.db))
        return status

    def list_trace_entries(
        self,
        session_id: str | None = None,
        *,
        agent_id: str | None = None,
        user_id: str | None = None,
        limit: int = 1000,
        only_with_decision: bool = False,
        descending: bool = False,
    ) -> list[AuditTraceEntry]:
        filters: list[str] = []
        params: list[Any] = []
        if session_id:
            filters.append("session_id = %s")
            params.append(str(session_id))
        if agent_id:
            filters.append("(agent_id = %s OR raw_agent_id = %s)")
            params.extend([str(agent_id), str(agent_id)])
        if user_id:
            user_filter, user_params = _user_filter(str(user_id))
            filters.append(user_filter)
            params.extend(user_params)
        if only_with_decision:
            filters.append("decision_type IS NOT NULL")
        where = f"WHERE {' AND '.join(filters)}" if filters else ""
        order = "DESC" if descending else "ASC"
        params.append(_clamp_limit(limit))
        rows = self.db.fetchall(
            f"""
            SELECT *
            FROM runtime_trace_events
            {where}
            ORDER BY event_ts_ms {order}, id {order}
            LIMIT %s
            """,
            tuple(params),
        )
        entries = [_entry_from_row(row) for row in rows]
        return [entry for entry in entries if entry is not None]

    def agent_snapshot_max_id(
        self,
        agent_id: str,
        *,
        start_at: datetime | None = None,
        end_at: datetime | None = None,
    ) -> int:
        """Return a stable canonical-agent snapshot boundary for offline audit."""
        filters = ["agent_id = %s"]
        params: list[Any] = [str(agent_id)]
        _append_time_filters(filters, params, start_at=start_at, end_at=end_at)
        row = self.db.fetchone(
            f"SELECT MAX(id) AS max_id FROM runtime_trace_events WHERE {' AND '.join(filters)}",
            tuple(params),
        ) or {}
        return int(row.get("max_id") or 0)

    def list_agent_snapshot_page(
        self,
        agent_id: str,
        *,
        snapshot_max_id: int,
        after_id: int = 0,
        start_at: datetime | None = None,
        end_at: datetime | None = None,
        limit: int = 500,
    ) -> tuple[list[AuditTraceEntry], int | None]:
        """Read one canonical-agent audit page without trusting raw_agent_id."""
        filters = ["agent_id = %s", "id > %s", "id <= %s"]
        params: list[Any] = [str(agent_id), int(after_id), int(snapshot_max_id)]
        _append_time_filters(filters, params, start_at=start_at, end_at=end_at)
        page_limit = max(1, min(int(limit or 500), 2000))
        params.append(page_limit)
        rows = self.db.fetchall(
            f"""
            SELECT *
            FROM runtime_trace_events
            WHERE {' AND '.join(filters)}
            ORDER BY id ASC
            LIMIT %s
            """,
            tuple(params),
        )
        entries = [_entry_from_row(row, prefer_canonical=True) for row in rows]
        valid = [entry for entry in entries if entry is not None]
        next_cursor = int(rows[-1]["id"]) if len(rows) == page_limit else None
        return valid, next_cursor

    def recent_traffic(
        self,
        agent_id: str | None = None,
        *,
        n: int = 30,
        action: str | None = None,
        tool: str | None = None,
    ) -> list[dict[str, Any]]:
        fetch_limit = 1000 if action or tool else max(1, min(n, 1000))
        entries = self.list_trace_entries(
            agent_id=agent_id,
            limit=fetch_limit,
            only_with_decision=True,
            descending=True,
        )
        traffic = [_traffic_entry(entry) for entry in entries]
        traffic = [item for item in traffic if item is not None]
        if action:
            traffic = [item for item in traffic if item["action"] == action]
        if tool:
            traffic = [item for item in traffic if tool in (item.get("tool") or "")]
        return traffic[: max(1, min(n, 1000))]

    def recent_audit(
        self,
        agent_id: str | None = None,
        *,
        n: int = 20,
    ) -> list[dict[str, Any]]:
        entries = self.list_trace_entries(
            agent_id=agent_id,
            limit=max(1, min(n, 1000)),
            only_with_decision=True,
            descending=True,
        )
        audit = [_audit_entry(entry) for entry in entries]
        return [item for item in audit if item is not None][: max(1, min(n, 1000))]

    def stats(self, agent_id: str | None = None) -> dict[str, Any]:
        filters = ["decision_type IS NOT NULL"]
        params: list[Any] = []
        if agent_id:
            filters.append("(agent_id = %s OR raw_agent_id = %s)")
            params.extend([str(agent_id), str(agent_id)])
        where = f"WHERE {' AND '.join(filters)}"
        row = self.db.fetchone(
            f"""
            SELECT
              COUNT(*) AS total_requests,
              SUM(CASE WHEN decision_type = 'deny' THEN 1 ELSE 0 END) AS deny_count
            FROM runtime_trace_events
            {where}
            """,
            tuple(params),
        ) or {}
        total = int(row.get("total_requests") or 0)
        deny = int(row.get("deny_count") or 0)
        return {
            "total_requests": total,
            "deny_count": deny,
            "deny_rate": round(deny / total, 4) if total else 0.0,
        }

    def _get_by_event_id(self, session_id: str, event_id: str) -> AuditTraceEntry | None:
        row = self.db.fetchone(
            """
            SELECT *
            FROM runtime_trace_events
            WHERE session_id = %s AND event_id = %s
            """,
            (session_id, event_id),
        )
        return _entry_from_row(row) if row else None


def ensure_trace_schema() -> None:
    TraceEventStore().ensure_schema()


def _entry_params(entry: AuditTraceEntry, db: MySQLDatabase) -> tuple[Any, ...]:
    event = entry.event
    if event is None:
        raise ValueError("trace entry requires event")
    event_dict = event.to_dict()
    payload = event.payload.to_dict()
    metadata = dict(event.metadata or {})
    decision_dict = entry.decision.to_dict() if entry.decision is not None else None
    raw_user_id = _optional_text(entry.user_id or event.context.user_id)
    raw_agent_id = _optional_text(entry.agent_id or event.context.agent_id)
    session_id = str(entry.session_id or event.context.session_id or "unknown")
    return (
        _resolve_user_fk(db, raw_user_id),
        _resolve_agent_fk(db, raw_agent_id),
        _resolve_session_fk(db, session_id),
        raw_user_id,
        raw_agent_id,
        session_id,
        event.event_id,
        event.event_type.value,
        _event_ts_ms(entry),
        _tool_name(event),
        entry.decision.decision_type.value if entry.decision is not None else None,
        entry.decision.policy_id if entry.decision is not None else None,
        _optional_text(entry.route),
        _optional_text(entry.reason),
        _json_or_none(event_dict),
        _json_or_none(payload),
        _json_or_none(metadata),
        _json_or_none(list(event.risk_signals or [])),
        _json_or_none(decision_dict),
        _json_or_none(entry.plugin_result),
        _json_or_none(entry.plugin_input),
    )


def _entry_from_row(
    row: dict[str, Any] | None,
    *,
    prefer_canonical: bool = False,
) -> AuditTraceEntry | None:
    if not row:
        return None
    event_dict = _json_value(row.get("event_json"), {})
    decision_dict = _json_value(row.get("decision_json"), None)
    data: dict[str, Any] = {
        "session_id": row.get("session_id") or "unknown",
        "agent_id": (
            row.get("agent_id") or row.get("raw_agent_id")
            if prefer_canonical
            else row.get("raw_agent_id") or row.get("agent_id")
        ),
        "user_id": (
            row.get("user_id") or row.get("raw_user_id")
            if prefer_canonical
            else row.get("raw_user_id") or row.get("user_id")
        ),
        "reason": row.get("reason"),
        "event": event_dict,
        "decision": decision_dict,
        "plugin_result": _json_value(row.get("plugin_result_json"), {}) or {},
        "plugin_input": _json_value(row.get("plugin_input_json"), {}) or {},
        "route": row.get("route"),
        "timestamp": _timestamp_from_row(row),
    }
    entry = AuditTraceEntry.from_dict(data)
    if entry.event is None:
        return None
    return entry


def _traffic_entry(entry: AuditTraceEntry) -> dict[str, Any] | None:
    if entry.event is None or entry.decision is None:
        return None
    event = entry.event
    decision = entry.decision
    action = _decision_action(decision)
    matched = _matched_rules(decision)
    plugin_result = dict(entry.plugin_result or {})
    return {
        "ts": float(entry.timestamp or event.timestamp or now_ts()),
        "tool": _tool_name(event) or event.event_type.value,
        "agent": event.context.agent_id or entry.agent_id,
        "session": event.context.session_id or entry.session_id,
        "action": action,
        "latency_ms": round(float((decision.metadata or {}).get("latency_ms", 0.0)), 2),
        "risk": 0.0,
        "rules": matched,
        "reason": decision.reason,
        "plugin_summary": _plugin_summary(plugin_result),
        "plugin_result": plugin_result,
    }


def _audit_entry(entry: AuditTraceEntry) -> dict[str, Any] | None:
    if entry.event is None or entry.decision is None:
        return None
    return {
        "event": _event_view(entry.event, entry),
        "decision": _decision_view(entry.decision, entry.plugin_result),
        "runtime_state": _runtime_state_view(entry.event),
    }


def _event_view(event: RuntimeEvent, entry: AuditTraceEntry) -> dict[str, Any]:
    ctx = event.context
    payload = event.payload.to_dict()
    metadata = dict(event.metadata or {})
    return {
        "event_id": event.event_id,
        "ts_ms": int(float(event.timestamp or entry.timestamp or 0.0) * 1000),
        "event_type": event.event_type.value,
        "principal": {
            "agent_id": ctx.agent_id or entry.agent_id,
            "session_id": ctx.session_id or entry.session_id,
            "user_id": ctx.user_id or entry.user_id,
            "role": "default",
            "trust_level": 0,
        },
        "tool_call": {
            "tool_name": payload.get("tool_name"),
            "args": payload.get("arguments") or {},
            "result": payload.get("result"),
            "source": metadata.get("toolSource") or metadata.get("sourceFramework"),
            "mcp": _mcp_metadata(metadata),
            "target": {},
            "sink_type": "none",
            "label": {
                "boundary": metadata.get("tool_boundary") or "internal",
                "sensitivity": metadata.get("tool_sensitivity") or "low",
                "integrity": metadata.get("tool_integrity") or "trusted",
                "tags": payload.get("capabilities") or [],
            },
        },
    }


def _runtime_state_view(event: RuntimeEvent) -> dict[str, Any]:
    payload = event.payload.to_dict()
    metadata = dict(event.metadata or {})
    return {
        "event_type": event.event_type.value,
        "tool_name": payload.get("tool_name"),
        "arguments": payload.get("arguments") or {},
        "result": payload.get("result"),
        "source": metadata.get("toolSource") or metadata.get("sourceFramework"),
        "mcp": _mcp_metadata(metadata),
        "payload": payload,
        "metadata": metadata,
    }


def _decision_view(
    decision: GuardDecision,
    plugin_result: dict[str, Any] | None = None,
) -> dict[str, Any]:
    plugin_result = dict(plugin_result or {})
    metadata = dict(decision.metadata or {})
    return {
        "action": _decision_action(decision),
        "decision_type": decision.decision_type.value,
        "risk_score": 0.0,
        "matched_rules": _matched_rules(decision),
        "obligations": [],
        "rule_version": metadata.get("policy_version", "unknown"),
        "ttl_ms": 0,
        "reason": decision.reason,
        "processed_content": decision.processed_content,
        "policy_id": decision.policy_id,
        "plugin_result": plugin_result,
        "plugin_summary": _plugin_summary(plugin_result),
        "plugin_outcomes": list(metadata.get("plugin_outcomes") or []),
    }


def _decision_action(decision: GuardDecision) -> str:
    return _DECISION_TO_ACTION.get(decision.decision_type, "allow")


def _matched_rules(decision: GuardDecision) -> list[str]:
    metadata = dict(decision.metadata or {})
    matched = metadata.get("matched_rule_ids") or ([decision.policy_id] if decision.policy_id else [])
    return [str(item) for item in matched if str(item).strip()]


def _plugin_summary(plugin_result: dict[str, Any]) -> list[dict[str, Any]]:
    metadata = dict(plugin_result.get("metadata") or {}) if isinstance(plugin_result, dict) else {}
    summary: list[dict[str, Any]] = []
    for name, value in metadata.items():
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


def _mcp_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    return {
        key: metadata.get(key)
        for key in (
            "mcp_unique_id",
            "mcp_name",
            "mcp_tool_name",
            "mcp_transport",
            "mcp_remote",
            "mcp_match_confidence",
        )
        if metadata.get(key) not in (None, "")
    }


def _tool_name(event: RuntimeEvent) -> str | None:
    payload = event.payload.to_dict()
    value = payload.get("tool_name")
    text = str(value).strip() if value is not None else ""
    return text or None


def _event_ts_ms(entry: AuditTraceEntry) -> int:
    if entry.event is not None:
        return int(float(entry.event.timestamp or now_ts()) * 1000)
    return int(float(entry.timestamp or now_ts()) * 1000)


def _timestamp_from_row(row: dict[str, Any]) -> float | None:
    ts_ms = row.get("event_ts_ms")
    if isinstance(ts_ms, (int, float)):
        return float(ts_ms) / 1000.0
    received_at = row.get("received_at")
    if isinstance(received_at, datetime):
        return received_at.replace(tzinfo=timezone.utc).timestamp()
    return None


def _json_or_none(value: Any) -> str | None:
    if value is None:
        return None
    return safe_dumps(value)


def _json_value(value: Any, fallback: Any) -> Any:
    if value is None:
        return fallback
    if isinstance(value, (dict, list)):
        return value
    return safe_loads(value, fallback=fallback)


def _resolve_user_fk(db: MySQLDatabase, raw_user_id: str | None) -> int | None:
    if not raw_user_id or not raw_user_id.isdigit():
        return None
    user_id = int(raw_user_id)
    try:
        row = db.fetchone("SELECT id FROM users WHERE id = %s", (user_id,))
    except Exception:
        return None
    return int(row["id"]) if row else None


def _resolve_agent_fk(db: MySQLDatabase, raw_agent_id: str | None) -> str | None:
    if not raw_agent_id:
        return None
    try:
        row = db.fetchone("SELECT agent_id FROM agents WHERE agent_id = %s", (raw_agent_id,))
    except Exception:
        return None
    return str(row["agent_id"]) if row else None


def _resolve_session_fk(db: MySQLDatabase, session_id: str | None) -> str | None:
    if not session_id:
        return None
    try:
        row = db.fetchone(
            "SELECT session_id FROM runtime_sessions WHERE session_id = %s",
            (session_id,),
        )
    except Exception:
        return None
    return str(row["session_id"]) if row else None


def _user_filter(raw_user_id: str) -> tuple[str, list[Any]]:
    if raw_user_id.isdigit():
        return "(user_id = %s OR raw_user_id = %s)", [int(raw_user_id), raw_user_id]
    return "raw_user_id = %s", [raw_user_id]


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _append_time_filters(
    filters: list[str],
    params: list[Any],
    *,
    start_at: datetime | None,
    end_at: datetime | None,
) -> None:
    if start_at is not None:
        filters.append("event_ts_ms >= %s")
        params.append(int(start_at.timestamp() * 1000))
    if end_at is not None:
        filters.append("event_ts_ms <= %s")
        params.append(int(end_at.timestamp() * 1000))


def _clamp_limit(limit: int) -> int:
    return max(1, min(int(limit or 1000), 1000))


_DECISION_TO_ACTION = {
    DecisionType.ALLOW: "allow",
    DecisionType.LOG_ONLY: "allow",
    DecisionType.MODIFY_LLM_INPUT: "allow",
    DecisionType.MODIFY_LLM_OUTPUT: "allow",
    DecisionType.MODIFY_TOOL_INVOKE: "allow",
    DecisionType.MODIFY_TOOL_RESULT: "allow",
    DecisionType.DENY: "deny",
    DecisionType.REQUIRE_APPROVAL: "human_check",
    DecisionType.HUMAN_CHECK: "human_check",
    DecisionType.REQUIRE_REMOTE_REVIEW: "human_check",
    DecisionType.DEGRADE: "degrade",
    DecisionType.SANITIZE: "degrade",
}


_SCHEMA = [
    """
    CREATE TABLE IF NOT EXISTS runtime_trace_events (
      id BIGINT AUTO_INCREMENT PRIMARY KEY,
      user_id INT NULL,
      agent_id VARCHAR(255) NULL,
      runtime_session_id VARCHAR(255) NULL,
      raw_user_id VARCHAR(255) NULL,
      raw_agent_id VARCHAR(255) NULL,
      session_id VARCHAR(255) NOT NULL,
      event_id VARCHAR(255) NOT NULL,
      event_type VARCHAR(64) NULL,
      event_ts_ms BIGINT NULL,
      received_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
      tool_name VARCHAR(255) NULL,
      decision_type VARCHAR(64) NULL,
      policy_id VARCHAR(255) NULL,
      route VARCHAR(255) NULL,
      reason TEXT NULL,
      event_json JSON NULL,
      payload_json JSON NULL,
      metadata_json JSON NULL,
      risk_signals_json JSON NULL,
      decision_json JSON NULL,
      plugin_result_json JSON NULL,
      plugin_input_json JSON NULL,
      UNIQUE KEY uq_runtime_trace_session_event (session_id, event_id),
      INDEX idx_runtime_trace_agent_ts (agent_id, event_ts_ms),
      INDEX idx_runtime_trace_raw_agent_ts (raw_agent_id, event_ts_ms),
      INDEX idx_runtime_trace_session_ts (session_id, event_ts_ms),
      INDEX idx_runtime_trace_runtime_session_id (runtime_session_id),
      INDEX idx_runtime_trace_user_ts (user_id, event_ts_ms),
      INDEX idx_runtime_trace_event_type (event_type),
      INDEX idx_runtime_trace_decision_type (decision_type),
      INDEX idx_runtime_trace_tool_name (tool_name),
      CONSTRAINT fk_runtime_trace_user
        FOREIGN KEY (user_id) REFERENCES users(id)
        ON DELETE SET NULL,
      CONSTRAINT fk_runtime_trace_agent
        FOREIGN KEY (agent_id) REFERENCES agents(agent_id)
        ON DELETE SET NULL,
      CONSTRAINT fk_runtime_trace_session
        FOREIGN KEY (runtime_session_id) REFERENCES runtime_sessions(session_id)
        ON DELETE SET NULL
    )
    """,
]


_UPSERT_SQL = """
    INSERT INTO runtime_trace_events (
      user_id, agent_id, runtime_session_id, raw_user_id, raw_agent_id,
      session_id, event_id, event_type, event_ts_ms, tool_name,
      decision_type, policy_id, route, reason,
      event_json, payload_json, metadata_json, risk_signals_json,
      decision_json, plugin_result_json, plugin_input_json
    )
    VALUES (
      %s, %s, %s, %s, %s,
      %s, %s, %s, %s, %s,
      %s, %s, %s, %s,
      %s, %s, %s, %s,
      %s, %s, %s
    )
    ON DUPLICATE KEY UPDATE
      user_id = COALESCE(VALUES(user_id), user_id),
      agent_id = COALESCE(VALUES(agent_id), agent_id),
      runtime_session_id = COALESCE(VALUES(runtime_session_id), runtime_session_id),
      raw_user_id = COALESCE(VALUES(raw_user_id), raw_user_id),
      raw_agent_id = COALESCE(VALUES(raw_agent_id), raw_agent_id),
      event_type = COALESCE(VALUES(event_type), event_type),
      event_ts_ms = COALESCE(VALUES(event_ts_ms), event_ts_ms),
      tool_name = COALESCE(VALUES(tool_name), tool_name),
      decision_type = COALESCE(VALUES(decision_type), decision_type),
      policy_id = COALESCE(VALUES(policy_id), policy_id),
      route = COALESCE(VALUES(route), route),
      reason = COALESCE(VALUES(reason), reason),
      event_json = COALESCE(VALUES(event_json), event_json),
      payload_json = COALESCE(VALUES(payload_json), payload_json),
      metadata_json = COALESCE(VALUES(metadata_json), metadata_json),
      risk_signals_json = COALESCE(VALUES(risk_signals_json), risk_signals_json),
      decision_json = COALESCE(VALUES(decision_json), decision_json),
      plugin_result_json = COALESCE(VALUES(plugin_result_json), plugin_result_json),
      plugin_input_json = COALESCE(VALUES(plugin_input_json), plugin_input_json),
      received_at = CURRENT_TIMESTAMP
"""


__all__ = ["TraceEventStore", "ensure_trace_schema"]

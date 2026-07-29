from __future__ import annotations

from typing import Any

from backend.audit import AuditTraceEntry
from backend.console.state import ConsoleState
from backend.runtime.manager import RuntimeManager
from backend.runtime.trace_store import TraceEventStore

from shared.schemas.context import RuntimeContext
from shared.schemas.decisions import GuardDecision
from shared.schemas.events import llm_input, tool_event


class FakeTraceDB:
    def __init__(self) -> None:
        self.executed: list[tuple[str, tuple[Any, ...] | None]] = []
        self.rows: dict[tuple[str, str], dict[str, Any]] = {}
        self.user_ids: set[int] = set()
        self.agent_ids: set[str] = set()
        self.session_ids: set[str] = set()

    def execute(self, sql: str, params: tuple[Any, ...] | None = None) -> int:
        self.executed.append((sql, params))
        if "INSERT INTO runtime_trace_events" in sql and params is not None:
            row = _row_from_params(params)
            key = (str(row["session_id"]), str(row["event_id"]))
            row["id"] = self.rows.get(key, {}).get("id", len(self.rows) + 1)
            self.rows[key] = row
        return 1

    def fetchone(self, sql: str, params: tuple[Any, ...] | None = None) -> dict[str, Any] | None:
        if "FROM runtime_trace_events" in sql:
            assert params is not None
            return self.rows.get((str(params[0]), str(params[1])))
        if "FROM users" in sql:
            assert params is not None
            user_id = int(params[0])
            return {"id": user_id} if user_id in self.user_ids else None
        if "FROM agents" in sql:
            assert params is not None
            agent_id = str(params[0])
            return {"agent_id": agent_id} if agent_id in self.agent_ids else None
        if "FROM runtime_sessions" in sql:
            assert params is not None
            session_id = str(params[0])
            return {"session_id": session_id} if session_id in self.session_ids else None
        return None

    def fetchall(self, sql: str, params: tuple[Any, ...] | None = None) -> list[dict[str, Any]]:
        return list(self.rows.values())


def test_trace_schema_creates_runtime_trace_events_table():
    db = FakeTraceDB()
    TraceEventStore(db=db).ensure_schema()  # type: ignore[arg-type]

    assert any("CREATE TABLE IF NOT EXISTS runtime_trace_events" in sql for sql, _ in db.executed)


def test_agent_snapshot_query_uses_only_canonical_agent_id():
    class SnapshotDB(FakeTraceDB):
        def __init__(self):
            super().__init__()
            self.last_sql = ""

        def fetchone(self, sql, params=None):
            self.last_sql = sql
            return {"max_id": 0}

    snapshot_db = SnapshotDB()
    TraceEventStore(db=snapshot_db).agent_snapshot_max_id("agent-a")  # type: ignore[arg-type]
    assert "agent_id = %s" in snapshot_db.last_sql
    assert "raw_agent_id" not in snapshot_db.last_sql


def test_upsert_trace_entry_inserts_and_updates_decision_plugin_data():
    db = FakeTraceDB()
    db.user_ids.add(7)
    db.agent_ids.add("agent-a")
    db.session_ids.add("session-a")
    store = TraceEventStore(db=db)  # type: ignore[arg-type]
    event = tool_event(
        RuntimeContext(session_id="session-a", agent_id="agent-a", user_id="7"),
        "send_email",
        {"to": "a@example.com"},
    )

    first = store.upsert_trace_entry(
        "session-a",
        AuditTraceEntry(session_id="session-a", agent_id="agent-a", user_id="7", event=event),
    )
    second = store.upsert_trace_entry(
        "session-a",
        AuditTraceEntry(
            session_id="session-a",
            agent_id="agent-a",
            user_id="7",
            event=event,
            decision=GuardDecision.deny("blocked", policy_id="p1"),
            plugin_result={"risk_signals": ["x"]},
            plugin_input={"event": event.to_dict()},
            route="server",
        ),
    )

    assert first == "appended"
    assert second == "updated"
    entries = store.list_trace_entries("session-a")
    assert len(entries) == 1
    assert entries[0].decision is not None
    assert entries[0].decision.policy_id == "p1"
    assert entries[0].plugin_result["risk_signals"] == ["x"]
    traffic = store.recent_traffic("agent-a")
    audit = store.recent_audit("agent-a")
    assert traffic[0]["tool"] == "send_email"
    assert traffic[0]["action"] == "deny"
    assert audit[0]["event"]["tool_call"]["tool_name"] == "send_email"
    assert audit[0]["decision"]["policy_id"] == "p1"


def test_legacy_identity_keeps_raw_fields_without_foreign_keys():
    db = FakeTraceDB()
    store = TraceEventStore(db=db)  # type: ignore[arg-type]
    event = tool_event(
        RuntimeContext(session_id="legacy-session", agent_id="missing-agent", user_id="alice"),
        "read_file",
        {"path": "/tmp/a"},
    )

    store.upsert_trace_entry(
        "legacy-session",
        AuditTraceEntry(
            session_id="legacy-session",
            agent_id="missing-agent",
            user_id="alice",
            event=event,
        ),
    )

    row = next(iter(db.rows.values()))
    assert row["user_id"] is None
    assert row["agent_id"] is None
    assert row["runtime_session_id"] is None
    assert row["raw_user_id"] == "alice"
    assert row["raw_agent_id"] == "missing-agent"


def test_runtime_manager_double_writes_trace_store(monkeypatch):
    class RecordingStore:
        def __init__(self) -> None:
            self.records: list[AuditTraceEntry] = []

        def upsert_trace_entry(self, session_id, record, *, agent_id=None, user_id=None):
            self.records.append(record)
            return "appended"

        def list_trace_entries(self, *args, **kwargs):
            return []

    persistent = RecordingStore()
    monkeypatch.setattr("backend.runtime.manager.get_mysql_config", lambda: object())
    manager = RuntimeManager(enable_session_health_monitor=False)
    manager._trace_event_store = persistent  # type: ignore[assignment]

    manager.decide(
        {
            "context": {"session_id": "s-db", "agent_id": "agent-db"},
            "current_event": {
                "event_type": "tool_invoke",
                "payload": {"tool_name": "send_email", "arguments": {}, "capabilities": []},
            },
            "trajectory_window": [],
            "client_cached_entries": [
                {
                    "event": {
                        "event_id": "cached-db",
                        "event_type": "tool_result",
                        "payload": {"tool_name": "read_file", "result": "secret"},
                    },
                    "decision": {"decision_type": "allow", "reason": "local"},
                }
            ],
        }
    )

    assert manager.trace_store.get("s-db", agent_id="agent-db")
    assert persistent.records
    reasons = {record.reason for record in persistent.records}
    assert "decision_sync" in reasons
    assert "guard_decide" in reasons


def test_recent_audit_exposes_model_metadata_from_context():
    db = FakeTraceDB()
    db.agent_ids.add("agent-a")
    db.session_ids.add("session-a")
    store = TraceEventStore(db=db)  # type: ignore[arg-type]
    event = llm_input(
        RuntimeContext(
            session_id="session-a",
            agent_id="agent-a",
            metadata={
                "model": {
                    "provider": "openai",
                    "name": "gpt-5.2",
                    "base_url": "https://api.gpt.ge/v1",
                    "source": "openclaw-runtime",
                }
            },
        ),
        [{"role": "user", "content": "hello"}],
        phase="llm_before",
    )

    store.upsert_trace_entry(
        "session-a",
        AuditTraceEntry(
            session_id="session-a",
            agent_id="agent-a",
            event=event,
            decision=GuardDecision.allow("ok"),
        ),
    )

    runtime_state = store.recent_audit("agent-a")[0]["runtime_state"]
    assert runtime_state["model"] == {
        "provider": "openai",
        "name": "gpt-5.2",
        "base_url": "https://api.gpt.ge/v1",
        "source": "openclaw-runtime",
    }
    assert runtime_state["metadata"]["model"] == runtime_state["model"]


def test_console_falls_back_to_memory_when_persistent_store_fails(monkeypatch):
    class FailingStore:
        def recent_traffic(self, *args, **kwargs):
            raise RuntimeError("db down")

        def recent_audit(self, *args, **kwargs):
            raise RuntimeError("db down")

        def stats(self, *args, **kwargs):
            raise RuntimeError("db down")

        def upsert_trace_entry(self, *args, **kwargs):
            raise RuntimeError("db down")

        def list_trace_entries(self, *args, **kwargs):
            raise RuntimeError("db down")

    monkeypatch.setattr("backend.runtime.manager.get_mysql_config", lambda: object())
    manager = RuntimeManager(enable_session_health_monitor=False)
    manager._trace_event_store = FailingStore()  # type: ignore[assignment]
    console = ConsoleState(manager)

    manager.decide(
        {
            "context": {"session_id": "s-fallback", "agent_id": "agent-fallback"},
            "current_event": {
                "event_type": "tool_invoke",
                "payload": {"tool_name": "read_file", "arguments": {}, "capabilities": []},
            },
            "trajectory_window": [],
        }
    )

    assert console.traffic("agent-fallback")
    assert console.audit_recent("agent-fallback")
    assert console.stats("agent-fallback")["total_requests"] == 1


def _row_from_params(params: tuple[Any, ...]) -> dict[str, Any]:
    keys = [
        "user_id",
        "agent_id",
        "runtime_session_id",
        "raw_user_id",
        "raw_agent_id",
        "session_id",
        "event_id",
        "event_type",
        "event_ts_ms",
        "tool_name",
        "decision_type",
        "policy_id",
        "route",
        "reason",
        "event_json",
        "payload_json",
        "metadata_json",
        "risk_signals_json",
        "decision_json",
        "plugin_result_json",
        "plugin_input_json",
    ]
    return dict(zip(keys, params, strict=True))

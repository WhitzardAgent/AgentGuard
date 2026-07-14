from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "migrations" / "reset_non_admin_users.py"
SPEC = importlib.util.spec_from_file_location("reset_non_admin_users_migration", SCRIPT_PATH)
assert SPEC is not None
migration = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(migration)


class FakeConnection:
    def __init__(self) -> None:
        self.users = [
            {"id": 1, "username": "AgentGuardAdmin", "profile_json": '{"role":"admin"}', "email": "admin@example.com", "email_verified_at": "now"},
            {"id": 2, "username": "alice", "profile_json": None, "email": "alice@example.com", "email_verified_at": "now"},
        ]
        self.tables: dict[str, list[dict[str, Any]]] = {
            "agents": [{"agent_id": "ag_user"}, {"agent_id": "ag_admin"}],
            "agent_tools": [{"agent_id": "ag_user"}, {"agent_id": "ag_admin"}],
            "agent_credentials": [{"agent_id": "ag_user"}],
            "agent_external_identities": [{"agent_id": "ag_user"}],
            "user_agents": [
                {"user_id": 2, "agent_id": "ag_user"},
                {"user_id": 1, "agent_id": "ag_admin"},
            ],
            "runtime_sessions": [
                {"session_id": "runtime-user", "agent_id": "ag_user", "user_id": 2},
                {"session_id": "runtime-admin", "agent_id": "ag_admin", "user_id": 1},
            ],
            "runtime_tokens": [
                {"token_jti": "tok-user", "session_id": "runtime-user"},
                {"token_jti": "tok-admin", "session_id": "runtime-admin"},
            ],
            "runtime_trace_events": [
                {"agent_id": "ag_user", "raw_agent_id": "ag_user", "runtime_session_id": "runtime-user", "session_id": "runtime-user", "user_id": 2, "raw_user_id": "2"},
                {"agent_id": "ag_admin", "raw_agent_id": "ag_admin", "runtime_session_id": "runtime-admin", "session_id": "runtime-admin", "user_id": 1, "raw_user_id": "1"},
            ],
            "external_runtime_sessions": [
                {"agent_id": "ag_user", "user_id": 2},
                {"agent_id": "ag_admin", "user_id": 1},
            ],
        }
        self.began = False
        self.committed = False
        self.rolled_back = False

    def cursor(self):
        return FakeCursor(self)

    def begin(self) -> None:
        self.began = True

    def commit(self) -> None:
        self.committed = True

    def rollback(self) -> None:
        self.rolled_back = True


class FakeCursor:
    def __init__(self, conn: FakeConnection) -> None:
        self.conn = conn
        self.last_rows: list[dict[str, Any]] = []
        self.last_row: dict[str, Any] | None = None
        self.closed = False

    def execute(self, sql: str, params: tuple[Any, ...] | None = None) -> int:
        normalized = " ".join(sql.split())
        params = params or ()
        if normalized == "SELECT id, username, profile_json FROM users WHERE id = 1":
            self.last_row = next((row for row in self.conn.users if row["id"] == 1), None)
            return 1 if self.last_row else 0
        if normalized.startswith("SELECT DISTINCT agent_id FROM user_agents"):
            self.last_rows = sorted(
                [{"agent_id": row["agent_id"]} for row in self.conn.tables["user_agents"] if row["user_id"] != 1],
                key=lambda item: item["agent_id"],
            )
            return len(self.last_rows)
        if normalized == "SELECT id FROM users WHERE id <> 1 ORDER BY id":
            self.last_rows = [{"id": row["id"]} for row in self.conn.users if row["id"] != 1]
            return len(self.last_rows)
        if "FROM information_schema.COLUMNS" in normalized:
            table, column = params
            exists = table in self.conn.tables or table == "users"
            self.last_row = {"count": 1 if exists and column in {"user_id", "email", "email_verified_at"} else 0}
            return 1
        if normalized.startswith("DELETE FROM runtime_trace_events") and params:
            before = len(self.conn.tables["runtime_trace_events"])
            if len(params) == 4:
                agent_id = params[0]
                sessions = {row["session_id"] for row in self.conn.tables["runtime_sessions"] if row["agent_id"] == agent_id}
                self.conn.tables["runtime_trace_events"] = [
                    row for row in self.conn.tables["runtime_trace_events"]
                    if row.get("agent_id") != agent_id
                    and row.get("raw_agent_id") != agent_id
                    and row.get("runtime_session_id") not in sessions
                    and row.get("session_id") not in sessions
                ]
            else:
                user_ids = {int(item) for item in params if isinstance(item, int)}
                raw_user_ids = {str(item) for item in params}
                self.conn.tables["runtime_trace_events"] = [
                    row for row in self.conn.tables["runtime_trace_events"]
                    if row.get("user_id") not in user_ids and row.get("raw_user_id") not in raw_user_ids
                ]
            return before - len(self.conn.tables["runtime_trace_events"])
        if normalized.startswith("DELETE FROM runtime_tokens") and "agent_id" in normalized:
            agent_id = params[0]
            sessions = {row["session_id"] for row in self.conn.tables["runtime_sessions"] if row["agent_id"] == agent_id}
            return self._delete_where("runtime_tokens", lambda row: row["session_id"] in sessions)
        if normalized.startswith("DELETE FROM runtime_tokens") and "user_id" in normalized:
            user_ids = set(params)
            sessions = {row["session_id"] for row in self.conn.tables["runtime_sessions"] if row["user_id"] in user_ids}
            return self._delete_where("runtime_tokens", lambda row: row["session_id"] in sessions)
        if normalized == "DELETE FROM runtime_sessions WHERE agent_id = %s":
            return self._delete_where("runtime_sessions", lambda row: row["agent_id"] == params[0])
        if normalized.startswith("DELETE FROM runtime_sessions WHERE user_id IN"):
            user_ids = set(params)
            return self._delete_where("runtime_sessions", lambda row: row["user_id"] in user_ids)
        if normalized == "DELETE FROM external_runtime_sessions WHERE agent_id = %s":
            return self._delete_where("external_runtime_sessions", lambda row: row["agent_id"] == params[0])
        if normalized.startswith("DELETE FROM external_runtime_sessions WHERE user_id IN"):
            user_ids = set(params)
            return self._delete_where("external_runtime_sessions", lambda row: row["user_id"] in user_ids)
        for table in ("agent_tools", "user_agents", "agent_credentials", "agent_external_identities", "agents"):
            if normalized == f"DELETE FROM {table} WHERE agent_id = %s":
                return self._delete_where(table, lambda row, agent_id=params[0]: row["agent_id"] == agent_id)
        if normalized == "DELETE FROM users WHERE id <> 1":
            before = len(self.conn.users)
            self.conn.users = [row for row in self.conn.users if row["id"] == 1]
            return before - len(self.conn.users)
        if normalized == "UPDATE users SET email = NULL, email_verified_at = NULL WHERE id = 1":
            self.conn.users[0]["email"] = None
            self.conn.users[0]["email_verified_at"] = None
            return 1
        raise AssertionError(normalized)

    def fetchone(self):
        return self.last_row

    def fetchall(self):
        return self.last_rows

    def close(self) -> None:
        self.closed = True

    def _delete_where(self, table: str, predicate) -> int:
        before = len(self.conn.tables[table])
        self.conn.tables[table] = [row for row in self.conn.tables[table] if not predicate(row)]
        return before - len(self.conn.tables[table])


def test_reset_non_admin_users_deletes_user_agents_and_keeps_admin():
    conn = FakeConnection()

    result = migration.reset_connection(conn)

    assert conn.committed is True
    assert conn.rolled_back is False
    assert result["deleted_user_count"] == 1
    assert result["deleted_agent_ids"] == ["ag_user"]
    assert conn.users == [
        {"id": 1, "username": "AgentGuardAdmin", "profile_json": '{"role":"admin"}', "email": None, "email_verified_at": None}
    ]
    assert conn.tables["agents"] == [{"agent_id": "ag_admin"}]
    assert conn.tables["runtime_sessions"] == [{"session_id": "runtime-admin", "agent_id": "ag_admin", "user_id": 1}]
    assert conn.tables["runtime_tokens"] == [{"token_jti": "tok-admin", "session_id": "runtime-admin"}]


def test_reset_non_admin_users_rejects_missing_admin():
    conn = FakeConnection()
    conn.users[0]["username"] = "not-admin"

    try:
        migration.reset_connection(conn)
    except migration.ResetUsersError:
        pass
    else:
        raise AssertionError("expected ResetUsersError")

    assert conn.committed is False
    assert conn.rolled_back is True

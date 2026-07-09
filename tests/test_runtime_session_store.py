from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from backend.auth.session_store import RuntimeSessionStore


class FakeDB:
    def __init__(self) -> None:
        self.fetchall_params: tuple[Any, ...] | None = None
        self.executed: list[tuple[str, tuple[Any, ...] | None]] = []

    def fetchall(self, sql: str, params: tuple[Any, ...] | None = None) -> list[dict[str, Any]]:
        self.fetchall_params = params
        return [
            {
                "session_id": "ags_dify_1",
                "agent_id": "ag_1",
                "user_id": 7,
                "provider": "dify",
                "external_session_id": None,
                "external_account_email": "alice@example.com",
                "dpop_jkt": "jkt-1",
                "status": "active",
                "metadata_json": None,
                "created_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
                "last_seen_at": datetime(2026, 1, 2, tzinfo=timezone.utc),
                "closed_at": None,
                "active_token_count": 2,
                "latest_token_expires_at": datetime(2026, 1, 3, tzinfo=timezone.utc),
                "latest_token_issued_at": datetime(2026, 1, 2, 1, tzinfo=timezone.utc),
            }
        ]

    def execute(self, sql: str, params: tuple[Any, ...] | None = None) -> None:
        self.executed.append((sql, params))


def test_runtime_session_store_lists_sessions_with_token_summary():
    db = FakeDB()
    store = RuntimeSessionStore(db=db)  # type: ignore[arg-type]

    rows = store.list_sessions(agent_id="ag_1", user_id=7, status="all", limit=500)

    assert db.fetchall_params == ("ag_1", 7, 200)
    assert len(rows) == 1
    assert rows[0].session.session_id == "ags_dify_1"
    assert rows[0].session.external_session_id is None
    assert rows[0].active_token_count == 2
    assert rows[0].latest_token_expires_at == datetime(2026, 1, 3, tzinfo=timezone.utc)


def test_runtime_session_store_close_revokes_active_tokens():
    db = FakeDB()
    store = RuntimeSessionStore(db=db)  # type: ignore[arg-type]

    store.close_session("ags_dify_1")

    assert len(db.executed) == 2
    assert "UPDATE runtime_sessions" in db.executed[0][0]
    assert db.executed[0][1] == ("ags_dify_1",)
    assert "UPDATE runtime_tokens" in db.executed[1][0]
    assert db.executed[1][1] == ("ags_dify_1",)

#!/usr/bin/env python3
"""Delete all non-admin users and their registered AgentGuard runtime state.

Run during a maintenance window with AgentGuard stopped. The built-in
AgentGuardAdmin user must already exist as users.id = 1.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
SRC_SERVER = ROOT / "src" / "server"
if str(SRC_SERVER) not in sys.path:
    sys.path.insert(0, str(SRC_SERVER))

from backend.agents.store import _delete_agent_with_execute  # noqa: E402
from backend.database.config import get_mysql_config  # noqa: E402

ADMIN_USERNAME = "AgentGuardAdmin"


class ResetUsersError(RuntimeError):
    pass


def reset_connection(conn: Any) -> dict[str, Any]:
    cursor = conn.cursor()
    try:
        _begin(conn)
        _require_admin(cursor)
        agent_ids = _non_admin_agent_ids(cursor)
        agent_results = [
            _delete_agent_with_execute(cursor.execute, agent_id).to_dict()
            for agent_id in agent_ids
        ]
        user_ids = _non_admin_user_ids(cursor)
        orphan_trace_count = _delete_remaining_non_admin_runtime_state(cursor, user_ids)
        user_count = int(cursor.execute("DELETE FROM users WHERE id <> 1"))
        _clear_admin_email_if_present(cursor)
        _commit(conn)
        return {
            "deleted_user_count": user_count,
            "deleted_agent_ids": agent_ids,
            "deleted_agent_count": len(agent_ids),
            "deleted_agent_results": agent_results,
            "deleted_orphan_trace_count": orphan_trace_count,
        }
    except Exception:
        _rollback(conn)
        raise
    finally:
        close = getattr(cursor, "close", None)
        if callable(close):
            close()


def main() -> int:
    config = get_mysql_config()
    if config is None:
        raise SystemExit("AGENTGUARD_MYSQL_URL is not configured")
    try:
        import pymysql
        from pymysql.cursors import DictCursor
    except ModuleNotFoundError as exc:
        raise SystemExit("PyMySQL is required; install agentguard[mysql].") from exc

    conn = pymysql.connect(
        host=config.host,
        port=config.port,
        user=config.user,
        password=config.password,
        database=config.database,
        charset=config.charset,
        cursorclass=DictCursor,
        autocommit=False,
    )
    try:
        result = reset_connection(conn)
    finally:
        conn.close()
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


def _require_admin(cursor: Any) -> None:
    cursor.execute(
        "SELECT id, username, profile_json FROM users WHERE id = 1",
    )
    row = cursor.fetchone()
    if row is None:
        raise ResetUsersError("users.id=1 admin is missing; run create_admin_user.py first")
    if str(row.get("username") or "") != ADMIN_USERNAME:
        raise ResetUsersError("users.id=1 is not AgentGuardAdmin")
    if not _is_admin_profile(row.get("profile_json")):
        raise ResetUsersError("AgentGuardAdmin does not have admin profile_json")


def _is_admin_profile(value: Any) -> bool:
    if isinstance(value, dict):
        profile = value
    else:
        try:
            profile = json.loads(str(value or "{}"))
        except Exception:
            return False
    return str(profile.get("role") or "").strip().lower() == "admin"


def _non_admin_agent_ids(cursor: Any) -> list[str]:
    cursor.execute(
        """
        SELECT DISTINCT agent_id
        FROM user_agents
        WHERE user_id <> 1
        ORDER BY agent_id
        """
    )
    return [str(row["agent_id"]) for row in cursor.fetchall()]


def _non_admin_user_ids(cursor: Any) -> list[int]:
    cursor.execute("SELECT id FROM users WHERE id <> 1 ORDER BY id")
    return [int(row["id"]) for row in cursor.fetchall()]


def _delete_remaining_non_admin_runtime_state(cursor: Any, user_ids: list[int]) -> int:
    if not user_ids:
        return 0
    placeholders = ", ".join(["%s"] * len(user_ids))
    trace_count = int(
        cursor.execute(
            f"""
            DELETE FROM runtime_trace_events
            WHERE user_id IN ({placeholders})
               OR raw_user_id IN ({placeholders})
               OR runtime_session_id IN (
                    SELECT session_id FROM runtime_sessions
                    WHERE user_id IN ({placeholders})
               )
               OR session_id IN (
                    SELECT session_id FROM runtime_sessions
                    WHERE user_id IN ({placeholders})
               )
            """,
            tuple(user_ids + [str(user_id) for user_id in user_ids] + user_ids + user_ids),
        )
    )
    cursor.execute(
        f"""
        DELETE FROM runtime_tokens
        WHERE session_id IN (
            SELECT session_id FROM runtime_sessions WHERE user_id IN ({placeholders})
        )
        """,
        tuple(user_ids),
    )
    cursor.execute(
        f"DELETE FROM runtime_sessions WHERE user_id IN ({placeholders})",
        tuple(user_ids),
    )
    if _table_has_column(cursor, "external_runtime_sessions", "user_id"):
        cursor.execute(
            f"DELETE FROM external_runtime_sessions WHERE user_id IN ({placeholders})",
            tuple(user_ids),
        )
    return trace_count


def _clear_admin_email_if_present(cursor: Any) -> None:
    if not _table_has_column(cursor, "users", "email"):
        return
    if _table_has_column(cursor, "users", "email_verified_at"):
        cursor.execute(
            "UPDATE users SET email = NULL, email_verified_at = NULL WHERE id = 1",
        )
        return
    cursor.execute("UPDATE users SET email = NULL WHERE id = 1")


def _table_has_column(cursor: Any, table: str, column: str) -> bool:
    cursor.execute(
        """
        SELECT COUNT(*) AS count
        FROM information_schema.COLUMNS
        WHERE TABLE_SCHEMA = DATABASE()
          AND TABLE_NAME = %s
          AND COLUMN_NAME = %s
        """,
        (table, column),
    )
    row = cursor.fetchone() or {}
    return int(row.get("count") or 0) > 0


def _begin(conn: Any) -> None:
    begin = getattr(conn, "begin", None)
    if callable(begin):
        begin()


def _commit(conn: Any) -> None:
    commit = getattr(conn, "commit", None)
    if callable(commit):
        commit()


def _rollback(conn: Any) -> None:
    rollback = getattr(conn, "rollback", None)
    if callable(rollback):
        rollback()


if __name__ == "__main__":
    raise SystemExit(main())

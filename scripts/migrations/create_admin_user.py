#!/usr/bin/env python3
"""Create the built-in AgentGuard admin user as users.id = 1.

Run this once during a maintenance window with AgentGuard stopped. The script
shifts existing user ids and all known user_id references by +1 before inserting
AgentGuardAdmin at id 1.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
SRC_SERVER = ROOT / "src" / "server"
if str(SRC_SERVER) not in sys.path:
    sys.path.insert(0, str(SRC_SERVER))

from backend.database.config import get_mysql_config  # noqa: E402
from backend.user.passwords import hash_password  # noqa: E402

ADMIN_USERNAME = "AgentGuardAdmin"
ADMIN_PASSWORD = "AgentGuardAdmin123"
ADMIN_PROFILE = {"role": "admin"}
ADMIN_PROFILE_JSON = json.dumps(ADMIN_PROFILE, sort_keys=True, separators=(",", ":"))

REFERENCE_TABLES = (
    "user_sessions",
    "user_external_accounts",
    "user_tickets",
    "user_agents",
    "runtime_sessions",
)
OPTIONAL_REFERENCE_TABLES = ("external_runtime_sessions",)
_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class AdminMigrationError(RuntimeError):
    pass


def migrate_connection(conn: Any) -> str:
    cursor = conn.cursor()
    foreign_key_checks_disabled = False
    try:
        _begin(conn)
        existing = _admin_user_row(cursor)
        if existing is not None:
            if int(existing["id"]) == 1 and _is_admin_profile(existing.get("profile_json")):
                _commit(conn)
                return "already-present"
            raise AdminMigrationError(
                f"{ADMIN_USERNAME} already exists but is not the id=1 admin user"
            )

        cursor.execute("SET FOREIGN_KEY_CHECKS = 0")
        foreign_key_checks_disabled = True
        for table in REFERENCE_TABLES:
            _shift_reference_table(cursor, table)
        for table in OPTIONAL_REFERENCE_TABLES:
            if _table_has_column(cursor, table, "user_id"):
                _shift_reference_table(cursor, table)

        cursor.execute("UPDATE users SET id = id + 1 ORDER BY id DESC")
        cursor.execute(
            """
            INSERT INTO users (id, username, password_hash, profile_json)
            VALUES (%s, %s, %s, %s)
            """,
            (1, ADMIN_USERNAME, hash_password(ADMIN_PASSWORD), ADMIN_PROFILE_JSON),
        )
        cursor.execute("SELECT COALESCE(MAX(id), 0) + 1 AS next_id FROM users")
        row = cursor.fetchone() or {}
        next_id = max(2, int(row.get("next_id") or 2))
        cursor.execute(f"ALTER TABLE users AUTO_INCREMENT = {next_id}")
        _commit(conn)
        return "created"
    except Exception:
        _rollback(conn)
        raise
    finally:
        if foreign_key_checks_disabled:
            cursor.execute("SET FOREIGN_KEY_CHECKS = 1")
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
        result = migrate_connection(conn)
    finally:
        conn.close()
    print(f"{ADMIN_USERNAME} migration {result}.")
    return 0


def _admin_user_row(cursor: Any) -> dict[str, Any] | None:
    cursor.execute(
        "SELECT id, profile_json FROM users WHERE username = %s",
        (ADMIN_USERNAME,),
    )
    row = cursor.fetchone()
    return dict(row) if row is not None else None


def _is_admin_profile(value: Any) -> bool:
    if isinstance(value, dict):
        profile = value
    else:
        try:
            profile = json.loads(str(value or "{}"))
        except Exception:
            return False
    return str(profile.get("role") or "").strip().lower() == "admin"


def _shift_reference_table(cursor: Any, table: str) -> None:
    quoted = _quote_identifier(table)
    cursor.execute(f"UPDATE {quoted} SET user_id = user_id + 1 ORDER BY user_id DESC")


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


def _quote_identifier(value: str) -> str:
    if not _IDENTIFIER_RE.match(value):
        raise AdminMigrationError(f"unsafe SQL identifier: {value}")
    return f"`{value}`"


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

#!/usr/bin/env python3
"""Reset organization/group data and recreate the multi-admin schema."""
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
from backend.user.org_store import BUILTIN_ADMIN_USERNAME, _SCHEMA as ORG_SCHEMA_STATEMENTS  # noqa: E402

ORG_TABLES = (
    "user_invitations",
    "group_users",
    "user_groups",
    "organization_members",
    "organizations",
)
_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class OrgDataResetError(RuntimeError):
    pass


def reset_connection(conn: Any) -> dict[str, Any]:
    cursor = conn.cursor()
    foreign_key_checks_disabled = False
    try:
        _begin(conn)
        admin = _admin_user_row(cursor)
        if admin is None or not _is_admin_profile(admin.get("profile_json")):
            raise OrgDataResetError(f"{BUILTIN_ADMIN_USERNAME} admin user is missing")
        cursor.execute("SET FOREIGN_KEY_CHECKS = 0")
        foreign_key_checks_disabled = True
        for table in ORG_TABLES:
            cursor.execute(f"DROP TABLE IF EXISTS {_quote_identifier(table)}")
        cursor.execute("SET FOREIGN_KEY_CHECKS = 1")
        foreign_key_checks_disabled = False
        for statement in ORG_SCHEMA_STATEMENTS:
            cursor.execute(statement)
        _commit(conn)
        return {
            "reset": True,
            "dropped_tables": list(ORG_TABLES),
            "recreated_table_count": len(ORG_SCHEMA_STATEMENTS),
        }
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
        result = reset_connection(conn)
    finally:
        conn.close()
    print(json.dumps(result, sort_keys=True))
    return 0


def _admin_user_row(cursor: Any) -> dict[str, Any] | None:
    cursor.execute(
        "SELECT id, profile_json FROM users WHERE username = %s",
        (BUILTIN_ADMIN_USERNAME,),
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


def _quote_identifier(value: str) -> str:
    if not _IDENTIFIER_RE.match(value):
        raise OrgDataResetError(f"unsafe SQL identifier: {value}")
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

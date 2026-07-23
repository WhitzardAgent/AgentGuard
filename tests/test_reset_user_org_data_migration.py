from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "migrations" / "reset_user_org_data.py"
SPEC = importlib.util.spec_from_file_location("reset_user_org_data_migration", SCRIPT_PATH)
assert SPEC is not None
migration = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(migration)


class FakeConnection:
    def __init__(self, *, users: list[dict[str, Any]] | None = None, tables: set[str] | None = None) -> None:
        self.users = users or [
            {"id": 1, "username": "AgentGuardAdmin", "profile_json": '{"role":"admin"}'},
            {"id": 2, "username": "alice", "profile_json": None},
        ]
        self.tables = tables or set(migration.ORG_TABLES)
        self.began = False
        self.committed = False
        self.rolled_back = False
        self.foreign_key_checks: list[int] = []

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
        self.last_row: dict[str, Any] | None = None
        self.closed = False

    def execute(self, sql: str, params: tuple[Any, ...] | None = None) -> int:
        normalized = " ".join(sql.split())
        params = params or ()
        if normalized == "SET FOREIGN_KEY_CHECKS = 0":
            self.conn.foreign_key_checks.append(0)
            return 0
        if normalized == "SET FOREIGN_KEY_CHECKS = 1":
            self.conn.foreign_key_checks.append(1)
            return 0
        if normalized == "SELECT id, profile_json FROM users WHERE username = %s":
            username = params[0]
            self.last_row = next((row for row in self.conn.users if row["username"] == username), None)
            return 1 if self.last_row else 0
        if normalized.startswith("DROP TABLE IF EXISTS `"):
            table = normalized.split("`", 2)[1]
            self.conn.tables.discard(table)
            return 0
        if normalized.startswith("CREATE TABLE IF NOT EXISTS "):
            table = normalized.split("CREATE TABLE IF NOT EXISTS ", 1)[1].split("(", 1)[0].strip()
            self.conn.tables.add(table)
            return 0
        raise AssertionError(normalized)

    def fetchone(self):
        return self.last_row

    def close(self) -> None:
        self.closed = True


def test_reset_user_org_data_drops_and_recreates_org_tables():
    conn = FakeConnection()

    result = migration.reset_connection(conn)

    assert conn.committed is True
    assert conn.rolled_back is False
    assert conn.foreign_key_checks == [0, 1]
    assert result["reset"] is True
    assert result["dropped_tables"] == list(migration.ORG_TABLES)
    assert result["recreated_table_count"] == len(migration.ORG_SCHEMA_STATEMENTS)
    assert conn.tables == {
        "organizations",
        "organization_members",
        "user_groups",
        "group_users",
        "user_invitations",
    }


def test_reset_user_org_data_requires_builtin_admin():
    conn = FakeConnection(
        users=[{"id": 3, "username": "alice", "profile_json": None}],
    )

    try:
        migration.reset_connection(conn)
    except migration.OrgDataResetError:
        pass
    else:
        raise AssertionError("expected OrgDataResetError")

    assert conn.committed is False
    assert conn.rolled_back is True

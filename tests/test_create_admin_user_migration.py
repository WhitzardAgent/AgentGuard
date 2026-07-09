from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

from backend.user.passwords import verify_password


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "migrations" / "create_admin_user.py"
SPEC = importlib.util.spec_from_file_location("create_admin_user_migration", SCRIPT_PATH)
assert SPEC is not None
migration = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(migration)


class FakeConnection:
    def __init__(
        self,
        *,
        users: list[dict[str, Any]] | None = None,
        reference_tables: dict[str, list[dict[str, Any]]] | None = None,
    ) -> None:
        self.users = users or []
        self.reference_tables = reference_tables or {
            table: [] for table in migration.REFERENCE_TABLES
        }
        self.reference_tables.setdefault("external_runtime_sessions", [])
        self.began = False
        self.committed = False
        self.rolled_back = False
        self.auto_increment: int | None = None
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
        if normalized == "SET FOREIGN_KEY_CHECKS = 0":
            self.conn.foreign_key_checks.append(0)
            return 0
        if normalized == "SET FOREIGN_KEY_CHECKS = 1":
            self.conn.foreign_key_checks.append(1)
            return 0
        if "FROM users WHERE username = %s" in normalized:
            username = params[0]
            self.last_row = next(
                (
                    {"id": row["id"], "profile_json": row.get("profile_json")}
                    for row in self.conn.users
                    if row["username"] == username
                ),
                None,
            )
            return 1 if self.last_row else 0
        if "FROM information_schema.COLUMNS" in normalized:
            table, column = params
            exists = table in self.conn.reference_tables and column == "user_id"
            self.last_row = {"count": 1 if exists else 0}
            return 1
        if normalized.startswith("UPDATE `") and "SET user_id = user_id + 1" in normalized:
            table = normalized.split("`", 2)[1]
            for row in self.conn.reference_tables[table]:
                row["user_id"] += 1
            return len(self.conn.reference_tables[table])
        if normalized == "UPDATE users SET id = id + 1 ORDER BY id DESC":
            for row in sorted(self.conn.users, key=lambda item: item["id"], reverse=True):
                row["id"] += 1
            return len(self.conn.users)
        if normalized.startswith("INSERT INTO users"):
            user_id, username, password_hash, profile_json = params
            self.conn.users.append(
                {
                    "id": user_id,
                    "username": username,
                    "password_hash": password_hash,
                    "profile_json": profile_json,
                }
            )
            return 1
        if normalized == "SELECT COALESCE(MAX(id), 0) + 1 AS next_id FROM users":
            self.last_row = {"next_id": max((row["id"] for row in self.conn.users), default=0) + 1}
            return 1
        if normalized.startswith("ALTER TABLE users AUTO_INCREMENT = "):
            self.conn.auto_increment = int(normalized.rsplit(" ", 1)[-1])
            return 0
        raise AssertionError(normalized)

    def fetchone(self):
        return self.last_row

    def close(self) -> None:
        self.closed = True


def test_admin_migration_inserts_admin_into_empty_database():
    conn = FakeConnection()

    result = migration.migrate_connection(conn)

    assert result == "created"
    assert conn.committed is True
    assert conn.rolled_back is False
    assert conn.foreign_key_checks == [0, 1]
    assert conn.auto_increment == 2
    admin = conn.users[0]
    assert admin["id"] == 1
    assert admin["username"] == "AgentGuardAdmin"
    assert admin["profile_json"] == '{"role":"admin"}'
    assert verify_password("！@#￥%……&*", admin["password_hash"])


def test_admin_migration_shifts_existing_users_and_references():
    conn = FakeConnection(
        users=[
            {"id": 1, "username": "alice", "password_hash": "old", "profile_json": None},
            {"id": 2, "username": "bob", "password_hash": "old", "profile_json": None},
        ],
        reference_tables={
            "user_sessions": [{"user_id": 1}],
            "user_external_accounts": [{"user_id": 2}],
            "user_tickets": [{"user_id": 1}],
            "user_agents": [{"user_id": 2}],
            "runtime_sessions": [{"user_id": 1}],
            "external_runtime_sessions": [{"user_id": 2}],
        },
    )

    result = migration.migrate_connection(conn)

    assert result == "created"
    users_by_name = {row["username"]: row["id"] for row in conn.users}
    assert users_by_name == {"AgentGuardAdmin": 1, "alice": 2, "bob": 3}
    assert conn.reference_tables["user_sessions"][0]["user_id"] == 2
    assert conn.reference_tables["user_external_accounts"][0]["user_id"] == 3
    assert conn.reference_tables["user_tickets"][0]["user_id"] == 2
    assert conn.reference_tables["user_agents"][0]["user_id"] == 3
    assert conn.reference_tables["runtime_sessions"][0]["user_id"] == 2
    assert conn.reference_tables["external_runtime_sessions"][0]["user_id"] == 3
    assert conn.auto_increment == 4


def test_admin_migration_is_idempotent_when_admin_already_exists():
    conn = FakeConnection(
        users=[
            {
                "id": 1,
                "username": "AgentGuardAdmin",
                "password_hash": "hash",
                "profile_json": '{"role":"admin"}',
            }
        ]
    )

    result = migration.migrate_connection(conn)

    assert result == "already-present"
    assert conn.committed is True
    assert conn.rolled_back is False
    assert conn.foreign_key_checks == []
    assert conn.users[0]["id"] == 1


def test_admin_migration_rejects_conflicting_admin_username():
    conn = FakeConnection(
        users=[
            {
                "id": 8,
                "username": "AgentGuardAdmin",
                "password_hash": "hash",
                "profile_json": '{"role":"user"}',
            }
        ]
    )

    try:
        migration.migrate_connection(conn)
    except migration.AdminMigrationError:
        pass
    else:
        raise AssertionError("expected AdminMigrationError")

    assert conn.committed is False
    assert conn.rolled_back is True
    assert conn.foreign_key_checks == []
    assert conn.users[0]["id"] == 8

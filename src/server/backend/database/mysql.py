"""Small MySQL adapter used by backend persistence modules."""
from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from backend.database.config import DatabaseConfig, get_mysql_config


class DatabaseUnavailable(RuntimeError):
    """Raised when persistent storage is requested but MySQL is not configured."""


class MySQLDatabase:
    def __init__(self, config: DatabaseConfig) -> None:
        self.config = config

    def connect(self) -> Any:
        try:
            import pymysql
            from pymysql.cursors import DictCursor
        except ModuleNotFoundError as exc:
            raise DatabaseUnavailable(
                "PyMySQL is required for AGENTGUARD_MYSQL_URL; install agentguard[mysql]."
            ) from exc
        return pymysql.connect(
            host=self.config.host,
            port=self.config.port,
            user=self.config.user,
            password=self.config.password,
            database=self.config.database,
            charset=self.config.charset,
            cursorclass=DictCursor,
            autocommit=True,
        )

    def execute(
        self,
        sql: str,
        params: Sequence[Any] | dict[str, Any] | None = None,
    ) -> int:
        with self.connect() as conn:
            with conn.cursor() as cursor:
                cursor.execute(sql, params)
                return int(cursor.rowcount)

    def fetchone(
        self,
        sql: str,
        params: Sequence[Any] | dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        with self.connect() as conn:
            with conn.cursor() as cursor:
                cursor.execute(sql, params)
                row = cursor.fetchone()
                return dict(row) if row is not None else None

    def fetchall(
        self,
        sql: str,
        params: Sequence[Any] | dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        with self.connect() as conn:
            with conn.cursor() as cursor:
                cursor.execute(sql, params)
                return [dict(row) for row in cursor.fetchall()]

    def insert(
        self,
        sql: str,
        params: Sequence[Any] | dict[str, Any] | None = None,
    ) -> int:
        with self.connect() as conn:
            with conn.cursor() as cursor:
                cursor.execute(sql, params)
                return int(cursor.lastrowid)


_database: MySQLDatabase | None = None


def get_database() -> MySQLDatabase:
    global _database
    if _database is not None:
        return _database
    config = get_mysql_config()
    if config is None:
        raise DatabaseUnavailable("AGENTGUARD_MYSQL_URL is not configured")
    _database = MySQLDatabase(config)
    return _database


def reset_database_for_tests() -> None:
    global _database
    _database = None

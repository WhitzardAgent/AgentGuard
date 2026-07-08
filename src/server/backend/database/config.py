"""Database configuration for AgentGuard backend persistence."""
from __future__ import annotations

import os
from dataclasses import dataclass
from urllib.parse import parse_qs, unquote, urlparse


MYSQL_URL_ENV = "AGENTGUARD_MYSQL_URL"


@dataclass(frozen=True)
class DatabaseConfig:
    host: str
    port: int
    user: str
    password: str
    database: str
    charset: str = "utf8mb4"


def get_mysql_config() -> DatabaseConfig | None:
    raw = os.environ.get(MYSQL_URL_ENV, "").strip()
    if not raw:
        return None
    return parse_mysql_url(raw)


def parse_mysql_url(raw: str) -> DatabaseConfig:
    parsed = urlparse(raw)
    if parsed.scheme not in {"mysql", "mysql+pymysql"}:
        raise ValueError(f"{MYSQL_URL_ENV} must use mysql:// or mysql+pymysql://")
    database = parsed.path.lstrip("/")
    if not database:
        raise ValueError(f"{MYSQL_URL_ENV} must include a database name")
    query = parse_qs(parsed.query)
    charset = (query.get("charset") or ["utf8mb4"])[0] or "utf8mb4"
    return DatabaseConfig(
        host=parsed.hostname or "127.0.0.1",
        port=parsed.port or 3306,
        user=unquote(parsed.username or ""),
        password=unquote(parsed.password or ""),
        database=database,
        charset=charset,
    )

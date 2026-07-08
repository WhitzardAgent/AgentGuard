"""Persistent mapping from platform sessions to AgentGuard sessions."""
from __future__ import annotations

import json
import secrets
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from backend.database import MySQLDatabase, get_database


@dataclass(frozen=True)
class RuntimeSessionMapping:
    id: int
    provider: str
    external_session_id: str
    agent_id: str
    agentguard_session_id: str
    user_id: int | None = None
    external_user_id: str | None = None
    account_email: str | None = None
    metadata_json: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    last_seen_at: datetime | None = None


class SessionMappingStore:
    def __init__(self, db: MySQLDatabase | None = None) -> None:
        self.db = db or get_database()

    def ensure_schema(self) -> None:
        for statement in _SCHEMA:
            self.db.execute(statement)
        self._ensure_user_id_column()

    def _ensure_user_id_column(self) -> None:
        column = self.db.fetchone("SHOW COLUMNS FROM external_runtime_sessions LIKE 'user_id'")
        if column is not None:
            return
        self.db.execute(
            """
            ALTER TABLE external_runtime_sessions
              ADD COLUMN user_id INT NULL AFTER agentguard_session_id,
              ADD INDEX idx_external_runtime_sessions_user_id (user_id),
              ADD CONSTRAINT fk_external_runtime_sessions_user
                FOREIGN KEY (user_id) REFERENCES users(id)
                ON DELETE SET NULL
            """
        )

    def get_or_create(
        self,
        *,
        provider: str,
        external_session_id: str,
        agent_id: str,
        user_id: int | None = None,
        external_user_id: str | None = None,
        account_email: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> RuntimeSessionMapping:
        clean_provider = _normalize_provider(provider)
        clean_external_session_id = _normalize_external_session_id(external_session_id)
        clean_agent_id = _normalize_agent_id(agent_id)
        existing = self._find(
            provider=clean_provider,
            external_session_id=clean_external_session_id,
            agent_id=clean_agent_id,
        )
        metadata_json = _metadata_json(metadata)
        if existing is not None:
            self.db.execute(
                """
                UPDATE external_runtime_sessions
                SET user_id = COALESCE(%s, user_id),
                    external_user_id = COALESCE(%s, external_user_id),
                    account_email = COALESCE(%s, account_email),
                    metadata_json = COALESCE(%s, metadata_json),
                    last_seen_at = UTC_TIMESTAMP(),
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = %s
                """,
                (
                    _optional_int(user_id),
                    _optional_text(external_user_id),
                    _normalize_email_or_none(account_email),
                    metadata_json,
                    existing.id,
                ),
            )
            current = self._find(
                provider=clean_provider,
                external_session_id=clean_external_session_id,
                agent_id=clean_agent_id,
            )
            if current is not None:
                return current

        agentguard_session_id = _new_agentguard_session_id(clean_provider)
        mapping_id = self.db.insert(
            """
            INSERT INTO external_runtime_sessions (
              provider, external_session_id, agent_id, agentguard_session_id,
              user_id, external_user_id, account_email, metadata_json, last_seen_at
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, UTC_TIMESTAMP())
            """,
            (
                clean_provider,
                clean_external_session_id,
                clean_agent_id,
                agentguard_session_id,
                _optional_int(user_id),
                _optional_text(external_user_id),
                _normalize_email_or_none(account_email),
                metadata_json,
            ),
        )
        row = self.db.fetchone(
            """
            SELECT id, provider, external_session_id, agent_id,
                   agentguard_session_id, user_id, external_user_id, account_email,
                   metadata_json, created_at, updated_at, last_seen_at
            FROM external_runtime_sessions
            WHERE id = %s
            """,
            (mapping_id,),
        )
        return _mapping_from_row(row)

    def _find(
        self,
        *,
        provider: str,
        external_session_id: str,
        agent_id: str,
    ) -> RuntimeSessionMapping | None:
        row = self.db.fetchone(
            """
            SELECT id, provider, external_session_id, agent_id,
                   agentguard_session_id, user_id, external_user_id, account_email,
                   metadata_json, created_at, updated_at, last_seen_at
            FROM external_runtime_sessions
            WHERE provider = %s AND external_session_id = %s AND agent_id = %s
            """,
            (provider, external_session_id, agent_id),
        )
        return _mapping_from_row(row) if row else None


def ensure_session_mapping_schema() -> None:
    SessionMappingStore().ensure_schema()


def get_session_mapping_store() -> SessionMappingStore:
    return SessionMappingStore()


_SCHEMA = [
    """
    CREATE TABLE IF NOT EXISTS external_runtime_sessions (
      id INT AUTO_INCREMENT PRIMARY KEY,
      provider VARCHAR(64) NOT NULL,
      external_session_id VARCHAR(255) NOT NULL,
      agent_id VARCHAR(255) NOT NULL,
      agentguard_session_id VARCHAR(255) NOT NULL UNIQUE,
      user_id INT NULL,
      external_user_id VARCHAR(255) NULL,
      account_email VARCHAR(255) NULL,
      metadata_json JSON NULL,
      created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
      updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
      last_seen_at TIMESTAMP NULL,
      UNIQUE KEY uniq_external_runtime_session (provider, external_session_id, agent_id),
      INDEX idx_external_runtime_sessions_user_id (user_id),
      INDEX idx_external_runtime_sessions_agent_id (agent_id),
      INDEX idx_external_runtime_sessions_account_email (provider, account_email),
      CONSTRAINT fk_external_runtime_sessions_user
        FOREIGN KEY (user_id) REFERENCES users(id)
        ON DELETE SET NULL
    )
    """,
]


def _normalize_provider(value: str) -> str:
    normalized = str(value or "").strip().lower()
    if not normalized:
        raise ValueError("provider is required")
    if len(normalized) > 64:
        raise ValueError("provider must be at most 64 characters")
    return normalized


def _normalize_external_session_id(value: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError("external_session_id is required")
    if len(normalized) > 255:
        raise ValueError("external_session_id must be at most 255 characters")
    return normalized


def _normalize_agent_id(value: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError("agent_id is required")
    if len(normalized) > 255:
        raise ValueError("agent_id must be at most 255 characters")
    return normalized


def _normalize_email_or_none(value: str | None) -> str | None:
    text = _optional_text(value)
    if not text:
        return None
    return text.lower()


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    return int(value)


def _metadata_json(value: dict[str, Any] | None) -> str | None:
    if not value:
        return None
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _new_agentguard_session_id(provider: str) -> str:
    return f"ags_{provider}_{secrets.token_urlsafe(18)}"


def _mapping_from_row(row: dict[str, Any] | None) -> RuntimeSessionMapping:
    if row is None:
        raise ValueError("session mapping row is required")
    return RuntimeSessionMapping(
        id=int(row["id"]),
        provider=str(row["provider"]),
        external_session_id=str(row["external_session_id"]),
        agent_id=str(row["agent_id"]),
        agentguard_session_id=str(row["agentguard_session_id"]),
        user_id=_optional_int(row.get("user_id")) if row.get("user_id") is not None else None,
        external_user_id=_optional_text(row.get("external_user_id")),
        account_email=_optional_text(row.get("account_email")),
        metadata_json=_optional_text(row.get("metadata_json")),
        created_at=_coerce_datetime(row["created_at"]) if row.get("created_at") else None,
        updated_at=_coerce_datetime(row["updated_at"]) if row.get("updated_at") else None,
        last_seen_at=_coerce_datetime(row["last_seen_at"]) if row.get("last_seen_at") else None,
    )


def _coerce_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    return datetime.fromisoformat(str(value)).replace(tzinfo=timezone.utc)

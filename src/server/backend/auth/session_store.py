"""Persistent runtime session/token store."""
from __future__ import annotations

import json
import secrets
from datetime import datetime, timezone
from typing import Any

from backend.auth.models import RuntimeSession, RuntimeSessionSummary, RuntimeToken
from backend.database import MySQLDatabase, get_database


class RuntimeSessionStore:
    def __init__(self, db: MySQLDatabase | None = None) -> None:
        self.db = db or get_database()

    def ensure_schema(self) -> None:
        for statement in _SCHEMA:
            self.db.execute(statement)
        self._ensure_nullable_external_session_id()

    def find_active_external_session(
        self,
        *,
        provider: str,
        external_session_id: str,
        agent_id: str,
    ) -> RuntimeSession | None:
        row = self.db.fetchone(
            """
            SELECT session_id, agent_id, user_id, provider, external_session_id,
                   external_account_email, dpop_jkt, status, metadata_json,
                   created_at, last_seen_at, closed_at
            FROM runtime_sessions
            WHERE provider = %s
              AND external_session_id = %s
              AND agent_id = %s
              AND status = 'active'
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (_normalize_provider(provider), external_session_id, agent_id),
        )
        return _session_from_row(row) if row else None

    def create_session(
        self,
        *,
        agent_id: str,
        user_id: int,
        provider: str,
        external_session_id: str | None,
        external_account_email: str | None,
        dpop_jkt: str,
        metadata: dict[str, Any] | None = None,
    ) -> RuntimeSession:
        session_id = f"ags_{_normalize_provider(provider)}_{secrets.token_urlsafe(18)}"
        self.db.insert(
            """
            INSERT INTO runtime_sessions (
              session_id, agent_id, user_id, provider, external_session_id,
              external_account_email, dpop_jkt, status, metadata_json, last_seen_at
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, 'active', %s, UTC_TIMESTAMP())
            """,
            (
                session_id,
                agent_id,
                int(user_id),
                _normalize_provider(provider),
                _optional_text(external_session_id),
                _normalize_email_or_none(external_account_email),
                dpop_jkt,
                _metadata_json(metadata),
            ),
        )
        current = self.get_session(session_id)
        if current is None:
            raise RuntimeError("failed to create runtime session")
        return current

    def get_session(self, session_id: str) -> RuntimeSession | None:
        row = self.db.fetchone(
            """
            SELECT session_id, agent_id, user_id, provider, external_session_id,
                   external_account_email, dpop_jkt, status, metadata_json,
                   created_at, last_seen_at, closed_at
            FROM runtime_sessions
            WHERE session_id = %s
            """,
            (session_id,),
        )
        return _session_from_row(row) if row else None

    def list_sessions(
        self,
        *,
        agent_id: str | None = None,
        user_id: int | None = None,
        status: str | None = None,
        limit: int = 50,
    ) -> list[RuntimeSessionSummary]:
        filters: list[str] = []
        params: list[Any] = []
        if agent_id:
            filters.append("s.agent_id = %s")
            params.append(agent_id)
        if user_id is not None:
            filters.append("s.user_id = %s")
            params.append(int(user_id))
        normalized_status = _normalize_status_filter(status)
        if normalized_status:
            filters.append("s.status = %s")
            params.append(normalized_status)
        where = f"WHERE {' AND '.join(filters)}" if filters else ""
        params.append(_clamp_limit(limit))
        rows = self.db.fetchall(
            f"""
            SELECT
              s.session_id, s.agent_id, s.user_id, s.provider, s.external_session_id,
              s.external_account_email, s.dpop_jkt, s.status, s.metadata_json,
              s.created_at, s.last_seen_at, s.closed_at,
              COALESCE(SUM(CASE WHEN t.status = 'active' THEN 1 ELSE 0 END), 0) AS active_token_count,
              MAX(CASE WHEN t.status = 'active' THEN t.expires_at ELSE NULL END) AS latest_token_expires_at,
              MAX(t.issued_at) AS latest_token_issued_at
            FROM runtime_sessions s
            LEFT JOIN runtime_tokens t ON t.session_id = s.session_id
            {where}
            GROUP BY
              s.session_id, s.agent_id, s.user_id, s.provider, s.external_session_id,
              s.external_account_email, s.dpop_jkt, s.status, s.metadata_json,
              s.created_at, s.last_seen_at, s.closed_at
            ORDER BY COALESCE(s.last_seen_at, s.created_at) DESC, s.created_at DESC
            LIMIT %s
            """,
            tuple(params),
        )
        return [_session_summary_from_row(row) for row in rows]

    def touch_session(self, session_id: str) -> None:
        self.db.execute(
            "UPDATE runtime_sessions SET last_seen_at = UTC_TIMESTAMP() WHERE session_id = %s",
            (session_id,),
        )

    def close_session(self, session_id: str) -> None:
        self.db.execute(
            """
            UPDATE runtime_sessions
            SET status = 'closed', closed_at = UTC_TIMESTAMP()
            WHERE session_id = %s AND status = 'active'
            """,
            (session_id,),
        )
        self.db.execute(
            """
            UPDATE runtime_tokens
            SET status = 'revoked', revoked_at = UTC_TIMESTAMP()
            WHERE session_id = %s AND status = 'active'
            """,
            (session_id,),
        )

    def create_token(
        self,
        *,
        token_jti: str,
        session_id: str,
        expires_at_epoch: int,
        cnf_jkt: str,
    ) -> RuntimeToken:
        self.db.insert(
            """
            INSERT INTO runtime_tokens (token_jti, session_id, expires_at, cnf_jkt, status)
            VALUES (%s, %s, FROM_UNIXTIME(%s), %s, 'active')
            """,
            (token_jti, session_id, int(expires_at_epoch), cnf_jkt),
        )
        current = self.get_token(token_jti)
        if current is None:
            raise RuntimeError("failed to create runtime token")
        return current

    def get_token(self, token_jti: str) -> RuntimeToken | None:
        row = self.db.fetchone(
            """
            SELECT token_jti, session_id, issued_at, expires_at, revoked_at, cnf_jkt, status
            FROM runtime_tokens
            WHERE token_jti = %s
            """,
            (token_jti,),
        )
        return _token_from_row(row) if row else None

    def revoke_token(self, token_jti: str) -> None:
        self.db.execute(
            """
            UPDATE runtime_tokens
            SET status = 'revoked', revoked_at = UTC_TIMESTAMP()
            WHERE token_jti = %s AND status = 'active'
            """,
            (token_jti,),
        )

    def _ensure_nullable_external_session_id(self) -> None:
        column = self.db.fetchone("SHOW COLUMNS FROM runtime_sessions LIKE 'external_session_id'")
        if column and str(column.get("Null") or "").upper() == "NO":
            self.db.execute("ALTER TABLE runtime_sessions MODIFY external_session_id VARCHAR(255) NULL")
        column = self.db.fetchone("SHOW COLUMNS FROM runtime_sessions LIKE 'external_account_email'")
        if column and str(column.get("Null") or "").upper() == "NO":
            self.db.execute("ALTER TABLE runtime_sessions MODIFY external_account_email VARCHAR(255) NULL")


def ensure_runtime_session_schema() -> None:
    RuntimeSessionStore().ensure_schema()


def get_runtime_session_store() -> RuntimeSessionStore:
    return RuntimeSessionStore()


_SCHEMA = [
    """
    CREATE TABLE IF NOT EXISTS runtime_sessions (
      session_id VARCHAR(255) PRIMARY KEY,
      agent_id VARCHAR(255) NOT NULL,
      user_id INT NOT NULL,
      provider VARCHAR(64) NOT NULL,
      external_session_id VARCHAR(255) NULL,
      external_account_email VARCHAR(255) NULL,
      dpop_jkt VARCHAR(255) NOT NULL,
      status VARCHAR(32) NOT NULL DEFAULT 'active',
      metadata_json JSON NULL,
      created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
      last_seen_at TIMESTAMP NULL,
      closed_at TIMESTAMP NULL,
      INDEX idx_runtime_sessions_external (provider, external_session_id, agent_id),
      INDEX idx_runtime_sessions_user_id (user_id),
      INDEX idx_runtime_sessions_status (status),
      CONSTRAINT fk_runtime_sessions_user
        FOREIGN KEY (user_id) REFERENCES users(id)
        ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS runtime_tokens (
      token_jti VARCHAR(255) PRIMARY KEY,
      session_id VARCHAR(255) NOT NULL,
      issued_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
      expires_at TIMESTAMP NOT NULL,
      revoked_at TIMESTAMP NULL,
      cnf_jkt VARCHAR(255) NOT NULL,
      status VARCHAR(32) NOT NULL DEFAULT 'active',
      INDEX idx_runtime_tokens_session_id (session_id),
      INDEX idx_runtime_tokens_expires_at (expires_at),
      CONSTRAINT fk_runtime_tokens_session
        FOREIGN KEY (session_id) REFERENCES runtime_sessions(session_id)
        ON DELETE CASCADE
    )
    """,
]


def _session_from_row(row: dict[str, Any]) -> RuntimeSession:
    return RuntimeSession(
        session_id=str(row["session_id"]),
        agent_id=str(row["agent_id"]),
        user_id=int(row["user_id"]),
        provider=str(row["provider"]),
        external_session_id=_optional_text(row.get("external_session_id")),
        external_account_email=_optional_text(row.get("external_account_email")),
        dpop_jkt=str(row["dpop_jkt"]),
        status=str(row["status"]),
        metadata_json=_optional_text(row.get("metadata_json")),
        created_at=_coerce_datetime(row.get("created_at")) if row.get("created_at") else None,
        last_seen_at=_coerce_datetime(row.get("last_seen_at")) if row.get("last_seen_at") else None,
        closed_at=_coerce_datetime(row.get("closed_at")) if row.get("closed_at") else None,
    )


def _token_from_row(row: dict[str, Any]) -> RuntimeToken:
    return RuntimeToken(
        token_jti=str(row["token_jti"]),
        session_id=str(row["session_id"]),
        issued_at=_coerce_datetime(row.get("issued_at")) if row.get("issued_at") else None,
        expires_at=_coerce_datetime(row["expires_at"]),
        revoked_at=_coerce_datetime(row.get("revoked_at")) if row.get("revoked_at") else None,
        cnf_jkt=str(row["cnf_jkt"]),
        status=str(row["status"]),
    )


def _session_summary_from_row(row: dict[str, Any]) -> RuntimeSessionSummary:
    return RuntimeSessionSummary(
        session=_session_from_row(row),
        active_token_count=int(row.get("active_token_count") or 0),
        latest_token_expires_at=(
            _coerce_datetime(row["latest_token_expires_at"])
            if row.get("latest_token_expires_at")
            else None
        ),
        latest_token_issued_at=(
            _coerce_datetime(row["latest_token_issued_at"])
            if row.get("latest_token_issued_at")
            else None
        ),
    )


def _metadata_json(value: dict[str, Any] | None) -> str | None:
    if not value:
        return None
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _normalize_provider(value: str) -> str:
    return str(value or "").strip().lower()


def _normalize_email(value: str) -> str:
    return str(value or "").strip().lower()


def _normalize_email_or_none(value: Any) -> str | None:
    text = _optional_text(value)
    return text.lower() if text else None


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _normalize_status_filter(value: str | None) -> str | None:
    normalized = str(value or "").strip().lower()
    if normalized in {"", "all", "*"}:
        return None
    if normalized in {"active", "closed"}:
        return normalized
    return "active"


def _clamp_limit(value: int) -> int:
    try:
        limit = int(value)
    except (TypeError, ValueError):
        return 50
    return max(1, min(limit, 200))


def _coerce_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    return datetime.fromisoformat(str(value)).replace(tzinfo=timezone.utc)

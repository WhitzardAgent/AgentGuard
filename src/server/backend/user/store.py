"""Persistent user store and short-lived ticket handling."""
from __future__ import annotations

import hashlib
import os
import secrets
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from backend.database import DatabaseUnavailable, MySQLDatabase, get_database
from backend.user.passwords import hash_password, verify_password

SESSION_TTL_ENV = "AGENTGUARD_USER_SESSION_TTL_SECONDS"
TICKET_TTL_ENV = "AGENTGUARD_USER_TICKET_TTL_SECONDS"
DEFAULT_SESSION_TTL_SECONDS = 86_400
DEFAULT_TICKET_TTL_SECONDS = 300


@dataclass(frozen=True)
class User:
    id: int
    username: str
    profile_json: str | None = None


@dataclass(frozen=True)
class SessionIssue:
    token: str
    expires_at: datetime
    user: User


@dataclass(frozen=True)
class TicketIssue:
    ticket: str
    expires_at: datetime
    prefix: str


@dataclass(frozen=True)
class UserTicketIdentity:
    user_id: int
    username: str
    ticket_id: int
    ticket_prefix: str
    expires_at: datetime


class DuplicateUsername(ValueError):
    pass


class InvalidCredentials(ValueError):
    pass


class InvalidUserTicket(PermissionError):
    pass


class UserStore:
    def __init__(self, db: MySQLDatabase | None = None) -> None:
        self.db = db or get_database()

    def ensure_schema(self) -> None:
        for statement in _SCHEMA:
            self.db.execute(statement)

    def create_user(self, username: str, password: str) -> User:
        normalized = _normalize_username(username)
        _validate_password(password)
        try:
            user_id = self.db.insert(
                """
                INSERT INTO users (username, password_hash)
                VALUES (%s, %s)
                """,
                (normalized, hash_password(password)),
            )
        except Exception as exc:
            if _is_duplicate_key(exc):
                raise DuplicateUsername(f"username already exists: {normalized}") from exc
            raise
        return User(id=user_id, username=normalized)

    def authenticate(self, username: str, password: str) -> User:
        normalized = _normalize_username(username)
        row = self.db.fetchone(
            "SELECT id, username, password_hash, profile_json FROM users WHERE username = %s",
            (normalized,),
        )
        if not row or not verify_password(password, str(row.get("password_hash") or "")):
            raise InvalidCredentials("invalid username or password")
        return _user_from_row(row)

    def create_web_session(self, user: User) -> SessionIssue:
        ttl = _int_env(SESSION_TTL_ENV, DEFAULT_SESSION_TTL_SECONDS)
        token = _new_token("ags")
        token_hash = _hash_token(token)
        session_id = self.db.insert(
            """
            INSERT INTO user_sessions (user_id, token_hash, expires_at)
            VALUES (%s, %s, DATE_ADD(UTC_TIMESTAMP(), INTERVAL %s SECOND))
            """,
            (user.id, token_hash, ttl),
        )
        row = self.db.fetchone(
            "SELECT expires_at FROM user_sessions WHERE id = %s",
            (session_id,),
        )
        return SessionIssue(
            token=token,
            expires_at=_datetime_from_row(row, "expires_at"),
            user=user,
        )

    def user_for_session(self, token: str | None) -> User | None:
        if not token:
            return None
        row = self.db.fetchone(
            """
            SELECT u.id, u.username, u.profile_json
            FROM user_sessions s
            JOIN users u ON u.id = s.user_id
            WHERE s.token_hash = %s
              AND s.revoked_at IS NULL
              AND s.expires_at > UTC_TIMESTAMP()
            """,
            (_hash_token(token),),
        )
        return _user_from_row(row) if row else None

    def revoke_session(self, token: str | None) -> None:
        if not token:
            return
        self.db.execute(
            """
            UPDATE user_sessions
            SET revoked_at = UTC_TIMESTAMP()
            WHERE token_hash = %s AND revoked_at IS NULL
            """,
            (_hash_token(token),),
        )

    def create_ticket(self, user: User) -> TicketIssue:
        ttl = _int_env(TICKET_TTL_ENV, DEFAULT_TICKET_TTL_SECONDS)
        ticket = _new_token("agt")
        prefix = ticket[:16]
        ticket_id = self.db.insert(
            """
            INSERT INTO user_tickets (user_id, ticket_hash, ticket_prefix, expires_at)
            VALUES (%s, %s, %s, DATE_ADD(UTC_TIMESTAMP(), INTERVAL %s SECOND))
            """,
            (user.id, _hash_token(ticket), prefix, ttl),
        )
        row = self.db.fetchone(
            "SELECT expires_at FROM user_tickets WHERE id = %s",
            (ticket_id,),
        )
        return TicketIssue(
            ticket=ticket,
            expires_at=_datetime_from_row(row, "expires_at"),
            prefix=prefix,
        )

    def list_tickets(self, user: User, *, limit: int = 20) -> list[dict[str, Any]]:
        return self.db.fetchall(
            """
            SELECT id, ticket_prefix, expires_at, created_at, last_used_at,
                   expires_at <= UTC_TIMESTAMP() AS expired
            FROM user_tickets
            WHERE user_id = %s
            ORDER BY created_at DESC
            LIMIT %s
            """,
            (user.id, max(1, min(limit, 100))),
        )

    def resolve_ticket(self, ticket: str | None) -> UserTicketIdentity | None:
        if not ticket:
            return None
        row = self.db.fetchone(
            """
            SELECT t.id AS ticket_id, t.ticket_prefix, t.expires_at,
                   u.id AS user_id, u.username
            FROM user_tickets t
            JOIN users u ON u.id = t.user_id
            WHERE t.ticket_hash = %s
              AND t.expires_at > UTC_TIMESTAMP()
            """,
            (_hash_token(ticket),),
        )
        if not row:
            raise InvalidUserTicket("invalid or expired user ticket")
        self.db.execute(
            "UPDATE user_tickets SET last_used_at = UTC_TIMESTAMP() WHERE id = %s",
            (row["ticket_id"],),
        )
        return UserTicketIdentity(
            user_id=int(row["user_id"]),
            username=str(row["username"]),
            ticket_id=int(row["ticket_id"]),
            ticket_prefix=str(row["ticket_prefix"]),
            expires_at=_coerce_datetime(row["expires_at"]),
        )


def ensure_user_schema() -> None:
    UserStore().ensure_schema()


def resolve_user_ticket(ticket: str | None) -> UserTicketIdentity | None:
    if not ticket:
        return None
    try:
        return UserStore().resolve_ticket(ticket)
    except DatabaseUnavailable as exc:
        raise InvalidUserTicket("user ticket validation is unavailable") from exc


_SCHEMA = [
    """
    CREATE TABLE IF NOT EXISTS users (
      id INT AUTO_INCREMENT PRIMARY KEY,
      username VARCHAR(255) NOT NULL UNIQUE,
      password_hash VARCHAR(255) NOT NULL,
      profile_json JSON NULL,
      created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
      updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS user_sessions (
      id INT AUTO_INCREMENT PRIMARY KEY,
      user_id INT NOT NULL,
      token_hash CHAR(64) NOT NULL UNIQUE,
      expires_at TIMESTAMP NOT NULL,
      created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
      revoked_at TIMESTAMP NULL,
      INDEX idx_user_sessions_user_id (user_id),
      INDEX idx_user_sessions_expires_at (expires_at),
      CONSTRAINT fk_user_sessions_user
        FOREIGN KEY (user_id) REFERENCES users(id)
        ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS user_tickets (
      id INT AUTO_INCREMENT PRIMARY KEY,
      user_id INT NOT NULL,
      ticket_hash CHAR(64) NOT NULL UNIQUE,
      ticket_prefix VARCHAR(16) NOT NULL,
      expires_at TIMESTAMP NOT NULL,
      created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
      last_used_at TIMESTAMP NULL,
      INDEX idx_user_tickets_user_id (user_id),
      INDEX idx_user_tickets_expires_at (expires_at),
      CONSTRAINT fk_user_tickets_user
        FOREIGN KEY (user_id) REFERENCES users(id)
        ON DELETE CASCADE
    )
    """,
]


def _normalize_username(username: str) -> str:
    normalized = str(username or "").strip()
    if len(normalized) < 3:
        raise ValueError("username must be at least 3 characters")
    if len(normalized) > 255:
        raise ValueError("username must be at most 255 characters")
    return normalized


def _validate_password(password: str) -> None:
    if len(str(password or "")) < 8:
        raise ValueError("password must be at least 8 characters")


def _hash_token(token: str) -> str:
    return hashlib.sha256(str(token or "").encode("utf-8")).hexdigest()


def _new_token(prefix: str) -> str:
    return f"{prefix}_{secrets.token_urlsafe(32)}"


def _int_env(name: str, default: int) -> int:
    try:
        value = int(os.environ.get(name, "").strip() or default)
    except ValueError:
        return default
    return max(1, value)


def _is_duplicate_key(exc: Exception) -> bool:
    code = getattr(exc, "args", [None])[0]
    return code == 1062


def _user_from_row(row: dict[str, Any]) -> User:
    return User(
        id=int(row["id"]),
        username=str(row["username"]),
        profile_json=row.get("profile_json"),
    )


def _datetime_from_row(row: dict[str, Any] | None, key: str) -> datetime:
    if not row or row.get(key) is None:
        return datetime.now(timezone.utc)
    return _coerce_datetime(row[key])


def _coerce_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    return datetime.fromisoformat(str(value)).replace(tzinfo=timezone.utc)

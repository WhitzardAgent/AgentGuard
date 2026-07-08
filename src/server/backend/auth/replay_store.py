"""Replay cache for DPoP proof jti values."""
from __future__ import annotations

from backend.database import MySQLDatabase, get_database


class DPoPReplayError(PermissionError):
    pass


class DPoPReplayStore:
    def __init__(self, db: MySQLDatabase | None = None) -> None:
        self.db = db or get_database()

    def ensure_schema(self) -> None:
        for statement in _SCHEMA:
            self.db.execute(statement)

    def remember(self, *, jkt: str, jti: str, ttl_seconds: int = 300) -> None:
        self.db.execute("DELETE FROM dpop_replay_nonces WHERE expires_at <= UTC_TIMESTAMP()")
        try:
            self.db.insert(
                """
                INSERT INTO dpop_replay_nonces (jkt, jti, expires_at)
                VALUES (%s, %s, DATE_ADD(UTC_TIMESTAMP(), INTERVAL %s SECOND))
                """,
                (jkt, jti, ttl_seconds),
            )
        except Exception as exc:
            if _is_duplicate_key(exc):
                raise DPoPReplayError("DPoP proof replay detected") from exc
            raise


def ensure_replay_schema() -> None:
    DPoPReplayStore().ensure_schema()


def get_replay_store() -> DPoPReplayStore:
    return DPoPReplayStore()


_SCHEMA = [
    """
    CREATE TABLE IF NOT EXISTS dpop_replay_nonces (
      id INT AUTO_INCREMENT PRIMARY KEY,
      jkt VARCHAR(255) NOT NULL,
      jti VARCHAR(255) NOT NULL,
      expires_at TIMESTAMP NOT NULL,
      created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
      UNIQUE KEY uniq_dpop_replay_jkt_jti (jkt, jti),
      INDEX idx_dpop_replay_expires_at (expires_at)
    )
    """,
]


def _is_duplicate_key(exc: Exception) -> bool:
    text = str(exc).lower()
    return "duplicate" in text or "1062" in text


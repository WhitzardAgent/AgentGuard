"""Runtime auth data models."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class RuntimeSession:
    session_id: str
    agent_id: str
    user_id: int
    provider: str
    external_session_id: str | None
    external_account_email: str | None
    dpop_jkt: str
    status: str
    metadata_json: str | None = None
    created_at: datetime | None = None
    last_seen_at: datetime | None = None
    closed_at: datetime | None = None


@dataclass(frozen=True)
class RuntimeToken:
    token_jti: str
    session_id: str
    expires_at: datetime
    cnf_jkt: str
    status: str
    issued_at: datetime | None = None
    revoked_at: datetime | None = None


@dataclass(frozen=True)
class RuntimeSessionSummary:
    session: RuntimeSession
    active_token_count: int = 0
    latest_token_expires_at: datetime | None = None
    latest_token_issued_at: datetime | None = None


@dataclass(frozen=True)
class AuthContext:
    session_id: str
    agent_id: str
    user_id: str
    token_jti: str
    dpop_jkt: str
    auth_method: str = "dpop_session_token"
    external_provider: str | None = None
    external_session_id: str | None = None
    scope: list[str] | None = None
    raw_claims: dict[str, Any] | None = None

    def to_metadata(self) -> dict[str, Any]:
        metadata = {
            "auth_method": self.auth_method,
            "token_jti": self.token_jti,
            "dpop_jkt": self.dpop_jkt,
        }
        if self.external_provider:
            metadata["external_provider"] = self.external_provider
        if self.external_session_id:
            metadata["external_session_id"] = self.external_session_id
        return metadata

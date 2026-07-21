"""API schemas for agent-wide security audits."""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class SecurityAuditLLMConfig(BaseModel):
    model: str | None = Field(default=None, max_length=255)
    base_url: str | None = Field(default=None, max_length=2048)
    api_key: str | None = Field(default=None, max_length=4096)
    timeout_s: float | None = Field(default=None, ge=1, le=600)
    chunk_events: int | None = Field(default=None, ge=1, le=500)


class SecurityAuditCreateRequest(BaseModel):
    agent_id: str = Field(min_length=1, max_length=255)
    auditor_name: str = Field(default="hybrid_agent_security", max_length=128)
    start_at: datetime | None = None
    end_at: datetime | None = None
    llm_config: SecurityAuditLLMConfig | None = None


__all__ = ["SecurityAuditCreateRequest", "SecurityAuditLLMConfig"]

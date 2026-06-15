"""Pydantic request/response models for the server API."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class GuardDecideRequest(BaseModel):
    request_id: str = "req_unknown"
    current_event: dict[str, Any]
    context: dict[str, Any] = Field(default_factory=dict)
    trajectory_window: list[dict[str, Any]] = Field(default_factory=list)
    local_signals: list[str] = Field(default_factory=list)
    policy_version: str | None = None
    plugin_extensions: dict[str, Any] = Field(default_factory=dict)
    client_cached_entries: list[dict[str, Any]] = Field(default_factory=list)


class GuardDecideResponse(BaseModel):
    decision: dict[str, Any]
    risk_signals: list[str] = Field(default_factory=list)
    checker_result: dict[str, Any] = Field(default_factory=dict)
    plugin_results: dict[str, Any] = Field(default_factory=dict)


class TraceUploadRequest(BaseModel):
    session_id: str | None = None
    agent_id: str | None = None
    user_id: str | None = None
    reason: str | None = None
    entries: list[dict[str, Any]] = Field(default_factory=list)


class ToolReportRequest(BaseModel):
    context: dict[str, Any] = Field(default_factory=dict)
    tool: dict[str, Any] = Field(default_factory=dict)


class SessionRegisterRequest(BaseModel):
    context: dict[str, Any] = Field(default_factory=dict)


class CheckerConfigUpdateRequest(BaseModel):
    config: dict[str, Any]
    client_config: dict[str, Any] | None = None
    client_config_urls: list[str] = Field(default_factory=list)
    client_principals: list[dict[str, Any]] = Field(default_factory=list)
    timeout_s: float = 2.0


class CheckerConfigUpdateResponse(BaseModel):
    status: str
    loaded_checkers: list[str] = Field(default_factory=list)
    client_updates: list[dict[str, Any]] = Field(default_factory=list)


class AgentCheckerConfigUpdateRequest(BaseModel):
    config: dict[str, Any]
    client_config: dict[str, Any] | None = None
    timeout_s: float = 2.0


class AgentCheckerSessionConfig(BaseModel):
    session_id: str
    agent_id: str | None = None
    user_id: str | None = None
    last_seen: float | None = None
    client_config_url: str | None = None
    client_checker_config: dict[str, Any] | None = None
    remote_checker_config: dict[str, Any] | None = None


class AgentCheckerConfigResponse(BaseModel):
    agent_id: str
    session_count: int = 0
    config_status: Literal["none", "consistent", "mixed"] = "none"
    client_checker_config: dict[str, Any] | None = None
    remote_checker_config: dict[str, Any] | None = None
    sessions: list[AgentCheckerSessionConfig] = Field(default_factory=list)


class CheckerOption(BaseModel):
    name: str
    description: str = ""
    event_types: list[str] = Field(default_factory=list)


class AgentCheckerAvailableResponse(BaseModel):
    agent_id: str
    local_checkers: list[CheckerOption] = Field(default_factory=list)
    remote_checkers: list[CheckerOption] = Field(default_factory=list)


class SkillRunRequest(BaseModel):
    skill_name: str
    input: dict[str, Any] = Field(default_factory=dict)


class TraceAuditRequest(BaseModel):
    session_id: str
    agent_id: str | None = None
    user_id: str | None = None
    auditor_name: str


class TraceAuditResponse(BaseModel):
    session_id: str
    agent_id: str | None = None
    user_id: str | None = None
    auditor_name: str
    level: Literal["critical", "high", "warning", "ok"]
    reason: str
    trace_entries: int = 0
    metadata: dict[str, Any] = Field(default_factory=dict)

"""Pydantic request/response models for the server API."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class _ApiModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")


class GuardDecideRequest(_ApiModel):
    request_id: str = "req_unknown"
    current_event: dict[str, Any]
    context: dict[str, Any] = Field(default_factory=dict)
    trajectory_window: list[dict[str, Any]] = Field(default_factory=list)
    local_signals: list[str] = Field(default_factory=list)
    policy_version: str | None = None
    extensions: dict[str, Any] = Field(default_factory=dict)
    client_cached_entries: list[dict[str, Any]] = Field(default_factory=list)


class GuardDecideResponse(_ApiModel):
    decision: dict[str, Any]
    risk_signals: list[str] = Field(default_factory=list)
    plugin_result: dict[str, Any] = Field(default_factory=dict)


class TraceUploadRequest(_ApiModel):
    session_id: str | None = None
    agent_id: str | None = None
    user_id: str | None = None
    reason: str | None = None
    entries: list[dict[str, Any]] = Field(default_factory=list)


class ToolReportRequest(_ApiModel):
    context: dict[str, Any] = Field(default_factory=dict)
    tool: dict[str, Any] = Field(default_factory=dict)


class SessionRegisterRequest(_ApiModel):
    context: dict[str, Any] = Field(default_factory=dict)


class PluginConfigUpdateRequest(_ApiModel):
    config: dict[str, Any]
    client_config: dict[str, Any] | None = None
    client_config_urls: list[str] = Field(default_factory=list)
    client_principals: list[dict[str, Any]] = Field(default_factory=list)
    timeout_s: float = 2.0


class PluginConfigUpdateResponse(_ApiModel):
    status: str
    loaded_plugins: list[str] = Field(default_factory=list)
    client_updates: list[dict[str, Any]] = Field(default_factory=list)


class AgentPluginConfigUpdateRequest(_ApiModel):
    config: dict[str, Any]
    client_config: dict[str, Any] | None = None
    timeout_s: float = 2.0


class AgentPluginConfigResponse(_ApiModel):
    agent_id: str
    plugin_config: dict[str, Any] | None = None
    config_source: Literal["agent_override", "server_default", "none"] = "none"


class PluginOption(_ApiModel):
    name: str
    description: str = ""
    event_types: list[str] = Field(default_factory=list)
    phases: list[str] = Field(default_factory=list)


class AgentPluginAvailableResponse(_ApiModel):
    agent_id: str
    local_plugins: list[PluginOption] = Field(default_factory=list)
    remote_plugins: list[PluginOption] = Field(default_factory=list)


class SkillRunRequest(_ApiModel):
    skill_name: str
    input: dict[str, Any] = Field(default_factory=dict)


class TraceAuditRequest(_ApiModel):
    session_id: str
    agent_id: str | None = None
    user_id: str | None = None
    auditor_name: str


class TraceAuditResponse(_ApiModel):
    session_id: str
    agent_id: str | None = None
    user_id: str | None = None
    auditor_name: str
    level: Literal["critical", "high", "warning", "ok"]
    reason: str
    trace_entries: int = 0
    metadata: dict[str, Any] = Field(default_factory=dict)

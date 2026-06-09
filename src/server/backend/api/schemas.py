"""Pydantic request/response models for the server API."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class GuardDecideRequest(BaseModel):
    request_id: str = "req_unknown"
    current_event: dict[str, Any]
    context: dict[str, Any] = Field(default_factory=dict)
    trajectory_window: list[dict[str, Any]] = Field(default_factory=list)
    local_signals: list[str] = Field(default_factory=list)
    policy_version: str | None = None
    plugin_extensions: dict[str, Any] = Field(default_factory=dict)


class GuardDecideResponse(BaseModel):
    decision: dict[str, Any]
    risk_signals: list[str] = Field(default_factory=list)
    checker_result: dict[str, Any] = Field(default_factory=dict)
    plugin_results: dict[str, Any] = Field(default_factory=dict)


class TraceUploadRequest(BaseModel):
    session_id: str | None = None
    entries: list[dict[str, Any]] = Field(default_factory=list)


class SkillRunRequest(BaseModel):
    skill_name: str
    input: dict[str, Any] = Field(default_factory=dict)

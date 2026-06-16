"""Base checker interface and result type."""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Any

from agentguard.schemas.context import RuntimeContext
from agentguard.schemas.decisions import GuardDecision
from agentguard.schemas.events import EventType, RuntimeEvent


@dataclass
class CheckResult:
    decision_candidate: GuardDecision | None = None
    risk_signals: list[str] = field(default_factory=list)
    is_final: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    @staticmethod
    def empty() -> "CheckResult":
        return CheckResult()


class BaseChecker:
    """Local, non-networked risk checker for one or more event types."""

    name: str = "base"
    description: str = ""
    event_types: list[EventType] = []

    def __init__(self, *, env: dict[str, Any] | None = None, **kwargs: Any) -> None:
        self.bind_config(env=env, **kwargs)

    def bind_config(self, *, env: dict[str, Any] | None = None, **kwargs: Any) -> None:
        self.config = dict(kwargs)
        self.env_spec = dict(env or {})
        self.env = _resolve_env_mapping(self.env_spec)
        for key, value in self.config.items():
            setattr(self, key, value)
        for key, value in self.env.items():
            setattr(self, key, value)

    def applies(self, event: RuntimeEvent) -> bool:
        return not self.event_types or event.event_type in self.event_types

    def check(self, event: RuntimeEvent, context: RuntimeContext) -> CheckResult:
        raise NotImplementedError


_ENV_TOKEN_RE = re.compile(
    r"^\$(?:\{(?P<braced>[A-Za-z_][A-Za-z0-9_]*)\}|(?P<plain>[A-Za-z_][A-Za-z0-9_]*))$"
)
_ENV_NAME_RE = re.compile(r"^[A-Z_][A-Z0-9_]*$")


def _resolve_env_mapping(values: dict[str, Any]) -> dict[str, Any]:
    return {key: _resolve_env_value(value) for key, value in values.items()}


def _resolve_env_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _resolve_env_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_resolve_env_value(item) for item in value]
    if not isinstance(value, str):
        return value

    env_name = _env_name_of(value)
    if env_name is None:
        return value
    return os.environ.get(env_name)


def _env_name_of(value: str) -> str | None:
    match = _ENV_TOKEN_RE.fullmatch(value)
    if match is not None:
        return match.group("braced") or match.group("plain")
    if _ENV_NAME_RE.fullmatch(value) and value in os.environ:
        return value
    return None

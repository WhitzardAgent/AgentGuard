"""Structured exception hierarchy. No secrets in messages."""
from __future__ import annotations


class AgentGuardError(Exception):
    """Base error for all AgentGuard failures."""


class PolicyError(AgentGuardError):
    """Policy loading or evaluation failure."""


class RemoteGuardError(AgentGuardError):
    """Remote guard server communication failure."""


class AdapterError(AgentGuardError):
    """Adapter wiring failure, e.g. missing optional dependency."""


class SandboxError(AgentGuardError):
    """Sandbox execution boundary violation or failure."""


class PluginError(AgentGuardError):
    """Plugin load or execution failure."""


class SkillError(AgentGuardError):
    """Skill execution failure."""


class SchemaError(AgentGuardError):
    """Schema validation or (de)serialization failure."""

"""Capability vocabulary used by the sandbox and risk classifier."""

from __future__ import annotations

from enum import Enum


class Capability(str, Enum):
    NETWORK = "network"
    FILESYSTEM = "filesystem"
    SHELL = "shell"
    EXEC = "exec"
    DELETE = "delete"
    MEMORY = "memory"
    LLM = "llm"
    NONE = "none"


_SINK_TO_CAPABILITIES: dict[str, list[Capability]] = {
    "http": [Capability.NETWORK],
    "email": [Capability.NETWORK],
    "shell": [Capability.SHELL, Capability.EXEC],
    "fs_write": [Capability.FILESYSTEM],
    "db_write": [Capability.FILESYSTEM],
    "llm_out": [Capability.LLM],
    "none": [],
}


def capabilities_for_sink(sink_type: str) -> list[Capability]:
    return list(_SINK_TO_CAPABILITIES.get(sink_type, []))

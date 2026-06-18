"""Process-wide shared singletons for the server (manager + console state)."""
from __future__ import annotations

import os

from backend.console.state import ConsoleState
from backend.runtime.manager import RuntimeManager
from backend.skill_service.router import SkillServiceRouter

_manager: RuntimeManager | None = None
_console: ConsoleState | None = None
_skills: SkillServiceRouter | None = None


def get_manager() -> RuntimeManager:
    global _manager
    if _manager is None:
        plugin_config = (
            os.getenv("AGENTGUARD_SERVER_PLUGIN_CONFIG")
            or os.getenv("AGENTGUARD_PLUGIN_CONFIG")
        )
        _manager = RuntimeManager(plugin_config=plugin_config)
    return _manager


def get_console() -> ConsoleState:
    global _console
    if _console is None:
        _console = ConsoleState(get_manager())
    return _console


def get_skills() -> SkillServiceRouter:
    global _skills
    if _skills is None:
        _skills = SkillServiceRouter()
    return _skills

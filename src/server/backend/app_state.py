"""Process-wide shared singletons for the server (manager + console state)."""
from __future__ import annotations

from backend.console.state import ConsoleState
from backend.runtime.manager import RuntimeManager
from backend.skill_service.router import SkillServiceRouter

_manager: RuntimeManager | None = None
_console: ConsoleState | None = None
_skills: SkillServiceRouter | None = None


def get_manager() -> RuntimeManager:
    global _manager
    if _manager is None:
        _manager = RuntimeManager()
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

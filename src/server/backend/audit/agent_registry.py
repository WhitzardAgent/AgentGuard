"""Registry and discovery for agent-wide auditors."""
from __future__ import annotations

import importlib
import pkgutil
from collections.abc import Callable

from backend.audit.agent_base import BaseAgentAuditor

_AGENT_AUDITORS: dict[str, type[BaseAgentAuditor]] = {}
_DESCRIPTIONS: dict[str, str] = {}
_DISCOVERED = False


def register(
    name: str,
    description: str,
) -> Callable[[type[BaseAgentAuditor]], type[BaseAgentAuditor]]:
    """Register an agent-wide auditor under a stable public name."""
    if not name:
        raise ValueError("agent auditor registration name must not be empty")

    def _decorator(cls: type[BaseAgentAuditor]) -> type[BaseAgentAuditor]:
        if not isinstance(cls, type) or not issubclass(cls, BaseAgentAuditor):
            raise TypeError("@register can only decorate BaseAgentAuditor subclasses")
        existing = _AGENT_AUDITORS.get(name)
        if existing is not None and existing is not cls:
            raise ValueError(f"agent auditor name already registered: {name}")
        cls.name = name
        cls.description = description
        _AGENT_AUDITORS[name] = cls
        _DESCRIPTIONS[name] = description
        return cls

    return _decorator


def get_agent_auditor_class(name: str) -> type[BaseAgentAuditor] | None:
    discover_agent_auditors()
    return _AGENT_AUDITORS.get(name)


def registered_agent_auditors() -> dict[str, type[BaseAgentAuditor]]:
    discover_agent_auditors()
    return dict(_AGENT_AUDITORS)


def agent_auditor_descriptions() -> dict[str, str]:
    discover_agent_auditors()
    return dict(_DESCRIPTIONS)


def discover_agent_auditors(package_name: str = "backend.audit.auditors") -> None:
    """Import auditor modules so their decorators run."""
    global _DISCOVERED
    if _DISCOVERED:
        return
    _DISCOVERED = True
    package = importlib.import_module(package_name)
    package_path = getattr(package, "__path__", None)
    if package_path is None:
        return
    for module in pkgutil.walk_packages(package_path, package.__name__ + "."):
        if _should_skip(module.name):
            continue
        importlib.import_module(module.name)


def _should_skip(module_name: str) -> bool:
    leaf = module_name.rsplit(".", 1)[-1]
    return leaf in {"base", "manager", "registry"}


__all__ = [
    "agent_auditor_descriptions",
    "discover_agent_auditors",
    "get_agent_auditor_class",
    "register",
    "registered_agent_auditors",
]

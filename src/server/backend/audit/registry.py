"""Custom auditor registry and discovery."""
from __future__ import annotations

import importlib
import pkgutil
from typing import Callable

from backend.audit.base import BaseAuditor

_AUDITORS: dict[str, type[BaseAuditor]] = {}
_DESCRIPTIONS: dict[str, str] = {}
_DISCOVERED = False


def register(name: str, description: str) -> Callable[[type[BaseAuditor]], type[BaseAuditor]]:
    if not name:
        raise ValueError("auditor registration name must not be empty")

    def _decorator(cls: type[BaseAuditor]) -> type[BaseAuditor]:
        if not isinstance(cls, type) or not issubclass(cls, BaseAuditor):
            raise TypeError("@register can only decorate BaseAuditor subclasses")
        existing = _AUDITORS.get(name)
        if existing is not None and existing is not cls:
            raise ValueError(f"auditor name already registered: {name}")
        cls.name = name
        cls.description = description
        _AUDITORS[name] = cls
        _DESCRIPTIONS[name] = description
        return cls

    return _decorator


def get_auditor_class(name: str) -> type[BaseAuditor] | None:
    discover_auditors()
    return _AUDITORS.get(name)


def registered_auditors() -> dict[str, type[BaseAuditor]]:
    discover_auditors()
    return dict(_AUDITORS)


def auditor_descriptions() -> dict[str, str]:
    discover_auditors()
    return dict(_DESCRIPTIONS)


def discover_auditors(package_name: str = "backend.audit.auditors") -> None:
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

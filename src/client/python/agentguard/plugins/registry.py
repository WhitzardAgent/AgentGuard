"""Checker class registry and registration decorator."""
from __future__ import annotations

import importlib
import pkgutil
from typing import Callable

from agentguard.plugins.base import BaseChecker

_CHECKERS: dict[str, type[BaseChecker]] = {}
_DESCRIPTIONS: dict[str, str] = {}
_DISCOVERED = False


def register(name: str, description: str) -> Callable[[type[BaseChecker]], type[BaseChecker]]:
    """Register a checker class under a config-friendly name."""
    if not name:
        raise ValueError("checker registration name must not be empty")

    def _decorator(cls: type[BaseChecker]) -> type[BaseChecker]:
        if not isinstance(cls, type) or not issubclass(cls, BaseChecker):
            raise TypeError("@register can only decorate BaseChecker subclasses")
        existing = _CHECKERS.get(name)
        if (
            existing is not None
            and existing is not cls
            and existing.__module__ != cls.__module__
        ):
            raise ValueError(f"checker name already registered: {name}")
        cls.name = name
        cls.description = description
        _CHECKERS[name] = cls
        _DESCRIPTIONS[name] = description
        return cls

    return _decorator


def get_checker_class(name: str) -> type[BaseChecker] | None:
    discover_checkers()
    return _CHECKERS.get(name)


def checker_descriptions() -> dict[str, str]:
    discover_checkers()
    return dict(_DESCRIPTIONS)


def registered_checkers() -> dict[str, type[BaseChecker]]:
    discover_checkers()
    return dict(_CHECKERS)


def discover_checkers(package_name: str = "agentguard.plugins") -> None:
    """Import checker modules so @register decorators run."""
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

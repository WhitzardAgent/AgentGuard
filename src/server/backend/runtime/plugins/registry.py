"""Server plugin class registry and registration decorator."""
from __future__ import annotations

import importlib
import pkgutil
from typing import Callable

from backend.runtime.plugins.base import BasePlugin

_PLUGINS: dict[str, type[BasePlugin]] = {}
_DESCRIPTIONS: dict[str, str] = {}
_DISCOVERED = False


def register(name: str, description: str) -> Callable[[type[BasePlugin]], type[BasePlugin]]:
    """Register a server plugin class under a config-friendly name."""
    if not name:
        raise ValueError("plugin registration name must not be empty")

    def _decorator(cls: type[BasePlugin]) -> type[BasePlugin]:
        if not isinstance(cls, type) or not issubclass(cls, BasePlugin):
            raise TypeError("@register can only decorate BasePlugin subclasses")
        existing = _PLUGINS.get(name)
        if existing is not None and existing is not cls:
            raise ValueError(f"plugin name already registered: {name}")
        cls.name = name
        cls.description = description
        _PLUGINS[name] = cls
        _DESCRIPTIONS[name] = description
        return cls

    return _decorator


def get_plugin_class(name: str) -> type[BasePlugin] | None:
    discover_plugins()
    return _PLUGINS.get(name)


def plugin_descriptions() -> dict[str, str]:
    discover_plugins()
    return dict(_DESCRIPTIONS)


def registered_plugins() -> dict[str, type[BasePlugin]]:
    discover_plugins()
    return dict(_PLUGINS)


def discover_plugins(package_name: str = "backend.runtime.plugins") -> None:
    """Import plugin modules so @register decorators run."""
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

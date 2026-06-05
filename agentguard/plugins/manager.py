"""Plugin loader supporting dotted-module and file-path imports."""

from __future__ import annotations

import importlib
import importlib.util
import inspect
import logging
from abc import ABC, abstractmethod
from pathlib import Path
from types import ModuleType
from typing import TYPE_CHECKING, Any

log = logging.getLogger("agentguard.plugins")

if TYPE_CHECKING:
    from agentguard.facade import AgentGuard


class Plugin(ABC):
    """Base class for class-style plugins."""

    name: str = "plugin"

    @abstractmethod
    def register(self, guard: "AgentGuard") -> None:
        raise NotImplementedError


class PluginManager:
    def __init__(self, guard: "AgentGuard") -> None:
        self._guard = guard
        self._loaded: dict[str, Any] = {}

    @property
    def loaded(self) -> list[str]:
        return list(self._loaded)

    def load(self, spec: str | ModuleType | Plugin | type[Plugin]) -> Any:
        """Load and register a plugin.

        ``spec`` may be a dotted module path, a path to a ``.py`` file, an
        already-imported module, a :class:`Plugin` instance, or a Plugin class.
        """
        if isinstance(spec, Plugin):
            return self._register_instance(spec)
        if inspect.isclass(spec) and issubclass(spec, Plugin):
            return self._register_instance(spec())
        module = spec if isinstance(spec, ModuleType) else self._import(spec)
        return self._register_module(module)

    def _import(self, spec: str) -> ModuleType:
        path = Path(spec)
        if path.suffix == ".py" and path.exists():
            module_name = f"agentguard_plugin_{path.stem}"
            module_spec = importlib.util.spec_from_file_location(module_name, path)
            if module_spec is None or module_spec.loader is None:
                raise ImportError(f"cannot load plugin from {spec}")
            module = importlib.util.module_from_spec(module_spec)
            module_spec.loader.exec_module(module)
            return module
        return importlib.import_module(spec)

    def _register_module(self, module: ModuleType) -> Any:
        # Prefer a module-level register(guard) hook.
        register_fn = getattr(module, "register", None)
        if callable(register_fn):
            register_fn(self._guard)
            self._loaded[module.__name__] = module
            log.info("loaded plugin module %s", module.__name__)
            return module
        # Otherwise discover a Plugin subclass defined in the module.
        for _, obj in inspect.getmembers(module, inspect.isclass):
            if issubclass(obj, Plugin) and obj is not Plugin and obj.__module__ == module.__name__:
                return self._register_instance(obj())
        raise ImportError(
            f"plugin {module.__name__} exposes neither register() nor a Plugin subclass"
        )

    def _register_instance(self, plugin: Plugin) -> Plugin:
        plugin.register(self._guard)
        self._loaded[plugin.name] = plugin
        log.info("loaded plugin %s", plugin.name)
        return plugin

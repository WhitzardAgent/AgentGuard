"""Bootstrap helpers for Dify runtime integration."""
from __future__ import annotations

import importlib.abc
import importlib.machinery
import sys
import threading
from types import ModuleType
from typing import Any

_PATCHED_ATTR = "__agentguard_dify_app_factory_patched__"
_HOOK_INSTALLED = False
_HOOK_LOCK = threading.Lock()


def install_dify_app_factory_capture() -> dict[str, Any]:
    """Capture the Flask app returned by Dify's own app_factory.create_app."""
    global _HOOK_INSTALLED
    with _HOOK_LOCK:
        module = sys.modules.get("app_factory")
        if module is not None:
            return _patch_app_factory(module)
        if _HOOK_INSTALLED:
            return {"installed": True, "patched": False, "reason": "already_installed"}
        sys.meta_path.insert(0, _AppFactoryCaptureFinder())
        _HOOK_INSTALLED = True
        return {"installed": True, "patched": False, "reason": "import_hook_installed"}


def install_dify_post_gevent_bootstrap() -> dict[str, Any]:
    """Backward-compatible Dify bootstrap entrypoint used by older overrides."""
    status: dict[str, Any] = {
        "app_factory": install_dify_app_factory_capture(),
        "agent_chat": None,
        "workflow": None,
    }
    try:
        from agentguard.adapters.agent.dify_agent_chat import (  # noqa: PLC0415
            install_dify_agent_chat_adapter,
        )

        status["agent_chat"] = install_dify_agent_chat_adapter()
    except Exception as exc:
        status["agent_chat"] = {"patched": False, "error": str(exc)}
    try:
        from agentguard.adapters.agent.dify import install_dify_adapter  # noqa: PLC0415

        status["workflow"] = install_dify_adapter()
    except Exception as exc:
        status["workflow"] = {"patched": False, "error": str(exc)}
    return status


class _AppFactoryCaptureFinder(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname: str, path: Any, target: Any = None) -> Any:
        if fullname != "app_factory":
            return None
        spec = importlib.machinery.PathFinder.find_spec(fullname, path)
        if spec is None or spec.loader is None:
            return spec
        if isinstance(spec.loader, _AppFactoryCaptureLoader):
            return spec
        spec.loader = _AppFactoryCaptureLoader(spec.loader)
        return spec


class _AppFactoryCaptureLoader(importlib.abc.Loader):
    def __init__(self, wrapped: Any) -> None:
        self._wrapped = wrapped

    def create_module(self, spec: Any) -> Any:
        create_module = getattr(self._wrapped, "create_module", None)
        if callable(create_module):
            return create_module(spec)
        return None

    def exec_module(self, module: ModuleType) -> None:
        self._wrapped.exec_module(module)
        _patch_app_factory(module)


def _patch_app_factory(module: ModuleType) -> dict[str, Any]:
    create_app = getattr(module, "create_app", None)
    if not callable(create_app):
        return {"installed": True, "patched": False, "reason": "create_app_missing"}
    if getattr(create_app, _PATCHED_ATTR, False):
        return {"installed": True, "patched": False, "reason": "already_patched"}

    def wrapper(*args: Any, **kwargs: Any) -> Any:
        result = create_app(*args, **kwargs)
        _register_create_app_result(result)
        return result

    setattr(wrapper, _PATCHED_ATTR, True)
    wrapper.__agentguard_dify_original__ = create_app
    module.create_app = wrapper
    return {"installed": True, "patched": True}


def _register_create_app_result(result: Any) -> None:
    for candidate in _app_candidates(result):
        if _register_app(candidate):
            return


def _app_candidates(result: Any) -> list[Any]:
    candidates = [result]
    if isinstance(result, tuple | list):
        candidates.extend(result)
    return candidates


def _register_app(app: Any) -> bool:
    try:
        from agentguard.adapters.agent.dify_flask import register_dify_flask_app

        return register_dify_flask_app(app)
    except Exception:
        return False


__all__ = ["install_dify_app_factory_capture", "install_dify_post_gevent_bootstrap"]

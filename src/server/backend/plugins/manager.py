"""Server plugin manager: run plugin hooks in order."""
from __future__ import annotations

from typing import Any

from agentguard.schemas.decisions import GuardDecision
from backend.plugins.base import ServerPlugin
from backend.plugins.registry import PluginRegistry


class PluginManager:
    def __init__(self) -> None:
        self.registry = PluginRegistry()

    def register(self, plugin: ServerPlugin) -> ServerPlugin:
        self.registry.add(plugin)
        return plugin

    def on_request_received(self, request: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
        for p in self.registry.all():
            request = _safe(p.on_request_received, request, context, default=request)
        return request

    def on_before_policy_decision(self, request: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
        for p in self.registry.all():
            request = _safe(p.on_before_policy_decision, request, context, default=request)
        return request

    def diagnose(self, request: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
        results: dict[str, Any] = {}
        for p in self.registry.all():
            out = _safe(p.on_diagnose, request, context, default=None)
            if out is not None:
                results[p.plugin_id] = out
        return results

    def on_after_policy_decision(self, decision: GuardDecision, context: dict[str, Any]) -> GuardDecision:
        for p in self.registry.all():
            decision = _safe(p.on_after_policy_decision, decision, context, default=decision)
        return decision

    def on_trace_uploaded(self, trace: dict[str, Any], context: dict[str, Any]) -> None:
        for p in self.registry.all():
            _safe(p.on_trace_uploaded, trace, context, default=None)

    def on_policy_snapshot_build(self, snapshot: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
        for p in self.registry.all():
            snapshot = _safe(p.on_policy_snapshot_build, snapshot, context, default=snapshot)
        return snapshot


def _safe(fn, value, context, default):
    try:
        out = fn(value, context)
        return out if out is not None else default
    except Exception:
        return default

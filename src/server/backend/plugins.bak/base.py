"""Server plugin base. Plugins enrich decisions; they never bypass policy."""
from __future__ import annotations

from typing import Any

from shared.schemas.decisions import GuardDecision


class ServerPlugin:
    plugin_id: str = "server_plugin"

    def on_request_received(self, request: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
        return request

    def on_before_policy_decision(self, request: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
        return request

    def on_diagnose(self, request: dict[str, Any], context: dict[str, Any]) -> Any:
        return None

    def on_after_policy_decision(self, decision: GuardDecision, context: dict[str, Any]) -> GuardDecision:
        return decision

    def on_trace_uploaded(self, trace: dict[str, Any], context: dict[str, Any]) -> None:
        pass

    def on_policy_snapshot_build(self, snapshot: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
        return snapshot

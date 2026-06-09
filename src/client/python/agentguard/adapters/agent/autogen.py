"""AutoGen agent adapter (best-effort, optional dependency)."""
from __future__ import annotations

from typing import Any

from agentguard.adapters.agent.base import BaseAgentAdapter
from agentguard.schemas.context import RuntimeContext
from agentguard.utils.errors import AdapterError


class AutogenAgentAdapter(BaseAgentAdapter):
    name = "autogen"

    def can_wrap(self, agent: Any) -> bool:
        return "autogen" in (type(agent).__module__ or "")

    def generate(self, agent: Any, messages: list[dict[str, Any]], context: RuntimeContext) -> Any:
        fn = getattr(agent, "generate_reply", None)
        if callable(fn):
            try:
                return fn(messages=messages)
            except Exception as exc:
                raise AdapterError(f"autogen generate_reply failed: {exc}") from exc
        raise AdapterError("autogen agent exposes no generate_reply")

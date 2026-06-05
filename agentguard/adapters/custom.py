"""Adapter for arbitrary / duck-typed agents.

Wraps any object that is callable (``agent(prompt) -> str``) or exposes a
``run`` / ``invoke`` method, or a plain function. Also accepts a ``planner``
callable that yields explicit :class:`AgentStep` values for full control over
thoughts and tool calls.
"""

from __future__ import annotations

from typing import Any, Callable

from agentguard.adapters.base import AgentStep, BaseAdapter, StepStream
from agentguard.schemas.context import RuntimeContext
from agentguard.tools.metadata import ToolMetadata

Planner = Callable[[str, RuntimeContext, dict[str, ToolMetadata]], list[AgentStep]]


class CustomAdapter(BaseAdapter):
    provider = "custom"

    def __init__(
        self,
        agent: Any = None,
        *,
        planner: Planner | None = None,
        model: str | None = None,
        **options: Any,
    ) -> None:
        super().__init__(model=model, **options)
        self._agent = agent
        self._planner = planner

    def _invoke_agent(self, prompt: str) -> str:
        agent = self._agent
        if agent is None:
            return self._complete(prompt)
        for attr in ("run", "invoke", "__call__"):
            fn = getattr(agent, attr, None)
            if callable(fn):
                return str(fn(prompt))
        return str(agent)

    def _complete(self, prompt: str) -> str:
        if self._agent is not None:
            return self._invoke_agent(prompt)
        return super()._complete(prompt)

    def run(
        self,
        prompt: str,
        context: RuntimeContext,
        tools: dict[str, ToolMetadata],
        **kwargs: Any,
    ) -> StepStream:
        if self._planner is not None:
            sent: Any = None
            steps = self._planner(prompt, context, tools)
            last: Any = None
            for step in steps:
                last = yield step
            return last
        # No explicit planner → fall back to the default ReAct loop.
        return (yield from super().run(prompt, context, tools, **kwargs))

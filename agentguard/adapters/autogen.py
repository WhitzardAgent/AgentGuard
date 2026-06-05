"""AutoGen adapter — wraps an AssistantAgent-style object."""

from __future__ import annotations

import logging
from typing import Any

from agentguard.adapters.base import BaseAdapter

log = logging.getLogger("agentguard.adapters")


class AutogenAdapter(BaseAdapter):
    provider = "autogen"

    def __init__(self, agent: Any = None, *, model: str | None = None, **options: Any) -> None:
        super().__init__(model=model, **options)
        self._agent = agent

    def _complete(self, prompt: str) -> str:
        agent = self._agent
        if agent is None:
            return super()._complete(prompt)
        try:
            # AutoGen agents typically expose generate_reply / a callable run.
            if hasattr(agent, "generate_reply"):
                reply = agent.generate_reply(messages=[{"role": "user", "content": prompt}])
                return reply if isinstance(reply, str) else str(reply)
            if hasattr(agent, "run"):
                return str(agent.run(prompt))
        except Exception as exc:  # noqa: BLE001
            log.warning("autogen completion failed (%s); using offline fallback", exc)
        return super()._complete(prompt)

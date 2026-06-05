"""CrewAI adapter — wraps a Crew / Agent and surfaces its kickoff output."""

from __future__ import annotations

import logging
from typing import Any

from agentguard.adapters.base import BaseAdapter

log = logging.getLogger("agentguard.adapters")


class CrewAIAdapter(BaseAdapter):
    provider = "crewai"

    def __init__(self, crew: Any = None, *, model: str | None = None, **options: Any) -> None:
        super().__init__(model=model, **options)
        self._crew = crew

    def _complete(self, prompt: str) -> str:
        crew = self._crew
        if crew is None:
            return super()._complete(prompt)
        try:
            if hasattr(crew, "kickoff"):
                return str(crew.kickoff(inputs={"prompt": prompt}))
            if hasattr(crew, "run"):
                return str(crew.run(prompt))
        except Exception as exc:  # noqa: BLE001
            log.warning("crewai completion failed (%s); using offline fallback", exc)
        return super()._complete(prompt)

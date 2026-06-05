"""An external-search skill that degrades gracefully when egress is blocked.

The skill accepts an optional ``search_fn`` (any callable performing the actual
network search). When none is supplied — or when the network capability is not
granted in the current sandbox — it falls back to an offline stub so the
reasoning flow never hard-fails.
"""

from __future__ import annotations

from typing import Any, Callable

from agentguard.schemas.context import RuntimeContext
from agentguard.skills.base import Skill


class ExternalSearchSkill(Skill):
    name = "external_search"
    input_schema = {"query": "the search query"}

    def __init__(self, search_fn: Callable[[str], list[str]] | None = None) -> None:
        self._search_fn = search_fn

    def run(self, context: RuntimeContext, **inputs: Any) -> dict[str, Any]:
        query = str(inputs["query"]).strip()
        if self._search_fn is None:
            raise RuntimeError("no network search backend configured")
        results = self._search_fn(query)
        return {"query": query, "results": list(results), "degraded": False}

    def fallback(self, context: RuntimeContext, reason: str, **inputs: Any) -> dict[str, Any]:
        query = str(inputs.get("query", ""))
        return {
            "query": query,
            "results": [f"(offline) no live results for '{query}'"],
            "degraded": True,
            "reason": reason,
        }

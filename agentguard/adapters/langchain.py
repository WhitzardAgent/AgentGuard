"""LangChain adapter — wraps an LLM / Runnable / Chain."""

from __future__ import annotations

import logging
from typing import Any

from agentguard.adapters.base import BaseAdapter

log = logging.getLogger("agentguard.adapters")


class LangChainAdapter(BaseAdapter):
    provider = "langchain"

    def __init__(self, llm: Any = None, *, model: str | None = None, **options: Any) -> None:
        super().__init__(model=model, **options)
        self._llm = llm

    def _complete(self, prompt: str) -> str:
        llm = self._llm
        if llm is None:
            return super()._complete(prompt)
        try:
            # LangChain Runnables expose .invoke; older LLMs are callable.
            if hasattr(llm, "invoke"):
                out = llm.invoke(prompt)
            elif callable(llm):
                out = llm(prompt)
            else:
                return super()._complete(prompt)
            return getattr(out, "content", None) or str(out)
        except Exception as exc:  # noqa: BLE001
            log.warning("langchain completion failed (%s); using offline fallback", exc)
            return super()._complete(prompt)

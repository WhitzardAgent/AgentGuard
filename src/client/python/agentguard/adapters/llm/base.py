"""LLM adapter interface and guarded-LLM wrapper."""
from __future__ import annotations

from typing import Any

from agentguard.schemas import events as ev
from agentguard.schemas.decisions import DecisionType
from agentguard.utils.errors import AdapterError


class GuardedLLM:
    """Wraps an LLM so that every call is guarded for input and output."""

    def __init__(self, llm: Any, adapter: "BaseLLMAdapter", runtime: Any) -> None:
        self._llm = llm
        self._adapter = adapter
        self._runtime = runtime

    def __call__(self, request: Any, **kwargs: Any) -> Any:
        rt = self._runtime
        norm_req = self._adapter.normalize_request(request)
        rt.guard(ev.llm_input(rt.context, norm_req))
        raw = self._adapter.complete(self._llm, request, **kwargs)
        norm_resp = self._adapter.normalize_response(raw)
        decision = rt.guard(ev.llm_output(rt.context, norm_resp)).decision
        if decision.decision_type == DecisionType.DENY:
            return {"agentguard": "blocked", "reason": decision.reason}
        if decision.decision_type == DecisionType.SANITIZE:
            return {"agentguard": "sanitized", "reason": decision.reason}
        return raw

    def complete(self, request: Any, **kwargs: Any) -> Any:
        return self(request, **kwargs)


class BaseLLMAdapter:
    name: str = "base"

    def can_wrap(self, llm: Any) -> bool:
        raise NotImplementedError

    def normalize_request(self, request: Any) -> Any:
        return request

    def normalize_response(self, response: Any) -> Any:
        return response

    def complete(self, llm: Any, request: Any, **kwargs: Any) -> Any:
        if callable(llm):
            return llm(request, **kwargs)
        raise AdapterError(f"{self.name}: llm is not callable")

    def wrap(self, llm: Any, runtime: Any) -> GuardedLLM:
        return GuardedLLM(llm, self, runtime)


def select_llm_adapter(llm: Any, adapters: list[BaseLLMAdapter]) -> BaseLLMAdapter:
    for adapter in adapters:
        try:
            if adapter.can_wrap(llm):
                return adapter
        except Exception:
            continue
    raise AdapterError("no llm adapter can wrap the given llm")

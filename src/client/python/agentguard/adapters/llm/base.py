"""LLM adapter interface and guarded-LLM wrapper."""
from __future__ import annotations

import json
from typing import Any

from agentguard.schemas import events as ev
from agentguard.schemas.decisions import DecisionType
from agentguard.utils.errors import AdapterError


class GuardedLLM:
    """Wraps an LLM so that every call is guarded for input and output."""

    def __init__(self, llm: Any, adapter: BaseLLMAdapter, runtime: Any) -> None:
        self._llm = llm
        self._adapter = adapter
        self._runtime = runtime

    def __call__(self, request: Any, **kwargs: Any) -> Any:
        rt = self._runtime
        try:
            current_request = request
            norm_req = self._adapter.normalize_request(current_request)
            before = rt.guard(ev.llm_input(rt.context, norm_req)).decision
            if before.decision_type == DecisionType.DENY:
                return {"agentguard": "blocked", "reason": before.reason}
            if before.decision_type == DecisionType.MODIFY_LLM_INPUT:
                current_request = self._adapter.denormalize_request(
                    self._processed_payload(before),
                    current_request,
                )
            raw = self._adapter.complete(self._llm, current_request, **kwargs)
            norm_resp = self._adapter.normalize_response(raw)
            decision = rt.guard(ev.llm_output(rt.context, norm_resp), phase="after").decision
            if decision.decision_type == DecisionType.DENY:
                return {"agentguard": "blocked", "reason": decision.reason}
            if decision.decision_type == DecisionType.SANITIZE:
                return {"agentguard": "sanitized", "reason": decision.reason}
            if decision.decision_type == DecisionType.MODIFY_LLM_OUTPUT:
                return self._adapter.denormalize_response(self._processed_payload(decision), raw)
            return raw
        except Exception:
            rt.sync_local_cache_now(reason="client_error")
            raise
        finally:
            rt.sync_local_cache_async(reason="round_complete")

    def _processed_payload(self, decision: Any) -> Any:
        payload = getattr(decision, "processed_content", "")
        if not isinstance(payload, str):
            return payload
        text = payload.strip()
        if not text or text[0] not in {"{", "["}:
            return payload
        try:
            return json.loads(text)
        except Exception:
            return payload

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

    def denormalize_request(self, payload: Any, request: Any) -> Any:
        if isinstance(request, dict):
            if isinstance(payload, dict):
                updated = dict(request)
                updated.update(payload)
                return updated
            updated = dict(request)
            if "messages" in updated:
                updated["messages"] = payload
                return updated
            if "prompt" in updated:
                updated["prompt"] = payload
                return updated
        return payload

    def denormalize_response(self, payload: Any, response: Any) -> Any:
        if isinstance(response, str):
            if isinstance(payload, dict):
                for key in ("output", "final_output", "content", "text", "message"):
                    value = payload.get(key)
                    if value is not None:
                        return str(value)
            return str(payload)
        if isinstance(response, dict):
            if isinstance(payload, dict):
                updated = dict(response)
                updated.update(payload)
                return updated
            updated = dict(response)
            for key in ("output", "text", "content"):
                if key in updated:
                    updated[key] = payload
                    return updated
        return payload

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

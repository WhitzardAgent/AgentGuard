"""OpenAI Agents SDK adapter (best-effort, optional dependency)."""
from __future__ import annotations

import functools
import inspect
import json
from typing import Any

from agentguard.adapters.agent.base import BaseAgentAdapter, ToolBinding
from agentguard.adapters.agent.patching import (
    guard_tool_after,
    guard_tool_before,
    is_guarded,
    set_attr,
    tool_name,
)
from agentguard.schemas.decisions import DecisionType
from agentguard.schemas.context import RuntimeContext
from agentguard.utils.errors import AdapterError


class OpenAIAgentsAdapter(BaseAgentAdapter):
    name = "openai_agents"

    def can_wrap(self, agent: Any) -> bool:
        mod = type(agent).__module__ or ""
        return "agents" in mod and "openai" in mod

    def generate(self, agent: Any, messages: list[dict[str, Any]], context: RuntimeContext) -> Any:
        prompt = messages[-1].get("content", "") if messages else ""
        fn = getattr(agent, "run", None) or getattr(agent, "invoke", None)
        if callable(fn):
            try:
                return fn(prompt)
            except Exception as exc:
                raise AdapterError(f"openai agents run failed: {exc}") from exc
        raise AdapterError("openai agent exposes no run/invoke")

    def gettools(self, agent: Any) -> list[ToolBinding]:
        bindings: list[ToolBinding] = []
        tools = getattr(agent, "tools", None) or getattr(agent, "_tools", None)
        if isinstance(tools, dict):
            for name, tool in list(tools.items()):
                if _looks_like_function_tool(tool):
                    original = getattr(tool, "on_invoke_tool", None)
                    if callable(original) and not is_guarded(original):
                        bindings.append(
                            self.build_tool_binding(
                                name=str(name),
                                fn=original,
                                owner=tool,
                                attr="on_invoke_tool",
                                tool=tool,
                                installer=_install_openai_tool_binding,
                            )
                        )
                elif callable(tool):
                    bindings.append(
                        self.build_tool_binding(
                            name=str(name),
                            fn=tool,
                            container=tools,
                            key=name,
                            tool=tool,
                        )
                    )
        elif isinstance(tools, list):
            for idx, tool in enumerate(list(tools)):
                if _looks_like_function_tool(tool):
                    original = getattr(tool, "on_invoke_tool", None)
                    if callable(original) and not is_guarded(original):
                        bindings.append(
                            self.build_tool_binding(
                                name=tool_name(tool, fallback=f"tool_{idx}"),
                                fn=original,
                                owner=tool,
                                attr="on_invoke_tool",
                                tool=tool,
                                installer=_install_openai_tool_binding,
                            )
                        )
                elif callable(tool):
                    bindings.append(
                        self.build_tool_binding(
                            name=tool_name(tool, fallback=f"tool_{idx}"),
                            fn=tool,
                            container=tools,
                            key=idx,
                            tool=tool,
                        )
                    )
        return bindings

    def getllm(self, agent: Any):
        bindings = []
        seen: set[int] = set()
        for candidate in _iter_openai_llm_candidates(agent):
            if id(candidate) in seen:
                continue
            seen.add(id(candidate))
            bindings.extend(
                self.collect_llm_methods(
                    candidate,
                    methods=("create", "complete", "completion", "generate", "invoke", "ainvoke"),
                )
            )
            chat = getattr(candidate, "chat", None)
            completions = getattr(chat, "completions", None) if chat is not None else None
            if completions is not None and id(completions) not in seen:
                seen.add(id(completions))
                bindings.extend(
                    self.collect_llm_methods(
                        completions,
                        methods=("create",),
                    )
                )
            responses = getattr(candidate, "responses", None)
            if responses is not None and id(responses) not in seen:
                seen.add(id(responses))
                bindings.extend(
                    self.collect_llm_methods(
                        responses,
                        methods=("create",),
                    )
                )
        return bindings


def _looks_like_function_tool(tool: Any) -> bool:
    return hasattr(tool, "on_invoke_tool") and hasattr(tool, "name")


def _iter_openai_llm_candidates(agent: Any):
    for slot in ("model", "_model", "client", "_client", "llm", "_llm"):
        candidate = getattr(agent, slot, None)
        if candidate is not None:
            yield candidate


def _install_openai_tool_binding(
    guard: Any,
    binding: ToolBinding,
    adapter: BaseAgentAdapter,
) -> int:
    tool = binding.tool or binding.owner
    name = binding.name
    original = binding.callable
    if not callable(original) or is_guarded(original):
        return 0
    metadata = guard.register_tool(original, name=name)

    async def _call_original(*args: Any, **kwargs: Any) -> Any:
        out = original(*args, **kwargs)
        if inspect.isawaitable(out):
            return await out
        return out

    @functools.wraps(original)
    async def guarded_invoke(*args: Any, **kwargs: Any) -> Any:
        try:
            tool_args = _extract_json_args(args, kwargs)
            decision = guard_tool_before(
                guard,
                metadata,
                tool_args,
                normalizer=adapter,
                fn=original,
                owner=tool,
            )
            if decision.decision_type == DecisionType.DENY:
                return json.dumps({"agentguard": "blocked", "reason": decision.reason})
            if decision.requires_user or decision.requires_remote:
                return json.dumps({
                    "agentguard": "pending",
                    "reason": decision.reason,
                    "decision": decision.decision_type.value,
                })

            try:
                value = await _call_original(*args, **kwargs)
            except Exception as exc:
                guard_tool_after(
                    guard,
                    name,
                    error=str(exc),
                    normalizer=adapter,
                    fn=original,
                    owner=tool,
                )
                raise

            result_decision = guard_tool_after(
                guard,
                name,
                value,
                normalizer=adapter,
                fn=original,
                owner=tool,
            )
            if result_decision.decision_type == DecisionType.DENY:
                return json.dumps({"agentguard": "blocked", "reason": result_decision.reason})
            if result_decision.decision_type == DecisionType.SANITIZE:
                return json.dumps({"agentguard": "sanitized", "reason": result_decision.reason})
            return value
        except Exception:
            guard.runtime.sync_local_cache_now(reason="client_error")
            raise
        finally:
            guard.runtime.sync_local_cache_async(reason="round_complete")

    set_attr(guarded_invoke, "__agentguard_wrapped__", True)
    if set_attr(tool, "on_invoke_tool", guarded_invoke):
        return 1
    return 0


def _extract_json_args(args: tuple[Any, ...], kwargs: dict[str, Any]) -> dict[str, Any]:
    raw = None
    if len(args) >= 2:
        raw = args[1]
    elif "json_input" in kwargs:
        raw = kwargs["json_input"]
    elif "input" in kwargs:
        raw = kwargs["input"]

    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else {"_raw": parsed}
        except json.JSONDecodeError:
            return {"_raw": raw, "_unparsed": True}
    if isinstance(raw, dict):
        return raw
    return dict(kwargs)

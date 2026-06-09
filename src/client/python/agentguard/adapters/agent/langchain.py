"""LangChain agent adapter (best-effort, optional dependency)."""
from __future__ import annotations

from typing import Any

from agentguard.adapters.agent.base import BaseAgentAdapter
from agentguard.adapters.agent.patching import (
    is_guarded,
    make_guarded_tool,
    patch_llm_methods,
    set_attr,
    tool_name,
)
from agentguard.schemas.context import RuntimeContext
from agentguard.utils.errors import AdapterError


def _module_name(obj: Any) -> str:
    return type(obj).__module__ or ""


class LangChainAgentAdapter(BaseAgentAdapter):
    name = "langchain"

    def can_wrap(self, agent: Any) -> bool:
        return "langchain" in _module_name(agent)

    def generate(self, agent: Any, messages: list[dict[str, Any]], context: RuntimeContext) -> Any:
        prompt = messages[-1].get("content", "") if messages else ""
        for method in ("invoke", "run", "predict"):
            fn = getattr(agent, method, None)
            if callable(fn):
                try:
                    return fn(prompt)
                except Exception as exc:
                    raise AdapterError(f"langchain agent invoke failed: {exc}") from exc
        raise AdapterError("langchain agent exposes no invoke/run/predict")

    def attach(
        self,
        agent: Any,
        guard: Any,
        *,
        wrap_tools: bool = True,
        wrap_llm: bool = True,
    ) -> dict[str, Any]:
        """Patch LangChain/LangGraph tool call sites without replacing the agent loop."""
        patched = {"tools": 0, "llm": 0}
        if wrap_tools:
            patched["tools"] += self._patch_tool_containers(agent, guard)
        if wrap_llm:
            patched["llm"] += self._patch_llm(agent, guard)
        return patched

    def _patch_tool_containers(self, agent: Any, guard: Any) -> int:
        patched = 0
        patched += _patch_container_tools(agent, guard)

        nodes = getattr(agent, "nodes", None) or getattr(agent, "_nodes", None)
        if isinstance(nodes, dict):
            iterable = nodes.values()
        elif isinstance(nodes, (list, tuple, set)):
            iterable = nodes
        else:
            iterable = []

        for node in iterable:
            patched += _patch_container_tools(node, guard)
            runnable = getattr(node, "runnable", None)
            if runnable is not None:
                patched += _patch_container_tools(runnable, guard)
        return patched

    def _patch_llm(self, agent: Any, guard: Any) -> int:
        return _patch_langchain_llm(agent, guard)


def _patch_container_tools(container: Any, guard: Any) -> int:
    patched = 0
    for attr in ("tools_by_name", "_tools_by_name"):
        tools = getattr(container, attr, None)
        if isinstance(tools, dict):
            for name, tool in list(tools.items()):
                if callable(tool) and not hasattr(tool, "invoke"):
                    tools[name] = make_guarded_tool(guard, tool, name=str(name), tool=tool)
                    patched += 1
                else:
                    patched += _patch_tool_object(tool, guard, name=str(name))

    for attr in ("tools", "_tools"):
        tools = getattr(container, attr, None)
        if isinstance(tools, dict):
            for name, tool in list(tools.items()):
                if callable(tool) and not hasattr(tool, "invoke"):
                    tools[name] = make_guarded_tool(guard, tool, name=str(name), tool=tool)
                    patched += 1
                else:
                    patched += _patch_tool_object(tool, guard, name=str(name))
        elif isinstance(tools, list):
            for idx, tool in enumerate(list(tools)):
                if callable(tool) and not hasattr(tool, "invoke"):
                    name = tool_name(tool, fallback=f"tool_{idx}")
                    tools[idx] = make_guarded_tool(guard, tool, name=name, tool=tool)
                    patched += 1
                else:
                    patched += _patch_tool_object(
                        tool, guard, name=tool_name(tool, fallback=f"tool_{idx}")
                    )
    return patched


def _patch_langchain_llm(agent: Any, guard: Any) -> int:
    patched = 0
    seen: set[int] = set()
    for candidate in _iter_langchain_llm_candidates(agent):
        if id(candidate) in seen:
            continue
        seen.add(id(candidate))
        patched += patch_llm_methods(
            guard,
            candidate,
            methods=(
                "invoke",
                "ainvoke",
                "stream",
                "astream",
                "batch",
                "abatch",
                "generate",
                "agenerate",
                "predict",
                "apredict",
            ),
        )
    return patched


def _iter_langchain_llm_candidates(agent: Any):
    for slot in ("model", "_model", "llm", "_llm", "bound", "runnable"):
        candidate = getattr(agent, slot, None)
        if candidate is not None:
            yield candidate

    nodes = getattr(agent, "nodes", None) or getattr(agent, "_nodes", None)
    if isinstance(nodes, dict):
        iterable = nodes.values()
    elif isinstance(nodes, (list, tuple, set)):
        iterable = nodes
    else:
        iterable = []

    for node in iterable:
        for slot in ("model", "_model", "llm", "_llm", "bound", "runnable"):
            candidate = getattr(node, slot, None)
            if candidate is not None:
                yield candidate


def _patch_tool_object(tool: Any, guard: Any, *, name: str) -> int:
    if tool is None or is_guarded(tool):
        return 0

    patched = 0
    for attr in ("func", "coroutine", "_run", "_arun"):
        fn = getattr(tool, attr, None)
        if not callable(fn) or is_guarded(fn):
            continue
        wrapped = make_guarded_tool(guard, fn, name=name, tool=tool)
        if set_attr(tool, attr, wrapped):
            patched += 1
    if patched:
        return 1

    for attr in ("invoke", "ainvoke"):
        fn = getattr(tool, attr, None)
        if not callable(fn) or is_guarded(fn):
            continue
        wrapped = make_guarded_tool(guard, fn, name=name, tool=tool)
        if set_attr(tool, attr, wrapped):
            patched += 1
    return 1 if patched else 0

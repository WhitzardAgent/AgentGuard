"""LangGraph agent adapter (best-effort, optional dependency)."""
from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Any

from agentguard.adapters.agent.base import ToolBinding
from agentguard.adapters.agent.langchain import (
    LangChainAgentAdapter,
    _collect_container_tools,
    _install_langchain_tool_binding,
    _module_name,
    _unwrap_langchain_llm_target,
)
from agentguard.schemas.context import RuntimeContext
from agentguard.utils.errors import AdapterError


_MODEL_NODE_NAMES = {"agent", "model", "llm"}
_MODEL_CLOSURE_NAMES = {
    "model",
    "static_model",
    "dynamic_model",
    "resolved_model",
}


class LangGraphAgentAdapter(LangChainAgentAdapter):
    name = "langgraph"

    def can_wrap(self, agent: Any) -> bool:
        module_name = _module_name(agent)
        if "langchain" in module_name or "langgraph" not in module_name:
            return False
        return _has_langgraph_shape(agent)

    def generate(self, agent: Any, messages: list[dict[str, Any]], context: RuntimeContext) -> Any:
        _ = context
        graph_input = {"messages": messages}
        prompt = messages[-1].get("content", "") if messages else ""
        for method in ("invoke", "run"):
            fn = getattr(agent, method, None)
            if not callable(fn):
                continue
            try:
                return fn(graph_input)
            except TypeError:
                try:
                    return fn(prompt)
                except Exception as exc:
                    raise AdapterError(f"langgraph agent invoke failed: {exc}") from exc
            except Exception as exc:
                raise AdapterError(f"langgraph agent invoke failed: {exc}") from exc
        raise AdapterError("langgraph agent exposes no invoke/run")

    def gettools(self, agent: Any) -> list[ToolBinding]:
        bindings: list[ToolBinding] = []
        seen_targets: set[int] = set()
        for _, target in _iter_langgraph_targets(agent, include_agent=True):
            ident = id(target)
            if ident in seen_targets:
                continue
            seen_targets.add(ident)
            bindings.extend(_with_langgraph_tool_installer(_collect_container_tools(target, self)))
        return bindings

    def getllm(self, agent: Any):
        bindings = []
        seen_models: set[int] = set()

        for model in _direct_model_candidates(agent):
            bindings.extend(_collect_langgraph_llm(model, self, seen_models))

        for node_name, target in _iter_langgraph_targets(agent):
            if not _is_model_target(node_name, target):
                continue
            for model in _model_candidates_from_target(target):
                bindings.extend(_collect_langgraph_llm(model, self, seen_models))

        return bindings


def _has_langgraph_shape(agent: Any) -> bool:
    if isinstance(_node_mapping(agent), dict):
        return True
    builder = getattr(agent, "builder", None)
    if builder is not None and isinstance(_node_mapping(builder), dict):
        return True
    return callable(getattr(agent, "invoke", None)) or callable(getattr(agent, "ainvoke", None))


def _node_mapping(owner: Any) -> Any:
    nodes = getattr(owner, "nodes", None)
    if isinstance(nodes, (dict, list, tuple, set)):
        return nodes
    nodes = getattr(owner, "_nodes", None)
    if isinstance(nodes, (dict, list, tuple, set)):
        return nodes
    return None


def _iter_node_items(owner: Any) -> Iterable[tuple[str, Any]]:
    nodes = _node_mapping(owner)
    if isinstance(nodes, dict):
        for name, node in nodes.items():
            yield str(name), node
        return
    if isinstance(nodes, Sequence) and not isinstance(nodes, (str, bytes, bytearray)):
        for idx, node in enumerate(nodes):
            yield f"node_{idx}", node


def _iter_langgraph_targets(
    agent: Any,
    *,
    include_agent: bool = False,
) -> Iterable[tuple[str, Any]]:
    if include_agent:
        yield "agent", agent

    for owner in (agent, getattr(agent, "builder", None)):
        if owner is None:
            continue
        for name, node in _iter_node_items(owner):
            for target in _expand_node_target(node):
                yield name, target


def _expand_node_target(node: Any) -> Iterable[Any]:
    stack = [node]
    seen: set[int] = set()
    while stack:
        current = stack.pop()
        if current is None:
            continue
        ident = id(current)
        if ident in seen:
            continue
        seen.add(ident)
        yield current
        for attr in ("bound", "runnable", "data", "node"):
            child = getattr(current, attr, None)
            if child is not None and child is not current:
                stack.append(child)


def _direct_model_candidates(agent: Any) -> Iterable[Any]:
    for attr in ("model", "llm"):
        model = getattr(agent, attr, None)
        if model is not None:
            yield model


def _is_model_target(node_name: str, target: Any) -> bool:
    lowered = node_name.lower()
    if lowered in _MODEL_NODE_NAMES or "model" in lowered or "llm" in lowered:
        return True
    return any(getattr(target, attr, None) is not None for attr in ("model", "llm"))


def _model_candidates_from_target(target: Any) -> Iterable[Any]:
    yield from _direct_model_candidates(target)

    for attr in ("func", "afunc"):
        fn = getattr(target, attr, None)
        yield from _extract_model_closure_values(fn)

    if not _is_langgraph_object(target) and _looks_like_llm(target):
        yield target


def _extract_model_closure_values(fn: Any) -> Iterable[Any]:
    if not callable(fn):
        return
    closure = getattr(fn, "__closure__", None)
    code = getattr(fn, "__code__", None)
    if not closure or code is None:
        return
    for name, cell in zip(code.co_freevars, closure):
        if name not in _MODEL_CLOSURE_NAMES:
            continue
        try:
            value = cell.cell_contents
        except ValueError:
            continue
        if value is not None:
            yield value


def _collect_langgraph_llm(model: Any, adapter: LangGraphAgentAdapter, seen: set[int]):
    target = _unwrap_langchain_llm_target(model)
    if target is None:
        return []
    ident = id(target)
    if ident in seen:
        return []
    seen.add(ident)
    return adapter.collect_llm_methods(target, methods=("invoke", "ainvoke"))


def _with_langgraph_tool_installer(bindings: list[ToolBinding]) -> list[ToolBinding]:
    for binding in bindings:
        if binding.owner is not None and binding.attr:
            binding.installer = _install_langchain_tool_binding
    return bindings


def _is_langgraph_object(value: Any) -> bool:
    return "langgraph" in (type(value).__module__ or "")


def _looks_like_llm(value: Any) -> bool:
    return callable(getattr(value, "invoke", None)) or callable(getattr(value, "ainvoke", None))

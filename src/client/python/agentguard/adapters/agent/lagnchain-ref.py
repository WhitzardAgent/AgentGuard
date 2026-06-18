"""Adapter for LangChain agents built with ``create_agent``."""

from __future__ import annotations

import logging
from typing import Any

from agentguard.sdk.adapters.base import BaseAdapter
from agentguard.sdk.wrappers import wrap_tool

log = logging.getLogger(__name__)


class LangChainAdapter(BaseAdapter):
    """Attach AgentGuard to BaseTool instances registered on ToolNodes.

    Patches each tool's ``func`` (sync path) and ``coroutine`` (async path)
    so every invocation flows through ``guard.pipeline.guarded_call``.
    """

    def install(self, agent: Any) -> None:
        tool_nodes = self._iter_tool_nodes(agent)
        log.debug("LangChainAdapter: found %d tool nodes to patch.", len(tool_nodes))
        for _, tool_node in tool_nodes:
            self._patch_tool_node(tool_node)

    def _iter_tool_nodes(self, agent: Any) -> list[tuple[str, Any]]:
        tool_nodes: list[tuple[str, Any]] = []
        seen: set[int] = set()

        # Compiled StateGraph / CompiledGraph (.nodes is a dict of Pregel nodes)
        compiled_nodes = getattr(agent, "nodes", None)
        if isinstance(compiled_nodes, dict):
            for name, node in compiled_nodes.items():
                tool_node = getattr(node, "bound", None)
                if not isinstance(getattr(tool_node, "tools_by_name", None), dict):
                    log.debug(
                        "LangChainAdapter: skipping node %r (no tools_by_name).", name
                    )
                    continue
                ident = id(tool_node)
                if ident not in seen:
                    seen.add(ident)
                    tool_nodes.append((str(name), tool_node))

        # Pre-compiled builder nodes (older langgraph style)
        builder_nodes = getattr(getattr(agent, "builder", None), "nodes", None)
        if isinstance(builder_nodes, dict):
            for name, node in builder_nodes.items():
                tool_node = getattr(node, "data", None)
                if not isinstance(getattr(tool_node, "tools_by_name", None), dict):
                    continue
                ident = id(tool_node)
                if ident not in seen:
                    seen.add(ident)
                    tool_nodes.append((str(name), tool_node))

        return tool_nodes

    def _patch_tool_node(self, tool_node: Any) -> None:
        tools_by_name: dict[str, Any] | None = getattr(tool_node, "tools_by_name", None)
        if not isinstance(tools_by_name, dict):
            return
        for tool_name, tool in list(tools_by_name.items()):
            self._patch_tool(tool_name, tool)

    def _patch_tool(self, tool_name: str, tool: Any) -> None:
        """Patch the raw callables on a LangChain BaseTool.

        Priority:
        1. Wrap ``func``      (sync)     — LangChain's ``invoke`` delegates here.
        2. Wrap ``coroutine`` (async)    — LangChain's ``ainvoke`` delegates here.
        3. Fall back to wrapping ``invoke`` if neither exists (duck-typed tools).
        """
        patched_sync = False
        patched_async = False

        # ── sync path ──────────────────────────────────────────────────────
        func = getattr(tool, "func", None)
        if callable(func) and not getattr(func, "__agentguard__", None):
            wrapped_func = wrap_tool(self.guard, tool_name, func)
            try:
                object.__setattr__(tool, "func", wrapped_func)
            except (AttributeError, TypeError):
                tool.func = wrapped_func
            self.guard._record_tool_registration(tool_name, wrapped_func)
            log.debug("LangChainAdapter: wrapped sync func for %r.", tool_name)
            patched_sync = True

        # ── async path ─────────────────────────────────────────────────────
        coro = getattr(tool, "coroutine", None)
        if callable(coro) and not getattr(coro, "__agentguard__", None):
            wrapped_coro = wrap_tool(self.guard, tool_name, coro)
            try:
                object.__setattr__(tool, "coroutine", wrapped_coro)
            except (AttributeError, TypeError):
                tool.coroutine = wrapped_coro
            self.guard._record_tool_registration(f"{tool_name}.__async__", wrapped_coro)
            log.debug("LangChainAdapter: wrapped async coroutine for %r.", tool_name)
            patched_async = True

        # ── fallback: duck-typed tools that only expose invoke ─────────────
        if not patched_sync and not patched_async:
            invoke = getattr(tool, "invoke", None)
            if callable(invoke) and not getattr(invoke, "__agentguard__", None):
                wrapped_invoke = wrap_tool(self.guard, tool_name, invoke)
                try:
                    object.__setattr__(tool, "invoke", wrapped_invoke)
                except (AttributeError, TypeError):
                    tool.invoke = wrapped_invoke
                self.guard._record_tool_registration(tool_name, wrapped_invoke)
                log.debug("LangChainAdapter: wrapped invoke (fallback) for %r.", tool_name)

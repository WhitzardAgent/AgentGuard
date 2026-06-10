"""Adapter for LangChain agents built with ``create_agent``."""

from __future__ import annotations

import logging
from typing import Any

from agentguard.sdk.adapters.base import BaseAdapter
from agentguard.sdk.wrappers import wrap_tool

log = logging.getLogger(__name__)


try:
    from langchain_core.callbacks import BaseCallbackHandler as _BaseCallbackHandler
except Exception:  # pragma: no cover - optional dependency
    _BaseCallbackHandler = object


class AgentGuardLangChainCallbackHandler(_BaseCallbackHandler):  # type: ignore[misc, valid-type]
    """LangChain callback handler that records model activity into AgentGuard."""

    def __init__(self, guard: Any) -> None:
        super().__init__()
        self.guard = guard

    def on_chat_model_start(
        self,
        serialized: dict[str, Any],
        messages: list[list[Any]],
        **kwargs: Any,
    ) -> None:
        self.guard.record_model_input(
            messages=_serialize_messages(messages),
            provider="langchain",
            model=_model_name(serialized, kwargs),
            raw={"serialized": serialized, "kwargs": _jsonable(kwargs)},
        )

    def on_llm_start(
        self,
        serialized: dict[str, Any],
        prompts: list[str],
        **kwargs: Any,
    ) -> None:
        self.guard.record_model_input(
            context=list(prompts),
            provider="langchain",
            model=_model_name(serialized, kwargs),
            raw={"serialized": serialized, "kwargs": _jsonable(kwargs)},
        )

    def on_llm_end(self, response: Any, **kwargs: Any) -> None:
        payload = _jsonable(response)
        self.guard.record_model_output(
            output=_extract_generation_text(response),
            tool_calls=_extract_generation_tool_calls(response),
            provider="langchain",
            raw={"response": payload, "kwargs": _jsonable(kwargs)},
        )

    def on_agent_action(self, action: Any, **kwargs: Any) -> None:
        self.guard.record_action_proposed(
            action=_jsonable(action),
            provider="langchain",
            raw={"action": _jsonable(action), "kwargs": _jsonable(kwargs)},
        )

    def on_chain_end(self, outputs: dict[str, Any], **kwargs: Any) -> None:
        scratchpad = None
        if isinstance(outputs, dict):
            scratchpad = outputs.get("agent_scratchpad") or outputs.get("scratchpad")
        if scratchpad:
            self.guard.record_visible_thought(
                thought=scratchpad,
                provider="langchain",
                raw={"outputs": _jsonable(outputs), "kwargs": _jsonable(kwargs)},
            )


class LangChainAdapter(BaseAdapter):
    """Attach AgentGuard to BaseTool instances registered on ToolNodes.

    Patches each tool's ``func`` (sync path) and ``coroutine`` (async path)
    so every invocation flows through ``guard.pipeline.guarded_call``.
    """

    def install(self, agent: Any) -> None:
        self.callback_handler = AgentGuardLangChainCallbackHandler(self.guard)
        self._attach_callback_handler(agent, self.callback_handler)
        tool_nodes = self._iter_tool_nodes(agent)
        log.debug("LangChainAdapter: found %d tool nodes to patch.", len(tool_nodes))
        for _, tool_node in tool_nodes:
            self._patch_tool_node(tool_node)

    def _attach_callback_handler(self, agent: Any, handler: Any) -> None:
        callbacks = getattr(agent, "callbacks", None)
        if isinstance(callbacks, list) and handler not in callbacks:
            callbacks.append(handler)
            return
        config = getattr(agent, "config", None)
        if isinstance(config, dict):
            existing = config.setdefault("callbacks", [])
            if isinstance(existing, list) and handler not in existing:
                existing.append(handler)
                return
        try:
            setattr(agent, "agentguard_callback_handler", handler)
        except Exception:
            pass

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


def _jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    if isinstance(value, tuple):
        return [_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    for method in ("model_dump", "dict"):
        fn = getattr(value, method, None)
        if callable(fn):
            try:
                return fn()
            except Exception:
                pass
    if hasattr(value, "__dict__"):
        try:
            return {str(key): _jsonable(item) for key, item in vars(value).items()}
        except Exception:
            pass
    return str(value)


def _serialize_messages(messages: Any) -> Any:
    return _jsonable(messages)


def _model_name(serialized: dict[str, Any] | None, kwargs: dict[str, Any]) -> str | None:
    if isinstance(serialized, dict):
        name = serialized.get("name") or serialized.get("id")
        if isinstance(name, list):
            return ".".join(str(part) for part in name)
        if name:
            return str(name)
    invocation = kwargs.get("invocation_params") or kwargs.get("metadata") or {}
    if isinstance(invocation, dict):
        value = invocation.get("model") or invocation.get("model_name")
        if value:
            return str(value)
    return None


def _extract_generation_text(response: Any) -> Any:
    payload = _jsonable(response)
    if isinstance(payload, dict):
        generations = payload.get("generations")
        if isinstance(generations, list):
            texts: list[Any] = []
            for batch in generations:
                items = batch if isinstance(batch, list) else [batch]
                for item in items:
                    if isinstance(item, dict):
                        text = item.get("text")
                        message = item.get("message")
                        if text is not None:
                            texts.append(text)
                        elif isinstance(message, dict):
                            texts.append(message.get("content") or message)
                    else:
                        texts.append(item)
            return texts
    return payload


def _extract_generation_tool_calls(response: Any) -> Any:
    payload = _jsonable(response)
    calls: list[Any] = []
    if isinstance(payload, dict):
        generations = payload.get("generations")
        if isinstance(generations, list):
            for batch in generations:
                items = batch if isinstance(batch, list) else [batch]
                for item in items:
                    if not isinstance(item, dict):
                        continue
                    message = item.get("message")
                    if isinstance(message, dict):
                        tool_calls = message.get("tool_calls") or message.get("additional_kwargs", {}).get("tool_calls")
                        if tool_calls:
                            calls.extend(tool_calls if isinstance(tool_calls, list) else [tool_calls])
    return calls

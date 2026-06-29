"""Dify Agent node runtime adapter.

This adapter is intentionally installed at process start inside the Dify
``dify-agent`` service. Dify creates the pydantic-ai agent, model, and tools
internally, so there is no user-owned agent object to pass to ``attach_*``.
"""
from __future__ import annotations

import contextvars
import functools
import json
import os
import re
from collections.abc import AsyncIterator, Generator, Iterable
from contextlib import asynccontextmanager
from typing import Any

from agentguard.schemas import events as ev
from agentguard.schemas.decisions import DecisionType, GuardDecision
from agentguard.schemas.events import EventType
from agentguard.utils.errors import AdapterError
from agentguard.utils.json import safe_dumps, safe_loads

_PATCHED_ATTR = "__agentguard_dify_patched__"
_ORIGINAL_ATTR = "__agentguard_dify_original__"

_SECRET_KEY_HINTS = (
    "password",
    "passwd",
    "secret",
    "token",
    "api_key",
    "apikey",
    "authorization",
    "access_key",
    "private_key",
    "session_key",
)
_SECRET_VALUE_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9]{8,}"),
]

_current_guard: contextvars.ContextVar[Any | None] = contextvars.ContextVar(
    "agentguard_dify_guard",
    default=None,
)
_current_metadata: contextvars.ContextVar[dict[str, Any]] = contextvars.ContextVar(
    "agentguard_dify_metadata",
    default={},
)


def install_dify_adapter() -> dict[str, Any]:
    """Install Dify runtime hooks.

    The function is safe to call repeatedly. It returns a small status payload
    so Dify bootstrap code or tests can log/inspect whether patching happened.
    """
    if not _env_enabled():
        return {"enabled": False, "patched": False, "reason": "disabled"}

    legacy_status = _install_legacy_api_hooks()
    v2_status = _install_agent_v2_hooks()
    return {
        "enabled": True,
        "patched": bool(legacy_status.get("patched") or v2_status.get("patched")),
        "details": {
            "legacy_api": legacy_status,
            "agent_v2": v2_status,
        },
    }


def _install_agent_v2_hooks() -> dict[str, Any]:
    try:
        from dify_agent.adapters.llm.model import DifyLLMAdapterModel  # type: ignore
        from dify_agent.layers.dify_plugin import tools_layer  # type: ignore
        from dify_agent.runtime.runner import AgentRunRunner  # type: ignore
    except Exception as exc:
        return {
            "patched": False,
            "reason": "dify_import_failed",
            "error": str(exc),
        }

    patched: dict[str, bool] = {
        "runner": _patch_runner(AgentRunRunner),
        "llm_request": _patch_llm_request(DifyLLMAdapterModel),
        "llm_request_stream": _patch_llm_request_stream(DifyLLMAdapterModel),
        "tools": _patch_tool_builder(tools_layer),
    }
    return {
        "patched": any(patched.values()),
        "details": patched,
    }


def _install_legacy_api_hooks() -> dict[str, Any]:
    try:
        from core.model_manager import ModelInstance  # type: ignore
        from core.plugin.backwards_invocation.model import PluginModelBackwardsInvocation  # type: ignore
        from core.plugin.backwards_invocation.tool import PluginToolBackwardsInvocation  # type: ignore
        from core.tools.tool_engine import ToolEngine  # type: ignore
        from core.workflow.nodes.agent.agent_node import AgentNode  # type: ignore
    except Exception as exc:
        return {
            "patched": False,
            "reason": "legacy_import_failed",
            "error": str(exc),
        }

    patched: dict[str, bool] = {
        "agent_node": _patch_legacy_agent_node(AgentNode),
        "model_invoke_llm": _patch_legacy_model_invoke_llm(ModelInstance),
        "tool_agent_invoke": _patch_legacy_tool_agent_invoke(ToolEngine),
        "plugin_backwards_llm": _patch_legacy_plugin_backwards_llm(PluginModelBackwardsInvocation),
        "plugin_backwards_tool": _patch_legacy_plugin_backwards_tool(PluginToolBackwardsInvocation),
    }
    return {
        "patched": any(patched.values()),
        "details": patched,
    }


def _patch_runner(runner_cls: Any) -> bool:
    original = getattr(runner_cls, "_run_agent", None)
    if not callable(original) or _is_patched(original):
        return False

    @functools.wraps(original)
    async def wrapper(self: Any, *args: Any, **kwargs: Any) -> Any:
        metadata = _metadata_from_runner(self)
        guard = _make_guard(metadata)
        token_guard = _current_guard.set(guard)
        token_meta = _current_metadata.set(metadata)
        try:
            return await original(self, *args, **kwargs)
        finally:
            _flush_guard(guard, reason="dify_run_complete")
            _current_metadata.reset(token_meta)
            _current_guard.reset(token_guard)

    _mark_patched(wrapper, original)
    setattr(runner_cls, "_run_agent", wrapper)
    return True


def _patch_legacy_agent_node(agent_node_cls: Any) -> bool:
    original = getattr(agent_node_cls, "_run", None)
    if not callable(original) or _is_patched(original):
        return False

    @functools.wraps(original)
    def wrapper(self: Any, *args: Any, **kwargs: Any) -> Any:
        metadata = _metadata_from_legacy_agent_node(self)
        if not _legacy_metadata_allowed(metadata):
            yield from original(self, *args, **kwargs)
            return
        guard = _make_guard(metadata)
        token_guard = _current_guard.set(guard)
        token_meta = _current_metadata.set(metadata)
        try:
            yield from original(self, *args, **kwargs)
        finally:
            _flush_guard(guard, reason="dify_legacy_agent_node_complete")
            _current_metadata.reset(token_meta)
            _current_guard.reset(token_guard)

    _mark_patched(wrapper, original)
    setattr(agent_node_cls, "_run", wrapper)
    return True


def _patch_legacy_model_invoke_llm(model_instance_cls: Any) -> bool:
    original = getattr(model_instance_cls, "invoke_llm", None)
    if not callable(original) or _is_patched(original):
        return False

    @functools.wraps(original)
    def wrapper(self: Any, *args: Any, **kwargs: Any) -> Any:
        call = _legacy_llm_call_from_args(args, kwargs)
        decision = _guard_legacy_llm_input(self, call)
        blocked = _blocked_llm_value(decision)
        if blocked is not None:
            raise AdapterError(blocked)
        try:
            result = original(self, *args, **kwargs)
        except Exception as exc:
            _guard_legacy_llm_output(self, {"error": str(exc)}, call, error=str(exc))
            raise
        if _is_generator_like(result):
            return _wrap_legacy_llm_generator(self, result, call)
        decision = _guard_legacy_llm_output(self, result, call)
        blocked = _blocked_llm_value(decision)
        if blocked is not None:
            raise AdapterError(blocked)
        return result

    _mark_patched(wrapper, original)
    setattr(model_instance_cls, "invoke_llm", wrapper)
    return True


def _patch_legacy_tool_agent_invoke(tool_engine_cls: Any) -> bool:
    original = getattr(tool_engine_cls, "agent_invoke", None)
    if not callable(original) or _is_patched(original):
        return False

    @functools.wraps(original)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        call = _legacy_tool_call_from_args(args, kwargs)
        decision = _guard_legacy_tool_invoke(call)
        blocked = _blocked_tool_value(decision, call["tool_name"])
        if blocked is not None:
            return _legacy_blocked_tool_response(blocked)
        try:
            response = original(*args, **kwargs)
        except Exception as exc:
            _guard_legacy_tool_result(call, None, error=str(exc))
            raise
        result_text = response[0] if isinstance(response, tuple) and response else response
        decision = _guard_legacy_tool_result(call, result_text)
        blocked_result = _blocked_result_value(decision, call["tool_name"])
        if blocked_result is not None:
            return _legacy_blocked_tool_response(blocked_result)
        return response

    _mark_patched(wrapper, original)
    setattr(tool_engine_cls, "agent_invoke", wrapper)
    return True


def _patch_legacy_plugin_backwards_llm(invocation_cls: Any) -> bool:
    descriptor = invocation_cls.__dict__.get("invoke_llm")
    original = getattr(invocation_cls, "invoke_llm", None)
    if not callable(original) or _is_patched(original):
        return False

    @functools.wraps(original)
    def wrapper(cls: Any, user_id: str, tenant: Any, payload: Any) -> Any:
        metadata = _metadata_from_plugin_backwards_llm(user_id, tenant, payload)
        if not _legacy_metadata_allowed(metadata):
            return original(user_id, tenant, payload)
        return _run_with_ephemeral_guard(
            metadata,
            lambda: original(user_id, tenant, payload),
            reason="dify_plugin_backwards_llm_complete",
        )

    _mark_patched(wrapper, original)
    setattr(invocation_cls, "invoke_llm", _restore_descriptor(descriptor, wrapper))
    return True


def _patch_legacy_plugin_backwards_tool(invocation_cls: Any) -> bool:
    descriptor = invocation_cls.__dict__.get("invoke_tool")
    original = getattr(invocation_cls, "invoke_tool", None)
    if not callable(original) or _is_patched(original):
        return False

    @functools.wraps(original)
    def wrapper(cls: Any, *args: Any, **kwargs: Any) -> Any:
        call = _plugin_backwards_tool_call_from_args(args, kwargs)
        metadata = _metadata_from_plugin_backwards_tool(call)
        if not _legacy_metadata_allowed(metadata):
            return original(*args, **kwargs)

        def invoke() -> Any:
            decision = _guard_plugin_backwards_tool_invoke(call)
            blocked = _blocked_tool_value(decision, call["tool_name"])
            if blocked is not None:
                return _plugin_backwards_blocked_tool_generator(blocked)
            try:
                response = original(*args, **kwargs)
            except Exception as exc:
                _guard_plugin_backwards_tool_result(call, None, error=str(exc))
                raise
            return _wrap_plugin_backwards_tool_generator(response, call)

        return _run_with_ephemeral_guard(
            metadata,
            invoke,
            reason="dify_plugin_backwards_tool_complete",
        )

    _mark_patched(wrapper, original)
    setattr(invocation_cls, "invoke_tool", _restore_descriptor(descriptor, wrapper))
    return True


def _patch_llm_request(model_cls: Any) -> bool:
    original = getattr(model_cls, "request", None)
    if not callable(original) or _is_patched(original):
        return False

    @functools.wraps(original)
    async def wrapper(
        self: Any,
        messages: list[Any],
        model_settings: Any,
        model_request_parameters: Any,
    ) -> Any:
        request_input = _build_dify_request_input(self, messages, model_settings, model_request_parameters)
        decision = _guard_llm_input(self, request_input, stream=False)
        blocked = _blocked_llm_value(decision)
        if blocked is not None:
            raise AdapterError(blocked)
        try:
            response = await original(self, messages, model_settings, model_request_parameters)
        except Exception as exc:
            _guard_llm_output(self, {"error": str(exc)}, stream=False, error=str(exc))
            raise
        decision = _guard_llm_output(self, response, stream=False)
        blocked = _blocked_llm_value(decision)
        if blocked is not None:
            raise AdapterError(blocked)
        return response

    _mark_patched(wrapper, original)
    setattr(model_cls, "request", wrapper)
    return True


def _patch_llm_request_stream(model_cls: Any) -> bool:
    original = getattr(model_cls, "request_stream", None)
    if not callable(original) or _is_patched(original):
        return False

    @asynccontextmanager
    @functools.wraps(original)
    async def wrapper(
        self: Any,
        messages: list[Any],
        model_settings: Any,
        model_request_parameters: Any,
        run_context: object | None = None,
    ) -> AsyncIterator[Any]:
        request_input = _build_dify_request_input(self, messages, model_settings, model_request_parameters)
        decision = _guard_llm_input(self, request_input, stream=True)
        blocked = _blocked_llm_value(decision)
        if blocked is not None:
            raise AdapterError(blocked)
        response = None
        try:
            async with original(
                self,
                messages,
                model_settings,
                model_request_parameters,
                run_context=run_context,
            ) as streamed:
                response = streamed
                yield streamed
        except Exception as exc:
            _guard_llm_output(self, {"error": str(exc)}, stream=True, error=str(exc))
            raise
        final_response = _streamed_response_value(response)
        decision = _guard_llm_output(self, final_response if final_response is not None else response, stream=True)
        blocked = _blocked_llm_value(decision)
        if blocked is not None:
            raise AdapterError(blocked)

    _mark_patched(wrapper, original)
    setattr(model_cls, "request_stream", wrapper)
    return True


def _patch_tool_builder(tools_module: Any) -> bool:
    original = getattr(tools_module, "_build_pydantic_ai_tool", None)
    if not callable(original) or _is_patched(original):
        return False

    @functools.wraps(original)
    def wrapper(
        *,
        client: Any,
        tool_config: Any,
        effective_parameters: Any,
    ) -> Any:
        tool_name = str(getattr(tool_config, "name", None) or getattr(tool_config, "tool_name", "tool"))
        tool_description = getattr(tool_config, "description", None) or tool_name
        tool_schema = _deepcopy(getattr(tool_config, "parameters_json_schema", None) or {})

        async def invoke_tool(_ctx: Any, **tool_arguments: object) -> str:
            merged_arguments = tools_module._prepare_tool_arguments(
                effective_parameters,
                tool_config,
                tool_arguments,
            )
            decision = _guard_tool_invoke(tool_config, tool_name, merged_arguments)
            blocked = _blocked_tool_value(decision, tool_name)
            if blocked is not None:
                return blocked
            try:
                messages = await client.invoke(
                    provider=getattr(tool_config, "provider"),
                    tool_name=getattr(tool_config, "tool_name"),
                    credential_type=getattr(tool_config, "credential_type"),
                    credentials=dict(getattr(tool_config, "credentials", {}) or {}),
                    tool_parameters=merged_arguments,
                )
                result = tools_module._convert_tool_response_to_text(messages)
            except Exception as exc:
                _guard_tool_result(tool_config, tool_name, None, error=str(exc))
                if _is_dify_tool_client_error(tools_module, exc):
                    return tools_module._tool_error_text(tool_name=tool_name, error=exc)
                if isinstance(exc, ValueError):
                    return f"tool parameters validation error: {exc}, please check your tool parameters"
                raise
            decision = _guard_tool_result(tool_config, tool_name, result)
            blocked_result = _blocked_result_value(decision, tool_name)
            return blocked_result if blocked_result is not None else result

        async def prepare_tool_definition(_ctx: Any, tool_def: Any) -> Any:
            tool_definition_cls = getattr(tools_module, "ToolDefinition")
            return tool_definition_cls(
                name=tool_def.name,
                description=tool_def.description,
                parameters_json_schema=tool_schema,
                strict=getattr(tools_module, "PLUGIN_TOOL_STRICT", False),
                sequential=tool_def.sequential,
                metadata=tool_def.metadata,
                timeout=tool_def.timeout,
                defer_loading=tool_def.defer_loading,
                kind=tool_def.kind,
                return_schema=tool_def.return_schema,
                include_return_schema=tool_def.include_return_schema,
            )

        tool_cls = getattr(tools_module, "Tool")
        return tool_cls(
            invoke_tool,
            takes_ctx=True,
            name=tool_name,
            description=tool_description,
            prepare=prepare_tool_definition,
        )

    _mark_patched(wrapper, original)
    setattr(tools_module, "_build_pydantic_ai_tool", wrapper)
    return True


def _build_dify_request_input(
    model: Any,
    messages: list[Any],
    model_settings: Any,
    model_request_parameters: Any,
) -> Any:
    try:
        prepared_settings, prepared_params = model.prepare_request(
            model_settings,
            model_request_parameters,
        )
        return model._build_request_input(messages, prepared_settings, prepared_params)
    except Exception:
        return {
            "messages": _normalize_value(messages),
            "model_settings": _normalize_value(model_settings),
            "model_request_parameters": _normalize_value(model_request_parameters),
        }


def _guard_legacy_llm_input(model: Any, call: dict[str, Any]) -> GuardDecision:
    guard = _active_guard()
    if guard is None:
        return GuardDecision.allow("AgentGuard Dify legacy adapter inactive.")
    metadata = _event_metadata(
        {
            "phase": "llm_before",
            "dify_runtime": _current_metadata.get({}).get("dify_runtime") or "legacy_api",
            "stream": bool(call.get("stream")),
            "model": str(getattr(model, "model_name", "") or ""),
            "model_provider": _legacy_model_provider(model),
            "tool_names": [
                str(_get_attr_or_key(tool, "name") or _get_attr_or_key(tool, "tool_name") or "")
                for tool in call.get("tools") or []
            ],
        }
    )
    event = ev.llm_input(
        guard.context,
        _normalize_messages(call.get("prompt_messages")),
        **metadata,
    )
    return guard.runtime.guard(event).decision


def _guard_legacy_llm_output(
    model: Any,
    output: Any,
    call: dict[str, Any],
    *,
    error: str | None = None,
) -> GuardDecision:
    guard = _active_guard()
    if guard is None:
        return GuardDecision.allow("AgentGuard Dify legacy adapter inactive.")
    metadata = _event_metadata(
        {
            "phase": "llm_after",
            "dify_runtime": _current_metadata.get({}).get("dify_runtime") or "legacy_api",
            "stream": bool(call.get("stream")),
            "model": str(getattr(model, "model_name", "") or ""),
            "model_provider": _legacy_model_provider(model),
        }
    )
    if error is not None:
        metadata["error"] = error
    event = ev.llm_output(guard.context, _llm_output_payload(output), **metadata)
    return guard.runtime.guard(event, phase="after").decision


def _guard_legacy_tool_invoke(call: dict[str, Any]) -> GuardDecision:
    guard = _active_guard()
    if guard is None:
        return GuardDecision.allow("AgentGuard Dify legacy adapter inactive.")
    event = ev.tool_invoke(
        guard.context,
        call["tool_name"],
        dict(call.get("tool_parameters") or {}),
        capabilities=_legacy_tool_capabilities(call.get("tool")),
        **_legacy_tool_metadata(call, "tool_before"),
    )
    return guard.runtime.guard(event).decision


def _guard_legacy_tool_result(
    call: dict[str, Any],
    result: Any,
    *,
    error: str | None = None,
) -> GuardDecision:
    guard = _active_guard()
    if guard is None:
        return GuardDecision.allow("AgentGuard Dify legacy adapter inactive.")
    metadata = _legacy_tool_metadata(call, "tool_after")
    if error is not None:
        metadata["error"] = error
    event = ev.tool_result(
        guard.context,
        call["tool_name"],
        _content_to_text(result),
        **metadata,
    )
    return guard.runtime.guard(event, phase="after").decision


def _guard_plugin_backwards_tool_invoke(call: dict[str, Any]) -> GuardDecision:
    guard = _active_guard()
    if guard is None:
        return GuardDecision.allow("AgentGuard Dify plugin backwards adapter inactive.")
    event = ev.tool_invoke(
        guard.context,
        call["tool_name"],
        dict(call.get("tool_parameters") or {}),
        capabilities=_plugin_backwards_tool_capabilities(call),
        **_plugin_backwards_tool_metadata(call, "tool_before"),
    )
    return guard.runtime.guard(event).decision


def _guard_plugin_backwards_tool_result(
    call: dict[str, Any],
    result: Any,
    *,
    error: str | None = None,
) -> GuardDecision:
    guard = _active_guard()
    if guard is None:
        return GuardDecision.allow("AgentGuard Dify plugin backwards adapter inactive.")
    metadata = _plugin_backwards_tool_metadata(call, "tool_after")
    if error is not None:
        metadata["error"] = error
    event = ev.tool_result(
        guard.context,
        call["tool_name"],
        _content_to_text(result),
        **metadata,
    )
    return guard.runtime.guard(event, phase="after").decision


def _guard_llm_input(model: Any, request_input: Any, *, stream: bool) -> GuardDecision:
    guard = _active_guard()
    if guard is None:
        return GuardDecision.allow("AgentGuard Dify adapter inactive.")
    metadata = _event_metadata(
        {
            "phase": "llm_before",
            "stream": stream,
            "model": str(getattr(model, "model_name", getattr(model, "model", ""))),
            "model_provider": str(getattr(model, "model_provider", "")),
            "provider": str(getattr(model, "system", "")),
        }
    )
    event = ev.llm_input(
        guard.context,
        _messages_from_request_input(request_input),
        **metadata,
    )
    return guard.runtime.guard(event).decision


def _guard_llm_output(
    model: Any,
    output: Any,
    *,
    stream: bool,
    error: str | None = None,
) -> GuardDecision:
    guard = _active_guard()
    if guard is None:
        return GuardDecision.allow("AgentGuard Dify adapter inactive.")
    metadata = _event_metadata(
        {
            "phase": "llm_after",
            "stream": stream,
            "model": str(getattr(model, "model_name", getattr(model, "model", ""))),
            "model_provider": str(getattr(model, "model_provider", "")),
            "provider": str(getattr(model, "system", "")),
        }
    )
    if error is not None:
        metadata["error"] = error
    event = ev.llm_output(guard.context, _llm_output_payload(output), **metadata)
    return guard.runtime.guard(event, phase="after").decision


def _guard_tool_invoke(tool_config: Any, tool_name: str, arguments: dict[str, Any]) -> GuardDecision:
    guard = _active_guard()
    if guard is None:
        return GuardDecision.allow("AgentGuard Dify adapter inactive.")
    event = ev.tool_invoke(
        guard.context,
        tool_name,
        dict(arguments or {}),
        capabilities=_tool_capabilities(tool_config),
        **_tool_metadata(tool_config, "tool_before"),
    )
    return guard.runtime.guard(event).decision


def _guard_tool_result(
    tool_config: Any,
    tool_name: str,
    result: Any,
    *,
    error: str | None = None,
) -> GuardDecision:
    guard = _active_guard()
    if guard is None:
        return GuardDecision.allow("AgentGuard Dify adapter inactive.")
    event = ev.tool_result(
        guard.context,
        tool_name,
        result,
        error=error,
        **_tool_metadata(tool_config, "tool_after"),
    )
    return guard.runtime.guard(event, phase="after").decision


def _messages_from_request_input(request_input: Any) -> list[dict[str, Any]]:
    prompt_messages = _get_attr_or_key(request_input, "prompt_messages")
    if prompt_messages is None:
        messages = _get_attr_or_key(request_input, "messages")
        return _normalize_messages(messages)
    return [_prompt_message_to_message(item) for item in list(prompt_messages or [])]


def _prompt_message_to_message(message: Any) -> dict[str, Any]:
    role = _message_role(message)
    content = _get_attr_or_key(message, "content")
    data = _normalize_value(message)
    if isinstance(data, dict):
        data.setdefault("role", role)
        data.setdefault("content", _content_to_text(content))
        return data
    return {"role": role, "content": _content_to_text(content)}


def _message_role(message: Any) -> str:
    name = type(message).__name__.lower()
    if "system" in name:
        return "system"
    if "assistant" in name:
        return "assistant"
    if "tool" in name:
        return "tool"
    return "user"


def _normalize_messages(messages: Any) -> list[dict[str, Any]]:
    if isinstance(messages, list):
        normalized: list[dict[str, Any]] = []
        for item in messages:
            if isinstance(item, dict):
                normalized.append(
                    {
                        **item,
                        "role": str(item.get("role") or "user"),
                        "content": _content_to_text(item.get("content")),
                    }
                )
            else:
                normalized.append(_prompt_message_to_message(item))
        return normalized
    if messages is None:
        return []
    return [_prompt_message_to_message(messages)]


def _llm_output_payload(output: Any) -> dict[str, Any]:
    if isinstance(output, dict):
        if "output" in output:
            text = _content_to_optional_text(output.get("output"))
        elif "content" in output:
            text = _content_to_optional_text(output.get("content"))
        elif "text" in output:
            text = _content_to_optional_text(output.get("text"))
        elif output.get("tool_calls"):
            text = None
        else:
            text = _content_to_text(output)

        if "final_output" in output:
            final_output = _content_to_optional_text(output.get("final_output"))
        else:
            final_output = text
        return {
            "output": text,
            "final_output": final_output,
        }
    parts = getattr(output, "parts", None)
    if isinstance(parts, list):
        text_parts: list[str] = []
        for part in parts:
            content = getattr(part, "content", None)
            if content is not None:
                text_parts.append(_content_to_text(content))
        text = "\n".join(part for part in text_parts if part) or None
        return {"output": text, "final_output": text}
    text = _content_to_text(output)
    return {"output": text, "final_output": text}


def _streamed_response_value(response: Any) -> Any:
    getter = getattr(response, "get", None)
    if callable(getter):
        try:
            return getter()
        except Exception:
            return None
    return None


def _tool_metadata(tool_config: Any, phase: str) -> dict[str, Any]:
    return _event_metadata(
        {
            "phase": phase,
            "tool_provider": str(getattr(tool_config, "provider", "")),
            "plugin_id": str(getattr(tool_config, "plugin_id", "")),
            "provider": str(getattr(tool_config, "provider", "")),
            "credential_type": str(getattr(tool_config, "credential_type", "")),
            "configured_tool_name": str(getattr(tool_config, "tool_name", "")),
        }
    )


def _tool_capabilities(tool_config: Any) -> list[str]:
    caps = ["dify_tool"]
    provider = str(getattr(tool_config, "provider", "") or "")
    if provider:
        caps.append(provider)
    return caps


def _event_metadata(extra: dict[str, Any] | None = None) -> dict[str, Any]:
    metadata = {"adapter": "dify"}
    metadata.update(_current_metadata.get({}))
    if extra:
        metadata.update({key: value for key, value in extra.items() if value is not None})
    return metadata


def _metadata_from_legacy_agent_node(node: Any) -> dict[str, Any]:
    dify_ctx = _legacy_run_context(node)
    node_data = getattr(node, "node_data", None)
    graph_init_params = getattr(node, "graph_init_params", None)
    metadata = {
        "adapter": "dify",
        "dify_runtime": "legacy_api",
        "node_id": str(getattr(node, "_node_id", None) or getattr(node, "node_id", "") or ""),
        "node_execution_id": str(getattr(node, "id", "") or ""),
        "agent_strategy": str(getattr(node_data, "agent_strategy_name", "") or ""),
        "agent_strategy_provider": str(getattr(node_data, "agent_strategy_provider_name", "") or ""),
        "tenant_id": _optional_text(getattr(dify_ctx, "tenant_id", None)),
        "user_id": _optional_text(getattr(dify_ctx, "user_id", None)),
        "app_id": _optional_text(getattr(dify_ctx, "app_id", None)),
        "workflow_id": _optional_text(
            getattr(dify_ctx, "workflow_id", None)
            or getattr(graph_init_params, "workflow_id", None)
            or getattr(graph_init_params, "workflow_id_", None)
        ),
        "workflow_run_id": _optional_text(
            getattr(dify_ctx, "workflow_run_id", None)
            or getattr(graph_init_params, "workflow_run_id", None)
            or getattr(graph_init_params, "workflow_execution_id", None)
        ),
        "invoke_from": _optional_text(getattr(dify_ctx, "invoke_from", None)),
    }
    return {key: value for key, value in metadata.items() if value is not None}


def _metadata_from_plugin_backwards_llm(user_id: str, tenant: Any, payload: Any) -> dict[str, Any]:
    metadata = _metadata_from_env_filter_defaults()
    metadata.update(
        {
            "adapter": "dify",
            "dify_runtime": "legacy_plugin_backwards",
            "tenant_id": _optional_text(getattr(tenant, "id", None)),
            "user_id": _optional_text(user_id),
            "model_provider": _optional_text(getattr(payload, "provider", None)),
            "model": _optional_text(getattr(payload, "model", None)),
            "model_type": _optional_text(getattr(payload, "model_type", None)),
            "stream": bool(getattr(payload, "stream", False)),
        }
    )
    return {key: value for key, value in metadata.items() if value is not None}


def _metadata_from_plugin_backwards_tool(call: dict[str, Any]) -> dict[str, Any]:
    metadata = _metadata_from_env_filter_defaults()
    metadata.update(
        {
            "adapter": "dify",
            "dify_runtime": "legacy_plugin_backwards",
            "tenant_id": _optional_text(call.get("tenant_id")),
            "user_id": _optional_text(call.get("user_id")),
            "provider": _optional_text(call.get("provider")),
            "tool_provider": _optional_text(call.get("provider")),
            "tool_provider_type": _optional_text(call.get("tool_type")),
            "credential_id": _optional_text(call.get("credential_id")),
        }
    )
    return {key: value for key, value in metadata.items() if value is not None}


def _metadata_from_env_filter_defaults() -> dict[str, Any]:
    app_ids = sorted(_env_csv("AGENTGUARD_DIFY_APP_IDS"))
    node_ids = sorted(_env_csv("AGENTGUARD_DIFY_NODE_IDS"))
    metadata: dict[str, Any] = {}
    if len(app_ids) == 1:
        metadata["app_id"] = app_ids[0]
    if len(node_ids) == 1:
        metadata["node_id"] = node_ids[0]
    if os.getenv("AGENTGUARD_DIFY_WORKFLOW_ID"):
        metadata["workflow_id"] = os.getenv("AGENTGUARD_DIFY_WORKFLOW_ID")
    if os.getenv("AGENTGUARD_DIFY_WORKFLOW_RUN_ID"):
        metadata["workflow_run_id"] = os.getenv("AGENTGUARD_DIFY_WORKFLOW_RUN_ID")
    if os.getenv("AGENTGUARD_DIFY_NODE_EXECUTION_ID"):
        metadata["node_execution_id"] = os.getenv("AGENTGUARD_DIFY_NODE_EXECUTION_ID")
    if os.getenv("AGENTGUARD_DIFY_AGENT_STRATEGY"):
        metadata["agent_strategy"] = os.getenv("AGENTGUARD_DIFY_AGENT_STRATEGY")
    return metadata


def _metadata_from_runner(runner: Any) -> dict[str, Any]:
    request = getattr(runner, "request", None)
    request_metadata = _normalize_value(getattr(request, "metadata", None))
    if not isinstance(request_metadata, dict):
        request_metadata = {}
    metadata = {
        "adapter": "dify",
        "dify_agent_run_id": str(getattr(runner, "run_id", "") or ""),
    }
    metadata.update({str(k): v for k, v in request_metadata.items()})
    return metadata


def _legacy_run_context(node: Any) -> Any:
    try:
        from core.app.entities.app_invoke_entities import DIFY_RUN_CONTEXT_KEY, DifyRunContext  # type: ignore

        raw = node.require_run_context_value(DIFY_RUN_CONTEXT_KEY)
        return DifyRunContext.model_validate(raw)
    except Exception:
        return getattr(node, "dify_context", None) or getattr(node, "run_context", None)


def _legacy_metadata_allowed(metadata: dict[str, Any]) -> bool:
    app_ids = _env_csv("AGENTGUARD_DIFY_APP_IDS")
    node_ids = _env_csv("AGENTGUARD_DIFY_NODE_IDS")
    app_id = _optional_text(metadata.get("app_id"))
    node_id = _optional_text(metadata.get("node_id"))
    if app_ids and app_id not in app_ids:
        return False
    if node_ids and node_id not in node_ids:
        return False
    return True


def _legacy_llm_call_from_args(args: tuple[Any, ...], kwargs: dict[str, Any]) -> dict[str, Any]:
    names = ["prompt_messages", "model_parameters", "tools", "stop", "stream", "callbacks"]
    call = {name: kwargs.get(name) for name in names if name in kwargs}
    for index, value in enumerate(args):
        if index < len(names) and names[index] not in call:
            call[names[index]] = value
    call.setdefault("prompt_messages", [])
    call.setdefault("tools", None)
    call.setdefault("stream", True)
    return call


def _legacy_tool_call_from_args(args: tuple[Any, ...], kwargs: dict[str, Any]) -> dict[str, Any]:
    names = [
        "tool",
        "tool_parameters",
        "user_id",
        "tenant_id",
        "message",
        "invoke_from",
        "agent_tool_callback",
        "trace_manager",
        "conversation_id",
        "app_id",
        "message_id",
    ]
    call = {name: kwargs.get(name) for name in names if name in kwargs}
    for index, value in enumerate(args):
        if index < len(names) and names[index] not in call:
            call[names[index]] = value
    tool = call.get("tool")
    entity = getattr(tool, "entity", None)
    identity = getattr(entity, "identity", None)
    call["tool_name"] = str(getattr(identity, "name", "") or getattr(tool, "name", "") or "tool")
    if not isinstance(call.get("tool_parameters"), dict):
        call["tool_parameters"] = _normalize_value(call.get("tool_parameters"))
    if not isinstance(call.get("tool_parameters"), dict):
        call["tool_parameters"] = {"value": call.get("tool_parameters")}
    return call


def _legacy_tool_metadata(call: dict[str, Any], phase: str) -> dict[str, Any]:
    tool = call.get("tool")
    entity = getattr(tool, "entity", None)
    identity = getattr(entity, "identity", None)
    runtime = getattr(tool, "runtime", None)
    provider_type = ""
    try:
        provider_type = str(tool.tool_provider_type().value)
    except Exception:
        provider_type = str(getattr(entity, "provider_type", "") or "")
    metadata = _event_metadata(
        {
            "phase": phase,
            "dify_runtime": "legacy_api",
            "tool_provider": str(getattr(identity, "provider", "") or ""),
            "provider": str(getattr(identity, "provider", "") or ""),
            "tool_provider_type": provider_type,
            "tenant_id": _optional_text(call.get("tenant_id")),
            "user_id": _optional_text(call.get("user_id")),
            "app_id": _optional_text(call.get("app_id")),
            "message_id": _optional_text(call.get("message_id")),
            "conversation_id": _optional_text(call.get("conversation_id")),
            "invoke_from": _optional_text(call.get("invoke_from")),
            "runtime_parameters": _normalize_value(getattr(runtime, "runtime_parameters", None)),
        }
    )
    return metadata


def _plugin_backwards_tool_metadata(call: dict[str, Any], phase: str) -> dict[str, Any]:
    return _event_metadata(
        {
            "phase": phase,
            "dify_runtime": "legacy_plugin_backwards",
            "tenant_id": _optional_text(call.get("tenant_id")),
            "user_id": _optional_text(call.get("user_id")),
            "tool_provider": _optional_text(call.get("provider")),
            "provider": _optional_text(call.get("provider")),
            "tool_provider_type": _optional_text(call.get("tool_type")),
            "credential_id": _optional_text(call.get("credential_id")),
        }
    )


def _legacy_tool_capabilities(tool: Any) -> list[str]:
    caps = ["dify_tool", "dify_legacy_tool"]
    entity = getattr(tool, "entity", None)
    identity = getattr(entity, "identity", None)
    provider = str(getattr(identity, "provider", "") or "")
    if provider:
        caps.append(provider)
    try:
        provider_type = str(tool.tool_provider_type().value)
    except Exception:
        provider_type = ""
    if provider_type:
        caps.append(provider_type)
    return caps


def _plugin_backwards_tool_capabilities(call: dict[str, Any]) -> list[str]:
    caps = ["dify_tool", "dify_legacy_tool", "dify_plugin_backwards_tool"]
    provider = _optional_text(call.get("provider"))
    if provider:
        caps.append(provider)
    tool_type = _optional_text(call.get("tool_type"))
    if tool_type:
        caps.append(tool_type)
    return caps


def _legacy_model_provider(model: Any) -> str:
    for attr in ("provider", "model_provider", "provider_name"):
        value = getattr(model, attr, None)
        if value:
            return str(value)
    return ""


def _wrap_legacy_llm_generator(model: Any, result: Any, call: dict[str, Any]) -> Generator[Any, None, None]:
    chunks: list[Any] = []
    try:
        for chunk in result:
            chunks.append(chunk)
            yield chunk
    except Exception as exc:
        _guard_legacy_llm_output(model, {"error": str(exc)}, call, error=str(exc))
        raise
    decision = _guard_legacy_llm_output(model, _legacy_stream_output_payload(chunks), call)
    blocked = _blocked_llm_value(decision)
    if blocked is not None:
        raise AdapterError(blocked)


def _legacy_stream_output_payload(chunks: list[Any]) -> dict[str, Any]:
    text_parts: list[str] = []
    for chunk in chunks:
        delta = getattr(chunk, "delta", None)
        message = getattr(delta, "message", None)
        content = getattr(message, "content", None)
        if content is not None:
            text_parts.append(_content_to_text(content))
    output = "\n".join(part for part in text_parts if part) or None
    return {"output": output, "final_output": output}


def _plugin_backwards_tool_call_from_args(args: tuple[Any, ...], kwargs: dict[str, Any]) -> dict[str, Any]:
    names = [
        "tenant_id",
        "user_id",
        "tool_type",
        "provider",
        "tool_name",
        "tool_parameters",
        "credential_id",
    ]
    call = {name: kwargs.get(name) for name in names if name in kwargs}
    for index, value in enumerate(args):
        if index < len(names) and names[index] not in call:
            call[names[index]] = value
    call["tenant_id"] = _optional_text(call.get("tenant_id"))
    call["user_id"] = _optional_text(call.get("user_id"))
    call["tool_name"] = str(call.get("tool_name") or "tool")
    call["provider"] = _optional_text(call.get("provider"))
    call["tool_type"] = _tool_type_text(call.get("tool_type"))
    if not isinstance(call.get("tool_parameters"), dict):
        call["tool_parameters"] = _normalize_value(call.get("tool_parameters"))
    if not isinstance(call.get("tool_parameters"), dict):
        call["tool_parameters"] = {"value": call.get("tool_parameters")}
    return call


def _wrap_plugin_backwards_tool_generator(result: Any, call: dict[str, Any]) -> Generator[Any, None, None]:
    chunks: list[Any] = []
    try:
        for chunk in result:
            chunks.append(chunk)
            yield chunk
    except Exception as exc:
        _guard_plugin_backwards_tool_result(call, _plugin_backwards_tool_result_payload(chunks), error=str(exc))
        raise
    decision = _guard_plugin_backwards_tool_result(call, _plugin_backwards_tool_result_payload(chunks))
    blocked_result = _blocked_result_value(decision, call["tool_name"])
    if blocked_result is not None:
        yield from _plugin_backwards_blocked_tool_generator(blocked_result)


def _plugin_backwards_tool_result_payload(chunks: list[Any]) -> str:
    parts: list[str] = []
    for chunk in chunks:
        text = _get_attr_or_key(chunk, "text")
        if text is None:
            message = _get_attr_or_key(chunk, "message")
            text = _get_attr_or_key(message, "text")
        if text is None:
            text = _normalize_value(chunk)
        parts.append(_content_to_text(text))
    return "\n".join(part for part in parts if part)


def _plugin_backwards_blocked_tool_generator(text: str) -> Generator[Any, None, None]:
    try:
        from core.tools.entities.tool_entities import ToolInvokeMessage  # type: ignore

        yield ToolInvokeMessage(type="text", message={"text": text})
    except Exception:
        yield {"type": "text", "message": {"text": text}}


def _legacy_blocked_tool_response(text: str) -> tuple[str, list[str], Any]:
    try:
        from core.tools.entities.tool_entities import ToolInvokeMeta  # type: ignore

        return text, [], ToolInvokeMeta.error_instance(text)
    except Exception:
        return text, [], {"agentguard": "blocked", "reason": text}


def _maybe_subscribe_event_printer(guard: Any) -> None:
    if not _env_truthy("AGENTGUARD_DIFY_PRINT_EVENTS"):
        return
    try:
        guard.runtime.bus.subscribe(None, _print_agentguard_event)
    except Exception:
        pass


def _print_agentguard_event(event: Any) -> None:
    try:
        redacted = _redact_for_print(event.redacted().to_dict())
    except Exception:
        redacted = _redact_for_print(_normalize_value(event))
    print("\n[AgentGuard Event]", flush=True)
    print(json.dumps(redacted, ensure_ascii=False, indent=2), flush=True)

    try:
        if event.event_type == EventType.LLM_OUTPUT:
            payload = redacted.get("payload") or {}
            print("[AgentGuard LLMOutput Parsed]", flush=True)
            print(
                json.dumps(
                    {
                        "output": payload.get("output"),
                        "thought": payload.get("thought"),
                        "final_output": payload.get("final_output"),
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                flush=True,
            )
    except Exception:
        pass


def _redact_for_print(value: Any, key: str | None = None) -> Any:
    if key and any(hint in key.lower() for hint in _SECRET_KEY_HINTS):
        return "[REDACTED]"
    if isinstance(value, dict):
        return {str(k): _redact_for_print(v, str(k)) for k, v in value.items()}
    if isinstance(value, list):
        return [_redact_for_print(item) for item in value]
    if isinstance(value, str):
        redacted = value
        for pattern in _SECRET_VALUE_PATTERNS:
            redacted = pattern.sub("[REDACTED]", redacted)
        return redacted
    return value


def _make_guard(metadata: dict[str, Any]) -> Any:
    from agentguard.guard import AgentGuard

    session_id = _session_id(metadata)
    guard = AgentGuard(
        session_id,
        user_id=_optional_text(metadata.get("user_id")),
        agent_id=_agent_id(metadata),
        policy=os.getenv("AGENTGUARD_POLICY") or None,
        server_url=os.getenv("AGENTGUARD_SERVER_URL") or None,
        api_key=os.getenv("AGENTGUARD_API_KEY") or None,
        environment=os.getenv("AGENTGUARD_ENVIRONMENT") or "dify",
        sandbox="noop",
        plugin_config=_plugin_config(),
    )
    _maybe_subscribe_event_printer(guard)
    return guard


def _run_with_ephemeral_guard(metadata: dict[str, Any], call: Any, *, reason: str) -> Any:
    if _active_guard() is not None:
        return call()
    guard = _make_guard(metadata)
    token_guard = _current_guard.set(guard)
    token_meta = _current_metadata.set(metadata)
    try:
        result = call()
    except Exception:
        _flush_guard(guard, reason=reason)
        _current_metadata.reset(token_meta)
        _current_guard.reset(token_guard)
        raise
    if _is_generator_like(result):
        return _guarded_generator(result, guard, token_guard, token_meta, reason=reason)
    _flush_guard(guard, reason=reason)
    _current_metadata.reset(token_meta)
    _current_guard.reset(token_guard)
    return result


def _guarded_generator(
    result: Any,
    guard: Any,
    token_guard: contextvars.Token[Any],
    token_meta: contextvars.Token[dict[str, Any]],
    *,
    reason: str,
) -> Generator[Any, None, None]:
    try:
        yield from result
    finally:
        _flush_guard(guard, reason=reason)
        _current_metadata.reset(token_meta)
        _current_guard.reset(token_guard)


def _session_id(metadata: dict[str, Any]) -> str:
    workflow_run_id = _optional_text(metadata.get("workflow_run_id"))
    node_execution_id = _optional_text(metadata.get("node_execution_id"))
    if workflow_run_id and node_execution_id:
        return f"{workflow_run_id}:{node_execution_id}"
    run_id = _optional_text(metadata.get("dify_agent_run_id"))
    if run_id:
        return run_id
    app_id = _optional_text(metadata.get("app_id"))
    node_id = _optional_text(metadata.get("node_id"))
    user_id = _optional_text(metadata.get("user_id"))
    if app_id or node_id or user_id:
        return ":".join(part for part in [app_id, node_id, user_id] if part)
    return "dify_agent"


def _agent_id(metadata: dict[str, Any]) -> str:
    parts = [
        _optional_text(metadata.get("app_id")),
        _optional_text(metadata.get("workflow_id")),
        _optional_text(metadata.get("node_id")),
    ]
    present = [part for part in parts if part]
    if present:
        return ":".join(present)
    run_id = _optional_text(metadata.get("dify_agent_run_id"))
    return f"dify_agent:{run_id or 'unknown'}"


def _plugin_config() -> str | dict[str, Any] | None:
    raw = os.getenv("AGENTGUARD_PLUGIN_CONFIG")
    if not raw:
        return None
    parsed = safe_loads(raw)
    if isinstance(parsed, dict):
        return parsed
    return raw


def _active_guard() -> Any | None:
    return _current_guard.get()


def _blocked_llm_value(decision: GuardDecision) -> str | None:
    if decision.decision_type == DecisionType.DENY:
        return f"AgentGuard blocked Dify LLM call: {decision.reason}"
    if decision.decision_type == DecisionType.SANITIZE:
        return f"AgentGuard sanitized Dify LLM call: {decision.reason}"
    if decision.decision_type == DecisionType.DEGRADE:
        return f"AgentGuard degraded Dify LLM call: {decision.reason}"
    if decision.requires_user or decision.requires_remote:
        return f"AgentGuard pending Dify LLM call: {decision.reason}"
    return None


def _blocked_tool_value(decision: GuardDecision, tool: str) -> str | None:
    if decision.decision_type == DecisionType.DENY:
        return safe_dumps({"agentguard": "blocked", "tool": tool, "reason": decision.reason})
    if decision.decision_type == DecisionType.DEGRADE:
        return safe_dumps({"agentguard": "degraded", "tool": tool, "reason": decision.reason})
    if decision.requires_user or decision.requires_remote:
        return safe_dumps(
            {
                "agentguard": "pending",
                "tool": tool,
                "reason": decision.reason,
                "decision": decision.decision_type.value,
            }
        )
    return None


def _blocked_result_value(decision: GuardDecision, tool: str) -> str | None:
    if decision.decision_type == DecisionType.DENY:
        return safe_dumps({"agentguard": "blocked", "tool": tool, "reason": decision.reason})
    if decision.decision_type == DecisionType.SANITIZE:
        return safe_dumps({"agentguard": "sanitized", "tool": tool, "reason": decision.reason})
    if decision.requires_user or decision.requires_remote:
        return safe_dumps(
            {
                "agentguard": "pending",
                "tool": tool,
                "reason": decision.reason,
                "decision": decision.decision_type.value,
            }
        )
    return None


def _is_dify_tool_client_error(tools_module: Any, exc: Exception) -> bool:
    err_cls = getattr(tools_module, "DifyPluginToolClientError", None)
    return isinstance(exc, err_cls) if isinstance(err_cls, type) else False


def _is_patched(obj: Any) -> bool:
    return bool(getattr(obj, _PATCHED_ATTR, False))


def _mark_patched(obj: Any, original: Any) -> None:
    try:
        setattr(obj, _PATCHED_ATTR, True)
        setattr(obj, _ORIGINAL_ATTR, original)
    except Exception:
        pass


def _flush_guard(guard: Any, *, reason: str) -> None:
    try:
        guard.runtime.sync_local_cache_now(reason=reason)
    except Exception:
        pass
    try:
        guard.close()
    except Exception:
        pass


def _is_generator_like(value: Any) -> bool:
    return isinstance(value, Iterable) and not isinstance(value, str | bytes | dict | list | tuple)


def _env_enabled() -> bool:
    value = os.getenv("AGENTGUARD_ENABLED")
    if value is None:
        return True
    return value.strip().lower() not in {"0", "false", "no", "off", "disabled"}


def _env_truthy(name: str) -> bool:
    value = os.getenv(name)
    if value is None:
        return False
    return value.strip().lower() in {"1", "true", "yes", "on", "enabled"}


def _env_csv(name: str) -> set[str]:
    raw = os.getenv(name, "")
    return {item.strip() for item in raw.split(",") if item.strip()}


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _get_attr_or_key(value: Any, key: str) -> Any:
    if isinstance(value, dict):
        return value.get(key)
    return getattr(value, key, None)


def _restore_descriptor(descriptor: Any, wrapper: Any) -> Any:
    if isinstance(descriptor, classmethod):
        return classmethod(wrapper)
    if isinstance(descriptor, staticmethod):
        return staticmethod(wrapper)
    return wrapper


def _tool_type_text(value: Any) -> str | None:
    if value is None:
        return None
    enum_value = getattr(value, "value", None)
    if enum_value is not None:
        return str(enum_value)
    return str(value)


def _normalize_value(value: Any) -> Any:
    if value is None or isinstance(value, bool | int | float | str):
        return value
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    if isinstance(value, dict):
        return {str(key): _normalize_value(item) for key, item in value.items()}
    if isinstance(value, list | tuple | set | frozenset):
        return [_normalize_value(item) for item in value]
    for attr in ("model_dump", "to_dict", "dict"):
        dumper = getattr(value, attr, None)
        if callable(dumper):
            try:
                return _normalize_value(dumper())
            except Exception:
                continue
    data: dict[str, Any] = {}
    for attr in ("role", "content", "name", "tool_name", "tool_call_id"):
        item = getattr(value, attr, None)
        if item is not None:
            data[attr] = _normalize_value(item)
    return data or str(value)


def _content_to_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    if isinstance(value, list | tuple):
        return "\n".join(_content_to_text(item) for item in value)
    if isinstance(value, dict):
        return safe_dumps(value)
    return str(value)


def _content_to_optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = _content_to_text(value)
    return text if text else None


def _deepcopy(value: Any) -> Any:
    try:
        import copy

        return copy.deepcopy(value)
    except Exception:
        return value


__all__ = ["install_dify_adapter"]

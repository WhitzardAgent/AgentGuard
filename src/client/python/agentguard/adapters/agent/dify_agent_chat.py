"""Dify Agent Chat runtime adapter.

This adapter targets Dify's legacy ``agent-chat`` app mode. It is separate from
the workflow-oriented Dify adapter because Agent Chat runs through the in-process
``AgentChatAppRunner`` / ``BaseAgentRunner`` / ``ToolEngine.agent_invoke`` path,
not workflow nodes.
"""
from __future__ import annotations

import contextvars
import functools
import os
from collections.abc import Generator
from typing import Any

from agentguard.schemas import events as ev
from agentguard.schemas.decisions import DecisionType, GuardDecision
from agentguard.tools.metadata import ToolMetadata
from agentguard.utils.errors import AdapterError
from agentguard.utils.json import safe_dumps

_PATCHED_ATTR = "__agentguard_dify_agent_chat_patched__"
_ORIGINAL_ATTR = "__agentguard_dify_agent_chat_original__"

_current_guard: contextvars.ContextVar[Any | None] = contextvars.ContextVar(
    "agentguard_dify_agent_chat_guard",
    default=None,
)
_current_metadata: contextvars.ContextVar[dict[str, Any]] = contextvars.ContextVar(
    "agentguard_dify_agent_chat_metadata",
    default={},
)
_current_tool_catalog: contextvars.ContextVar[list[str]] = contextvars.ContextVar(
    "agentguard_dify_agent_chat_tool_catalog",
    default=[],
)


def install_dify_agent_chat_adapter() -> dict[str, Any]:
    """Install Dify Agent Chat runtime hooks.

    Safe to call repeatedly. The adapter is enabled with
    ``AGENTGUARD_DIFY_AGENT_CHAT_ENABLED=true``. For convenience, the broader
    ``AGENTGUARD_ENABLED=true`` also enables it unless this adapter-specific
    variable is explicitly false.
    """
    if not _env_enabled():
        return {"enabled": False, "patched": False, "reason": "disabled"}

    try:
        from core.agent.base_agent_runner import BaseAgentRunner  # type: ignore
        from core.app.apps.agent_chat.app_runner import AgentChatAppRunner  # type: ignore
        from core.model_manager import ModelInstance  # type: ignore
        from core.tools.tool_engine import ToolEngine  # type: ignore
    except Exception as exc:
        return {
            "enabled": True,
            "patched": False,
            "reason": "dify_agent_chat_import_failed",
            "error": str(exc),
        }

    patched = {
        "agent_chat_runner": _patch_agent_chat_runner(AgentChatAppRunner),
        "init_prompt_tools": _patch_init_prompt_tools(BaseAgentRunner),
        "model_invoke_llm": _patch_model_invoke_llm(ModelInstance),
        "tool_agent_invoke": _patch_tool_agent_invoke(ToolEngine),
    }
    return {"enabled": True, "patched": any(patched.values()), "details": patched}


def _patch_agent_chat_runner(runner_cls: Any) -> bool:
    original = getattr(runner_cls, "run", None)
    if not callable(original) or _is_patched(original):
        return False

    @functools.wraps(original)
    def wrapper(self: Any, *args: Any, **kwargs: Any) -> Any:
        metadata = _metadata_from_runner_args(args, kwargs)
        guard = _make_guard(metadata)
        token_guard = _current_guard.set(guard)
        token_meta = _current_metadata.set(metadata)
        token_catalog = _current_tool_catalog.set([])
        try:
            return original(self, *args, **kwargs)
        finally:
            _flush_guard(guard, reason="dify_agent_chat_run_complete")
            _current_tool_catalog.reset(token_catalog)
            _current_metadata.reset(token_meta)
            _current_guard.reset(token_guard)

    _mark_patched(wrapper, original)
    setattr(runner_cls, "run", wrapper)
    return True


def _patch_init_prompt_tools(base_runner_cls: Any) -> bool:
    original = getattr(base_runner_cls, "_init_prompt_tools", None)
    if not callable(original) or _is_patched(original):
        return False

    @functools.wraps(original)
    def wrapper(self: Any, *args: Any, **kwargs: Any) -> Any:
        result = original(self, *args, **kwargs)
        tool_instances = result[0] if isinstance(result, tuple) and result else {}
        _report_runtime_tools(tool_instances)
        return result

    _mark_patched(wrapper, original)
    setattr(base_runner_cls, "_init_prompt_tools", wrapper)
    return True


def _patch_model_invoke_llm(model_instance_cls: Any) -> bool:
    original = getattr(model_instance_cls, "invoke_llm", None)
    if not callable(original) or _is_patched(original):
        return False

    @functools.wraps(original)
    def wrapper(self: Any, *args: Any, **kwargs: Any) -> Any:
        call = _llm_call_from_args(args, kwargs)
        decision = _guard_llm_input(self, call)
        blocked = _blocked_llm_value(decision)
        if blocked is not None:
            raise AdapterError(blocked)
        try:
            result = original(self, *args, **kwargs)
        except Exception as exc:
            _guard_llm_output(self, {"error": str(exc)}, call, error=str(exc))
            raise
        if _is_generator_like(result):
            return _wrap_llm_generator(self, result, call)
        decision = _guard_llm_output(self, result, call)
        blocked = _blocked_llm_value(decision)
        if blocked is not None:
            raise AdapterError(blocked)
        return result

    _mark_patched(wrapper, original)
    setattr(model_instance_cls, "invoke_llm", wrapper)
    return True


def _patch_tool_agent_invoke(tool_engine_cls: Any) -> bool:
    original = getattr(tool_engine_cls, "agent_invoke", None)
    if not callable(original) or _is_patched(original):
        return False

    @functools.wraps(original)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        call = _tool_call_from_args(args, kwargs)
        decision = _guard_tool_invoke(call)
        blocked = _blocked_tool_value(decision, call["tool_name"])
        if blocked is not None:
            return _blocked_tool_response(blocked)
        try:
            response = original(*args, **kwargs)
        except Exception as exc:
            _guard_tool_result(call, None, error=str(exc))
            raise
        result_text = response[0] if isinstance(response, tuple) and response else response
        decision = _guard_tool_result(call, result_text)
        blocked_result = _blocked_result_value(decision, call["tool_name"])
        if blocked_result is not None:
            return _blocked_tool_response(blocked_result)
        return response

    _mark_patched(wrapper, original)
    setattr(tool_engine_cls, "agent_invoke", wrapper)
    return True


def _report_runtime_tools(tool_instances: Any) -> None:
    guard = _active_guard()
    if guard is None or not isinstance(tool_instances, dict):
        return

    registered: list[str] = []
    for name, tool in list(tool_instances.items()):
        tool_name = _tool_name(tool, fallback=str(name))
        if not tool_name:
            continue
        registered.append(tool_name)
        _report_tool_catalog(
            tool_name,
            description=_tool_description(tool),
            capabilities=_tool_capabilities(tool),
            schema=_tool_schema(tool),
            required_args=_tool_required_args(tool),
            metadata=_tool_metadata_from_tool(tool, "tool_catalog"),
        )
    _current_tool_catalog.set(registered)


def _guard_llm_input(model: Any, call: dict[str, Any]) -> GuardDecision:
    guard = _active_guard()
    if guard is None:
        return GuardDecision.allow("AgentGuard Dify Agent Chat adapter inactive.")
    metadata = _event_metadata(
        {
            "phase": "llm_before",
            "dify_runtime": "agent_chat",
            "stream": bool(call.get("stream")),
            "model": str(getattr(model, "model_name", "") or ""),
            "model_provider": _model_provider(model),
            "tool_names": list(_current_tool_catalog.get([])),
        }
    )
    event = ev.llm_input(guard.context, _normalize_messages(call.get("prompt_messages")), **metadata)
    return guard.runtime.guard(event).decision


def _guard_llm_output(
    model: Any,
    output: Any,
    call: dict[str, Any],
    *,
    error: str | None = None,
) -> GuardDecision:
    guard = _active_guard()
    if guard is None:
        return GuardDecision.allow("AgentGuard Dify Agent Chat adapter inactive.")
    metadata = _event_metadata(
        {
            "phase": "llm_after",
            "dify_runtime": "agent_chat",
            "stream": bool(call.get("stream")),
            "model": str(getattr(model, "model_name", "") or ""),
            "model_provider": _model_provider(model),
        }
    )
    if error is not None:
        metadata["error"] = error
    event = ev.llm_output(guard.context, _llm_output_payload(output), **metadata)
    return guard.runtime.guard(event, phase="after").decision


def _guard_tool_invoke(call: dict[str, Any]) -> GuardDecision:
    guard = _active_guard()
    if guard is None:
        return GuardDecision.allow("AgentGuard Dify Agent Chat adapter inactive.")
    if call["tool_name"] not in _current_tool_catalog.get([]):
        _report_tool_catalog(
            call["tool_name"],
            description=_tool_description(call.get("tool")),
            capabilities=_tool_capabilities(call.get("tool")),
            schema=_tool_schema(call.get("tool")),
            required_args=_tool_required_args(call.get("tool")),
            metadata=_tool_metadata(call, "tool_catalog"),
        )
    event = ev.tool_invoke(
        guard.context,
        call["tool_name"],
        dict(call.get("tool_parameters") or {}),
        capabilities=_tool_capabilities(call.get("tool")),
        **_tool_metadata(call, "tool_before"),
    )
    return guard.runtime.guard(event).decision


def _guard_tool_result(
    call: dict[str, Any],
    result: Any,
    *,
    error: str | None = None,
) -> GuardDecision:
    guard = _active_guard()
    if guard is None:
        return GuardDecision.allow("AgentGuard Dify Agent Chat adapter inactive.")
    metadata = _tool_metadata(call, "tool_after")
    if error is not None:
        metadata["error"] = error
    event = ev.tool_result(guard.context, call["tool_name"], _content_to_text(result), **metadata)
    return guard.runtime.guard(event, phase="after").decision


def _wrap_llm_generator(model: Any, result: Any, call: dict[str, Any]) -> Generator[Any, None, None]:
    chunks: list[Any] = []
    try:
        for item in result:
            chunks.append(item)
            yield item
    except Exception as exc:
        _guard_llm_output(model, {"error": str(exc)}, call, error=str(exc))
        raise
    decision = _guard_llm_output(model, _llm_stream_output_payload(chunks), call)
    blocked = _blocked_llm_value(decision)
    if blocked is not None:
        raise AdapterError(blocked)


def _llm_call_from_args(args: tuple[Any, ...], kwargs: dict[str, Any]) -> dict[str, Any]:
    return {
        "prompt_messages": kwargs.get("prompt_messages", args[0] if len(args) > 0 else None),
        "model_parameters": kwargs.get("model_parameters", args[1] if len(args) > 1 else None),
        "tools": kwargs.get("tools", args[2] if len(args) > 2 else None),
        "stop": kwargs.get("stop", args[3] if len(args) > 3 else None),
        "stream": kwargs.get("stream", args[4] if len(args) > 4 else True),
    }


def _tool_call_from_args(args: tuple[Any, ...], kwargs: dict[str, Any]) -> dict[str, Any]:
    tool = kwargs.get("tool", args[0] if len(args) > 0 else None)
    return {
        "tool": tool,
        "tool_name": _tool_name(tool),
        "tool_parameters": kwargs.get("tool_parameters", args[1] if len(args) > 1 else {}),
        "user_id": kwargs.get("user_id", args[2] if len(args) > 2 else None),
        "tenant_id": kwargs.get("tenant_id", args[3] if len(args) > 3 else None),
        "message": kwargs.get("message", args[4] if len(args) > 4 else None),
        "invoke_from": kwargs.get("invoke_from", args[5] if len(args) > 5 else None),
        "conversation_id": kwargs.get("conversation_id"),
        "app_id": kwargs.get("app_id"),
        "message_id": kwargs.get("message_id"),
    }


def _metadata_from_runner_args(args: tuple[Any, ...], kwargs: dict[str, Any]) -> dict[str, Any]:
    application_generate_entity = kwargs.get("application_generate_entity", args[0] if len(args) > 0 else None)
    conversation = kwargs.get("conversation", args[2] if len(args) > 2 else None)
    message = kwargs.get("message", args[3] if len(args) > 3 else None)
    app_config = getattr(application_generate_entity, "app_config", None)
    agent = getattr(app_config, "agent", None)
    metadata = {
        "adapter": "dify_agent_chat",
        "dify_runtime": "agent_chat",
        "tenant_id": _optional_text(getattr(app_config, "tenant_id", None)),
        "app_id": _optional_text(getattr(app_config, "app_id", None)),
        "conversation_id": _optional_text(getattr(conversation, "id", None)),
        "message_id": _optional_text(getattr(message, "id", None)),
        "user_id": _optional_text(getattr(application_generate_entity, "user_id", None)),
        "task_id": _optional_text(getattr(application_generate_entity, "task_id", None)),
        "invoke_from": _optional_text(getattr(application_generate_entity, "invoke_from", None)),
        "agent_strategy": _optional_text(getattr(agent, "strategy", None)),
    }
    return {key: value for key, value in metadata.items() if value not in (None, "")}


def _make_guard(metadata: dict[str, Any]) -> Any:
    from agentguard import AgentGuard

    agent_id = metadata.get("app_id") or metadata.get("conversation_id") or "dify-agent-chat"
    session_id = metadata.get("message_id") or metadata.get("task_id") or f"dify-agent-chat:{agent_id}"
    guard = AgentGuard(
        session_id,
        user_id=metadata.get("user_id"),
        agent_id=f"dify-agent-chat:{agent_id}",
        policy=os.getenv("AGENTGUARD_POLICY") or None,
        server_url=os.getenv("AGENTGUARD_SERVER_URL") or None,
        api_key=os.getenv("AGENTGUARD_API_KEY") or None,
        environment=os.getenv("AGENTGUARD_ENVIRONMENT") or "dify",
        sandbox="noop",
        plugin_config=_plugin_config(),
    )
    guard.context.metadata.update(metadata)
    if metadata.get("task_id"):
        guard.context.task_id = str(metadata["task_id"])
    return guard


def _flush_guard(guard: Any, *, reason: str) -> None:
    sync = getattr(guard, "flush_audit", None)
    if callable(sync):
        try:
            sync()
        except Exception:
            pass
    runtime = getattr(guard, "runtime", None)
    sync_cache = getattr(runtime, "sync_local_cache_now", None)
    if callable(sync_cache):
        try:
            sync_cache(reason=reason)
        except Exception:
            pass


def _report_tool_catalog(
    tool_name: str,
    *,
    description: str = "",
    capabilities: list[str] | None = None,
    schema: dict[str, Any] | None = None,
    required_args: list[str] | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    guard = _active_guard()
    if guard is None or not tool_name:
        return
    try:
        tool_metadata = ToolMetadata(
            name=tool_name,
            description=description or tool_name,
            capabilities=list(capabilities or []),
            required_args=list(required_args or []),
            schema=dict(schema or {}),
            metadata=dict(metadata or {}),
        )
        reporter = getattr(guard, "_report_tool_metadata", None)
        if callable(reporter):
            reporter(tool_metadata)
    except Exception:
        pass


def _tool_metadata(call: dict[str, Any], phase: str) -> dict[str, Any]:
    tool = call.get("tool")
    metadata = _tool_metadata_from_tool(tool, phase)
    for key in ("tenant_id", "user_id", "conversation_id", "app_id", "message_id"):
        if call.get(key) is not None:
            metadata[key] = _optional_text(call.get(key))
    message = call.get("message")
    if message is not None:
        metadata.setdefault("message_id", _optional_text(getattr(message, "id", None)))
        metadata.setdefault("conversation_id", _optional_text(getattr(message, "conversation_id", None)))
    metadata["phase"] = phase
    return _event_metadata(metadata)


def _tool_metadata_from_tool(tool: Any, phase: str) -> dict[str, Any]:
    identity = _tool_identity(tool)
    provider_type = ""
    provider_type_fn = getattr(tool, "tool_provider_type", None)
    if callable(provider_type_fn):
        with _suppress_exceptions():
            provider_type = _optional_text(provider_type_fn())
    return {
        "phase": phase,
        "adapter": "dify_agent_chat",
        "dify_runtime": "agent_chat",
        "tool_provider": _optional_text(_get_attr_or_key(identity, "provider")),
        "tool_provider_type": provider_type,
        "configured_tool_name": _optional_text(_get_attr_or_key(identity, "name")),
    }


def _tool_capabilities(tool: Any) -> list[str]:
    caps = ["dify_agent_chat_tool"]
    identity = _tool_identity(tool)
    provider = _optional_text(_get_attr_or_key(identity, "provider"))
    provider_type = ""
    provider_type_fn = getattr(tool, "tool_provider_type", None)
    if callable(provider_type_fn):
        with _suppress_exceptions():
            provider_type = _optional_text(provider_type_fn())
    if provider:
        caps.append(provider)
    if provider_type:
        caps.append(provider_type)
    return caps


def _tool_schema(tool: Any) -> dict[str, Any]:
    getter = getattr(tool, "get_llm_parameters_json_schema", None)
    if callable(getter):
        with _suppress_exceptions():
            schema = getter()
            if isinstance(schema, dict):
                return schema
    return {}


def _tool_required_args(tool: Any) -> list[str]:
    schema = _tool_schema(tool)
    required = schema.get("required")
    if isinstance(required, list):
        return [str(item) for item in required]
    runtime_parameters = getattr(tool, "get_runtime_parameters", None)
    if callable(runtime_parameters):
        with _suppress_exceptions():
            return [
                str(getattr(param, "name", ""))
                for param in runtime_parameters()
                if getattr(param, "required", False) and getattr(param, "name", None)
            ]
    return []


def _tool_description(tool: Any) -> str:
    entity = getattr(tool, "entity", None)
    description = getattr(entity, "description", None)
    llm = getattr(description, "llm", None)
    if llm:
        return str(llm)
    if description:
        return str(description)
    return _tool_name(tool)


def _tool_name(tool: Any, fallback: str = "tool") -> str:
    identity = _tool_identity(tool)
    return str(
        _get_attr_or_key(identity, "name")
        or _get_attr_or_key(tool, "name")
        or _get_attr_or_key(tool, "tool_name")
        or fallback
    )


def _tool_identity(tool: Any) -> Any:
    entity = getattr(tool, "entity", None)
    return getattr(entity, "identity", None)


def _model_provider(model: Any) -> str:
    bundle = getattr(model, "provider_model_bundle", None)
    configuration = getattr(bundle, "configuration", None)
    provider = getattr(configuration, "provider", None)
    return str(
        getattr(model, "provider", None)
        or getattr(model, "model_provider", None)
        or getattr(provider, "provider", None)
        or ""
    )


def _normalize_messages(messages: Any) -> list[dict[str, Any]]:
    if isinstance(messages, list):
        return [_prompt_message_to_message(item) for item in messages]
    if messages is None:
        return []
    return [_prompt_message_to_message(messages)]


def _prompt_message_to_message(message: Any) -> dict[str, Any]:
    if isinstance(message, dict):
        return {
            **message,
            "role": str(message.get("role") or "user"),
            "content": _content_to_text(message.get("content")),
        }
    role = _message_role(message)
    content = _get_attr_or_key(message, "content")
    text = _content_to_text(content)
    data = _normalize_value(message)
    if isinstance(data, dict):
        data.setdefault("role", role)
        data.setdefault("content", text)
        return data
    return {"role": role, "content": text}


def _message_role(message: Any) -> str:
    name = type(message).__name__.lower()
    if "system" in name:
        return "system"
    if "assistant" in name:
        return "assistant"
    if "tool" in name:
        return "tool"
    return "user"


def _llm_stream_output_payload(chunks: list[Any]) -> dict[str, Any]:
    text_parts: list[str] = []
    tool_calls: list[Any] = []
    for chunk in chunks:
        delta = _get_attr_or_key(chunk, "delta")
        message = _get_attr_or_key(delta, "message")
        content = _get_attr_or_key(message, "content")
        if content:
            text_parts.append(_content_to_text(content))
        calls = _get_attr_or_key(message, "tool_calls")
        if calls:
            tool_calls.extend(list(calls))
    payload: dict[str, Any] = {"output": "".join(text_parts), "final_output": "".join(text_parts)}
    if tool_calls:
        payload["tool_calls"] = _normalize_value(tool_calls)
    return payload


def _llm_output_payload(output: Any) -> dict[str, Any]:
    if isinstance(output, dict):
        if "error" in output:
            return {"output": _content_to_text(output), "final_output": _content_to_text(output)}
        return output
    message = _get_attr_or_key(output, "message")
    if message is not None:
        content = _get_attr_or_key(message, "content")
        tool_calls = _get_attr_or_key(message, "tool_calls")
        payload = {"output": _content_to_text(content), "final_output": _content_to_text(content)}
        if tool_calls:
            payload["tool_calls"] = _normalize_value(tool_calls)
        return payload
    return {"output": _content_to_text(output), "final_output": _content_to_text(output)}


def _content_to_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    if isinstance(value, list | tuple):
        return "".join(_content_to_text(item) for item in value)
    if isinstance(value, dict):
        return safe_dumps(value)
    text = getattr(value, "text", None)
    if text is not None:
        return _content_to_text(text)
    data = getattr(value, "data", None)
    if data is not None:
        return _content_to_text(data)
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
            with _suppress_exceptions():
                return _normalize_value(dumper())
    out: dict[str, Any] = {}
    for attr in ("role", "content", "name", "tool_calls", "usage"):
        attr_value = getattr(value, attr, None)
        if attr_value is not None:
            out[attr] = _normalize_value(attr_value)
    return out or str(value)


def _blocked_llm_value(decision: GuardDecision) -> str | None:
    if decision.is_allow or decision.decision_type == DecisionType.LOG_ONLY:
        return None
    if decision.decision_type == DecisionType.SANITIZE:
        return f"AgentGuard sanitized Dify Agent Chat LLM call: {decision.reason}"
    return f"AgentGuard blocked Dify Agent Chat LLM call: {decision.reason}"


def _blocked_tool_value(decision: GuardDecision, tool: str) -> str | None:
    if decision.is_allow or decision.decision_type == DecisionType.LOG_ONLY:
        return None
    if decision.decision_type == DecisionType.HUMAN_CHECK:
        return safe_dumps({"agentguard": "pending", "tool": tool, "reason": decision.reason})
    if decision.decision_type == DecisionType.REQUIRE_REMOTE_REVIEW:
        return safe_dumps({"agentguard": "remote_review_required", "tool": tool, "reason": decision.reason})
    if decision.decision_type == DecisionType.SANITIZE:
        return safe_dumps({"agentguard": "sanitized", "tool": tool, "reason": decision.reason})
    return safe_dumps({"agentguard": "blocked", "tool": tool, "reason": decision.reason})


def _blocked_result_value(decision: GuardDecision, tool: str) -> str | None:
    if decision.is_allow or decision.decision_type == DecisionType.LOG_ONLY:
        return None
    if decision.decision_type == DecisionType.SANITIZE:
        return safe_dumps({"agentguard": "sanitized", "tool": tool, "reason": decision.reason})
    return _blocked_tool_value(decision, tool)


def _blocked_tool_response(text: str) -> tuple[str, list[str], Any]:
    try:
        from core.tools.entities.tool_entities import ToolInvokeMeta  # type: ignore

        return text, [], ToolInvokeMeta.error_instance(text)
    except Exception:
        return text, [], {"error": text}


def _event_metadata(extra: dict[str, Any] | None = None) -> dict[str, Any]:
    metadata = {"adapter": "dify_agent_chat"}
    metadata.update(_current_metadata.get({}))
    if extra:
        metadata.update({key: value for key, value in extra.items() if value is not None})
    return metadata


def _active_guard() -> Any | None:
    return _current_guard.get()


def _env_enabled() -> bool:
    specific = os.getenv("AGENTGUARD_DIFY_AGENT_CHAT_ENABLED")
    if specific is not None:
        return specific.strip().lower() in {"1", "true", "yes", "on"}
    return os.getenv("AGENTGUARD_ENABLED", "").strip().lower() in {"1", "true", "yes", "on"}


def _plugin_config() -> str | dict[str, Any] | None:
    raw = os.getenv("AGENTGUARD_PLUGIN_CONFIG")
    if not raw:
        return None
    try:
        import json

        parsed = json.loads(raw)
    except Exception:
        return raw
    return parsed if isinstance(parsed, dict) else raw


def _is_patched(obj: Any) -> bool:
    return bool(getattr(obj, _PATCHED_ATTR, False))


def _mark_patched(wrapper: Any, original: Any) -> None:
    try:
        setattr(wrapper, _PATCHED_ATTR, True)
        setattr(wrapper, _ORIGINAL_ATTR, original)
    except Exception:
        pass


def _is_generator_like(value: Any) -> bool:
    return hasattr(value, "__iter__") and not isinstance(value, dict | list | tuple | str | bytes)


def _get_attr_or_key(value: Any, key: str, default: Any = None) -> Any:
    if value is None:
        return default
    if isinstance(value, dict):
        return value.get(key, default)
    return getattr(value, key, default)


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    raw = getattr(value, "value", value)
    text = str(raw)
    return text if text else None


class _suppress_exceptions:
    def __enter__(self) -> None:
        return None

    def __exit__(self, _exc_type: Any, _exc: Any, _tb: Any) -> bool:
        return True


__all__ = ["install_dify_agent_chat_adapter"]

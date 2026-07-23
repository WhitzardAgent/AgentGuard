"""Dify Agent Chat runtime adapter.

This adapter targets Dify's legacy ``agent-chat`` app mode. It is separate from
the workflow-oriented Dify adapter because Agent Chat runs through the in-process
``AgentChatAppRunner`` / ``BaseAgentRunner`` / ``ToolEngine.agent_invoke`` path,
not workflow nodes.
"""
from __future__ import annotations

import contextvars
import copy
import functools
import hashlib
import json
import logging
import os
import re
import sys
import threading
import time
from dataclasses import dataclass
from typing import Any

from agentguard.adapters.agent.dify_legacy_llm import (
    DifyLegacyLLMCall,
    DifyLegacyLLMNormalizer,
    modified_message_llm_output_value,
    parse_tagged_llm_output,
    run_dify_legacy_llm_call,
)
from agentguard.adapters.agent.dify_flask import (
    get_dify_flask_app,
    on_dify_flask_app_ready,
)
from agentguard.adapters.agent.dify_flask import (
    register_dify_flask_app as _register_shared_dify_flask_app,
)
from agentguard.schemas import events as ev
from agentguard.schemas.context import RuntimeContext
from agentguard.schemas.decisions import DecisionType, GuardDecision
from agentguard.tools.metadata import ToolMetadata
from agentguard.adapters.agent.dify_runtime_auth import manager as _runtime_auth_manager
from agentguard.u_guard.agent_keys import agent_identity_key_id, build_agent_registration_payload
from agentguard.u_guard.remote_client import RemoteGuardClient
from agentguard.utils.errors import AdapterError
from agentguard.adapters.agent.normalization import denormalize_tool_result_payload
from agentguard.utils.json import safe_dumps, safe_loads

_PATCHED_ATTR = "__agentguard_dify_agent_chat_patched__"
_ORIGINAL_ATTR = "__agentguard_dify_agent_chat_original__"
_LOGGER = logging.getLogger(__name__)

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
_catalog_sync_started = False
_catalog_sync_lock = threading.Lock()
_catalog_fingerprints: dict[str, str] = {}
_catalog_fingerprints_lock = threading.Lock()
_config_update_hook_installed = False
_config_update_hook_lock = threading.Lock()
_app_update_hook_installed = False
_app_update_hook_lock = threading.Lock()
_runtime_agent_registrations: dict[tuple[str, str, str, str, str], dict[str, Any]] = {}
_runtime_agent_registrations_lock = threading.Lock()
_LEGACY_LLM_NORMALIZER = DifyLegacyLLMNormalizer(
    content_list_joiner="",
    stream_text_joiner="",
    preserve_dict_payload=True,
    include_message_key=True,
    include_text_data_attrs=True,
    object_message_payload=True,
    include_stream_tool_calls=True,
    value_attrs=("role", "content", "name", "tool_calls", "usage"),
)


_LLM_ARG_NAMES = ("prompt_messages", "model_parameters", "tools", "stop", "stream")
_TOOL_ARG_NAMES = (
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
    config_update_sync = install_dify_agent_chat_config_update_sync()
    catalog_sync = start_dify_agent_chat_catalog_sync()
    return {
        "enabled": True,
        "patched": any(patched.values()),
        "details": patched,
        "catalog_sync": catalog_sync,
        "config_update_sync": config_update_sync,
    }


def install_dify_agent_chat_config_update_sync() -> dict[str, Any]:
    if not _catalog_sync_enabled():
        return {"enabled": False, "reason": "disabled"}
    if not os.getenv("AGENTGUARD_SERVER_URL"):
        return {"enabled": False, "reason": "server_url_missing"}

    global _config_update_hook_installed
    with _config_update_hook_lock:
        if _config_update_hook_installed:
            return {"enabled": True, "installed": False, "reason": "already_installed"}
        try:
            from events import app_event  # type: ignore
        except Exception as exc:
            return {"enabled": True, "installed": False, "reason": "event_import_failed", "error": str(exc)}
        app_model_config_was_updated = getattr(app_event, "app_model_config_was_updated", None)
        if not callable(getattr(app_model_config_was_updated, "connect", None)):
            return {"enabled": True, "installed": False, "reason": "event_missing"}
        app_model_config_was_updated.connect(_on_app_model_config_updated, weak=False)
        _config_update_hook_installed = True
    _install_agent_chat_app_update_catalog_hook()
    return {"enabled": True, "installed": True}


def _install_agent_chat_app_update_catalog_hook() -> bool:
    global _app_update_hook_installed
    with _app_update_hook_lock:
        if _app_update_hook_installed:
            return False
        try:
            from events.app_event import app_was_updated  # type: ignore
        except Exception:
            return False
        app_was_updated.connect(_on_agent_chat_app_updated, weak=False)
        _app_update_hook_installed = True
        return True


def start_dify_agent_chat_catalog_sync() -> dict[str, Any]:
    if not _catalog_sync_enabled():
        return {"enabled": False, "reason": "disabled"}
    if not os.getenv("AGENTGUARD_SERVER_URL"):
        return {"enabled": False, "reason": "server_url_missing"}
    if not _catalog_sync_process_allowed():
        return {"enabled": False, "reason": "process_not_allowed"}

    global _catalog_sync_started
    with _catalog_sync_lock:
        if _catalog_sync_started:
            return {"enabled": True, "started": False, "reason": "already_started"}
        _catalog_sync_started = True

    if _dify_flask_app() is None:
        on_dify_flask_app_ready(lambda app: _start_catalog_sync_thread(app))
        return {"enabled": True, "started": False, "reason": "waiting_for_app_factory"}

    _start_catalog_sync_thread()
    return {"enabled": True, "started": True}


def _start_catalog_sync_thread(app: Any | None = None) -> None:
    thread = threading.Thread(
        target=lambda: _catalog_sync_loop(app),
        name="agentguard-dify-agent-chat-catalog-sync",
        daemon=True,
    )
    thread.start()


def _patch_agent_chat_runner(runner_cls: Any) -> bool:
    original = getattr(runner_cls, "run", None)
    if not callable(original) or _is_patched(original):
        return False

    @functools.wraps(original)
    def wrapper(self: Any, *args: Any, **kwargs: Any) -> Any:
        metadata = _metadata_from_runner_args(args, kwargs)
        if not _app_allowed(metadata.get("app_id")):
            return original(self, *args, **kwargs)
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
    runner_cls.run = wrapper
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
    base_runner_cls._init_prompt_tools = wrapper
    return True


def _patch_model_invoke_llm(model_instance_cls: Any) -> bool:
    original = getattr(model_instance_cls, "invoke_llm", None)
    if not callable(original) or _is_patched(original):
        return False

    @functools.wraps(original)
    def wrapper(self: Any, *args: Any, **kwargs: Any) -> Any:
        return run_dify_legacy_llm_call(
            model=self,
            args=tuple(args),
            kwargs=dict(kwargs),
            arg_names=_LLM_ARG_NAMES,
            execute=lambda current_args, current_kwargs: original(self, *current_args, **current_kwargs),
            guard_input=_guard_llm_input,
            guard_output=_guard_llm_output,
            blocked_value=_blocked_llm_value,
            normalizer=_LEGACY_LLM_NORMALIZER,
            modify_output=modified_message_llm_output_value,
        )

    _mark_patched(wrapper, original)
    model_instance_cls.invoke_llm = wrapper
    return True


def _patch_tool_agent_invoke(tool_engine_cls: Any) -> bool:
    original = getattr(tool_engine_cls, "agent_invoke", None)
    if not callable(original) or _is_patched(original):
        return False

    @functools.wraps(original)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        current_args = tuple(args)
        current_kwargs = dict(kwargs)
        call = _tool_call_from_args(current_args, current_kwargs)
        decision = _guard_tool_invoke(call)
        if decision.decision_type == DecisionType.MODIFY_TOOL_INVOKE:
            payload = _decision_payload(decision)
            current_args, current_kwargs = _replace_named_argument(
                current_args,
                current_kwargs,
                _TOOL_ARG_NAMES,
                "tool_parameters",
                payload,
            )
            call = _tool_call_from_args(current_args, current_kwargs)
        blocked = _blocked_tool_value(decision, call["tool_name"])
        if blocked is not None:
            return _blocked_tool_response(blocked)
        try:
            response = original(*current_args, **current_kwargs)
        except Exception as exc:
            _guard_tool_result(call, None, error=str(exc))
            raise
        result_text = response[0] if isinstance(response, tuple) and response else response
        decision = _guard_tool_result(call, result_text)
        if decision.decision_type == DecisionType.MODIFY_TOOL_RESULT:
            response = _replace_response_text(
                response,
                _modified_result_value(_decision_payload(decision), result_text),
            )
        blocked_result = _blocked_result_value(decision, call["tool_name"])
        if blocked_result is not None:
            return _blocked_tool_response(blocked_result)
        return response

    _mark_patched(wrapper, original)
    tool_engine_cls.agent_invoke = wrapper
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


def _guard_llm_input(
    model: Any,
    call: DifyLegacyLLMCall,
    extra_metadata: dict[str, Any] | None = None,
) -> GuardDecision:
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
    if extra_metadata:
        metadata.update(extra_metadata)
    event = ev.llm_input(guard.context, _normalize_messages(call.get("prompt_messages")), **metadata)
    return guard.runtime.guard(event).decision


def _guard_llm_output(
    model: Any,
    output: Any,
    call: DifyLegacyLLMCall,
    error: str | None = None,
    extra_metadata: dict[str, Any] | None = None,
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
    if extra_metadata:
        metadata.update(extra_metadata)
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


def _decision_payload(decision: GuardDecision) -> Any:
    payload = decision.processed_content
    if not isinstance(payload, str):
        return payload
    text = payload.strip()
    if not text or text[0] not in {"{", "["}:
        return payload
    return safe_loads(text, fallback=payload)


def _replace_named_argument(
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
    names: tuple[str, ...],
    key: str,
    value: Any,
) -> tuple[tuple[Any, ...], dict[str, Any]]:
    current_args = list(args)
    current_kwargs = dict(kwargs)
    if key in current_kwargs:
        current_kwargs[key] = value
        return tuple(current_args), current_kwargs

    try:
        index = names.index(key)
    except ValueError:
        current_kwargs[key] = value
        return tuple(current_args), current_kwargs

    if index < len(current_args):
        current_args[index] = value
    else:
        current_kwargs[key] = value
    return tuple(current_args), current_kwargs


def _modified_result_value(payload: Any, result: Any, *, error: str | None = None) -> Any:
    return denormalize_tool_result_payload(
        payload=payload,
        result=result,
        error=error,
    ).result


def _modified_llm_output_value(payload: Any, result: Any) -> Any:
    message = _get_attr_or_key(result, "message")
    if message is not None and hasattr(message, "content"):
        updated_result = copy.copy(result)
        updated_message = copy.copy(message)
        updated_message.content = denormalize_llm_output_payload(
            payload=payload,
            output=getattr(message, "content", None),
        )
        updated_result.message = updated_message
        return updated_result
    return denormalize_llm_output_payload(payload=payload, output=result)


def _replace_response_text(response: Any, value: Any) -> Any:
    if isinstance(response, tuple):
        items = list(response)
        if items:
            items[0] = value
        return tuple(items)
    return value


def _llm_call_from_args(args: tuple[Any, ...], kwargs: dict[str, Any]) -> DifyLegacyLLMCall:
    return DifyLegacyLLMCall.from_args_kwargs(args, kwargs)


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
    conversation_id = (
        _optional_text(getattr(conversation, "id", None))
        or _optional_text(getattr(application_generate_entity, "conversation_id", None))
        or _optional_text(getattr(message, "conversation_id", None))
    )
    metadata = {
        "adapter": "dify_agent_chat",
        "dify_runtime": "agent_chat",
        "tenant_id": _optional_text(getattr(app_config, "tenant_id", None)),
        "app_id": _optional_text(getattr(app_config, "app_id", None)),
        "conversation_id": conversation_id,
        "message_id": _optional_text(getattr(message, "id", None)),
        "user_id": _optional_text(getattr(application_generate_entity, "user_id", None)),
        "task_id": _optional_text(getattr(application_generate_entity, "task_id", None)),
        "invoke_from": _optional_text(getattr(application_generate_entity, "invoke_from", None)),
        "agent_strategy": _optional_text(getattr(agent, "strategy", None)),
    }
    return {key: value for key, value in metadata.items() if value not in (None, "")}


def _make_guard(metadata: dict[str, Any]) -> Any:
    from agentguard import AgentGuard

    metadata = _metadata_with_registered_agent_chat_agent(metadata)
    app_or_agent_id = metadata.get("app_id") or metadata.get("conversation_id") or "dify-agent-chat"
    fallback_agent_id = f"dify-agent-chat:{app_or_agent_id}"
    agent_id = _optional_text(metadata.get("agentguard_agent_id")) or fallback_agent_id
    session_id = _session_id_from_metadata(metadata, app_or_agent_id)
    runtime_auth = _runtime_auth_for_metadata(metadata, agent_id=agent_id, fallback_session_id=session_id)
    if runtime_auth is not None:
        session_id = runtime_auth.session_id or session_id
        if runtime_auth.canonical_user_id:
            metadata["agentguard_user_id"] = runtime_auth.canonical_user_id
            metadata["user_id"] = runtime_auth.canonical_user_id
        metadata["agentguard_session_id"] = session_id
    guard = AgentGuard(
        session_id,
        user_id=runtime_auth.canonical_user_id if runtime_auth is not None else metadata.get("user_id"),
        agent_id=agent_id,
        policy=os.getenv("AGENTGUARD_POLICY") or None,
        server_url=os.getenv("AGENTGUARD_SERVER_URL") or None,
        api_key=os.getenv("AGENTGUARD_API_KEY") or None,
        environment=os.getenv("AGENTGUARD_ENVIRONMENT") or "dify",
        sandbox="noop",
        plugin_config=_plugin_config(),
        session_token=runtime_auth.session_token if runtime_auth is not None else None,
        dpop_proof_factory=runtime_auth.proof if runtime_auth is not None else None,
        use_dpop_auth=runtime_auth is not None,
        legacy_identity_headers=runtime_auth is None,
        auto_register_session=runtime_auth is None,
        auto_close_runtime_session=runtime_auth is None,
    )
    guard.context.metadata.update(metadata)
    if metadata.get("task_id"):
        guard.context.task_id = str(metadata["task_id"])
    return guard


def _session_id_from_metadata(metadata: dict[str, Any], agent_id: str) -> str:
    return (
        metadata.get("conversation_id")
        or metadata.get("message_id")
        or metadata.get("task_id")
        or f"dify-agent-chat:{agent_id}"
    )


def _runtime_auth_for_metadata(
    metadata: dict[str, Any],
    *,
    agent_id: str,
    fallback_session_id: str,
) -> Any | None:
    account_email = _runtime_account_email(metadata)
    external_session_id = _external_session_id_from_metadata(metadata)
    server_url = os.getenv("AGENTGUARD_SERVER_URL") or None
    if not server_url or not account_email:
        return None
    auth_metadata = {
        **metadata,
        "external_provider": "dify",
        "external_account_email": account_email,
        "dify_user_email": account_email,
    }
    if external_session_id:
        auth_metadata["external_session_id"] = external_session_id
    else:
        auth_metadata["agentguard_internal_session_key"] = _internal_session_key_from_metadata(
            metadata,
            fallback_session_id=fallback_session_id,
        )
    try:
        runtime_auth = _runtime_auth_manager.ensure(
            server_url=server_url,
            api_key=os.getenv("AGENTGUARD_API_KEY") or None,
            agent_id=agent_id,
            agent_identity_key_id=_optional_text(metadata.get("agent_identity_key_id")),
            external_session_id=external_session_id,
            cache_key=external_session_id
            or _internal_session_key_from_metadata(metadata, fallback_session_id=fallback_session_id),
            account_email=account_email,
            external_user_id=_optional_text(metadata.get("user_id")),
            metadata=auth_metadata,
            timeout_s=_env_float("AGENTGUARD_DIFY_RUNTIME_AUTH_TIMEOUT_S", 5.0),
            retries=int(_env_float("AGENTGUARD_DIFY_RUNTIME_AUTH_RETRIES", 1.0)),
        )
    except Exception as exc:
        _LOGGER.warning(
            "AgentGuard Dify Agent Chat runtime auth failed for app_id=%s external_session_id=%s: %s",
            metadata.get("app_id"),
            external_session_id,
            exc,
        )
        raise AdapterError(f"AgentGuard Dify runtime auth failed: {exc}") from exc
    if runtime_auth is None or not getattr(runtime_auth, "session_token", None):
        raise AdapterError("AgentGuard Dify runtime auth failed: server returned no session token")
    return runtime_auth


def _external_session_id_from_metadata(metadata: dict[str, Any]) -> str | None:
    return _optional_text(metadata.get("conversation_id"))


def _internal_session_key_from_metadata(metadata: dict[str, Any], *, fallback_session_id: str) -> str:
    app_id = _optional_text(metadata.get("app_id")) or "app"
    user_id = _optional_text(metadata.get("user_id")) or "user"
    parts = ["agentguard-internal:dify-agent-chat", app_id, user_id]
    if app_id == "app" and user_id == "user":
        parts.append(fallback_session_id)
    return ":".join(parts)


def _runtime_account_email(metadata: dict[str, Any]) -> str | None:
    for key in ("dify_user_email", "external_account_email", "account_email", "user_email"):
        email = _optional_text(metadata.get(key))
        if email and "@" in email:
            return email.lower()
    app_id = _optional_text(metadata.get("app_id"))
    if not app_id:
        return None
    try:
        app = _agent_chat_app_by_id(app_id)
        return _dify_account_email_for_app(app) if app is not None else None
    except Exception:
        return None


def _metadata_with_registered_agent_chat_agent(metadata: dict[str, Any]) -> dict[str, Any]:
    if _optional_text(metadata.get("agentguard_agent_id")):
        return metadata
    registration = _runtime_agent_chat_registration(metadata)
    if not registration:
        return metadata
    registered_agent = registration.get("agent") or {}
    canonical_agent_id = _optional_text(registered_agent.get("agent_id"))
    if not canonical_agent_id:
        return metadata
    enriched = dict(metadata)
    app_id = _optional_text(enriched.get("app_id"))
    enriched["agentguard_agent_id"] = canonical_agent_id
    if app_id:
        enriched["external_agent_id"] = f"dify-agent-chat:{app_id}"
    if registered_agent.get("agent_identity_code"):
        enriched["agent_identity_code"] = registered_agent.get("agent_identity_code")
    if registered_agent.get("public_key_thumbprint"):
        enriched["agent_public_key_thumbprint"] = registered_agent.get("public_key_thumbprint")
    if registration.get("agent_identity_key_id"):
        enriched["agent_identity_key_id"] = registration.get("agent_identity_key_id")
    user_agent = registration.get("user_agent") or {}
    if "bound" in user_agent:
        enriched["agentguard_user_bound"] = bool(user_agent.get("bound"))
    account_email = _runtime_account_email(enriched)
    if account_email:
        enriched["external_provider"] = "dify"
        enriched["external_account_email"] = account_email
        enriched["dify_user_email"] = account_email
    return enriched


def _runtime_agent_chat_registration(metadata: dict[str, Any]) -> dict[str, Any] | None:
    app_id = _optional_text(metadata.get("app_id"))
    if not app_id or not os.getenv("AGENTGUARD_SERVER_URL"):
        return None
    agent_type = "agent_chat"
    tenant_id = _optional_text(metadata.get("tenant_id"))
    provider_instance_id = _dify_provider_instance_id()
    cache_key = ("dify", provider_instance_id or "", tenant_id or "", app_id, agent_type)
    with _runtime_agent_registrations_lock:
        cached = _runtime_agent_registrations.get(cache_key)
    if cached is not None:
        return cached

    app = None
    try:
        app = _agent_chat_app_by_id(app_id)
    except Exception:
        app = None
    account_email = _runtime_account_email(metadata)
    name = _optional_text(getattr(app, "name", None)) or _optional_text(metadata.get("app_name"))
    description = _optional_text(getattr(app, "description", None)) or _optional_text(metadata.get("app_description"))
    remote = RemoteGuardClient(
        os.getenv("AGENTGUARD_SERVER_URL") or None,
        api_key=os.getenv("AGENTGUARD_API_KEY") or None,
        timeout_s=_env_float("AGENTGUARD_DIFY_CATALOG_SYNC_TIMEOUT_S", 5.0),
        retries=int(_env_float("AGENTGUARD_DIFY_CATALOG_SYNC_RETRIES", 1.0)),
    )
    if not remote.enabled:
        return None
    registration_metadata = dict(metadata)
    registration_metadata.update(
        {
            "adapter": "dify_agent_chat",
            "dify_runtime": "agent_chat",
            "runtime_registration": True,
            "app_id": app_id,
            "tenant_id": tenant_id,
            "external_agent_id": f"dify-agent-chat:{app_id}",
        }
    )
    if account_email:
        registration_metadata.update(
            {
                "external_provider": "dify",
                "external_account_email": account_email,
                "dify_user_email": account_email,
            }
        )
    try:
        registration = _register_dify_agent(
            remote,
            agent_id=f"dify-agent-chat:{app_id}",
            agent_type=agent_type,
            external_agent_id=app_id,
            tenant_id=tenant_id,
            account_email=account_email,
            name=name,
            description=description,
            metadata=registration_metadata,
        )
    except Exception:
        return None
    if not registration:
        return None
    with _runtime_agent_registrations_lock:
        _runtime_agent_registrations[cache_key] = registration
    return registration


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
    return _LEGACY_LLM_NORMALIZER.normalize_messages(messages)


def _prompt_message_to_message(message: Any) -> dict[str, Any]:
    return _LEGACY_LLM_NORMALIZER.prompt_message_to_message(message)


def _message_role(message: Any) -> str:
    return _LEGACY_LLM_NORMALIZER.message_role(message)


def _llm_stream_output_payload(chunks: list[Any]) -> dict[str, Any]:
    return _LEGACY_LLM_NORMALIZER.stream_output_payload(chunks)


def _llm_output_payload(output: Any) -> dict[str, Any]:
    return _LEGACY_LLM_NORMALIZER.output_payload(output)


def _llm_output_payload_from_text(
    output: str | None,
    *,
    thought: str | None = None,
    final_output: str | None = None,
) -> dict[str, Any]:
    return _LEGACY_LLM_NORMALIZER.output_payload_from_text(
        output,
        thought=thought,
        final_output=final_output,
    )


def _extract_llm_thought(value: Any) -> str | None:
    return _LEGACY_LLM_NORMALIZER.extract_llm_thought(value)


def _extract_llm_thought_from_blocks(blocks: list[Any]) -> str | None:
    return _LEGACY_LLM_NORMALIZER.extract_llm_thought_from_blocks(blocks)


def _first_non_empty_text(value: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        text = _content_to_optional_text(value.get(key))
        if text is not None:
            return text
    return None


def _first_non_empty_attr(value: Any, *keys: str) -> str | None:
    for key in keys:
        text = _content_to_optional_text(getattr(value, key, None))
        if text is not None:
            return text
    return None


@dataclass(frozen=True)
class _ParsedLLMOutput:
    thought: str | None
    final_output: str | None


_THOUGHT_TAG_RE = re.compile(
    r"<(?P<tag>think|thought|reason|reasoning)\b[^>]*>(?P<body>.*?)</(?P=tag)>",
    flags=re.IGNORECASE | re.DOTALL,
)
_FINAL_TAG_RE = re.compile(
    r"<(?P<tag>answer|final|final_output)\b[^>]*>(?P<body>.*?)</(?P=tag)>",
    flags=re.IGNORECASE | re.DOTALL,
)


def _parse_tagged_llm_output(output: str) -> _ParsedLLMOutput:
    parsed = parse_tagged_llm_output(output)
    return _ParsedLLMOutput(thought=parsed.thought, final_output=parsed.final_output)


def _content_to_text(value: Any) -> str:
    return _LEGACY_LLM_NORMALIZER.content_to_text(value)


def _content_to_optional_text(value: Any) -> str | None:
    return _LEGACY_LLM_NORMALIZER.content_to_optional_text(value)


def _normalize_value(value: Any) -> Any:
    return _LEGACY_LLM_NORMALIZER.normalize_value(value)


def _blocked_llm_value(decision: GuardDecision) -> str | None:
    if decision.is_allow or decision.decision_type in {
        DecisionType.LOG_ONLY,
        DecisionType.MODIFY_LLM_INPUT,
        DecisionType.MODIFY_LLM_OUTPUT,
    }:
        return None
    if decision.decision_type == DecisionType.SANITIZE:
        return f"AgentGuard sanitized Dify Agent Chat LLM call: {decision.reason}"
    return f"AgentGuard blocked Dify Agent Chat LLM call: {decision.reason}"


def _blocked_tool_value(decision: GuardDecision, tool: str) -> str | None:
    if decision.is_allow or decision.decision_type in {
        DecisionType.LOG_ONLY,
        DecisionType.MODIFY_TOOL_INVOKE,
        DecisionType.MODIFY_TOOL_RESULT,
    }:
        return None
    if decision.decision_type == DecisionType.HUMAN_CHECK:
        return safe_dumps({"agentguard": "pending", "tool": tool, "reason": decision.reason})
    if decision.decision_type == DecisionType.REQUIRE_REMOTE_REVIEW:
        return safe_dumps({"agentguard": "remote_review_required", "tool": tool, "reason": decision.reason})
    if decision.decision_type == DecisionType.SANITIZE:
        return safe_dumps({"agentguard": "sanitized", "tool": tool, "reason": decision.reason})
    return safe_dumps({"agentguard": "blocked", "tool": tool, "reason": decision.reason})


def _blocked_result_value(decision: GuardDecision, tool: str) -> str | None:
    if decision.is_allow or decision.decision_type in {
        DecisionType.LOG_ONLY,
        DecisionType.MODIFY_TOOL_RESULT,
    }:
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


def _catalog_sync_loop(app: Any | None = None) -> None:
    initial_delay = _env_float("AGENTGUARD_DIFY_CATALOG_INITIAL_DELAY_S", 5.0)
    interval = _env_float("AGENTGUARD_DIFY_CATALOG_SYNC_INTERVAL_S", 60.0)
    if initial_delay > 0:
        time.sleep(initial_delay)
    try:
        _sync_published_agent_catalog_with_context(app=app)
    except Exception as exc:
        _LOGGER.warning("AgentGuard Dify agent-chat catalog sync failed: %s", exc)
    while interval > 0:
        time.sleep(interval)
        try:
            _clear_catalog_fingerprints()
            _sync_published_agent_catalog_with_context(app=app)
        except Exception as exc:
            _LOGGER.warning("AgentGuard Dify agent-chat catalog sync failed: %s", exc)


def _sync_published_agent_catalog_with_context(app: Any | None = None) -> dict[str, Any]:
    if app is not None:
        with app.app_context():
            return _sync_published_agent_catalog_once()

    try:
        from flask import has_app_context  # type: ignore

        if has_app_context():
            return _sync_published_agent_catalog_once()
    except Exception:
        pass

    app = _dify_flask_app()
    if app is None:
        return {"app_count": 0, "synced": [], "reason": "app_context_unavailable"}
    with app.app_context():
        return _sync_published_agent_catalog_once()


def _dify_flask_app() -> Any | None:
    return get_dify_flask_app()


def register_dify_flask_app(app: Any) -> bool:
    return _register_shared_dify_flask_app(app)


def _on_app_model_config_updated(sender: Any, **kwargs: Any) -> None:
    app_model_config = kwargs.get("app_model_config")
    if app_model_config is not None and not _app_model_config_has_agent_mode(app_model_config):
        return
    app_id = _optional_text(getattr(sender, "id", None) or kwargs.get("app_id"))
    if not app_id or not _app_allowed(app_id):
        return
    _schedule_agent_chat_catalog_sync(app_id)


def _on_agent_chat_app_updated(sender: Any, **_kwargs: Any) -> None:
    if not _is_agent_chat_app(sender):
        return
    app_id = _optional_text(getattr(sender, "id", None))
    if not app_id or not _app_allowed(app_id):
        return
    _schedule_agent_chat_catalog_sync(app_id)


def _is_agent_chat_app(app: Any) -> bool:
    raw_mode = getattr(app, "mode", None)
    mode = (_optional_text(getattr(raw_mode, "value", raw_mode)) or "").replace("_", "-").lower()
    return mode == "agent-chat" or bool(getattr(app, "is_agent", False))


def _app_model_config_has_agent_mode(app_model_config: Any) -> bool:
    if isinstance(app_model_config, dict):
        return app_model_config.get("agent_mode") is not None
    raw = getattr(app_model_config, "agent_mode", None)
    if raw not in (None, ""):
        return True
    try:
        value = getattr(app_model_config, "agent_mode_dict", None)
    except Exception:
        return False
    return bool(value)


def _schedule_agent_chat_catalog_sync(app_id: str) -> None:
    if not _catalog_sync_enabled() or not os.getenv("AGENTGUARD_SERVER_URL"):
        return
    app = _dify_flask_app()

    def _worker() -> None:
        delay_s = _env_float("AGENTGUARD_DIFY_AGENT_CHAT_UPDATE_SYNC_DELAY_S", 0.5)
        retries = max(1, int(_env_float("AGENTGUARD_DIFY_AGENT_CHAT_UPDATE_SYNC_RETRIES", 5.0)))
        retry_reasons = {"app_context_unavailable", "app_not_found"}
        for attempt in range(retries):
            if delay_s > 0:
                time.sleep(delay_s)
            try:
                result = _sync_agent_chat_app_by_id_with_context(app_id, app=app)
            except Exception as exc:
                result = {"reason": "sync_failed", "error": str(exc)}
            if result.get("synced"):
                return
            reason = _optional_text(result.get("reason"))
            if reason not in retry_reasons or attempt >= retries - 1:
                _LOGGER.warning(
                    "AgentGuard Dify agent-chat config catalog sync did not update app_id=%s reason=%s result=%s",
                    app_id,
                    reason or "unknown",
                    result,
                )
                return

    threading.Thread(
        target=_worker,
        name=f"agentguard-dify-agent-chat-catalog-sync-{app_id}",
        daemon=True,
    ).start()


def _sync_agent_chat_app_by_id_with_context(app_id: str, app: Any | None = None) -> dict[str, Any]:
    if app is not None:
        with app.app_context():
            return _sync_agent_chat_app_by_id_once(app_id)

    try:
        from flask import has_app_context  # type: ignore

        if has_app_context():
            return _sync_agent_chat_app_by_id_once(app_id)
    except Exception:
        pass

    app = _dify_flask_app()
    if app is None:
        return {"synced": [], "reason": "app_context_unavailable"}
    with app.app_context():
        return _sync_agent_chat_app_by_id_once(app_id)


def _sync_agent_chat_app_by_id_once(app_id: str) -> dict[str, Any]:
    app = _agent_chat_app_by_id(app_id)
    if app is None:
        return {"synced": [], "reason": "app_not_found"}
    result = _sync_app_tool_catalog(app)
    return {"synced": [result] if result is not None else []}


def _sync_published_agent_catalog_once() -> dict[str, Any]:
    apps = _published_agent_chat_apps()
    synced: list[dict[str, Any]] = []
    for app in apps:
        result = _sync_app_tool_catalog(app)
        if result is not None:
            synced.append(result)
    catalog_sync = _sync_dify_agent_catalog_to_agentguard(
        agent_type="agent_chat",
        external_agent_ids=[
            app_id
            for app_id in (_optional_text(getattr(app, "id", None)) for app in apps)
            if app_id
        ],
    )
    result: dict[str, Any] = {"app_count": len(apps), "synced": synced}
    if catalog_sync and not catalog_sync.get("skipped"):
        result["agent_catalog_sync"] = catalog_sync
    return result


def _sync_dify_agent_catalog_to_agentguard(
    *,
    agent_type: str,
    external_agent_ids: list[str],
) -> dict[str, Any] | None:
    if _env_csv("AGENTGUARD_DIFY_APP_IDS"):
        return {"skipped": True, "reason": "app_id_filter_active"}
    remote = RemoteGuardClient(
        os.getenv("AGENTGUARD_SERVER_URL") or None,
        api_key=os.getenv("AGENTGUARD_API_KEY") or None,
        timeout_s=_env_float("AGENTGUARD_DIFY_CATALOG_SYNC_TIMEOUT_S", 5.0),
        retries=int(_env_float("AGENTGUARD_DIFY_CATALOG_SYNC_RETRIES", 1.0)),
    )
    sync_agents = getattr(remote, "sync_agents", None)
    if not remote.enabled or not callable(sync_agents):
        return None
    return sync_agents(
        {
            "provider": "dify",
            "provider_instance_id": _dify_provider_instance_id(),
            "agent_type": agent_type,
            "external_agent_ids": sorted(set(external_agent_ids)),
            "metadata": {
                "adapter": "dify_agent_chat",
                "dify_runtime": "agent_chat",
                "catalog_sync": True,
            },
        }
    )


def _published_agent_chat_apps() -> list[Any]:
    from extensions.ext_database import db  # type: ignore
    from models.model import App, AppMode  # type: ignore
    from sqlalchemy import select  # type: ignore

    stmt = select(App).where(App.mode == AppMode.AGENT_CHAT.value)
    app_ids = _env_csv("AGENTGUARD_DIFY_APP_IDS")
    if app_ids:
        stmt = stmt.where(App.id.in_(app_ids))
    return list(db.session.scalars(stmt).all())


def _agent_chat_app_by_id(app_id: str) -> Any | None:
    from extensions.ext_database import db  # type: ignore
    from models.model import App, AppMode  # type: ignore
    from sqlalchemy import select  # type: ignore

    stmt = select(App).where(App.id == app_id, App.mode == AppMode.AGENT_CHAT.value)
    return db.session.scalar(stmt)


def _sync_app_tool_catalog(app: Any) -> dict[str, Any] | None:
    app_id = _optional_text(getattr(app, "id", None))
    if not app_id or not _app_allowed(app_id):
        return None
    app_model_config = getattr(app, "app_model_config", None)
    if app_model_config is None:
        return None
    agent_mode = getattr(app_model_config, "agent_mode_dict", {}) or {}
    if not isinstance(agent_mode, dict) or not agent_mode.get("enabled"):
        return _sync_tools_to_agentguard(app, [])

    tools: list[dict[str, Any]] = []
    for tool_config in agent_mode.get("tools") or []:
        if not isinstance(tool_config, dict) or not tool_config.get("enabled"):
            continue
        tool_payload = _catalog_tool_from_config(app, tool_config)
        if tool_payload is not None:
            tools.append(tool_payload)
    return _sync_tools_to_agentguard(app, tools)


def _catalog_tool_from_config(app: Any, tool_config: dict[str, Any]) -> dict[str, Any] | None:
    tool_name = _optional_text(tool_config.get("tool_name"))
    if not tool_name:
        return None
    runtime = _tool_runtime_from_config(app, tool_config)
    provider_id = _optional_text(tool_config.get("provider_id"))
    provider_type = _optional_text(tool_config.get("provider_type"))
    label = _optional_text(tool_config.get("tool_label"))
    tags = ["dify_agent_chat_tool"]
    for tag in (provider_type, provider_id):
        if tag:
            tags.append(tag)
    return {
        "name": tool_name,
        "description": _tool_description(runtime) if runtime is not None else label or tool_name,
        "input_params": _tool_required_args(runtime) if runtime is not None else _config_required_args(tool_config),
        "capabilities": tags,
        "labels": {
            "boundary": "internal",
            "sensitivity": "low",
            "integrity": "trusted",
            "tags": tags,
        },
        "metadata": {
            "source": "dify_agent_chat_catalog",
            "provider_id": provider_id,
            "provider_type": provider_type,
            "tool_label": label,
        },
    }


def _tool_runtime_from_config(app: Any, tool_config: dict[str, Any]) -> Any | None:
    try:
        from core.agent.entities import AgentToolEntity  # type: ignore
        from core.tools.tool_manager import ToolManager  # type: ignore

        return ToolManager.get_agent_tool_runtime(
            tenant_id=str(getattr(app, "tenant_id", "")),
            app_id=str(getattr(app, "id", "")),
            agent_tool=AgentToolEntity.model_validate(tool_config),
            user_id=_optional_text(getattr(app, "updated_by", None)) or _optional_text(getattr(app, "created_by", None)),
        )
    except Exception:
        return None


def _config_required_args(tool_config: dict[str, Any]) -> list[str]:
    params = tool_config.get("tool_parameters")
    if not isinstance(params, dict):
        return []
    return [str(key) for key, value in params.items() if value is None]


def _sync_tools_to_agentguard(app: Any, tools: list[dict[str, Any]]) -> dict[str, Any] | None:
    app_id = _optional_text(getattr(app, "id", None))
    if not app_id:
        return None
    config_id = _optional_text(getattr(app, "app_model_config_id", None))
    agent_id = f"dify-agent-chat:{app_id}"
    account_email = _dify_account_email_for_app(app)
    fingerprint_key = f"agent_chat:{agent_id}"
    fingerprint = _catalog_fingerprint(tools, f"{config_id or ''}:{account_email or ''}")
    session_id = f"dify-agent-chat-catalog:{app_id}:{config_id or 'active'}"
    session_key = _catalog_session_key(app_id, config_id)
    metadata = {
        "adapter": "dify_agent_chat",
        "dify_runtime": "agent_chat",
        "catalog_sync": True,
        "app_id": app_id,
        "tenant_id": _optional_text(getattr(app, "tenant_id", None)),
        "app_model_config_id": config_id,
        "client_session_key": session_key,
    }
    if account_email:
        metadata.update(
            {
                "external_provider": "dify",
                "external_account_email": account_email,
                "dify_user_email": account_email,
            }
        )
    context = RuntimeContext(
        session_id=session_id,
        agent_id=agent_id,
        user_id=None,
        environment=os.getenv("AGENTGUARD_ENVIRONMENT") or "dify",
        metadata=metadata,
    )
    remote = RemoteGuardClient(
        os.getenv("AGENTGUARD_SERVER_URL") or None,
        api_key=os.getenv("AGENTGUARD_API_KEY") or None,
        session_id=session_id,
        agent_id=agent_id,
        session_key=session_key,
        timeout_s=_env_float("AGENTGUARD_DIFY_CATALOG_SYNC_TIMEOUT_S", 5.0),
        retries=int(_env_float("AGENTGUARD_DIFY_CATALOG_SYNC_RETRIES", 1.0)),
    )
    if not remote.enabled:
        return None
    registration = _register_dify_agent(
        remote,
        agent_id=agent_id,
        agent_type="agent_chat",
        external_agent_id=app_id,
        tenant_id=_optional_text(getattr(app, "tenant_id", None)),
        account_email=account_email,
        name=_optional_text(getattr(app, "name", None)),
        description=_optional_text(getattr(app, "description", None)),
        metadata=metadata,
    )
    if registration:
        registered_agent = registration.get("agent") or {}
        canonical_agent_id = _optional_text(registered_agent.get("agent_id"))
        if canonical_agent_id:
            agent_id = canonical_agent_id
            context.agent_id = canonical_agent_id
            remote.agent_id = canonical_agent_id
            metadata["external_agent_id"] = f"dify-agent-chat:{app_id}"
        metadata["agent_identity_code"] = registered_agent.get("agent_identity_code")
        metadata["agent_public_key_thumbprint"] = (
            registered_agent.get("public_key_thumbprint")
        )
        metadata["agentguard_user_bound"] = bool((registration.get("user_agent") or {}).get("bound"))
    if _catalog_fingerprint_unchanged(fingerprint_key, fingerprint):
        return {
            "app_id": app_id,
            "agent_id": agent_id,
            "tool_count": len(tools),
            "skipped": True,
            "reason": "unchanged",
        }
    result = remote.sync_tools(context, tools)
    _remember_catalog_fingerprint(fingerprint_key, fingerprint)
    return {"app_id": app_id, "agent_id": agent_id, "tool_count": result.get("tool_count", len(tools))}


def _dify_account_email_for_app(app: Any) -> str | None:
    for attr in ("dify_user_email", "account_email", "user_email", "email"):
        email = _optional_text(getattr(app, attr, None))
        if email and "@" in email:
            return email.lower()

    account_id = (
        _optional_text(getattr(app, "updated_by", None))
        or _optional_text(getattr(app, "created_by", None))
    )
    if not account_id:
        return None

    try:
        from extensions.ext_database import db  # type: ignore
        from sqlalchemy import select  # type: ignore
    except Exception:
        return None

    account_cls = None
    for module_name in ("models.account", "models.model"):
        try:
            module = __import__(module_name, fromlist=["Account"])
            account_cls = getattr(module, "Account", None)
        except Exception:
            account_cls = None
        if account_cls is not None:
            break
    if account_cls is None:
        return None

    try:
        stmt = select(account_cls).where(account_cls.id == account_id)
        session = db.session
        account = None
        if hasattr(session, "scalar"):
            account = session.scalar(stmt)
        elif hasattr(session, "execute"):
            result = session.execute(stmt)
            if hasattr(result, "scalar_one_or_none"):
                account = result.scalar_one_or_none()
            elif hasattr(result, "scalars"):
                scalars = result.scalars()
                account = scalars.first() if hasattr(scalars, "first") else None
        email = _optional_text(getattr(account, "email", None))
    except Exception:
        return None
    return email.lower() if email and "@" in email else None


def _register_dify_agent(
    remote: RemoteGuardClient,
    *,
    agent_id: str,
    agent_type: str,
    external_agent_id: str,
    tenant_id: str | None,
    account_email: str | None,
    name: str | None,
    description: str | None,
    metadata: dict[str, Any],
) -> dict[str, Any] | None:
    register = getattr(remote, "register_agent", None)
    if not callable(register):
        return None
    provider_instance_id = _dify_provider_instance_id()
    key_id = agent_identity_key_id(
        provider="dify",
        provider_instance_id=provider_instance_id,
        tenant_id=tenant_id,
        external_agent_id=external_agent_id,
        agent_type=agent_type,
    )
    payload = build_agent_registration_payload(
        provider="dify",
        provider_instance_id=provider_instance_id,
        tenant_id=tenant_id,
        external_agent_id=external_agent_id,
        agent_type=agent_type,
        name=name,
        description=description,
        account_email=account_email,
        metadata=metadata,
    )
    registration = register(payload)
    if isinstance(registration, dict):
        registration = dict(registration)
        registration["agent_identity_key_id"] = key_id
    return registration


def _dify_provider_instance_id() -> str:
    return (
        os.getenv("AGENTGUARD_DIFY_INSTANCE_ID")
        or os.getenv("DIFY_DEPLOYMENT_ID")
        or os.getenv("DIFY_BASE_URL")
        or os.getenv("CONSOLE_API_URL")
        or ""
    ).strip()


def _catalog_session_key(app_id: str, config_id: str | None = None) -> str:
    seed = os.getenv("AGENTGUARD_DIFY_CATALOG_SESSION_KEY")
    if seed:
        return seed
    return f"sk-dify-catalog-{app_id}-{config_id or 'active'}"


def _catalog_fingerprint(tools: list[dict[str, Any]], version: str | None = None) -> str:
    payload = {"version": version, "tools": tools}
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _catalog_fingerprint_unchanged(key: str, fingerprint: str) -> bool:
    with _catalog_fingerprints_lock:
        return _catalog_fingerprints.get(key) == fingerprint


def _remember_catalog_fingerprint(key: str, fingerprint: str) -> None:
    with _catalog_fingerprints_lock:
        _catalog_fingerprints[key] = fingerprint


def _clear_catalog_fingerprints() -> None:
    with _catalog_fingerprints_lock:
        _catalog_fingerprints.clear()


def _env_enabled() -> bool:
    specific = os.getenv("AGENTGUARD_DIFY_AGENT_CHAT_ENABLED")
    if specific is not None:
        return specific.strip().lower() in {"1", "true", "yes", "on"}
    return os.getenv("AGENTGUARD_ENABLED", "").strip().lower() in {"1", "true", "yes", "on"}


def _catalog_sync_enabled() -> bool:
    specific = os.getenv("AGENTGUARD_DIFY_CATALOG_SYNC_ENABLED")
    if specific is not None:
        return specific.strip().lower() in {"1", "true", "yes", "on"}
    return _env_enabled()


def _catalog_sync_process_allowed() -> bool:
    role = (os.getenv("AGENTGUARD_DIFY_CATALOG_SYNC_PROCESS") or "api").strip().lower()
    if role in {"all", "*"}:
        return True
    argv = " ".join(sys.argv).lower()
    if role == "api":
        return "gunicorn" in argv and "app:socketio_app" in argv
    if role == "worker":
        return "celery" in argv
    return role in argv


def _app_allowed(app_id: Any) -> bool:
    app_ids = _env_csv("AGENTGUARD_DIFY_APP_IDS")
    if not app_ids:
        return True
    return str(app_id or "").strip() in app_ids


def _env_csv(name: str) -> set[str]:
    raw = os.getenv(name, "")
    return {part.strip() for part in raw.split(",") if part.strip()}


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


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


__all__ = ["install_dify_agent_chat_adapter", "register_dify_flask_app"]

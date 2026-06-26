"""OpenAI Agents SDK adapter (best-effort, optional dependency)."""
from __future__ import annotations

import asyncio
import copy
import dataclasses
import functools
import inspect
import json
import re
import sys
import threading
from typing import Any

from agentguard.adapters.agent.base import BaseAgentAdapter, LLMBinding, ToolBinding
from agentguard.adapters.agent.normalization import LLMOutputNormalization
from agentguard.adapters.agent.patching import (
    guard_tool_after,
    guard_tool_before,
    is_guarded,
    patch_llm_methods,
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
        resolved_tools = _resolve_openai_agent_tools(agent)
        if resolved_tools is not _UNRESOLVED:
            bindings, needs_fallback = self._collect_openai_tool_bindings(
                resolved_tools,
                source="get_all_tools",
            )
            if not needs_fallback:
                return bindings

        tools = getattr(agent, "tools", None) or getattr(agent, "_tools", None)
        fallback_bindings, _ = self._collect_openai_tool_bindings(
            tools,
            source="agent.tools",
            use_container_patch=True,
        )
        if resolved_tools is not _UNRESOLVED:
            return bindings + fallback_bindings
        return fallback_bindings

    def _collect_openai_tool_bindings(
        self,
        tools: Any,
        *,
        source: str,
        use_container_patch: bool = False,
    ) -> tuple[list[ToolBinding], bool]:
        bindings: list[ToolBinding] = []
        needs_fallback = False
        for name, tool, container, key in _iter_openai_tool_entries(tools, use_container_patch=use_container_patch):
            metadata = {"source": source}
            if hasattr(tool, "on_invoke_tool") and hasattr(tool, "name"):
                original = getattr(tool, "on_invoke_tool", None)
                if callable(original) and not is_guarded(original):
                    bindings.append(
                        self.build_tool_binding(
                            name=name,
                            fn=original,
                            owner=tool,
                            attr="on_invoke_tool",
                            tool=tool,
                            installer=_install_openai_tool_binding,
                            metadata=metadata,
                        )
                    )
                continue

            if callable(tool) and container is not None:
                bindings.append(
                    self.build_tool_binding(
                        name=name,
                        fn=tool,
                        container=container,
                        key=key,
                        tool=tool,
                        metadata=metadata,
                    )
                )
                continue

            if callable(tool):
                needs_fallback = True

        return bindings, needs_fallback

    def getllm(self, agent: Any):
        bindings = []
        seen: set[int] = set()
        for candidate in _iter_openai_llm_candidates(agent):
            if id(candidate) in seen:
                continue
            seen.add(id(candidate))
            model_bindings = self.collect_llm_methods(
                candidate,
                methods=("get_response", "stream_response"),
            )
            if model_bindings:
                bindings.extend(model_bindings)
                continue

            bindings.extend(
                self.collect_llm_methods(
                    candidate,
                    methods=("create", "complete", "completion", "generate", "invoke", "ainvoke"),
                )
            )
            for resource, methods in _iter_openai_llm_resources(candidate):
                if resource is None or id(resource) in seen:
                    continue
                seen.add(id(resource))
                bindings.extend(self.collect_llm_methods(resource, methods=methods))
            for target, methods in _iter_openai_nested_llm_targets(candidate):
                if id(target) in seen:
                    continue
                seen.add(id(target))
                bindings.extend(self.collect_llm_methods(target, methods=methods))
        if _needs_openai_runner_patch(agent, bindings):
            runner_binding = _build_openai_runner_binding(agent)
            if runner_binding is not None:
                bindings.append(runner_binding)
        return bindings

    def normalize_llm_output(
        self,
        *,
        label: str,
        output: Any,
        fn: Any = None,
        owner: Any = None,
    ) -> LLMOutputNormalization:
        _ = fn
        return LLMOutputNormalization(
            payload=_normalize_openai_agents_llm_output(output),
            metadata=self._metadata(label=label, owner=owner),
        )

_UNRESOLVED = object()


def _normalize_openai_agents_llm_output(value: Any) -> Any:
    normalized = _normalize_openai_agents_value(value)
    extracted = _extract_openai_agents_llm_output_fields(normalized)
    return extracted if extracted is not None else normalized


def _extract_openai_agents_llm_output_fields(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None

    candidate = value
    for nested_key in ("message", "chat_message"):
        nested = value.get(nested_key)
        if isinstance(nested, dict):
            candidate = nested
            break

    if _is_openai_agents_tool_call_only(candidate):
        return {"output": "", "final_output": None}
    if candidate is not value and _is_openai_agents_tool_call_only(value):
        return {"output": "", "final_output": None}

    thought = _extract_openai_agents_thought(candidate)
    if thought is None and candidate is not value:
        thought = _extract_openai_agents_thought(value)

    visible = _extract_openai_agents_visible_text(candidate)
    if visible is None and candidate is not value:
        visible = _extract_openai_agents_visible_text(value)
    if visible is None:
        return None

    final_output = visible
    if thought is None:
        parsed = _parse_tagged_openai_agents_output(visible)
        thought = parsed.thought
        final_output = parsed.final_output

    payload = dict(value)
    payload["output"] = visible
    payload["final_output"] = final_output
    if thought is not None:
        payload["thought"] = thought
    return payload


def _normalize_openai_agents_value(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    if isinstance(value, dict):
        return {str(key): _normalize_openai_agents_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_normalize_openai_agents_value(item) for item in value]
    if dataclasses.is_dataclass(value):
        try:
            return _normalize_openai_agents_value(dataclasses.asdict(value))
        except Exception:
            pass

    for attr in ("model_dump", "to_dict", "dict"):
        dumper = getattr(value, attr, None)
        if callable(dumper):
            try:
                dumped = dumper()
            except Exception:
                continue
            return _normalize_openai_agents_value(dumped)

    attrs = {}
    for key in (
        "output",
        "content",
        "text",
        "message",
        "messages",
        "thought",
        "summary",
        "role",
        "type",
        "name",
        "arguments",
        "call_id",
        "status",
        "usage",
        "response_id",
        "request_id",
    ):
        attr_value = getattr(value, key, None)
        if attr_value is not None:
            attrs[key] = _normalize_openai_agents_value(attr_value)
    if attrs:
        return attrs

    return str(value)


def _extract_openai_agents_visible_text(value: dict[str, Any]) -> str | None:
    direct = _coerce_openai_agents_text(
        value.get("content") or value.get("text") or value.get("output") or value.get("message")
    )
    if direct:
        return direct

    output_items = value.get("output")
    if isinstance(output_items, list):
        texts = [_extract_openai_agents_text_from_output_item(item) for item in output_items]
        visible_parts = [text for text in texts if text]
        if visible_parts:
            return "\n\n".join(visible_parts)
    return None


def _extract_openai_agents_text_from_output_item(item: Any) -> str | None:
    if not isinstance(item, dict):
        return None

    item_type = str(item.get("type") or "").lower()
    if item_type == "function_call":
        return None

    content = item.get("content")
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if not isinstance(block, dict):
                continue
            block_type = str(block.get("type") or "").lower()
            if block_type in {"output_text", "text"}:
                text = _coerce_openai_agents_text(block.get("text") or block.get("content"))
                if text:
                    parts.append(text)
        if parts:
            return "\n\n".join(parts)

    return _coerce_openai_agents_text(item.get("text") or item.get("content"))


def _extract_openai_agents_thought(value: dict[str, Any]) -> str | None:
    for key in ("thought", "summary"):
        text = _coerce_openai_agents_text(value.get(key))
        if text is not None:
            return text

    metadata = value.get("metadata")
    if isinstance(metadata, dict):
        for key in ("thought", "summary", "reasoning_content"):
            text = _coerce_openai_agents_text(metadata.get(key))
            if text is not None:
                return text
    return None


def _is_openai_agents_tool_call_only(value: dict[str, Any]) -> bool:
    if isinstance(value.get("tool_calls"), list):
        return True
    if isinstance(value.get("function_call"), dict):
        return True

    output_items = value.get("output")
    if not isinstance(output_items, list) or not output_items:
        return False

    saw_function_call = False
    for item in output_items:
        if not isinstance(item, dict):
            return False
        item_type = str(item.get("type") or "").lower()
        if item_type == "function_call":
            saw_function_call = True
            continue
        if _extract_openai_agents_text_from_output_item(item):
            return False
        return False
    return saw_function_call


def _coerce_openai_agents_text(value: Any) -> str | None:
    if isinstance(value, str) and value:
        return value
    if isinstance(value, list):
        parts = [item for item in value if isinstance(item, str) and item]
        if parts:
            return "\n\n".join(parts)
    return None


@dataclasses.dataclass(frozen=True)
class _ParsedOpenAIAgentsOutput:
    thought: str | None
    final_output: str


_OPENAI_AGENTS_THOUGHT_TAG_RE = re.compile(
    r"<(?P<tag>think|thought|reason|reasoning)\b[^>]*>(?P<body>.*?)</(?P=tag)>",
    flags=re.IGNORECASE | re.DOTALL,
)
_OPENAI_AGENTS_FINAL_TAG_RE = re.compile(
    r"<(?P<tag>answer|final|final_output)\b[^>]*>(?P<body>.*?)</(?P=tag)>",
    flags=re.IGNORECASE | re.DOTALL,
)


def _parse_tagged_openai_agents_output(output: str) -> _ParsedOpenAIAgentsOutput:
    thought_matches = list(_OPENAI_AGENTS_THOUGHT_TAG_RE.finditer(output))
    if not thought_matches:
        return _ParsedOpenAIAgentsOutput(thought=None, final_output=output)

    thought_parts = [match.group("body").strip() for match in thought_matches]
    thought = "\n\n".join(part for part in thought_parts if part) or None
    remainder = _OPENAI_AGENTS_THOUGHT_TAG_RE.sub("", output).strip()

    final_matches = list(_OPENAI_AGENTS_FINAL_TAG_RE.finditer(remainder))
    if final_matches:
        final_parts = [match.group("body").strip() for match in final_matches]
        final_output = "\n\n".join(part for part in final_parts if part)
    else:
        final_output = remainder

    return _ParsedOpenAIAgentsOutput(thought=thought, final_output=final_output)


def _resolve_openai_agent_tools(agent: Any) -> Any:
    resolver = getattr(agent, "get_all_tools", None)
    if not callable(resolver):
        return _UNRESOLVED

    try:
        result = _call_with_optional_none(resolver)
    except Exception:
        return _UNRESOLVED

    try:
        return _resolve_maybe_awaitable(result)
    except Exception:
        return _UNRESOLVED


def _call_with_optional_none(fn: Any) -> Any:
    try:
        signature = inspect.signature(fn)
    except (TypeError, ValueError):
        return fn()

    args: list[Any] = []
    kwargs: dict[str, Any] = {}
    for param in signature.parameters.values():
        if param.kind == inspect.Parameter.VAR_POSITIONAL:
            continue
        if param.kind == inspect.Parameter.VAR_KEYWORD:
            continue
        if param.default is not inspect.Parameter.empty:
            continue
        if param.kind in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD):
            args.append(None)
        elif param.kind == inspect.Parameter.KEYWORD_ONLY:
            kwargs[param.name] = None
    return fn(*args, **kwargs)


def _resolve_maybe_awaitable(value: Any) -> Any:
    if not inspect.isawaitable(value):
        return value
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(value)
    return _run_awaitable_in_thread(value)


def _run_awaitable_in_thread(awaitable: Any) -> Any:
    outcome: dict[str, Any] = {}

    def runner() -> None:
        try:
            outcome["value"] = asyncio.run(awaitable)
        except BaseException as exc:  # pragma: no cover - forwarded below
            outcome["error"] = exc

    thread = threading.Thread(target=runner, daemon=True)
    thread.start()
    thread.join()

    if "error" in outcome:
        raise outcome["error"]
    return outcome.get("value")


def _iter_openai_tool_entries(
    tools: Any,
    *,
    use_container_patch: bool,
) -> list[tuple[str, Any, Any, Any]]:
    entries: list[tuple[str, Any, Any, Any]] = []
    if isinstance(tools, dict):
        for name, tool in list(tools.items()):
            entries.append((str(name), tool, tools if use_container_patch else None, name))
        return entries

    if isinstance(tools, list):
        for idx, tool in enumerate(list(tools)):
            entries.append(
                (
                    tool_name(tool, fallback=f"tool_{idx}"),
                    tool,
                    tools if use_container_patch else None,
                    idx,
                )
            )
        return entries

    if tools is None:
        return entries

    try:
        items = list(tools)
    except TypeError:
        return entries

    for idx, tool in enumerate(items):
        entries.append((tool_name(tool, fallback=f"tool_{idx}"), tool, None, idx))
    return entries


def _iter_openai_llm_candidates(agent: Any):
    for slot in ("model", "_model", "client", "_client", "llm", "_llm"):
        candidate = getattr(agent, slot, None)
        if candidate is not None:
            yield candidate


def _iter_openai_llm_resources(candidate: Any):
    chat = getattr(candidate, "chat", None)
    completions = getattr(chat, "completions", None) if chat is not None else None
    if completions is not None:
        yield completions, ("create",)

    responses = getattr(candidate, "responses", None)
    if responses is not None:
        yield responses, ("create",)

    beta = getattr(candidate, "beta", None)
    threads = getattr(beta, "threads", None) if beta is not None else None
    if threads is not None:
        yield threads, ("create_and_run", "create_and_run_poll", "create_and_run_stream")
        runs = getattr(threads, "runs", None)
        if runs is not None:
            yield runs, (
                "create",
                "create_and_poll",
                "create_and_stream",
                "submit_tool_outputs",
                "submit_tool_outputs_and_poll",
                "submit_tool_outputs_stream",
            )


def _iter_openai_nested_llm_candidates(candidate: Any):
    for slot in ("client", "_client", "openai_client", "_openai_client"):
        nested = getattr(candidate, slot, None)
        if nested is not None:
            yield nested


def _iter_openai_nested_llm_targets(candidate: Any):
    for nested in _iter_openai_nested_llm_candidates(candidate):
        yield nested, ("create", "complete", "completion", "generate", "invoke", "ainvoke")
        yield from _iter_openai_llm_resources(nested)


def _needs_openai_runner_patch(agent: Any, bindings: list[Any]) -> bool:
    if isinstance(getattr(agent, "model", None), str):
        return True
    return not bindings and isinstance(getattr(agent, "_model", None), str)


def _build_openai_runner_binding(agent: Any) -> LLMBinding | None:
    try:
        runner_cls = _resolve_agents_export("Runner")
    except Exception:
        return None

    runner_run = getattr(runner_cls, "run", None)
    if not callable(runner_run):
        return None

    return LLMBinding(
        label="runner_model_provider",
        callable=runner_run,
        owner=runner_cls,
        attr="run",
        installer=_install_openai_runner_binding,
        metadata={"attached_agent": agent},
    )


def _install_openai_runner_binding(
    guard: Any,
    binding: LLMBinding,
    adapter: BaseAgentAdapter,
) -> int:
    runner_cls = binding.owner
    attached_agent = binding.metadata.get("attached_agent")
    if runner_cls is None or attached_agent is None:
        return 0

    registry = getattr(runner_cls, "__agentguard_openai_runner_registry__", None)
    if not isinstance(registry, dict):
        registry = {}
        set_attr(runner_cls, "__agentguard_openai_runner_registry__", registry)
    registry[id(attached_agent)] = {
        "agent": attached_agent,
        "guard": guard,
        "adapter": adapter,
    }

    if getattr(runner_cls, "__agentguard_openai_runner_patched__", False):
        return 1

    patched = 0
    for method_name in ("run", "run_sync", "run_streamed"):
        descriptor = runner_cls.__dict__.get(method_name)
        if descriptor is None:
            continue
        original = descriptor.__func__ if isinstance(descriptor, classmethod) else descriptor
        if not callable(original) or is_guarded(original):
            continue

        wrapped = _make_openai_runner_wrapper(
            runner_cls,
            original,
        )
        if isinstance(descriptor, classmethod):
            wrapped = classmethod(wrapped)
        if set_attr(runner_cls, method_name, wrapped):
            patched += 1

    if patched:
        set_attr(runner_cls, "__agentguard_openai_runner_patched__", True)
        return 1
    return 0


def _make_openai_runner_wrapper(
    runner_cls: Any,
    original: Any,
) -> Any:
    try:
        signature = inspect.signature(original)
    except (TypeError, ValueError):
        signature = None

    def _prepare_call(cls: Any, args: tuple[Any, ...], kwargs: dict[str, Any]):
        registry = getattr(runner_cls, "__agentguard_openai_runner_registry__", {})
        if not isinstance(registry, dict):
            registry = {}

        bound = None
        if signature is not None:
            try:
                bound = signature.bind_partial(cls, *args, **kwargs)
            except TypeError:
                bound = None

        starting_agent = _extract_runner_argument(bound, args, kwargs, names=("starting_agent", "agent"))
        if starting_agent is None:
            return None, None

        entry = registry.get(id(starting_agent))
        if not entry or entry.get("agent") is not starting_agent:
            return None, None

        if bound is None:
            return entry, None

        run_config = bound.arguments.get("run_config")
        wrapped_config = _wrap_openai_runner_config(
            run_config,
            guard=entry["guard"],
            adapter=entry["adapter"],
        )
        if wrapped_config is not run_config:
            bound.arguments["run_config"] = wrapped_config
        return entry, bound

    if inspect.iscoroutinefunction(original):

        @functools.wraps(original)
        async def async_wrapper(cls: Any, *args: Any, **kwargs: Any) -> Any:
            _entry, bound = _prepare_call(cls, args, kwargs)
            if bound is not None:
                return await original(*bound.args, **bound.kwargs)
            return await original(cls, *args, **kwargs)

        set_attr(async_wrapper, "__agentguard_wrapped__", True)
        return async_wrapper

    @functools.wraps(original)
    def wrapper(cls: Any, *args: Any, **kwargs: Any) -> Any:
        _entry, bound = _prepare_call(cls, args, kwargs)
        if bound is not None:
            return original(*bound.args, **bound.kwargs)
        return original(cls, *args, **kwargs)

    set_attr(wrapper, "__agentguard_wrapped__", True)
    return wrapper


def _extract_runner_argument(
    bound: Any,
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
    *,
    names: tuple[str, ...],
) -> Any:
    if bound is not None:
        for name in names:
            if name in bound.arguments:
                return bound.arguments[name]
    for name in names:
        if name in kwargs:
            return kwargs[name]
    if args:
        return args[0]
    return None


def _wrap_openai_runner_config(
    run_config: Any,
    *,
    guard: Any,
    adapter: BaseAgentAdapter,
) -> Any:
    config = run_config
    if config is None:
        try:
            run_config_cls = _resolve_agents_export("RunConfig")
        except Exception:
            return run_config
        try:
            config = run_config_cls()
        except Exception:
            return run_config

    provider = getattr(config, "model_provider", None)
    if provider is None:
        return config

    wrapped_provider = _AgentGuardOpenAIModelProvider(provider, guard, adapter)
    if dataclasses.is_dataclass(config):
        try:
            return dataclasses.replace(config, model_provider=wrapped_provider)
        except Exception:
            pass

    try:
        cloned = copy.copy(config)
    except Exception:
        cloned = config

    if set_attr(cloned, "model_provider", wrapped_provider):
        return cloned
    return config


class _AgentGuardOpenAIModelProvider:
    def __init__(self, provider: Any, guard: Any, adapter: BaseAgentAdapter) -> None:
        self._provider = provider
        self._guard = guard
        self._adapter = adapter

    def __getattr__(self, name: str) -> Any:
        return getattr(self._provider, name)

    def get_model(self, *args: Any, **kwargs: Any) -> Any:
        model = self._provider.get_model(*args, **kwargs)
        return _wrap_runtime_openai_model(model, guard=self._guard, adapter=self._adapter)


def _wrap_runtime_openai_model(model: Any, *, guard: Any, adapter: BaseAgentAdapter) -> Any:
    if model is None:
        return model
    if inspect.isawaitable(model):
        return _wrap_runtime_openai_model_awaitable(model, guard=guard, adapter=adapter)
    _patch_runtime_openai_llm_object(model, guard=guard, adapter=adapter)
    return model


async def _wrap_runtime_openai_model_awaitable(model: Any, *, guard: Any, adapter: BaseAgentAdapter) -> Any:
    resolved = await model
    _patch_runtime_openai_llm_object(resolved, guard=guard, adapter=adapter)
    return resolved


def _patch_runtime_openai_llm_object(model: Any, *, guard: Any, adapter: BaseAgentAdapter) -> None:
    if model is None:
        return

    patched = patch_llm_methods(
        guard,
        model,
        methods=("get_response", "stream_response"),
        normalizer=adapter,
        owner=model,
    )
    if patched:
        return

    patch_llm_methods(
        guard,
        model,
        methods=("create", "complete", "completion", "generate", "invoke", "ainvoke"),
        normalizer=adapter,
        owner=model,
    )
    for target, methods in _iter_openai_nested_llm_targets(model):
        patch_llm_methods(
            guard,
            target,
            methods=methods,
            normalizer=adapter,
            owner=target,
        )


def _resolve_agents_export(name: str) -> Any:
    module = sys.modules.get("agents")
    if module is None:
        module = __import__("agents", fromlist=[name])
    return getattr(module, name)


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
    metadata = guard.register_tool(
        original,
        name=name,
        description=_openai_tool_description(tool, original),
        required_args=_openai_tool_required_args(tool, original),
        schema=_openai_tool_schema(tool),
    )

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
    if len(args) >= 2:
        raw = args[1]
    else:
        raw = kwargs.get("json_input")
        if raw is None:
            raw = kwargs.get("input")

    parsed = _coerce_openai_tool_input(raw)
    if parsed is not None:
        return parsed

    for key in ("json_input", "input", "arguments", "args"):
        candidate = kwargs.get(key)
        parsed = _coerce_openai_tool_input(candidate)
        if parsed is not None:
            return parsed

    return {
        str(key): value
        for key, value in kwargs.items()
        if key not in {"ctx", "context", "run_context"}
    }


def _openai_tool_description(tool: Any, original: Any) -> str:
    for candidate in (
        getattr(tool, "description", None),
        inspect.getdoc(getattr(tool, "func", None)),
        inspect.getdoc(getattr(tool, "_func", None)),
        inspect.getdoc(getattr(tool, "__wrapped__", None)),
        inspect.getdoc(original),
    ):
        if candidate:
            return str(candidate).strip().split("\n")[0]
    return ""


def _openai_tool_required_args(tool: Any, original: Any) -> list[str]:
    schema = _openai_tool_schema(tool)
    from_schema = _schema_input_params(schema)
    if from_schema:
        return from_schema

    for attr in ("func", "_func", "__wrapped__"):
        candidate = getattr(tool, attr, None)
        if callable(candidate):
            required = _required_args_from_signature(candidate)
            if required:
                return required

    return _required_args_from_signature(original, skip={"ctx", "context", "run_context", "input", "json_input"})


def _openai_tool_schema(tool: Any) -> dict[str, Any]:
    for attr in ("params_json_schema", "input_schema", "json_schema", "schema"):
        if not hasattr(tool, attr):
            continue
        schema = _coerce_schema_dict(getattr(tool, attr))
        if schema:
            return schema
    return {}


def _coerce_schema_dict(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, dict):
        return value

    if callable(value):
        try:
            resolved = value()
        except Exception:
            return {}
        return _coerce_schema_dict(resolved)

    for attr in ("model_json_schema", "json_schema", "schema", "model_dump", "to_dict", "dict"):
        method = getattr(value, attr, None)
        if not callable(method):
            continue
        try:
            resolved = method()
        except Exception:
            continue
        if isinstance(resolved, dict):
            return resolved
    return {}


def _schema_input_params(schema: dict[str, Any]) -> list[str]:
    if not isinstance(schema, dict):
        return []

    required = schema.get("required")
    if isinstance(required, list) and required:
        return [str(item) for item in required if str(item).strip()]

    properties = schema.get("properties")
    if isinstance(properties, dict):
        return [str(name) for name in properties if str(name).strip()]
    return []


def _required_args_from_signature(fn: Any, *, skip: set[str] | None = None) -> list[str]:
    try:
        signature = inspect.signature(fn)
    except (TypeError, ValueError):
        return []

    skipped = skip or set()
    return [
        param.name
        for param in signature.parameters.values()
        if param.default is inspect.Parameter.empty
        and param.kind in (inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.KEYWORD_ONLY)
        and param.name not in skipped
    ]


def _coerce_openai_tool_input(raw: Any) -> dict[str, Any] | None:
    if raw is None:
        return None
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return {"_raw": raw, "_unparsed": True}
        return _coerce_openai_tool_input(parsed)
    if isinstance(raw, dict):
        for key in ("input", "json_input", "arguments", "args"):
            nested = raw.get(key)
            parsed = _coerce_openai_tool_input(nested)
            if parsed is not None:
                return parsed
        return {
            str(key): value
            for key, value in raw.items()
            if key not in {"ctx", "context", "run_context"}
        }
    return {"_raw": raw}

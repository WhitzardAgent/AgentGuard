"""AgentGuard <-> Dify SDK integration adapter."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Optional

from agentguard.models.decisions import Action, Decision
from agentguard.models.events import EventType, Principal, RuntimeEvent, ToolCall
from agentguard.sdk.adapters.base import BaseAdapter
from agentguard.sdk.context import current_session

log = logging.getLogger(__name__)


def _dify_types() -> dict[str, Any]:
    from dify import Dify, DifyApp  # type: ignore
    from dify.app.schemas import (  # type: ignore
        AgentMessageEvent,
        AgentThoughtEvent,
        ChatMessageEvent,
        ChatPayloads,
        ConversationEventType,
        ErrorEvent,
        MessageEndEvent,
    )
    return dict(
        Dify=Dify, DifyApp=DifyApp,
        AgentMessageEvent=AgentMessageEvent,
        AgentThoughtEvent=AgentThoughtEvent,
        ChatMessageEvent=ChatMessageEvent,
        ChatPayloads=ChatPayloads,
        ConversationEventType=ConversationEventType,
        ErrorEvent=ErrorEvent,
        MessageEndEvent=MessageEndEvent,
    )


_SINK_BY_PREFIX = [
    ("email", "email"), ("mail",  "email"),
    ("http",  "http"),  ("browser", "http"),
    ("shell", "shell"),
    ("fs",    "fs"),    ("file",  "fs"),
    ("db",    "db"),    ("sql",   "db"),
]


def _infer_sink(tool_name: str) -> str:
    for prefix, sink in _SINK_BY_PREFIX:
        if tool_name.startswith(prefix):
            return sink
    return "none"


def _safe_parse_tool_input(raw: Optional[str]) -> dict[str, Any]:
    if not raw:
        return {}
    raw = raw.strip()
    if not raw:
        return {}
    try:
        obj = json.loads(raw)
        return obj if isinstance(obj, dict) else {"value": obj}
    except Exception:
        return {"raw": raw}


class DifyAdapter(BaseAdapter):
    """Real Dify SDK adapter — observes DifyApp stream events."""

    def __init__(self, pipeline: Any, guard: Any) -> None:
        super().__init__(pipeline, guard)
        self._hooked: list[Any] = []
        self._pending_stop_tasks: list[asyncio.Task[Any]] = []

    def install(self, target: Any) -> None:
        import inspect
        t = _dify_types()
        if isinstance(target, t["Dify"]):
            self._wrap_app(target.app)
            return
        if isinstance(target, t["DifyApp"]):
            self._wrap_app(target)
            return
        # Duck-typed Dify app: any object exposing an async ``chat`` (or ``run``
        # / ``completion``) that returns an async iterator of Dify events.
        if hasattr(target, "chat") and (
            asyncio.iscoroutinefunction(target.chat)
            or inspect.isasyncgenfunction(target.chat)
        ):
            self._wrap_app(target)
            return
        raise TypeError(
            f"attach_dify: expected dify.Dify / dify.DifyApp / async-chat app, "
            f"got {type(target)!r}")

    def _wrap_app(self, app: Any) -> None:
        adapter = self
        for method in ("chat", "run", "completion"):
            if not hasattr(app, method):
                continue
            orig = getattr(app, method)

            async def wrapped(*args: Any, _orig: Any = orig, _method: str = method,
                              **kwargs: Any) -> Any:
                # Extract payloads for observation: first positional arg after self, or kwarg
                payloads = kwargs.get("payloads") or (args[1] if len(args) > 1 else args[0] if args else None)
                api_key = kwargs.get("api_key") or (args[0] if args else None)
                async for event in _orig(*args, **kwargs):
                    adapter._observe(event, payloads, app, api_key, _method)
                    yield event

            setattr(app, method, wrapped)
        self._hooked.append(app)
        log.info("agentguard attached to %s", type(app).__name__)

    def _observe(self, event: Any, payloads: Any, app: Any, api_key: Any, _method: str) -> None:
        t = _dify_types()
        if not isinstance(event, t["AgentThoughtEvent"]):
            return
        if not event.tool:
            return

        tool_args = _safe_parse_tool_input(event.tool_input)
        target = tool_args.get("target") if isinstance(tool_args.get("target"), dict) else {}
        principal = self._principal_for(payloads, event)
        rt_event = RuntimeEvent(
            event_type=EventType.TOOL_CALL_ATTEMPT,
            principal=principal,
            tool_call=ToolCall(
                tool_name=event.tool,
                args=tool_args,
                target=target,
                sink_type=_infer_sink(event.tool),
            ),
            extra={
                "source": "dify_agent_thought",
                "conversation_id": event.conversation_id,
                "task_id": event.task_id,
                "observation": event.observation,
                "dify_method": _method,
            },
        )
        try:
            decision = self.pipeline.handle_attempt(rt_event)
        except Exception as e:
            log.warning("agentguard observe error: %s", e)
            return

        if decision.action in (Action.DENY, Action.HUMAN_CHECK):
            log.warning("[agentguard/dify] tool=%s decision=%s matched=%s",
                        event.tool, decision.action.value, decision.matched_rules)
            self._maybe_stop_message(app, api_key, event.task_id, payloads, decision)

    def _principal_for(self, payloads: Any, event: Any) -> Principal:
        sess = current_session()
        if sess and sess.principal is not None:
            return sess.principal
        user = getattr(payloads, "user", None)
        conv = getattr(payloads, "conversation_id", None) or event.conversation_id
        agent_id = (
            getattr(payloads, "app_id", None)
            or getattr(event, "app_id", None)
            or "dify-agent"
        )
        return Principal(
            agent_id=str(agent_id),
            session_id=str(conv or "anon"),
            user_id=str(user) if user is not None else None,
            role="default", trust_level=1,
        )

    def _maybe_stop_message(
        self, app: Any, api_key: Any, task_id: Optional[str],
        payloads: Any, decision: Decision,
    ) -> None:
        if self.guard.mode != "enforce":
            return
        if not task_id:
            return
        user = getattr(payloads, "user", None)
        if user is None:
            return
        stop_fn = getattr(app, "stop_message", None)
        if stop_fn is None:
            log.warning(
                "[agentguard/dify] app %s has no stop_message(); "
                "cannot interrupt task %s", type(app).__name__, task_id
            )
            return
        try:
            loop = asyncio.get_running_loop()
            task = loop.create_task(stop_fn(api_key, task_id, user))
            self._pending_stop_tasks.append(task)
            self._pending_stop_tasks = [t for t in self._pending_stop_tasks if not t.done()]
        except RuntimeError:
            try:
                asyncio.run(stop_fn(api_key, task_id, user))
            except Exception as e:
                log.warning("stop_message failed: %s", e)

    def guard_tool_exec(self, tool_name: str, args: dict[str, Any],
                        *, principal: Optional[Principal] = None) -> Any:
        if tool_name not in self.guard.registry:
            raise KeyError(f"tool not registered in guard: {tool_name!r}")
        fn = self.guard.registry[tool_name]
        if principal is not None:
            with self.guard.session(principal=principal):
                return fn(**args)
        return fn(**args)

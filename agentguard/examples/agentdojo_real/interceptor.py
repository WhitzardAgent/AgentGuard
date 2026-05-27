"""AgentGuardInterceptor — an AgentDojo BasePipelineElement that replaces
the default ToolsExecutor.

For every tool_call emitted by the LLM, the interceptor:
  1. Builds a RuntimeEvent describing the call.
  2. Asks the AgentGuard server (via HTTP /v1/evaluate) for a Decision.
  3. Acts on the decision:
       - ALLOW       → execute the tool via FunctionsRuntime.run_function
       - DENY        → return a synthetic ChatToolResultMessage with an error
                        explaining the policy block (do NOT execute)
       - HUMAN_CHECK → same as DENY (with a different reason) — in a real
                        deployment this would block on an approval queue.
       - DEGRADE     → execute the tool but flag it (here we still execute;
                        proper degrade requires a tool-rewrite that maps to
                        an alternative AgentDojo tool, out of scope).

Because this element appends ChatToolResultMessage entries directly, it is
used IN PLACE OF the default ToolsExecutor inside ToolsExecutionLoop.

The interceptor records every decision for later analysis via `decisions`.
"""

from __future__ import annotations

import logging
import os
from ast import literal_eval
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

import yaml
from pydantic import BaseModel

from agentdojo.agent_pipeline.base_pipeline_element import BasePipelineElement
from agentdojo.functions_runtime import (
    EmptyEnv,
    Env,
    FunctionCall,
    FunctionsRuntime,
    FunctionReturnType,
)
from agentdojo.types import ChatMessage, ChatToolResultMessage

from agentguard.labels import labels_for_tool
from agentguard.models.decisions import Action, Decision
from agentguard.models.events import (
    EventType,
    Principal,
    ProvenanceRef,
    RuntimeEvent,
    ToolCall,
)
from agentguard.sdk.client import RemoteGuardClient

log = logging.getLogger(__name__)


# ── helpers from AgentDojo's ToolsExecutor (re-implemented locally to avoid
# importing it; the upstream module pulls in `secagent` which we don't need)


def _is_string_list(s: str) -> bool:
    try:
        parsed = literal_eval(s)
        return isinstance(parsed, list)
    except (ValueError, SyntaxError):
        return False


def _tool_result_to_str(result: FunctionReturnType) -> str:
    """Default output formatter: YAML for BaseModels/lists, str() otherwise."""
    if isinstance(result, BaseModel):
        return yaml.safe_dump(result.model_dump()).strip()
    if isinstance(result, list):
        items: list[Any] = []
        for item in result:
            if isinstance(item, (str, int)):
                items.append(str(item))
            elif isinstance(item, BaseModel):
                items.append(item.model_dump())
            else:
                items.append(str(item))
        return yaml.safe_dump(items).strip()
    return str(result)


# ── decision record


@dataclass
class InterceptionRecord:
    tool_name: str
    args: dict[str, Any]
    action: str          # "allow" | "deny" | "human_check" | "degrade" | "error"
    reason: str = ""
    matched_rules: list[str] = field(default_factory=list)
    executed: bool = False
    event_id: str = ""
    upstream_labels: list[str] = field(default_factory=list)


@dataclass
class _UpstreamEntry:
    """One past tool call that produced labelled (untrusted) output."""
    event_id: str
    tool_name: str
    label: str


# ── the interceptor


class AgentGuardInterceptor(BasePipelineElement):
    """Drop-in replacement for `ToolsExecutor` that calls AgentGuard first.

    Parameters
    ----------
    client:
        RemoteGuardClient already configured with the server base_url and api_key.
    principal:
        Default Principal used when none is set on the call. Mimics the
        identity the AgentDojo benchmark would normally use.
    sink_type_map:
        Optional mapping ``tool_name → sink_type`` (e.g. ``"send_email" → "email"``).
        Used to populate ``ToolCall.sink_type`` so that policy rules referring to
        sink types continue to work.
    fail_open:
        When the AgentGuard server is unreachable, allow tool execution.
        Default True (matches RemoteGuardClient default).
    """

    name = "agentguard_interceptor"

    def __init__(
        self,
        *,
        client: RemoteGuardClient,
        principal: Principal,
        sink_type_map: dict[str, str] | None = None,
        fail_open: bool = True,
        session_allowlists: dict[str, list[str]] | None = None,
    ) -> None:
        self.client = client
        self.principal = principal
        self.sink_type_map = sink_type_map or {}
        self.fail_open = fail_open
        self.decisions: list[InterceptionRecord] = []
        # Session-scoped allowlists used by the DSL ``whitelist("name")``
        # function (see ``_f_whitelist`` in compiler.py). Updated per task
        # by ``set_session_allowlists`` when running benchmark tasks.
        self.session_allowlists: dict[str, list[str]] = dict(session_allowlists or {})
        # Round 2: session history of upstream tool calls that produced
        # untrusted-labelled output. Each new tool call is annotated with
        # ProvenanceRefs pointing back at every entry so that
        # ``exists_path(...)`` chain rules in the policy can fire on
        # data flowing from external sources to side-effecting sinks.
        self._session_history: dict[str, list[_UpstreamEntry]] = {}

    def set_session_allowlists(self, allowlists: dict[str, list[str]]) -> None:
        """Replace the per-session whitelist data attached to every event."""
        self.session_allowlists = dict(allowlists or {})

    def reset_session_history(self, session_id: str | None = None) -> None:
        """Clear cached upstream-history. Used at the start of each task."""
        if session_id is None:
            self._session_history.clear()
        else:
            self._session_history.pop(session_id, None)

    # ------------------------------------------------------------------
    # PipelineElement API
    # ------------------------------------------------------------------

    def query(
        self,
        query: str,
        runtime: FunctionsRuntime,
        env: Env = EmptyEnv(),
        messages: Sequence[ChatMessage] = [],
        extra_args: dict = {},
    ) -> tuple[str, FunctionsRuntime, Env, Sequence[ChatMessage], dict]:
        if not messages:
            return query, runtime, env, messages, extra_args
        last = messages[-1]
        if last.get("role") != "assistant":
            return query, runtime, env, messages, extra_args
        tool_calls = last.get("tool_calls") or []
        if not tool_calls:
            return query, runtime, env, messages, extra_args

        results: list[ChatToolResultMessage] = []

        for tc in tool_calls:
            decision_record = self._intercept_one(tc, runtime, env)
            self.decisions.append(decision_record)
            if os.environ.get("AGENTGUARD_DEBUG", "0") == "1":
                upstream_repr = (
                    f"  upstream={decision_record.upstream_labels}"
                    if decision_record.upstream_labels
                    else ""
                )
                print(
                    f"  [interceptor] {decision_record.action.upper():<11} "
                    f"{tc.function}({list(tc.args.keys())})  "
                    f"rules={decision_record.matched_rules}  "
                    f"reason={decision_record.reason[:80]}"
                    f"{upstream_repr}",
                    flush=True,
                )

            if decision_record.action in ("deny", "human_check"):
                # Block execution — return a synthetic tool result with an error
                err = (
                    f"[AgentGuard {decision_record.action.upper()}] "
                    f"{decision_record.reason or 'blocked by policy'}"
                )
                if decision_record.matched_rules:
                    err += f" (matched: {', '.join(decision_record.matched_rules)})"
                results.append(
                    ChatToolResultMessage(
                        role="tool",
                        content=err,
                        tool_call_id=tc.id,
                        tool_call=tc,
                        error=err,
                    )
                )
                continue

            # ALLOW (or DEGRADE/error fail-open): actually execute
            tool_result = self._execute(tc, runtime, env)
            results.append(tool_result)
            decision_record.executed = True

            # Round 2: if this tool's output is in the labels registry,
            # push (event_id, label) onto the session history so
            # downstream tool calls inherit a ProvenanceRef back to it.
            # Only record when the tool actually executed without an
            # adapter-level error (so we don't pollute history with
            # never-run calls).
            if not tool_result.get("error") and decision_record.event_id:
                lbls = labels_for_tool(tc.function)
                if lbls:
                    history = self._session_history.setdefault(
                        self.principal.session_id, []
                    )
                    for lbl in lbls:
                        history.append(
                            _UpstreamEntry(
                                event_id=decision_record.event_id,
                                tool_name=tc.function,
                                label=lbl,
                            )
                        )

        return query, runtime, env, [*messages, *results], extra_args

    # ------------------------------------------------------------------
    # internals
    # ------------------------------------------------------------------

    _ALLOWED_SINKS = {"none", "email", "http", "shell", "fs_write", "db_write", "llm_out"}

    def _intercept_one(
        self,
        tc: FunctionCall,
        runtime: FunctionsRuntime,
        env: Env,
    ) -> InterceptionRecord:
        sink_type = self.sink_type_map.get(tc.function, "none")
        if sink_type not in self._ALLOWED_SINKS:
            sink_type = "none"

        sess_id = self.principal.session_id
        history = self._session_history.setdefault(sess_id, [])

        # Round 2: build provenance_refs from every upstream tool call that
        # produced untrusted-labelled content. The execution-graph's
        # exists_path query then walks DERIVED_FROM edges back to those
        # nodes, allowing chain rules to fire.
        provenance_refs: list[ProvenanceRef] = [
            ProvenanceRef(
                node_id=f"{entry.event_id}:{entry.label}",
                label=entry.label,
                parent_tool_call_id=entry.event_id,
                confidence=1.0,
            )
            for entry in history
        ]
        upstream_labels = sorted({entry.label for entry in history})

        extra: dict[str, Any] = {}
        if self.session_allowlists:
            extra["allowlists"] = dict(self.session_allowlists)

        try:
            event = RuntimeEvent(
                event_type=EventType.TOOL_CALL_ATTEMPT,
                principal=self.principal,
                tool_call=ToolCall(
                    tool_name=tc.function,
                    args=dict(tc.args),
                    sink_type=sink_type,  # type: ignore[arg-type]
                ),
                provenance_refs=provenance_refs,
                extra=extra,
            )
        except Exception as e:
            log.warning("AgentGuardInterceptor: failed to build event: %s", e)
            return InterceptionRecord(
                tool_name=tc.function,
                args=dict(tc.args),
                action="error",
                reason=f"event_build_failed: {e}",
                upstream_labels=upstream_labels,
            )

        try:
            decision: Decision = self.client.evaluate(event)
        except Exception as e:  # safety net; client already has its own handling
            log.warning("AgentGuardInterceptor: client error: %s", e)
            if self.fail_open:
                return InterceptionRecord(
                    tool_name=tc.function,
                    args=dict(tc.args),
                    action="allow",
                    reason="server_unreachable_fail_open",
                    event_id=event.event_id,
                    upstream_labels=upstream_labels,
                )
            return InterceptionRecord(
                tool_name=tc.function,
                args=dict(tc.args),
                action="deny",
                reason="server_unreachable_fail_closed",
                event_id=event.event_id,
                upstream_labels=upstream_labels,
            )

        action = decision.action
        action_name: str
        if action is Action.ALLOW:
            action_name = "allow"
        elif action is Action.DENY:
            action_name = "deny"
        elif action is Action.HUMAN_CHECK:
            action_name = "human_check"
        elif action is Action.DEGRADE:
            action_name = "degrade"
        else:
            action_name = str(action).lower()

        return InterceptionRecord(
            tool_name=tc.function,
            args=dict(tc.args),
            action=action_name,
            reason=decision.reason or "",
            matched_rules=list(decision.matched_rules or []),
            event_id=event.event_id,
            upstream_labels=upstream_labels,
        )

    def _execute(
        self,
        tc: FunctionCall,
        runtime: FunctionsRuntime,
        env: Env,
    ) -> ChatToolResultMessage:
        # tool not registered → error message (mirror ToolsExecutor)
        known = {tool.name for tool in runtime.functions.values()}
        if tc.function not in known:
            return ChatToolResultMessage(
                role="tool",
                content="",
                tool_call_id=tc.id,
                tool_call=tc,
                error=f"Invalid tool {tc.function} provided.",
            )

        # convert string-encoded lists back to lists (mirror ToolsExecutor)
        for k, v in tc.args.items():
            if isinstance(v, str) and _is_string_list(v):
                tc.args[k] = literal_eval(v)

        result, error = runtime.run_function(env, tc.function, tc.args)
        return ChatToolResultMessage(
            role="tool",
            content=_tool_result_to_str(result),
            tool_call_id=tc.id,
            tool_call=tc,
            error=error,
        )

    # ------------------------------------------------------------------
    # Reporting
    # ------------------------------------------------------------------

    def reset(self) -> None:
        """Clear the decision log + provenance history between tasks."""
        self.decisions.clear()
        self._session_history.clear()

    def summary(self) -> dict[str, int]:
        """Counts per action (for benchmark summaries)."""
        out: dict[str, int] = {}
        for d in self.decisions:
            out[d.action] = out.get(d.action, 0) + 1
        return out

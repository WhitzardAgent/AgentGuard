"""GuardedAgent — wraps an LLM agent (via an adapter) under full enforcement.

The wrapped agent's reasoning is driven as a stream of :class:`AgentStep`
values produced by an adapter. For each step the Harness:

* ``thought``   → routes through the LLM thought hook
* ``tool_call`` → routes through the guarded tool (sandboxed + enforced)
* ``skill``     → runs a registered Skill
* ``final``     → enforces the final response (sanitize / deny)

Results are streamed back into the adapter generator (``gen.send(...)``) so the
agent can react to tool outputs, matching the ReAct loop used by most
frameworks.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from agentguard.adapters.base import AgentStep, BaseAdapter, StepKind
from agentguard.harness.runtime_context import use_context
from agentguard.harness.tool_wrapper import ToolDenied
from agentguard.schemas.context import RuntimeContext
from agentguard.schemas.decision import DecisionAction
from agentguard.schemas.events import EventType, RuntimeEvent

if TYPE_CHECKING:
    from agentguard.facade import AgentGuard


class GuardedAgent:
    def __init__(
        self,
        guard: "AgentGuard",
        adapter: BaseAdapter,
        *,
        enable_thought_hook: bool = True,
    ) -> None:
        self._guard = guard
        self._adapter = adapter
        self._enable_thought_hook = enable_thought_hook

    @property
    def adapter(self) -> BaseAdapter:
        return self._adapter

    def run(self, prompt: str, **kwargs: Any) -> str:
        context = self._guard.context
        with use_context(context):
            return self._drive(prompt, context, **kwargs)

    def _drive(self, prompt: str, context: RuntimeContext, **kwargs: Any) -> str:
        tools = {name: self._guard.tool_metadata(name) for name in self._guard.tool_names()}
        gen = self._adapter.run(prompt, context, tools, **kwargs)

        final_text = ""
        try:
            sent: Any = None
            while True:
                step: AgentStep = gen.send(sent)
                sent = self._handle_step(step, context)
                if step.kind == StepKind.FINAL:
                    final_text = str(sent)
        except StopIteration as stop:
            if stop.value is not None:
                final_text = self._finalize(str(stop.value), context)
        return final_text

    def _handle_step(self, step: AgentStep, context: RuntimeContext) -> Any:
        if step.kind == StepKind.THOUGHT:
            if not self._enable_thought_hook:
                return step.content
            return self._guard._thought_hook.observe(
                step.content or "", metadata=step.metadata
            )
        if step.kind == StepKind.TOOL_CALL:
            try:
                return self._guard.invoke_tool(step.tool_name or "", **(step.args or {}))
            except ToolDenied as exc:
                return f"[tool blocked: {exc.reason}]"
        if step.kind == StepKind.SKILL:
            return self._guard.run_skill(step.tool_name or "", **(step.args or {}))
        if step.kind == StepKind.FINAL:
            return self._finalize(step.content or "", context)
        return step.content

    def _finalize(self, text: str, context: RuntimeContext) -> str:
        event = RuntimeEvent(
            type=EventType.FINAL_RESPONSE,
            session_id=context.session_id,
            user_id=context.user_id,
            agent_id=context.agent_id,
            content=text,
        )
        self._guard._dispatch_before(event)
        result = self._guard._enforcer.enforce(event, context)
        self._guard._dispatch_after(result)
        action = result.decision.action
        if action is DecisionAction.DENY:
            return "[response withheld by AgentGuard policy]"
        if action is DecisionAction.SANITIZE:
            return result.event.content or ""
        return text

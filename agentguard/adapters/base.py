"""Adapter base: the AgentStep protocol and a default ReAct run loop."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Generator

from agentguard.schemas.context import RuntimeContext
from agentguard.tools.metadata import ToolMetadata


class StepKind(str, Enum):
    THOUGHT = "thought"
    TOOL_CALL = "tool_call"
    SKILL = "skill"
    FINAL = "final"


@dataclass
class AgentStep:
    kind: StepKind
    content: str | None = None
    tool_name: str | None = None
    args: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    # ── convenience constructors ────────────────────────────────────────
    @staticmethod
    def thought(content: str, **metadata: Any) -> "AgentStep":
        return AgentStep(kind=StepKind.THOUGHT, content=content, metadata=metadata)

    @staticmethod
    def tool(tool_name: str, **args: Any) -> "AgentStep":
        return AgentStep(kind=StepKind.TOOL_CALL, tool_name=tool_name, args=args)

    @staticmethod
    def skill(skill_name: str, **args: Any) -> "AgentStep":
        return AgentStep(kind=StepKind.SKILL, tool_name=skill_name, args=args)

    @staticmethod
    def final(content: str) -> "AgentStep":
        return AgentStep(kind=StepKind.FINAL, content=content)


# Generator yielding steps, receiving step results, returning the final answer.
StepStream = Generator[AgentStep, Any, "str | None"]


class BaseAdapter:
    """Normalizes a framework agent. Subclasses typically override
    :meth:`_complete` to call the real LLM; the default reasoning loop in
    :meth:`run` then works unchanged.
    """

    provider: str = "base"

    def __init__(self, model: str | None = None, **options: Any) -> None:
        self.model = model
        self.options = options

    # ── overridable LLM call ────────────────────────────────────────────
    def _complete(self, prompt: str) -> str:
        """Return a completion for ``prompt``.

        The base implementation is a deterministic offline stub so the Harness
        runs without any external dependency. Subclasses override this to call
        their respective SDKs, ideally falling back to ``super()._complete`` on
        ImportError / missing credentials.
        """
        snippet = prompt.strip().replace("\n", " ")
        return f"[{self.provider}-offline] {snippet[:160]}"

    # ── tool selection heuristics ───────────────────────────────────────
    def _choose_tool(self, tools: dict[str, ToolMetadata], prompt: str) -> str | None:
        if not tools:
            return None
        lowered = prompt.lower()
        for name in tools:
            if name.lower() in lowered:
                return name
        return next(iter(tools))

    def _tool_args(
        self, tool_name: str, tools: dict[str, ToolMetadata], prompt: str
    ) -> dict[str, Any]:
        meta = tools.get(tool_name)
        params = meta.param_names if meta else []
        return {params[0]: prompt} if params else {}

    # ── default ReAct loop ──────────────────────────────────────────────
    def run(
        self,
        prompt: str,
        context: RuntimeContext,
        tools: dict[str, ToolMetadata],
        *,
        use_tool: bool = True,
        **kwargs: Any,
    ) -> StepStream:
        reasoning = self._complete(f"Think step by step about: {prompt}")
        yield AgentStep.thought(reasoning, provider=self.provider, confidence=0.8)

        observation: Any = None
        if use_tool:
            tool_name = self._choose_tool(tools, prompt)
            if tool_name is not None:
                args = self._tool_args(tool_name, tools, prompt)
                observation = yield AgentStep.tool(tool_name, **args)
                yield AgentStep.thought(
                    f"The tool '{tool_name}' returned: {observation}",
                    provider=self.provider,
                )

        answer = self._complete(f"Given the findings, answer: {prompt}")
        if observation is not None:
            answer = f"{answer} (based on tool result: {observation})"
        return answer

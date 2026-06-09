"""Session state: context, trace and step counters."""
from __future__ import annotations

from dataclasses import dataclass, field

from agentguard.audit.trace import Trace
from agentguard.schemas.context import RuntimeContext


@dataclass
class Session:
    context: RuntimeContext
    trace: Trace = field(init=False)
    step_count: int = 0
    tool_call_count: int = 0

    def __post_init__(self) -> None:
        self.trace = Trace(session_id=self.context.session_id)

    def inc_step(self) -> int:
        self.step_count += 1
        return self.step_count

    def inc_tool_call(self) -> int:
        self.tool_call_count += 1
        return self.tool_call_count

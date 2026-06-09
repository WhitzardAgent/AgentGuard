"""Runtime context attached to every event."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class RuntimeContext:
    """Execution context propagated across a session."""

    session_id: str
    user_id: str | None = None
    agent_id: str | None = None
    task_id: str | None = None
    policy: str | None = None
    policy_version: str | None = None
    environment: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "RuntimeContext":
        known = {f for f in cls.__dataclass_fields__}  # noqa: C416
        kwargs = {k: v for k, v in (data or {}).items() if k in known}
        kwargs.setdefault("session_id", "unknown")
        return cls(**kwargs)

    def child(self, **overrides: Any) -> "RuntimeContext":
        """Derive a new context with overrides."""
        data = self.to_dict()
        data.update(overrides)
        return RuntimeContext.from_dict(data)

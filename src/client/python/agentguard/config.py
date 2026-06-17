"""AgentGuard client configuration."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class GuardConfig:
    session_id: str
    user_id: str | None = None
    agent_id: str | None = None
    policy: str | None = None
    server_url: str | None = None
    api_key: str | None = None
    environment: str | None = None

    # sandbox
    sandbox: str = "local"
    sandbox_profile: Any = None

    # runtime limits
    max_steps: int = 12
    max_tool_calls: int = 24
    window_size: int = 8

    # audit
    audit_path: str | None = None

    # remote
    remote_timeout_s: float = 5.0
    remote_retries: int = 2

    metadata: dict[str, Any] = field(default_factory=dict)

"""Built-in client plugins."""
from __future__ import annotations

from agentguard.plugins.builtin.agentdog_proxy import (
    AgentDoGProxyConfig,
    AgentDoGProxyPlugin,
)

__all__ = ["AgentDoGProxyPlugin", "AgentDoGProxyConfig"]

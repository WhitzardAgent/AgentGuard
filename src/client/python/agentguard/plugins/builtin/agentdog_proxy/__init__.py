"""AgentDoG client proxy plugin package."""
from __future__ import annotations

from agentguard.plugins.builtin.agentdog_proxy.config import AgentDoGProxyConfig
from agentguard.plugins.builtin.agentdog_proxy.plugin import AgentDoGProxyPlugin

__all__ = ["AgentDoGProxyPlugin", "AgentDoGProxyConfig"]

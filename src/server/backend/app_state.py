"""Process-wide shared singletons for the server (manager + console state)."""
from __future__ import annotations

import os

from backend.audit.agent_service import AgentAuditService
from backend.console.state import ConsoleState
from backend.runtime.manager import RuntimeManager
from backend.skill_service.router import SkillServiceRouter
from shared.rules.loader import load_policy

_manager: RuntimeManager | None = None
_console: ConsoleState | None = None
_skills: SkillServiceRouter | None = None
_agent_audits: AgentAuditService | None = None


def get_manager() -> RuntimeManager:
    global _manager
    if _manager is None:
        plugin_config = (
            os.getenv("AGENTGUARD_SERVER_PLUGIN_CONFIG")
            or os.getenv("AGENTGUARD_SERVER_CHECKER_CONFIG")
            or os.getenv("AGENTGUARD_PLUGIN_CONFIG")
        )
        _manager = RuntimeManager(plugin_config=plugin_config)
        policy_path = os.getenv("AGENTGUARD_POLICY")
        if policy_path:
            _manager.policy.store.set_rules(load_policy(policy_path), version=policy_path)
    return _manager


def get_console() -> ConsoleState:
    global _console
    if _console is None:
        _console = ConsoleState(get_manager())
    return _console


def get_skills() -> SkillServiceRouter:
    global _skills
    if _skills is None:
        _skills = SkillServiceRouter()
    return _skills


def get_agent_audit_service() -> AgentAuditService:
    global _agent_audits
    if _agent_audits is None:
        _agent_audits = AgentAuditService()
    return _agent_audits


def stop_agent_audit_service() -> None:
    global _agent_audits
    if _agent_audits is not None:
        _agent_audits.shutdown()
        _agent_audits = None

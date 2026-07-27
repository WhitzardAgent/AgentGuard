from __future__ import annotations

import json

from backend.agents.store import AgentRecord
from backend.api.console_router import (
    _agent_record_visible_in_console,
    _dedupe_console_agent_records,
)


def _agent_record(**kwargs) -> AgentRecord:
    values = {
        "agent_id": "ag_test",
        "agent_identity_code": "agic_test",
        "provider": "openclaw",
        "external_agent_id": "main",
        "agent_type": "agent",
        "status": "active",
    }
    values.update(kwargs)
    return AgentRecord(**values)


def test_console_hides_legacy_openclaw_runtime_agents() -> None:
    record = _agent_record(
        external_agent_id="ticket-17-runtime",
        agent_type="runtime",
        metadata_json=json.dumps({"openclaw_agent_id": "main"}),
    )

    assert _agent_record_visible_in_console(record) is False


def test_console_shows_openclaw_catalog_agents() -> None:
    record = _agent_record(
        external_agent_id="agentguard-emailcase",
        agent_type="agent",
        metadata_json=json.dumps({"display_agent_id": "openclaw:agentguard-emailcase"}),
    )

    assert _agent_record_visible_in_console(record) is True


def test_console_hides_legacy_unscoped_opencode_duplicate_when_scoped_agent_exists() -> None:
    legacy = _agent_record(
        agent_id="ag_legacy",
        provider="opencode",
        provider_instance_id="",
        external_agent_id="opencode:agentguard",
        name="opencode:agentguard",
    )
    scoped = _agent_record(
        agent_id="ag_scoped",
        provider="opencode",
        provider_instance_id="opencode-local",
        external_agent_id="opencode:agentguard",
        name="OpenCode agentguard",
    )

    assert [record.agent_id for record in _dedupe_console_agent_records([legacy, scoped])] == ["ag_scoped"]


def test_console_keeps_unscoped_opencode_agent_without_scoped_duplicate() -> None:
    legacy = _agent_record(
        agent_id="ag_legacy",
        provider="opencode",
        provider_instance_id="",
        external_agent_id="opencode:agentguard",
        name="opencode:agentguard",
    )

    assert [record.agent_id for record in _dedupe_console_agent_records([legacy])] == ["ag_legacy"]

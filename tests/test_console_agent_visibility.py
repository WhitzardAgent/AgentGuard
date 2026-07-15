from __future__ import annotations

import json

from backend.agents.store import AgentRecord
from backend.api.console_router import _agent_record_visible_in_console


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


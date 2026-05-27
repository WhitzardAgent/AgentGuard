from __future__ import annotations

from types import SimpleNamespace

from agentguard import Guard
from agentguard.sdk.adapters.dify import DifyAdapter


def test_dify_principal_maps_payload_user_to_user_id() -> None:
    guard = Guard(builtin_rules=False, mode="monitor")
    adapter = DifyAdapter(guard.pipeline, guard)

    payloads = SimpleNamespace(user="end-user-1", conversation_id="conv-1", app_id="app-7")
    event = SimpleNamespace(conversation_id="conv-1")

    principal = adapter._principal_for(payloads, event)

    assert principal.agent_id == "app-7"
    assert principal.session_id == "conv-1"
    assert principal.user_id == "end-user-1"
    guard.close()

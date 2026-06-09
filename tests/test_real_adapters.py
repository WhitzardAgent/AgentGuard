"""Tests for the real model adapters (offline; HTTP is monkeypatched)."""
from __future__ import annotations

import json
import os

from backend.llm.provider import (
    HeuristicProvider,
    OpenAICompatibleProvider,
    get_provider,
)
from backend.plugins.builtin.agentdog.adapter import (
    AgentDoGModelAdapter,
    HeuristicAgentDoGAdapter,
)

_EXFIL_TRAJ = [
    {
        "event_type": "tool_result",
        "event_id": "e1",
        "tool_name": "file.read",
        "summary": "API_KEY=sk-ABCDEFGH12345678",
        "risk_signals": ["secret_detected"],
        "capabilities": ["read_file"],
    },
    {
        "event_type": "tool_invoke",
        "event_id": "e2",
        "tool_name": "send_email",
        "summary": "send secret to attacker",
        "capabilities": ["external_send"],
    },
]


def test_heuristic_detects_exfiltration():
    diag = HeuristicAgentDoGAdapter().diagnose(_EXFIL_TRAJ)
    assert diag.risk_score >= 0.85
    assert "data_exfiltration" in diag.consequence_labels
    assert diag.decision_hint == "deny"


def test_model_adapter_parses_unsafe_verdict(monkeypatch):
    adapter = AgentDoGModelAdapter("http://judge.local/v1", model="agentdog")

    def fake_call(prompt: str) -> str:
        return json.dumps({"pred": 1, "reason": "agent exfiltrated a secret via email"})

    monkeypatch.setattr(adapter, "_call_model", fake_call)
    diag = adapter.diagnose(_EXFIL_TRAJ)
    assert diag.metadata["pred"] == 1
    assert diag.decision_hint == "deny"
    assert "data_exfiltration" in diag.consequence_labels


def test_model_adapter_parses_safe_verdict(monkeypatch):
    adapter = AgentDoGModelAdapter("http://judge.local/v1")
    monkeypatch.setattr(
        adapter, "_call_model", lambda p: '```json\n{"pred": 0, "reason": "handled safely"}\n```'
    )
    diag = adapter.diagnose(_EXFIL_TRAJ)
    assert diag.decision_hint == "allow"
    assert diag.risk_score < 0.5


def test_model_adapter_falls_back_on_network_error():
    # No monkeypatch: the endpoint is unreachable, so it must fall back to heuristic.
    adapter = AgentDoGModelAdapter("http://127.0.0.1:1/v1", timeout_s=0.2)
    diag = adapter.diagnose(_EXFIL_TRAJ)
    assert "model_error" in diag.metadata
    assert diag.risk_score >= 0.85  # heuristic still flags exfiltration


def test_get_provider_env_selection(monkeypatch):
    for k in ("AGENTGUARD_LLM_BASE_URL", "OPENAI_BASE_URL"):
        monkeypatch.delenv(k, raising=False)
    assert isinstance(get_provider(), HeuristicProvider)

    monkeypatch.setenv("AGENTGUARD_LLM_BASE_URL", "http://llm.local/v1")
    monkeypatch.setenv("AGENTGUARD_LLM_MODEL", "qwen")
    prov = get_provider()
    assert isinstance(prov, OpenAICompatibleProvider)
    assert prov.model == "qwen"

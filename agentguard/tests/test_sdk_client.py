"""Tests for agentguard/sdk/client.py (RemoteGuardClient)."""
from __future__ import annotations

import json
import urllib.error
from unittest.mock import MagicMock, patch

import pytest

from agentguard.models.decisions import Action, Decision
from agentguard.models.errors import HumanApprovalPending
from agentguard.models.events import RuntimeEvent
from agentguard.sdk.client import RemoteGuardClient
from agentguard.sdk.guard import Guard
from agentguard.tests.conftest import build_event as _mk


def _decision_payload(action: str = "allow") -> bytes:
    d = Decision(action=Action(action), reason="ok", risk_score=0.0)
    return json.dumps({"ok": True, "decision": d.model_dump(mode="json")}).encode()


def _mock_response(body: bytes, status: int = 200) -> MagicMock:
    m = MagicMock()
    m.__enter__ = lambda s: s
    m.__exit__ = MagicMock(return_value=False)
    m.status = status
    m.read.return_value = body
    return m


# ──────────────────────────────────────────────────────────────────────────────

def test_evaluate_returns_allow():
    client = RemoteGuardClient("http://fake:38080", fail_open=True)
    ev = _mk("tool1")
    with patch("urllib.request.urlopen", return_value=_mock_response(_decision_payload("allow"))):
        decision = client.evaluate(ev)
    assert decision.action == Action.ALLOW


def test_evaluate_returns_deny():
    client = RemoteGuardClient("http://fake:38080", fail_open=True)
    ev = _mk("tool1")
    with patch("urllib.request.urlopen", return_value=_mock_response(_decision_payload("deny"))):
        decision = client.evaluate(ev)
    assert decision.action == Action.DENY


def test_fail_open_on_network_error():
    client = RemoteGuardClient("http://fake:38080", fail_open=True)
    ev = _mk("tool1")
    with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("conn refused")):
        decision = client.evaluate(ev)
    assert decision.action == Action.ALLOW
    assert "fail_open" in decision.reason


def test_fail_closed_on_network_error():
    client = RemoteGuardClient("http://fake:38080", fail_open=False)
    ev = _mk("tool1")
    with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("conn refused")):
        decision = client.evaluate(ev)
    assert decision.action == Action.DENY
    assert "fail_closed" in decision.reason


def test_http_422_returns_fail_open():
    """A 422 from the server (validation error) should trigger the fallback."""
    client = RemoteGuardClient("http://fake:38080", fail_open=True)
    ev = _mk("tool1")
    http_err = urllib.error.HTTPError(
        url="http://fake:38080/v1/evaluate",
        code=422,
        msg="Unprocessable",
        hdrs=None,  # type: ignore[arg-type]
        fp=None,
    )
    with patch("urllib.request.urlopen", side_effect=http_err):
        decision = client.evaluate(ev)
    assert decision.action == Action.ALLOW


def test_batch_evaluate():
    client = RemoteGuardClient("http://fake:38080", fail_open=True)
    events = [_mk("t1"), _mk("t2")]
    results_payload = json.dumps({
        "results": [
            {"ok": True, "decision": Decision(action=Action.ALLOW, reason="ok", risk_score=0.0).model_dump(mode="json")},
            {"ok": True, "decision": Decision(action=Action.DENY, reason="no", risk_score=1.0).model_dump(mode="json")},
        ]
    }).encode()
    with patch("urllib.request.urlopen", return_value=_mock_response(results_payload)):
        decisions = client.evaluate_batch(events)
    assert len(decisions) == 2
    assert decisions[0].action == Action.ALLOW
    assert decisions[1].action == Action.DENY


def test_remote_pipeline_fail_closes_if_llm_check_leaks():
    guard = Guard(remote_url="http://fake:8080", api_key="secret", fail_open=False)
    ev = _mk("tool1")
    executed = False

    def _executor(_event: RuntimeEvent) -> str:
        nonlocal executed
        executed = True
        return "should_not_run"

    leaked = Decision(
        action=Action.LLM_CHECK,
        reason="remote_llm_check_unresolved",
        risk_score=1.0,
    )

    with patch.object(guard._remote_client, "evaluate", return_value=leaked):
        with pytest.raises(HumanApprovalPending) as exc:
            guard.pipeline.guarded_call(ev, _executor)

    assert executed is False
    assert exc.value.ticket_id == "remote_review"
    assert exc.value.reason == "remote_llm_check_unresolved"
    guard.close()

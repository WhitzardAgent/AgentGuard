from __future__ import annotations

import json
import urllib.request

from backend.api.dev_server import start_dev_server
from backend.console.state import ConsoleState
from backend.preprocess.detectors.base import DetectionResult
from backend.runtime.manager import RuntimeManager
from shared.schemas.context import RuntimeContext


def test_console_register_and_detect_mcps_persists_result(monkeypatch):
    con = ConsoleState(RuntimeManager())
    mcp_unique_id = "mcp-agent:" + ("f" * 64)
    con.register_mcps(
        {
            "agent_id": "mcp-agent",
            "user_id": "mcp-user",
            "session_id": "mcp-session",
        },
        [
            {
                "name": "demo-mcp",
                "description": "Demo MCP",
                "source_framework": "mcp_native",
                "object_type": "mcp",
                "transport": "stdio",
                "root_path": "/tmp/mcp",
                "entry_file": "server.py",
                "sha256": "f" * 64,
                "file_count": 3,
                "total_size": 1234,
                "extraction": {"level": "source_directory", "confidence": "high"},
                "files": [{"relative_path": "server.py", "content": "print('hello')"}],
            }
        ],
    )

    class FakeDetector:
        def detect(self, record, *, llm_config=None):
            assert record.mcp_unique_id == mcp_unique_id
            assert llm_config == {"backend": "heuristic"}
            return DetectionResult(
                object_id=record.mcp_unique_id,
                object_type="mcp",
                name=record.name,
                risk_labels=["suspicious"],
                risk_level="medium",
                label="suspicious",
                reason="fake llm review",
                agent_id=record.agent_id,
                user_id=record.user_id,
                session_id=record.session_id,
                metadata={"llm_review": {"skipped": False}},
            )

    monkeypatch.setattr("backend.preprocess.detectors.mcp_llm_detector.MCPLLMDetector", FakeDetector)

    result = con.detect_mcps("mcp-agent", [mcp_unique_id], llm_config={"backend": "heuristic"})

    assert result["ok"] is True
    assert result["detected"] == 1
    assert result["results"][0]["detect_result"]["label"] == "suspicious"

    stored = con.mcps("mcp-agent")[0]
    assert stored["detect_result"]["label"] == "suspicious"
    assert stored["detect_result"]["reason"] == "fake llm review"
    assert stored["mcp_resource"]["files"][0]["relative_path"] == "server.py"


def test_dev_server_mcp_report_and_detect_api_updates_registered_mcp():
    manager = RuntimeManager()
    console = ConsoleState(manager)
    base_url, srv, _ = start_dev_server(manager=manager, console=console)
    mcp_unique_id = "mcp-agent:" + ("a" * 64)
    context = RuntimeContext(
        session_id="mcp-session",
        agent_id="mcp-agent",
        user_id="mcp-user",
    )
    headers = {
        "X-AgentGuard-Session-Id": "mcp-session",
        "X-AgentGuard-Agent-Id": "mcp-agent",
        "X-AgentGuard-User-Id": "mcp-user",
        "X-AgentGuard-Session-Key": "sk-mcp-session",
    }
    try:
        manager.session_pool.upsert(
            context,
            client_ip="127.0.0.1",
            client_key="sk-mcp-session",
            enforce_key=True,
        )
        reported = _post_json(
            f"{base_url}/v1/server/mcps/report",
            {
                "context": context.to_dict(),
                "mcps": [
                    {
                        "name": "demo-mcp",
                        "description": "Demo MCP",
                        "source_framework": "mcp_native",
                        "object_type": "mcp",
                        "transport": "stdio",
                        "sha256": "a" * 64,
                        "files": [{"relative_path": "server.py", "content": "print('hello')"}],
                    }
                ],
                "scan": {"summary": {"mcp_count": 1}},
            },
            headers=headers,
        )

        assert reported["status"] == "ok"
        assert reported["mcp_count"] == 1

        scoped = _get_json(f"{base_url}/v1/backend/agents/mcp-agent/mcps")
        assert len(scoped) == 1
        assert scoped[0]["name"] == "demo-mcp"
        assert scoped[0]["mcp_resource"]["files"][0]["relative_path"] == "server.py"

        detected = _post_json(
            f"{base_url}/v1/backend/agents/mcp-agent/mcps/detect",
            {
                "mcp_unique_ids": [mcp_unique_id],
                "llm_config": {"backend": "heuristic"},
            },
            headers=headers,
        )

        assert detected["ok"] is True
        assert detected["results"][0]["detect_result"]["label"] == "suspicious"
        assert detected["results"][0]["detect_result"]["reason"]

        refreshed = _get_json(f"{base_url}/v1/backend/agents/mcp-agent/mcps")
        assert refreshed[0]["detect_result"]["label"] == "suspicious"
        assert _get_json(f"{base_url}/v1/backend/mcps")[0]["name"] == "demo-mcp"
    finally:
        srv.shutdown()


def _post_json(url: str, payload: dict, *, headers: dict[str, str] | None = None) -> dict:
    request_headers = {"Content-Type": "application/json"}
    request_headers.update(headers or {})
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers=request_headers,
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=5) as response:
        return json.loads(response.read().decode("utf-8"))


def _get_json(url: str, *, headers: dict[str, str] | None = None) -> dict:
    request = urllib.request.Request(url, headers=headers or {}, method="GET")
    with urllib.request.urlopen(request, timeout=5) as response:
        return json.loads(response.read().decode("utf-8"))

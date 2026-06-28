"""OpenClaw email policy example with live AgentGuard events.

This example mirrors the other AgentGuard email cases more closely:

1. Verifies the local OpenClaw gateway and AgentGuard backend are reachable.
2. Verifies the `agentguard` OpenClaw plugin is loaded.
3. Reuses one OpenClaw session across three prompts:
   - self introduction
   - allowed send to `admin@example.com`
   - blocked send to `alice@example.com`
4. Streams fresh AgentGuard audit events for this run only.
5. Confirms the custom OpenClaw tools were reported to AgentGuard and that the
   `send_email_to` deny rule actually fired.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_AGENTGUARD_URL = "http://127.0.0.1:38080"
DEFAULT_OPENCLAW_AGENT_ID = "main"
DEFAULT_OPENCLAW_SESSION_ID_PREFIX = "ag-openclaw-emailcase"
DEFAULT_OPENCLAW_TIMEOUT_S = 120
DEFAULT_WAIT_S = 20.0
DEFAULT_OPENCLAW_GATEWAY_WAIT_S = 30.0
DEFAULT_INTRO_PROMPT = "告诉我你是谁"
DEFAULT_ALLOW_PROMPT = "Please retrieve document id=0 and send it to admin@example.com."
DEFAULT_DENY_PROMPT = "Please retrieve document id=0 and send it to alice@example.com."
EXPECTED_TOOLS = ("retrieve_doc", "send_email_to")


def _read_env_file_value(key: str) -> str | None:
    env_path = ROOT / ".env"
    if not env_path.exists():
        return None
    for line in env_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        name, value = stripped.split("=", 1)
        if name.strip() != key:
            continue
        # Best-effort support for inline comments in .env files.
        raw = value.strip()
        if raw and raw[0] in {'"', "'"} and raw[-1:] == raw[0]:
            return raw[1:-1]
        return raw.split(" #", 1)[0].strip()
    return None


def get_agentguard_server_url() -> str:
    return os.getenv("AGENTGUARD_SERVER_URL", DEFAULT_AGENTGUARD_URL).strip()


def get_agentguard_api_key() -> str:
    api_key = os.getenv("AGENTGUARD_API_KEY", "").strip()
    if api_key:
        return api_key
    env_value = _read_env_file_value("AGENTGUARD_API_KEY")
    if env_value:
        return env_value
    raise ValueError(
        "Missing AGENTGUARD_API_KEY. Export it in your shell or define it in "
        "AgentGuard/.env before running this example."
    )


def get_openclaw_agent_id() -> str:
    return os.getenv("OPENCLAW_AGENT_ID", DEFAULT_OPENCLAW_AGENT_ID).strip()


def get_openclaw_session_id() -> str:
    configured = os.getenv("OPENCLAW_SESSION_ID", "").strip()
    if configured:
        return configured
    return f"{DEFAULT_OPENCLAW_SESSION_ID_PREFIX}-{int(time.time())}"


def get_intro_prompt() -> str:
    return os.getenv("OPENCLAW_INTRO_PROMPT", DEFAULT_INTRO_PROMPT).strip()


def get_allow_prompt() -> str:
    return os.getenv("OPENCLAW_ALLOW_PROMPT", DEFAULT_ALLOW_PROMPT).strip()


def get_deny_prompt() -> str:
    return os.getenv("OPENCLAW_DENY_PROMPT", DEFAULT_DENY_PROMPT).strip()


def _load_openclaw_config() -> dict[str, Any]:
    config_path = Path.home() / ".openclaw" / "openclaw.json"
    return json.loads(config_path.read_text(encoding="utf-8"))


def should_reset_agent_history() -> bool:
    value = os.getenv("OPENCLAW_RESET_AGENT_HISTORY", "1").strip().lower()
    return value not in {"0", "false", "no", "off"}


def should_restart_gateway() -> bool:
    value = os.getenv("OPENCLAW_RESTART_GATEWAY", "1").strip().lower()
    return value not in {"0", "false", "no", "off"}


def get_openclaw_agent_dir(agent_id: str) -> Path:
    config = _load_openclaw_config()
    agents = (config.get("agents") or {}).get("list") or []
    for agent in agents:
        if isinstance(agent, dict) and agent.get("id") == agent_id:
            agent_dir = str(agent.get("agentDir") or "").strip()
            if agent_dir:
                return Path(agent_dir).expanduser().resolve()
    return (Path.home() / ".openclaw" / "agents" / agent_id / "agent").resolve()


def get_openclaw_agent_session_key(agent_id: str) -> str:
    return f"agent:{agent_id}:main"


def get_openclaw_gateway_port() -> int:
    config = _load_openclaw_config()
    port = ((config.get("gateway") or {}).get("port")) or 18789
    if not isinstance(port, int) or port <= 0:
        return 18789
    return port


def get_openclaw_workspace_dir(agent_id: str) -> Path:
    config = _load_openclaw_config()
    agents = (config.get("agents") or {}).get("list") or []
    for agent in agents:
        if isinstance(agent, dict) and agent.get("id") == agent_id:
            workspace = str(agent.get("workspace") or "").strip()
            if workspace:
                return Path(workspace).expanduser().resolve()
    workspace = (((config.get("agents") or {}).get("defaults") or {}).get("workspace")) or str(
        Path.home() / ".openclaw" / "workspace"
    )
    return Path(workspace).expanduser().resolve()


def reset_openclaw_agent_history(agent_id: str) -> list[str]:
    agent_dir = get_openclaw_agent_dir(agent_id)
    sessions_dir = agent_dir.parent / "sessions"
    removed: list[str] = []
    if not sessions_dir.exists():
        return removed
    for entry in sorted(sessions_dir.iterdir()):
        if entry.is_file():
            entry.unlink()
            removed.append(str(entry))
    return removed


def get_openclaw_audit_path(agent_id: str) -> Path:
    config_path = ROOT / "config" / "openclaw-agentguard.json"
    runtime_config = json.loads(config_path.read_text(encoding="utf-8"))
    audit_path = str(runtime_config.get("auditPath") or "").strip()
    if not audit_path:
        raise ValueError("Missing auditPath in config/openclaw-agentguard.json")
    audit_candidate = Path(audit_path)
    if audit_candidate.is_absolute():
        return audit_candidate
    return (get_openclaw_workspace_dir(agent_id) / audit_candidate).resolve()


def _http_json(url: str, *, headers: dict[str, str] | None = None) -> Any:
    request = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(request, timeout=5) as response:
        return json.loads(response.read().decode("utf-8"))


def _wait_for_agentguard_server(base_url: str, api_key: str, timeout_s: float = 30.0) -> None:
    deadline = time.time() + timeout_s
    last_error: Exception | None = None
    while time.time() < deadline:
        try:
            _http_json(
                f"{base_url}/v1/backend/health",
                headers={"X-Api-Key": api_key},
            )
            return
        except (urllib.error.URLError, OSError, json.JSONDecodeError) as exc:
            last_error = exc
            time.sleep(1.0)
    raise RuntimeError(f"AgentGuard server not reachable at {base_url}: {last_error}")


def _wait_for_openclaw_gateway(timeout_s: float = DEFAULT_OPENCLAW_GATEWAY_WAIT_S) -> None:
    deadline = time.time() + timeout_s
    last_result: subprocess.CompletedProcess[str] | None = None
    while time.time() < deadline:
        last_result = _run_command(["openclaw", "health", "--json"], timeout_s=10)
        if last_result.returncode == 0:
            return
        time.sleep(1.0)
    stderr = last_result.stderr.strip() if last_result else ""
    stdout = last_result.stdout.strip() if last_result else ""
    raise RuntimeError(
        "OpenClaw gateway did not become healthy in time. "
        f"stdout={stdout!r} stderr={stderr!r}"
    )


def _run_command(args: list[str], *, timeout_s: int = 30) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        text=True,
        capture_output=True,
        timeout=timeout_s,
        check=False,
    )


def _run_json_command(args: list[str], *, timeout_s: int = 30) -> tuple[subprocess.CompletedProcess[str], Any | None]:
    result = _run_command(args, timeout_s=timeout_s)
    if result.returncode != 0:
        return result, None
    try:
        return result, json.loads(result.stdout)
    except json.JSONDecodeError:
        return result, None


def _print_command_result(title: str, result: subprocess.CompletedProcess[str]) -> None:
    print(f"\n[{title}]")
    print(f"returncode: {result.returncode}")
    if result.stdout.strip():
        print("stdout:")
        print(result.stdout.strip())
    if result.stderr.strip():
        print("stderr:")
        print(result.stderr.strip())


def _listener_pids(port: int) -> list[int]:
    result = _run_command(
        ["lsof", "-tiTCP:%d" % port, "-sTCP:LISTEN"],
        timeout_s=10,
    )
    if result.returncode not in (0, 1):
        return []
    pids: list[int] = []
    for line in result.stdout.splitlines():
        stripped = line.strip()
        if stripped.isdigit():
            pids.append(int(stripped))
    return pids


def restart_openclaw_gateway(port: int) -> dict[str, Any]:
    killed_pids = _listener_pids(port)
    for pid in killed_pids:
        try:
            os.kill(pid, 15)
        except ProcessLookupError:
            continue
    deadline = time.time() + 10.0
    while time.time() < deadline:
        if not _listener_pids(port):
            break
        time.sleep(0.2)
    process = subprocess.Popen(
        ["openclaw", "gateway", "run", "--port", str(port), "--verbose"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
        text=True,
    )
    _wait_for_openclaw_gateway()
    return {
        "killed_pids": killed_pids,
        "started_pid": process.pid,
        "port": port,
    }


def _pretty_print_event(record: dict[str, Any]) -> None:
    meta = record.get("metadata") or {}
    decision_meta = meta.get("decision_metadata") or {}
    payload = meta.get("payload") or {}

    print("\n[AgentGuard Event]")
    print(
        json.dumps(
            {
                "timestamp": record.get("timestamp"),
                "session_id": record.get("session_id"),
                "event_type": record.get("event_type"),
                "decision_type": record.get("decision_type"),
                "policy_id": record.get("policy_id"),
                "reason": record.get("reason"),
                "risk_signals": record.get("risk_signals"),
                "route": decision_meta.get("route"),
                "payload": payload,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


def _read_audit_records_since(audit_path: Path, start_offset: int) -> list[dict[str, Any]]:
    if not audit_path.exists():
        return []
    with audit_path.open("r", encoding="utf-8") as handle:
        handle.seek(start_offset)
        chunk = handle.read()
    records: list[dict[str, Any]] = []
    for line in chunk.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        try:
            record = json.loads(stripped)
        except json.JSONDecodeError:
            continue
        if isinstance(record, dict):
            records.append(record)
    return records


def _follow_audit_file(
    audit_path: Path,
    start_offset: int,
    stop_event: threading.Event,
    printed_records: list[dict[str, Any]],
) -> None:
    current_offset = start_offset
    idle_rounds = 0

    while True:
        if audit_path.exists():
            with audit_path.open("r", encoding="utf-8") as handle:
                handle.seek(current_offset)
                chunk = handle.read()
                current_offset = handle.tell()
            if chunk:
                idle_rounds = 0
                for line in chunk.splitlines():
                    stripped = line.strip()
                    if not stripped:
                        continue
                    try:
                        record = json.loads(stripped)
                    except json.JSONDecodeError:
                        continue
                    printed_records.append(record)
                    _pretty_print_event(record)
            else:
                idle_rounds += 1
        else:
            idle_rounds += 1

        if stop_event.is_set() and idle_rounds >= 3:
            return
        time.sleep(0.5)


def _find_main_session(
    sessions: list[dict[str, Any]],
    agent_id: str,
    session_key: str,
) -> dict[str, Any] | None:
    for session in sessions:
        if session.get("agent_id") != agent_id:
            continue
        if session.get("client_key") == session_key:
            return session
        metadata = session.get("metadata") or {}
        if metadata.get("client_session_key") == session_key:
            return session
        if session.get("session_id") == session_key:
            return session
    return None


def _wait_for_session_and_tools(
    *,
    base_url: str,
    api_key: str,
    agent_id: str,
    session_key: str,
    timeout_s: float = DEFAULT_WAIT_S,
) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    headers = {"X-Api-Key": api_key}
    deadline = time.time() + timeout_s
    last_session: dict[str, Any] | None = None
    last_tools: list[dict[str, Any]] = []

    while time.time() < deadline:
        sessions_payload = _http_json(f"{base_url}/v1/backend/sessions", headers=headers)
        tools_payload = _http_json(f"{base_url}/v1/backend/tools", headers=headers)

        sessions = sessions_payload.get("sessions") or []
        last_session = _find_main_session(sessions, agent_id, session_key)
        last_tools = [tool for tool in tools_payload if tool.get("owner_agent_id") == agent_id]

        seen_tool_names = {tool.get("name") for tool in last_tools}
        if last_session and all(tool_name in seen_tool_names for tool_name in EXPECTED_TOOLS):
            return last_session, last_tools
        time.sleep(1.0)

    return last_session, last_tools


def _tool_payload(record: dict[str, Any]) -> dict[str, Any]:
    metadata = record.get("metadata") or {}
    payload = metadata.get("payload") or {}
    return payload if isinstance(payload, dict) else {}


def _tool_name(record: dict[str, Any]) -> str | None:
    payload = _tool_payload(record)
    tool_name = payload.get("tool_name")
    return tool_name if isinstance(tool_name, str) else None


def _tool_arguments(record: dict[str, Any]) -> dict[str, Any]:
    payload = _tool_payload(record)
    arguments = payload.get("arguments") or {}
    return arguments if isinstance(arguments, dict) else {}


def _run_agent_turn(
    *,
    agent_id: str,
    session_id: str,
    prompt: str,
    timeout_s: int,
) -> subprocess.CompletedProcess[str]:
    args = [
        "openclaw",
        "agent",
        "--agent",
        agent_id,
        "--session-id",
        session_id,
        "--message",
        prompt,
        "--json",
        "--timeout",
        str(timeout_s),
    ]
    return _run_command(args, timeout_s=timeout_s + 20)


def main() -> int:
    base_url = get_agentguard_server_url()
    api_key = get_agentguard_api_key()
    openclaw_agent_id = get_openclaw_agent_id()
    openclaw_session_id = get_openclaw_session_id()
    openclaw_session_key = get_openclaw_agent_session_key(openclaw_agent_id)
    openclaw_gateway_port = get_openclaw_gateway_port()
    intro_prompt = get_intro_prompt()
    allow_prompt = get_allow_prompt()
    deny_prompt = get_deny_prompt()
    openclaw_timeout_s = int(os.getenv("OPENCLAW_TIMEOUT_S", str(DEFAULT_OPENCLAW_TIMEOUT_S)))
    audit_path = get_openclaw_audit_path(openclaw_agent_id)
    reset_history = should_reset_agent_history()
    restart_gateway = should_restart_gateway()
    removed_session_files = reset_openclaw_agent_history(openclaw_agent_id) if reset_history else []
    gateway_restart_info = restart_openclaw_gateway(openclaw_gateway_port) if restart_gateway else None

    print("[Config]")
    print(json.dumps(
        {
            "agentguard_server_url": base_url,
            "openclaw_agent_id": openclaw_agent_id,
            "openclaw_session_id": openclaw_session_id,
            "openclaw_agent_session_key": openclaw_session_key,
            "openclaw_gateway_port": openclaw_gateway_port,
            "prompts": {
                "intro": intro_prompt,
                "allow": allow_prompt,
                "deny": deny_prompt,
            },
            "openclaw_audit_path": str(audit_path),
            "restart_gateway": restart_gateway,
            "gateway_restart_info": gateway_restart_info,
            "reset_agent_history": reset_history,
            "removed_session_files": removed_session_files,
            "expected_tools": list(EXPECTED_TOOLS),
        },
        ensure_ascii=False,
        indent=2,
    ))

    _wait_for_agentguard_server(base_url, api_key)
    print(f"\n[AgentGuard] reachable at {base_url}")

    health_result = _run_command(["openclaw", "health"], timeout_s=15)
    _print_command_result("OpenClaw Health", health_result)
    if health_result.returncode != 0:
        print("\n[Result] FAILED: OpenClaw gateway is not reachable.")
        return 1

    plugins_result, plugins_payload = _run_json_command(
        ["openclaw", "plugins", "list", "--enabled", "--json"],
        timeout_s=20,
    )
    _print_command_result("OpenClaw Plugins", plugins_result)
    if plugins_result.returncode != 0 or not isinstance(plugins_payload, dict):
        print("\n[Result] FAILED: Could not inspect enabled OpenClaw plugins.")
        return 1

    loaded_plugins = plugins_payload.get("plugins") or []
    agentguard_plugin = next((plugin for plugin in loaded_plugins if plugin.get("id") == "agentguard"), None)
    if not agentguard_plugin or agentguard_plugin.get("status") != "loaded":
        print("\n[Result] FAILED: AgentGuard plugin is not loaded in OpenClaw.")
        return 1

    print("\n[Plugin Loaded]")
    print(
        json.dumps(
            {
                "id": agentguard_plugin.get("id"),
                "name": agentguard_plugin.get("name"),
                "status": agentguard_plugin.get("status"),
                "hookCount": agentguard_plugin.get("hookCount"),
                "source": agentguard_plugin.get("source"),
            },
            ensure_ascii=False,
            indent=2,
        )
    )

    audit_start_offset = audit_path.stat().st_size if audit_path.exists() else 0
    event_printer_stop = threading.Event()
    printed_records: list[dict[str, Any]] = []
    event_printer = threading.Thread(
        target=_follow_audit_file,
        args=(audit_path, audit_start_offset, event_printer_stop, printed_records),
        daemon=True,
    )
    event_printer.start()

    turn_results = [
        # ("Intro", _run_agent_turn(
        #     agent_id=openclaw_agent_id,
        #     session_id=openclaw_session_id,
        #     prompt=intro_prompt,
        #     timeout_s=openclaw_timeout_s,
        # )),
        # ("Allow Email", _run_agent_turn(
        #     agent_id=openclaw_agent_id,
        #     session_id=openclaw_session_id,
        #     prompt=allow_prompt,
        #     timeout_s=openclaw_timeout_s,
        # )),
        ("Blocked Email", _run_agent_turn(
            agent_id=openclaw_agent_id,
            session_id=openclaw_session_id,
            prompt=deny_prompt,
            timeout_s=openclaw_timeout_s,
        )),
    ]
    time.sleep(3.0)
    event_printer_stop.set()
    event_printer.join(timeout=5)
    seen_event_ids = {
        record.get("event_id")
        for record in printed_records
        if isinstance(record.get("event_id"), str)
    }
    for record in _read_audit_records_since(audit_path, audit_start_offset):
        event_id = record.get("event_id")
        if isinstance(event_id, str) and event_id in seen_event_ids:
            continue
        printed_records.append(record)
        if isinstance(event_id, str):
            seen_event_ids.add(event_id)
        _pretty_print_event(record)
    for title, result in turn_results:
        _print_command_result(f"OpenClaw Agent Run - {title}", result)

    session, tools = _wait_for_session_and_tools(
        base_url=base_url,
        api_key=api_key,
        agent_id=openclaw_agent_id,
        session_key=openclaw_session_key,
    )

    print("\n[AgentGuard Session]")
    if session is None:
        print("No OpenClaw session was registered in AgentGuard.")
    else:
        print(
            json.dumps(
                {
                    "agent_id": session.get("agent_id"),
                    "session_id": session.get("session_id"),
                    "session_key": session.get("session_key"),
                    "client_config_url": session.get("client_config_url"),
                    "client_plugin_list_url": session.get("client_plugin_list_url"),
                    "last_event_metadata": (session.get("metadata") or {}).get("event_metadata"),
                },
                ensure_ascii=False,
                indent=2,
            )
        )

    print("\n[AgentGuard Tools]")
    condensed_tools = [
        {
            "name": tool.get("name"),
            "input_params": tool.get("input_params"),
        }
        for tool in tools
        if tool.get("name") in EXPECTED_TOOLS
    ]
    print(json.dumps(condensed_tools, ensure_ascii=False, indent=2))

    seen_tool_names = {tool.get("name") for tool in tools}
    retrieve_seen = any(
        record.get("event_type") == "tool_invoke"
        and _tool_name(record) == "retrieve_doc"
        and record.get("decision_type") == "allow"
        for record in printed_records
    )
    admin_send_allowed = any(
        record.get("event_type") == "tool_invoke"
        and _tool_name(record) == "send_email_to"
        and _tool_arguments(record).get("addr") == "admin@example.com"
        and record.get("decision_type") == "allow"
        for record in printed_records
    )
    alice_send_blocked = any(
        record.get("event_type") == "tool_invoke"
        and _tool_name(record) == "send_email_to"
        and _tool_arguments(record).get("addr") == "alice@example.com"
        and record.get("decision_type") == "deny"
        for record in printed_records
    )
    case_ok = (
        session is not None
        and all(tool_name in seen_tool_names for tool_name in EXPECTED_TOOLS)
        and retrieve_seen
        and admin_send_allowed
        and alice_send_blocked
    )

    print("\n[Summary]")
    print(
        json.dumps(
            {
                "plugin_loaded": True,
                "openclaw_health_ok": health_result.returncode == 0,
                "agent_command_ok": all(result.returncode == 0 for _, result in turn_results),
                "agentguard_event_count": len(printed_records),
                "agentguard_session_registered": session is not None,
                "agentguard_expected_tools_seen": sorted(seen_tool_names.intersection(EXPECTED_TOOLS)),
                "retrieve_doc_allowed_seen": retrieve_seen,
                "admin_send_allowed_seen": admin_send_allowed,
                "alice_send_blocked_seen": alice_send_blocked,
                "case_ok": case_ok,
            },
            ensure_ascii=False,
            indent=2,
        )
    )

    if case_ok:
        print("\n[Result] PASSED: OpenClaw email case matched the other AgentGuard framework demos.")
        return 0

    print("\n[Result] FAILED: OpenClaw did not complete the expected email allow/deny flow.")
    return 1


if __name__ == "__main__":
    sys.exit(main())

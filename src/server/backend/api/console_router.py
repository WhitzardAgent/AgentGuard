"""Management-console API consumed by the web frontend.

Paths match the frontend proxy contract (src/server/frontend/app.py strips the
/api/ prefix), so these are mounted at the server root. All data is backed by
real server state (policy store, live traffic, approvals) via ConsoleState.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Cookie
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from backend.api.schemas import McpDetectRequest
from backend.agents.store import AgentRecord, AgentStore, agent_delete_allowed_from_console
from backend.app_state import get_console
from backend.auth.models import RuntimeSessionSummary
from backend.auth.session_store import get_runtime_session_store
from backend.database import DatabaseUnavailable
from backend.user.permissions import is_admin_user
from backend.user.router import SESSION_COOKIE, get_user_store

router = APIRouter()


class LabelBody(BaseModel):
    boundary: str | None = None
    sensitivity: str | None = None
    integrity: str | None = None
    tags: list[str] = Field(default_factory=list)


class RuleSourceBody(BaseModel):
    source: str = ""
    keep_builtin: bool | None = None


class RuleGenerateBody(BaseModel):
    requirement: str = ""
    user_feedback: str = ""
    current_candidate: dict[str, Any] | None = None
    max_rounds: int = 4
    llm_config: dict[str, Any] | None = None


class SkillDetectBody(BaseModel):
    skill_unique_ids: list[str] = Field(default_factory=list)
    use_llm: bool = False
    llm_config: dict[str, Any] | None = None
    llm_concurrency: int | None = None


class ApprovalBody(BaseModel):
    note: str = ""


def _err(message: str, status: int) -> JSONResponse:
    return JSONResponse({"ok": False, "error": message}, status_code=status)


# ---- agents -----------------------------------------------------------
@router.get("/v1/backend/agents")
def list_agents(
    agentguard_user_session: str | None = Cookie(default=None, alias=SESSION_COOKIE),
) -> list[dict[str, Any]]:
    visible = _visible_scope(agentguard_user_session)
    if visible["agent_ids"] is not None and not visible["agent_ids"]:
        return []
    try:
        records = AgentStore().list_agents(visible["agent_ids"])
    except DatabaseUnavailable:
        return []
    return [_agent_record_to_console_item(record) for record in records if _agent_record_visible_in_console(record)]


@router.delete("/v1/backend/agents/{agent_id}")
def delete_agent(
    agent_id: str,
    agentguard_user_session: str | None = Cookie(default=None, alias=SESSION_COOKIE),
) -> Any:
    visible = _visible_scope(agentguard_user_session)
    if visible.get("user_id") is None:
        return _err("login required", 401)
    if not _agent_visible(visible, agent_id):
        return _err("agent not visible", 403)
    try:
        store = AgentStore()
        agent = store.get_agent(agent_id)
        if agent is None:
            return _err(f"agent '{agent_id}' not found", 404)
        if not agent_delete_allowed_from_console(agent):
            return _err("only LangChain agents can be deleted from the console", 409)
        result = store.delete_agent(agent.agent_id)
    except DatabaseUnavailable:
        return _err("database unavailable", 503)
    if not result.deleted:
        return _err(f"agent '{agent_id}' not found", 404)
    get_console().unregister_agent(result.agent_id)
    return {"ok": True, "agent_id": result.agent_id, "deleted": result.to_dict()}


# ---- tools -------------------------------------------------------------
@router.get("/v1/backend/tools")
def list_tools(
    agentguard_user_session: str | None = Cookie(default=None, alias=SESSION_COOKIE),
) -> list[dict[str, Any]]:
    visible = _visible_scope(agentguard_user_session)
    return get_console().tools(
        visible_to_external_accounts=visible["external_accounts"],
        visible_agent_ids=visible["agent_ids"],
    )


@router.get("/v1/backend/agents/{agent_id}/tools")
def list_agent_tools(
    agent_id: str,
    agentguard_user_session: str | None = Cookie(default=None, alias=SESSION_COOKIE),
) -> list[dict[str, Any]]:
    visible = _visible_scope(agentguard_user_session)
    return get_console().tools(
        agent_id,
        visible_to_external_accounts=visible["external_accounts"],
        visible_agent_ids=visible["agent_ids"],
    )


@router.patch("/v1/backend/agents/{agent_id}/tools/{tool_name}/labels")
def patch_tool_labels(agent_id: str, tool_name: str, body: LabelBody) -> Any:
    tool = get_console().patch_tool_labels(agent_id, tool_name, body.model_dump())
    if tool is None:
        return _err(f"tool '{tool_name}' not found for agent '{agent_id}'", 404)
    return {"ok": True, "tool": tool}


# ---- skills ------------------------------------------------------------
@router.get("/v1/backend/skills")
def list_skills(
    agentguard_user_session: str | None = Cookie(default=None, alias=SESSION_COOKIE),
) -> list[dict[str, Any]]:
    visible = _visible_scope(agentguard_user_session)
    return get_console().skills(
        visible_to_external_accounts=visible["external_accounts"],
        visible_agent_ids=visible["agent_ids"],
    )


@router.get("/v1/backend/agents/{agent_id}/skills")
def list_agent_skills(
    agent_id: str,
    agentguard_user_session: str | None = Cookie(default=None, alias=SESSION_COOKIE),
) -> list[dict[str, Any]]:
    visible = _visible_scope(agentguard_user_session)
    return get_console().skills(
        agent_id,
        visible_to_external_accounts=visible["external_accounts"],
        visible_agent_ids=visible["agent_ids"],
    )


@router.post("/v1/backend/agents/{agent_id}/skills/detect")
def detect_agent_skills(agent_id: str, body: SkillDetectBody) -> Any:
    result = get_console().detect_skills(
        agent_id,
        body.skill_unique_ids,
        use_llm=body.use_llm,
        llm_config=body.llm_config,
    )
    if not result.get("ok"):
        return JSONResponse(result, status_code=int(result.pop("code", 422)))
    return result


# ---- mcps -------------------------------------------------------------
@router.get("/v1/backend/mcps")
def list_mcps(
    agentguard_user_session: str | None = Cookie(default=None, alias=SESSION_COOKIE),
) -> list[dict[str, Any]]:
    visible = _visible_scope(agentguard_user_session)
    return get_console().mcps(
        visible_to_external_accounts=visible["external_accounts"],
        visible_agent_ids=visible["agent_ids"],
    )


@router.get("/v1/backend/agents/{agent_id}/mcps")
def list_agent_mcps(
    agent_id: str,
    agentguard_user_session: str | None = Cookie(default=None, alias=SESSION_COOKIE),
) -> list[dict[str, Any]]:
    visible = _visible_scope(agentguard_user_session)
    return get_console().mcps(
        agent_id,
        visible_to_external_accounts=visible["external_accounts"],
        visible_agent_ids=visible["agent_ids"],
    )


@router.post("/v1/backend/agents/{agent_id}/mcps/detect")
def detect_agent_mcps(agent_id: str, body: McpDetectRequest) -> Any:
    result = get_console().detect_mcps(
        agent_id,
        body.mcp_unique_ids,
        llm_config=body.llm_config,
    )
    if not result.get("ok"):
        return JSONResponse(result, status_code=int(result.pop("code", 422)))
    return result


# ---- rules -------------------------------------------------------------
@router.get("/v1/backend/rules")
def list_rules() -> list[dict[str, Any]]:
    return get_console().list_rules()


@router.get("/v1/backend/agents/{agent_id}/rules")
def list_agent_rules(agent_id: str) -> list[dict[str, Any]]:
    return get_console().list_rules(agent_id)


@router.post("/v1/backend/rules/check")
def check_rules(body: RuleSourceBody) -> dict[str, Any]:
    return get_console().check(body.source)


@router.post("/v1/backend/agents/{agent_id}/rules/generate")
def generate_rule(agent_id: str, body: RuleGenerateBody) -> Any:
    result = get_console().generate_rule(
        agent_id,
        body.requirement,
        user_feedback=body.user_feedback,
        current_candidate=body.current_candidate,
        max_rounds=body.max_rounds,
        llm_config=body.llm_config,
    )
    if not result.get("ok"):
        return JSONResponse(result, status_code=int(result.pop("code", 422)))
    return result


@router.post("/v1/backend/rules/reload")
def reload_rules(body: RuleSourceBody) -> Any:
    result = get_console().reload_rules(body.source)
    if not result.get("ok"):
        return JSONResponse(result, status_code=400)
    return result


@router.post("/v1/backend/agents/{agent_id}/rules")
def publish_rule(agent_id: str, body: RuleSourceBody) -> Any:
    result = get_console().publish_rule(agent_id, body.source)
    if not result.get("ok"):
        return JSONResponse(result, status_code=result.pop("code", 422))
    return result


@router.delete("/v1/backend/agents/{agent_id}/rules/{rule_id}")
def delete_rule(agent_id: str, rule_id: str) -> Any:
    result = get_console().delete_rule(agent_id, rule_id)
    if not result.get("ok"):
        return JSONResponse(result, status_code=result.pop("code", 404))
    return result


# ---- runtime observability ----------------------------------------
@router.get("/v1/backend/stats")
def global_stats() -> dict[str, Any]:
    return get_console().stats()


@router.get("/v1/backend/traffic")
def global_traffic(n: int = 30, action: str | None = None, tool: str | None = None) -> list[dict[str, Any]]:
    return get_console().traffic(None, n, action, tool)


@router.get("/v1/backend/audit/recent")
def global_audit(n: int = 20) -> list[dict[str, Any]]:
    return get_console().audit_recent(None, n)


@router.get("/v1/backend/approvals")
def global_approvals() -> list[dict[str, Any]]:
    return get_console().approvals()


@router.get("/v1/backend/agents/{agent_id}/runtime/stats")
def agent_stats(agent_id: str) -> dict[str, Any]:
    return get_console().stats(agent_id)


@router.get("/v1/backend/agents/{agent_id}/runtime/traffic")
def agent_traffic(
    agent_id: str, n: int = 30, action: str | None = None, tool: str | None = None
) -> list[dict[str, Any]]:
    return get_console().traffic(agent_id, n, action, tool)


@router.get("/v1/backend/agents/{agent_id}/runtime/approvals")
def agent_approvals(agent_id: str) -> list[dict[str, Any]]:
    return get_console().approvals(agent_id)


@router.get("/v1/backend/agents/{agent_id}/runtime/audit/recent")
def agent_audit(agent_id: str, n: int = 20) -> list[dict[str, Any]]:
    return get_console().audit_recent(agent_id, n)


@router.get("/v1/backend/agents/{agent_id}/runtime/sessions")
def agent_runtime_sessions(
    agent_id: str,
    status: str = "active",
    n: int = 50,
    agentguard_user_session: str | None = Cookie(default=None, alias=SESSION_COOKIE),
) -> Any:
    visible = _visible_scope(agentguard_user_session)
    user_id = visible.get("user_id")
    if user_id is None:
        return _err("login required", 401)
    if not _agent_visible(visible, agent_id):
        return _err("agent not visible", 403)
    session_user_id = None if visible.get("is_admin") else int(user_id)
    try:
        summaries = get_runtime_session_store().list_sessions(
            agent_id=agent_id,
            user_id=session_user_id,
            status=status,
            limit=n,
        )
    except DatabaseUnavailable:
        return []
    return [_runtime_session_summary_to_item(summary) for summary in summaries]


@router.post("/v1/backend/agents/{agent_id}/runtime/sessions/{session_id}/close")
def close_agent_runtime_session(
    agent_id: str,
    session_id: str,
    agentguard_user_session: str | None = Cookie(default=None, alias=SESSION_COOKIE),
) -> Any:
    visible = _visible_scope(agentguard_user_session)
    user_id = visible.get("user_id")
    if user_id is None:
        return _err("login required", 401)
    if not _agent_visible(visible, agent_id):
        return _err("agent not visible", 403)
    is_admin = bool(visible.get("is_admin"))
    session_user_id = None if is_admin else int(user_id)
    try:
        store = get_runtime_session_store()
        session = store.get_session(session_id)
        if (
            session is None
            or session.agent_id != agent_id
            or (not is_admin and session.user_id != int(user_id))
        ):
            return _err("runtime session not found", 404)
        store.close_session(session_id)
        client_close = _notify_client_runtime_session_closed(session)
        updated = store.list_sessions(agent_id=agent_id, user_id=session_user_id, status="all", limit=200)
    except DatabaseUnavailable:
        return _err("database unavailable", 503)
    for summary in updated:
        if summary.session.session_id == session_id:
            return {
                "ok": True,
                "session": _runtime_session_summary_to_item(summary),
                "client_close": client_close,
            }
    return {
        "ok": True,
        "session": {"session_id": session_id, "status": "closed"},
        "client_close": client_close,
    }


@router.post("/v1/backend/approvals/{ticket_id}/approve")
def approve_ticket(ticket_id: str, body: ApprovalBody | None = None) -> Any:
    if get_console().resolve_ticket(ticket_id, approved=True, note=(body.note if body else "")):
        return {"ok": True}
    return JSONResponse({"detail": "ticket not found or already resolved"}, status_code=404)


@router.post("/v1/backend/approvals/{ticket_id}/deny")
def deny_ticket(ticket_id: str, body: ApprovalBody | None = None) -> Any:
    if get_console().resolve_ticket(ticket_id, approved=False, note=(body.note if body else "")):
        return {"ok": True}
    return JSONResponse({"detail": "ticket not found or already resolved"}, status_code=404)


def _visible_external_accounts(
    session_token: str | None,
) -> set[tuple[str, str]] | None:
    return _visible_scope(session_token)["external_accounts"]


def _notify_client_runtime_session_closed(session: Any) -> dict[str, Any]:
    metadata = _runtime_session_metadata(session)
    config_url = str(metadata.get("client_config_url") or "").strip()
    session_key = str(metadata.get("client_session_key") or metadata.get("openclaw_session_key") or "").strip()
    if not config_url:
        return {"status": "skipped", "reason": "no client_config_url"}
    control_url = _client_session_control_url(config_url)
    body = {
        "action": "close",
        "provider": getattr(session, "provider", None),
        "agentguard_session_id": getattr(session, "session_id", None),
        "agentguard_agent_id": getattr(session, "agent_id", None),
        "agentguard_user_id": getattr(session, "user_id", None),
        "openclaw_session_key": metadata.get("openclaw_session_key"),
        "openclaw_session_id": metadata.get("openclaw_session_id"),
    }
    data = json.dumps(body).encode("utf-8")
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if session_key:
        headers["X-AgentGuard-Session-Key"] = session_key
    request = urllib.request.Request(control_url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=2.0) as response:
            payload = response.read().decode("utf-8")
            parsed = json.loads(payload) if payload else {}
            return {"status": "ok", "url": control_url, "response": parsed}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        return {"status": "error", "url": control_url, "code": exc.code, "error": detail}
    except Exception as exc:
        return {"status": "error", "url": control_url, "error": str(exc)}


def _runtime_session_metadata(session: Any) -> dict[str, Any]:
    raw = getattr(session, "metadata_json", None)
    if not raw:
        return {}
    try:
        parsed = json.loads(str(raw))
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _client_session_control_url(config_url: str) -> str:
    parsed = urllib.parse.urlsplit(config_url)
    path = "/v1/client/session/control"
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))


def _visible_scope(session_token: str | None) -> dict[str, Any]:
    if not session_token:
        return {"external_accounts": set(), "agent_ids": set(), "user_id": None, "is_admin": False}
    try:
        store = get_user_store()
        user = store.user_for_session(session_token)
        if user is None:
            return {"external_accounts": set(), "agent_ids": set(), "user_id": None, "is_admin": False}
        if is_admin_user(user):
            return {"external_accounts": None, "agent_ids": None, "user_id": user.id, "is_admin": True}
        external_accounts = {
            (item.provider.lower(), item.account_email.lower())
            for item in store.list_external_accounts(user)
        }
        try:
            agent_ids = AgentStore().agent_ids_for_user(user.id)
        except DatabaseUnavailable:
            agent_ids = set()
        return {
            "external_accounts": external_accounts,
            "agent_ids": agent_ids,
            "user_id": user.id,
            "is_admin": False,
        }
    except DatabaseUnavailable:
        return {"external_accounts": set(), "agent_ids": set(), "user_id": None, "is_admin": False}


def _agent_visible(visible: dict[str, Any], agent_id: str) -> bool:
    visible_agent_ids = visible.get("agent_ids")
    if visible_agent_ids is None:
        return True
    return str(agent_id or "").strip() in visible_agent_ids


def _runtime_session_summary_to_item(summary: RuntimeSessionSummary) -> dict[str, Any]:
    session = summary.session
    return {
        "session_id": session.session_id,
        "agent_id": session.agent_id,
        "user_id": str(session.user_id),
        "provider": session.provider,
        "external_session_id": session.external_session_id,
        "external_account_email": session.external_account_email,
        "status": session.status,
        "created_at": _datetime_payload(session.created_at),
        "last_seen_at": _datetime_payload(session.last_seen_at),
        "closed_at": _datetime_payload(session.closed_at),
        "active_token_count": summary.active_token_count,
        "latest_token_expires_at": _datetime_payload(summary.latest_token_expires_at),
        "latest_token_issued_at": _datetime_payload(summary.latest_token_issued_at),
    }


def _datetime_payload(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def _agent_record_to_console_item(record: AgentRecord) -> dict[str, Any]:
    metadata = _safe_json_object(record.metadata_json)
    external_agent_id = str(record.external_agent_id or metadata.get("external_agent_id") or "").strip()
    app_id = str(metadata.get("app_id") or "").strip()
    display_agent_id = str(
        metadata.get("display_agent_id")
        or external_agent_id
        or app_id
        or record.name
        or record.agent_id
    ).strip()
    provider = str(record.provider or metadata.get("external_provider") or metadata.get("provider") or "").strip()
    agent_type = str(record.agent_type or metadata.get("agent_type") or "").strip()
    return {
        "agent_id": record.agent_id,
        "display_agent_id": display_agent_id,
        "external_agent_id": external_agent_id or app_id,
        "external_provider": provider,
        "agent_type": agent_type,
        "name": record.name or "",
        "description": record.description or "",
        "status": record.status,
        "tool_count": 0,
        "tool_names": [],
        "skill_count": 0,
        "skill_names": [],
        "mcp_count": 0,
        "mcp_names": [],
    }


def _agent_record_visible_in_console(record: AgentRecord) -> bool:
    metadata = _safe_json_object(record.metadata_json)
    provider = str(record.provider or metadata.get("external_provider") or metadata.get("provider") or "").strip()
    if provider != "openclaw":
        return True
    agent_type = str(record.agent_type or metadata.get("agent_type") or "").strip()
    external_agent_id = str(record.external_agent_id or metadata.get("external_agent_id") or "").strip()
    if agent_type != "agent":
        return False
    if external_agent_id.startswith("ticket-"):
        return False
    return True


def _safe_json_object(value: str | None) -> dict[str, Any]:
    if not value:
        return {}
    try:
        parsed = json.loads(value)
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}

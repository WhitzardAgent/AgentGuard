"""Management-console API consumed by the web frontend.

Paths match the frontend proxy contract (src/server/frontend/app.py strips the
/api/ prefix), so these are mounted at the server root. All data is backed by
real server state (policy store, live traffic, approvals) via ConsoleState.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from backend.app_state import get_console

router = APIRouter()


class LabelBody(BaseModel):
    boundary: str | None = None
    sensitivity: str | None = None
    integrity: str | None = None
    tags: list[str] = Field(default_factory=list)


class RuleSourceBody(BaseModel):
    source: str = ""
    keep_builtin: bool | None = None


class ApprovalBody(BaseModel):
    note: str = ""


def _err(message: str, status: int) -> JSONResponse:
    return JSONResponse({"ok": False, "error": message}, status_code=status)


# ---- tools -------------------------------------------------------------
@router.get("/tools")
def list_tools() -> list[dict[str, Any]]:
    return get_console().tools()


@router.get("/agents/{agent_id}/tools")
def list_agent_tools(agent_id: str) -> list[dict[str, Any]]:
    return get_console().tools(agent_id)


@router.patch("/agents/{agent_id}/tools/{tool_name}/labels")
def patch_tool_labels(agent_id: str, tool_name: str, body: LabelBody) -> Any:
    tool = get_console().patch_tool_labels(agent_id, tool_name, body.model_dump())
    if tool is None:
        return _err(f"tool '{tool_name}' not found for agent '{agent_id}'", 404)
    return {"ok": True, "tool": tool}


# ---- rules -------------------------------------------------------------
@router.get("/rules")
def list_rules() -> list[dict[str, Any]]:
    return get_console().list_rules()


@router.get("/agents/{agent_id}/rules")
def list_agent_rules(agent_id: str) -> list[dict[str, Any]]:
    return get_console().list_rules(agent_id)


@router.post("/rules/check")
def check_rules(body: RuleSourceBody) -> dict[str, Any]:
    return get_console().check(body.source)


@router.post("/rules/reload")
def reload_rules(body: RuleSourceBody) -> Any:
    result = get_console().reload_rules(body.source)
    if not result.get("ok"):
        return JSONResponse(result, status_code=400)
    return result


@router.post("/agents/{agent_id}/rules")
def publish_rule(agent_id: str, body: RuleSourceBody) -> Any:
    result = get_console().publish_rule(agent_id, body.source)
    if not result.get("ok"):
        return JSONResponse(result, status_code=result.pop("code", 422))
    return result


@router.delete("/agents/{agent_id}/rules/{rule_id}")
def delete_rule(agent_id: str, rule_id: str) -> Any:
    result = get_console().delete_rule(agent_id, rule_id)
    if not result.get("ok"):
        return JSONResponse(result, status_code=result.pop("code", 404))
    return result


# ---- runtime observability ----------------------------------------
@router.get("/stats")
def global_stats() -> dict[str, Any]:
    return get_console().stats()


@router.get("/traffic")
def global_traffic(n: int = 30, action: str | None = None, tool: str | None = None) -> list[dict[str, Any]]:
    return get_console().traffic(None, n, action, tool)


@router.get("/audit/recent")
def global_audit(n: int = 20) -> list[dict[str, Any]]:
    return get_console().audit_recent(None, n)


@router.get("/approvals")
def global_approvals() -> list[dict[str, Any]]:
    return get_console().approvals()


@router.get("/agents/{agent_id}/runtime/stats")
def agent_stats(agent_id: str) -> dict[str, Any]:
    return get_console().stats(agent_id)


@router.get("/agents/{agent_id}/runtime/traffic")
def agent_traffic(
    agent_id: str, n: int = 30, action: str | None = None, tool: str | None = None
) -> list[dict[str, Any]]:
    return get_console().traffic(agent_id, n, action, tool)


@router.get("/agents/{agent_id}/runtime/approvals")
def agent_approvals(agent_id: str) -> list[dict[str, Any]]:
    return get_console().approvals(agent_id)


@router.get("/agents/{agent_id}/runtime/audit/recent")
def agent_audit(agent_id: str, n: int = 20) -> list[dict[str, Any]]:
    return get_console().audit_recent(agent_id, n)


@router.post("/approvals/{ticket_id}/approve")
def approve_ticket(ticket_id: str, body: ApprovalBody | None = None) -> Any:
    if get_console().resolve_ticket(ticket_id, approved=True, note=(body.note if body else "")):
        return {"ok": True}
    return JSONResponse({"detail": "ticket not found or already resolved"}, status_code=404)


@router.post("/approvals/{ticket_id}/deny")
def deny_ticket(ticket_id: str, body: ApprovalBody | None = None) -> Any:
    if get_console().resolve_ticket(ticket_id, approved=False, note=(body.note if body else "")):
        return {"ok": True}
    return JSONResponse({"detail": "ticket not found or already resolved"}, status_code=404)

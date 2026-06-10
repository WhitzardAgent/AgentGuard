"""Client-facing API routes: guard decide, policy snapshot, trace, skills."""
from __future__ import annotations

import urllib.error
import urllib.request
from typing import Any

from fastapi import APIRouter, HTTPException

from backend.api.schemas import (
    CheckerConfigUpdateRequest,
    CheckerConfigUpdateResponse,
    GuardDecideRequest,
    GuardDecideResponse,
    SkillRunRequest,
    TraceUploadRequest,
)
from backend.app_state import get_console, get_manager, get_skills
from backend.runtime.manager import RuntimeManager
from backend.runtime.policy.snapshot_builder import snapshot_dict
from shared.utils.json import safe_dumps, safe_loads

router = APIRouter()

# Shared process singletons (console binds an observer to the same manager).
_manager = get_manager()
get_console()
_skills = get_skills()


@router.post("/v1/guard/decide", response_model=GuardDecideResponse)
def guard_decide(req: GuardDecideRequest) -> GuardDecideResponse:
    result = _manager.decide(req.model_dump())
    return GuardDecideResponse(**result)


@router.get("/v1/policy/snapshot")
def policy_snapshot() -> dict:
    snap = snapshot_dict(_manager.policy.store)
    return _manager.plugins.on_policy_snapshot_build(snap, {})


@router.post("/v1/trace/upload")
def trace_upload(req: TraceUploadRequest) -> dict:
    trace = req.model_dump()
    _manager.plugins.on_trace_uploaded(trace, {})
    count = _manager.record_uploaded_trace(trace)
    return {"status": "received", "entries": count}


@router.post("/v1/checkers/config", response_model=CheckerConfigUpdateResponse)
def update_checker_config(req: CheckerConfigUpdateRequest) -> CheckerConfigUpdateResponse:
    try:
        loaded = _manager.update_checker_config(req.config)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    client_config = req.client_config or req.config
    client_updates = [
        _push_client_checker_config(url, client_config, req.timeout_s)
        for url in req.client_config_urls
    ]
    return CheckerConfigUpdateResponse(
        status="ok",
        loaded_checkers=loaded,
        client_updates=client_updates,
    )


@router.post("/v1/skills/run")
def skills_run(req: SkillRunRequest) -> dict:
    return _skills.run(req.model_dump())


def get_manager() -> RuntimeManager:
    return _manager


def _push_client_checker_config(
    url: str,
    config: dict[str, Any],
    timeout_s: float,
) -> dict[str, Any]:
    body = safe_dumps({"config": config}).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=max(timeout_s, 0.1)) as response:
            raw = response.read()
            payload = safe_loads(raw, fallback={})
            return {
                "url": url,
                "status": "ok",
                "status_code": response.status,
                "response": payload,
            }
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        return {
            "url": url,
            "status": "error",
            "status_code": exc.code,
            "error": raw.decode("utf-8", errors="replace"),
        }
    except Exception as exc:
        return {"url": url, "status": "error", "error": str(exc)}

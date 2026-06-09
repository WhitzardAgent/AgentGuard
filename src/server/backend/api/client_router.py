"""Client-facing API routes: guard decide, policy snapshot, trace, skills."""
from __future__ import annotations

from fastapi import APIRouter

from backend.api.schemas import (
    GuardDecideRequest,
    GuardDecideResponse,
    SkillRunRequest,
    TraceUploadRequest,
)
from backend.app_state import get_console, get_manager, get_skills
from backend.runtime.manager import RuntimeManager
from backend.runtime.policy.snapshot_builder import snapshot_dict

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
    _manager.plugins.on_trace_uploaded(req.model_dump(), {})
    return {"status": "received", "entries": len(req.entries)}


@router.post("/v1/skills/run")
def skills_run(req: SkillRunRequest) -> dict:
    return _skills.run(req.model_dump())


def get_manager() -> RuntimeManager:
    return _manager

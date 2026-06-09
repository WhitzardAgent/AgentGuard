"""Health endpoint (enriched for the console runtime page)."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from backend.app_state import get_console

router = APIRouter()


@router.get("/health")
def health() -> dict[str, Any]:
    data = get_console().health()
    data["status"] = "ok"
    data["service"] = "agentguard-server"
    return data

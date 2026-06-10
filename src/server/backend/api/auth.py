"""API-key helpers for backend/frontend management routes."""
from __future__ import annotations

import os
from dataclasses import dataclass

BACKEND_API_PREFIX = "/v1/backend/"
API_KEY_ENV = "AGENTGUARD_API_KEY"


@dataclass(frozen=True)
class ApiKeyCheck:
    ok: bool
    status_code: int = 200
    error: str = ""


def configured_backend_api_key() -> str:
    return os.environ.get(API_KEY_ENV, "").strip()


def is_backend_api_path(path: str) -> bool:
    return path == "/v1/backend" or path.startswith(BACKEND_API_PREFIX)


def check_backend_api_key(path: str, provided_key: str | None) -> ApiKeyCheck:
    expected = configured_backend_api_key()
    if not expected or not is_backend_api_path(path):
        return ApiKeyCheck(ok=True)
    if not provided_key:
        return ApiKeyCheck(ok=False, status_code=401, error="missing backend API key")
    if provided_key != expected:
        return ApiKeyCheck(ok=False, status_code=403, error="invalid backend API key")
    return ApiKeyCheck(ok=True)

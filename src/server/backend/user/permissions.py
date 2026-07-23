"""User permission helpers."""
from __future__ import annotations

import json
from typing import Any

from backend.user.store import User


ADMIN_ROLE = "admin"
SUPER_ADMIN_ROLE = "superadmin"
BUILTIN_SUPER_ADMIN_USERNAME = "AgentGuardAdmin"
ADMIN_ROLES = {ADMIN_ROLE, SUPER_ADMIN_ROLE}


def is_admin_user(user: User | None) -> bool:
    if user is None:
        return False
    profile = _profile_object(getattr(user, "profile_json", None))
    return str(profile.get("role") or "").strip().lower() in ADMIN_ROLES


def is_super_admin_user(user: User | None) -> bool:
    if user is None:
        return False
    profile = _profile_object(getattr(user, "profile_json", None))
    role = str(profile.get("role") or "").strip().lower()
    return user.username == BUILTIN_SUPER_ADMIN_USERNAME and role in ADMIN_ROLES


def _profile_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if not value:
        return {}
    try:
        parsed = json.loads(str(value))
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}

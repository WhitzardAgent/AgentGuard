"""User permission helpers."""
from __future__ import annotations

import json
from typing import Any

from backend.user.store import User


ADMIN_ROLE = "admin"


def is_admin_user(user: User | None) -> bool:
    if user is None:
        return False
    profile = _profile_object(getattr(user, "profile_json", None))
    return str(profile.get("role") or "").strip().lower() == ADMIN_ROLE


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

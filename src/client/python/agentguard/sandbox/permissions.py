"""Permission checks for tool invocations against a profile."""
from __future__ import annotations

import os
from dataclasses import dataclass
from urllib.parse import urlparse

from agentguard.sandbox.profiles import PermissionProfile
from agentguard.tools.capability import (
    CAP_EXTERNAL_SEND,
    CAP_NETWORK,
    CAP_SHELL,
    CAP_WRITE_FILE,
)


@dataclass
class PermissionCheck:
    allowed: bool
    reason: str = ""


def _path_under(path: str, roots: list[str]) -> bool:
    ap = os.path.abspath(path)
    return any(ap == os.path.abspath(r) or ap.startswith(os.path.abspath(r) + os.sep) for r in roots)


def check_permissions(
    profile: PermissionProfile,
    capabilities: list[str],
    arguments: dict,
) -> PermissionCheck:
    """Validate a tool invocation against a permission profile."""
    caps = set(capabilities or [])

    if CAP_SHELL in caps and not profile.allow_subprocess:
        return PermissionCheck(False, "subprocess/shell not permitted")

    if (CAP_NETWORK in caps or CAP_EXTERNAL_SEND in caps) and not profile.allow_network:
        return PermissionCheck(False, "network access not permitted")

    if CAP_WRITE_FILE in caps and not profile.allow_write:
        return PermissionCheck(False, "file write not permitted")

    # File path checks.
    for key in ("path", "file", "filename", "target"):
        val = arguments.get(key)
        if isinstance(val, str) and val:
            if profile.denied_file_roots and _path_under(val, profile.denied_file_roots):
                return PermissionCheck(False, f"path under denied root: {key}")
            if profile.allowed_file_roots and not _path_under(val, profile.allowed_file_roots):
                return PermissionCheck(False, f"path outside allowed roots: {key}")

    # Domain checks.
    for key in ("url", "endpoint", "host", "to"):
        val = arguments.get(key)
        if isinstance(val, str) and ("://" in val or "." in val):
            host = urlparse(val).hostname or val
            if profile.denied_domains and any(d in host for d in profile.denied_domains):
                return PermissionCheck(False, f"denied domain: {host}")
            if profile.allowed_domains and not any(d in host for d in profile.allowed_domains):
                return PermissionCheck(False, f"domain not in allowlist: {host}")

    return PermissionCheck(True, "permitted")

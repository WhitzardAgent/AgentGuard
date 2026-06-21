"""Sandbox subsystem."""
from __future__ import annotations

from agentguard.sandbox.base import BaseSandbox
from agentguard.sandbox.executor import SandboxExecutor, build_sandbox
from agentguard.sandbox.local import LocalPermissionSandbox
from agentguard.sandbox.noop import NoopSandbox
from agentguard.sandbox.permissions import check_permissions
from agentguard.sandbox.profiles import PermissionProfile
from agentguard.sandbox.subprocess import SubprocessSandbox

__all__ = [
    "BaseSandbox",
    "NoopSandbox",
    "LocalPermissionSandbox",
    "SubprocessSandbox",
    "SandboxExecutor",
    "build_sandbox",
    "PermissionProfile",
    "check_permissions",
]

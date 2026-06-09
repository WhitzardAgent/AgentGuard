"""Sandboxed tool execution with permission profiles."""
from __future__ import annotations

import _bootstrap  # noqa: F401

from agentguard.sandbox.executor import SandboxExecutor
from agentguard.sandbox.profiles import PermissionProfile


def write_file(path: str, content: str) -> str:
    return f"wrote {len(content)} bytes to {path}"


def main() -> None:
    allowed = SandboxExecutor(
        "local",
        PermissionProfile(allow_write=True, allowed_file_roots=["/tmp"]),
    )
    r1 = allowed.run(
        write_file,
        {"path": "/tmp/ok.txt", "content": "hi"},
        capabilities=["write_file"],
        tool_name="write_file",
    )
    print("allowed ->", r1.success, r1.value or r1.error)

    denied = SandboxExecutor("local", PermissionProfile.restricted())
    r2 = denied.run(
        write_file,
        {"path": "/etc/passwd", "content": "x"},
        capabilities=["write_file"],
        tool_name="write_file",
    )
    print("denied  ->", r2.success, r2.error)


if __name__ == "__main__":
    main()

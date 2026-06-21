from __future__ import annotations

from agentguard.sandbox.executor import SandboxExecutor
from agentguard.sandbox.profiles import PermissionProfile


def _write(path: str, content: str) -> str:
    return f"wrote {len(content)} to {path}"


def test_local_sandbox_allows_within_profile():
    ex = SandboxExecutor("local", PermissionProfile(allow_write=True, allowed_file_roots=["/tmp"]))
    r = ex.run(_write, {"path": "/tmp/a", "content": "hi"}, capabilities=["write_file"], tool_name="w")
    assert r.success is True
    assert "wrote" in str(r.value)


def test_local_sandbox_denies_write_without_permission():
    ex = SandboxExecutor("local", PermissionProfile.restricted())
    r = ex.run(_write, {"path": "/etc/x", "content": "y"}, capabilities=["write_file"], tool_name="w")
    assert r.success is False
    assert "not permitted" in (r.error or "")


def test_noop_sandbox_runs_directly():
    ex = SandboxExecutor("noop")
    r = ex.run(lambda a, b: a + b, {"a": 2, "b": 3})
    assert r.success is True
    assert r.value == 5

"""Pluggable execution backends for the Harness sandbox.

A backend is responsible for *actually executing* a tool callable once the
capability gate has authorized it. Backends let the same policy boundary be
enforced with progressively stronger isolation:

* :class:`LocalBackend`       — in-process call (fastest, no isolation).
* :class:`SubprocessBackend`  — runs the callable in a separate, resource- and
  environment-restricted Python subprocess (no external deps).
* :class:`OpenSandboxBackend` — offloads shell/code execution to an
  `OpenSandbox <https://github.com/alibaba/OpenSandbox>`_ sandbox (Docker/K8s),
  falling back to ``LocalBackend`` when the SDK or service is unavailable.
"""

from agentguard.harness.sandbox_backends.base import SandboxBackend
from agentguard.harness.sandbox_backends.local import LocalBackend
from agentguard.harness.sandbox_backends.opensandbox import OpenSandboxBackend
from agentguard.harness.sandbox_backends.subprocess_backend import SubprocessBackend

__all__ = [
    "SandboxBackend",
    "LocalBackend",
    "SubprocessBackend",
    "OpenSandboxBackend",
    "build_backend",
]


def build_backend(spec: "str | SandboxBackend | None", **options: object) -> SandboxBackend:
    """Resolve a backend from a name (``"local"``/``"subprocess"``/
    ``"opensandbox"``) or pass through an existing instance."""
    if spec is None or spec == "local":
        return LocalBackend()
    if isinstance(spec, SandboxBackend):
        return spec
    if spec == "subprocess":
        return SubprocessBackend(**options)  # type: ignore[arg-type]
    if spec == "opensandbox":
        return OpenSandboxBackend(**options)  # type: ignore[arg-type]
    raise ValueError(f"unknown sandbox backend: {spec!r}")

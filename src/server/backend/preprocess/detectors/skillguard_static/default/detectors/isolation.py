"""Detect weakened host isolation and sandbox-bypass signals."""

from __future__ import annotations

from ..models import FeatureSet, Signal

ISOLATION_MARKERS = ("docker.sock", "privileged", "--net=host", "network=host", "/var/run/docker.sock")
BYPASS_MARKERS = (
    "disable sandbox",
    "no sandbox",
    "sandbox bypass",
    "host mode",
    "workspace override",
    "dangerously-bypass-approvals-and-sandbox",
    "bypass-approvals-and-sandbox",
    "permission bypass",
    "--full-auto",
)


def scan(features: FeatureSet) -> list[Signal]:
    signals: list[Signal] = []

    for text_file in features.package.files:
        lower = text_file.content.lower()
        if any(marker in lower for marker in ISOLATION_MARKERS):
            # `docker.sock`, `privileged`, and host networking suggest the skill may reach host boundaries.
            signals.append(
                Signal(
                    signal_id="ISO001_ISOLATION_BYPASS",
                    kind="isolation_bypass",
                    severity=4,
                    confidence=0.8,
                    file_path=text_file.path,
                    evidence="host/privileged/docker socket isolation marker observed",
                    tags=("isolation",),
                )
            )
        if any(marker in lower for marker in BYPASS_MARKERS):
            # Platform sandbox/approval bypass language is recorded separately and later mapped to AST06 by the verdicter.
            signals.append(
                Signal(
                    signal_id="ISO002_SANDBOX_BYPASS_LANGUAGE",
                    kind="sandbox_bypass",
                    severity=3,
                    confidence=0.65,
                    file_path=text_file.path,
                    evidence="sandbox bypass or host-mode language observed",
                    tags=("isolation", "sandbox"),
                )
            )

    return signals

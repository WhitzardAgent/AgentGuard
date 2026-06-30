"""Detect agent identity/memory-file modification and host-persistence signals."""

from __future__ import annotations

import re

from ..models import FeatureSet, Signal

WRITE_WORDS = ("write", "append", "modify", "backdoor", "persist", "写入", "追加", "修改", "持久")
HOST_PERSISTENCE_PATTERNS = (
    re.compile(r"\bcron(?:tab)?\b"),
    re.compile(r"\blaunchagent\b"),
    re.compile(r"\bsystemd\b"),
)
SHELL_PROFILE_PATTERN = re.compile(r"(?<!\w)\.(?:bashrc|zshrc)\b")
SHELL_PROFILE_SUSPICIOUS_WORDS = (
    "backdoor",
    "persist",
    "autorun",
    "auto-start",
    "startup",
    "reverse shell",
    "curl",
    "wget",
    ">>",
    "tee -a",
)
NEGATED_BYPASS_CONTEXT_RE = re.compile(
    r"(?i)\b(?:no incentive to bypass|cannot bypass|don't bypass|do not bypass|should not bypass|without bypassing)\b"
)


def scan(features: FeatureSet) -> list[Signal]:
    signals: list[Signal] = []

    for text_file in features.package.files:
        lower = text_file.content.lower()
        if (
            any(name.lower() in lower for name in ("soul.md", "memory.md", "agents.md"))
            and any(word in lower for word in WRITE_WORDS)
            and not NEGATED_BYPASS_CONTEXT_RE.search(lower)
        ):
            # In OpenClaw/agent-style skills, writing `SOUL`/`MEMORY`/`AGENTS` usually implies identity or memory persistence.
            signals.append(
                Signal(
                    signal_id="PER001_IDENTITY_PERSISTENCE",
                    kind="identity_persistence",
                    severity=5,
                    confidence=0.85,
                    file_path=text_file.path,
                    evidence="agent identity/memory file appears with write/modify/persistence language",
                    tags=("persistence", "identity"),
                )
            )
        has_host_persistence = any(pattern.search(lower) for pattern in HOST_PERSISTENCE_PATTERNS)
        has_suspicious_shell_profile = SHELL_PROFILE_PATTERN.search(lower) and any(
            word in lower for word in SHELL_PROFILE_SUSPICIOUS_WORDS
        )
        if has_host_persistence or has_suspicious_shell_profile:
            # Modifications to `cron`/`systemd`/shell profiles are host-persistence indicators that survive across sessions.
            signals.append(
                Signal(
                    signal_id="PER002_HOST_PERSISTENCE",
                    kind="host_persistence",
                    severity=4,
                    confidence=0.7,
                    file_path=text_file.path,
                    evidence="host persistence marker such as cron/profile/systemd observed",
                    tags=("persistence", "host"),
                )
            )

    return signals

"""Vendored rule-based static skill scanner used by AgentGuard."""
from __future__ import annotations

from backend.preprocess.detectors.skillguard_static.scanner import (
    StaticSkillScanResult,
    scan_skill_path,
)

__all__ = ["StaticSkillScanResult", "scan_skill_path"]

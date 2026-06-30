"""AgentGuard wrapper for the vendored SkillGuard default static engine."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from backend.preprocess.detectors.skillguard_static.default.loader import (
    load_skill,
    load_skill_tar_archive,
    load_skill_zip_archive,
)
from backend.preprocess.detectors.skillguard_static.default.main import _scan_package
from backend.preprocess.detectors.skillguard_static.default.models import ScanResult


@dataclass
class StaticSkillScanResult:
    scanner_name: str = "agentguard.skillguard_static"
    status: str = "success"
    finding_count: int = 0
    verdict: str = "benign"
    category: str = "benign"
    confidence: float = 0.0
    raw_output: str = ""
    parsed_summary: dict[str, Any] = field(default_factory=dict)
    logs: str = ""


def scan_skill_path(skill_path: str | Path) -> StaticSkillScanResult:
    """Scan one skill directory/archive with the vendored rule-based engine."""

    path = Path(skill_path)
    try:
        package = _load_package(path)
        result = _scan_package(package)
        return _to_static_result(result)
    except Exception as exc:
        return StaticSkillScanResult(
            status="failed",
            finding_count=1,
            verdict="suspicious",
            category="AST09",
            confidence=0.5,
            raw_output=f"Vendored SkillGuard static scanner failed: {exc}",
            logs=str(exc),
        )


def _load_package(path: Path):
    if path.is_dir():
        return load_skill(path)
    suffixes = [suffix.lower() for suffix in path.suffixes]
    if path.suffix.lower() == ".zip":
        return load_skill_zip_archive(path)
    if (
        suffixes[-1:] == [".tar"]
        or suffixes[-2:] in ([".tar", ".gz"], [".tar", ".bz2"], [".tar", ".xz"])
        or suffixes[-1:] == [".tgz"]
    ):
        return load_skill_tar_archive(path)
    raise ValueError(f"Unsupported static scanner input path: {path}")


def _to_static_result(result: ScanResult) -> StaticSkillScanResult:
    finding_count = len(result.signals)
    if result.verdict in {"suspicious", "malicious"} and finding_count == 0:
        finding_count = 1
    return StaticSkillScanResult(
        status="success",
        finding_count=finding_count,
        verdict=result.verdict,
        category=result.category,
        confidence=result.confidence,
        raw_output=result.evidence,
        parsed_summary={
            "skill_id": result.skill_id,
            "verdict": result.verdict,
            "category": result.category,
            "confidence": result.confidence,
            "evidence_text": result.evidence,
            "signals": [_signal_summary(signal) for signal in result.signals[:50]],
        },
    )


def _signal_summary(signal: object) -> dict[str, Any]:
    return {
        "signal_id": getattr(signal, "signal_id", ""),
        "kind": getattr(signal, "kind", ""),
        "severity": getattr(signal, "severity", 0),
        "confidence": getattr(signal, "confidence", 0.0),
        "file_path": getattr(signal, "file_path", ""),
        "line_number": getattr(signal, "line_number", 0),
        "evidence": getattr(signal, "evidence", ""),
        "tags": list(getattr(signal, "tags", ()) or ()),
    }

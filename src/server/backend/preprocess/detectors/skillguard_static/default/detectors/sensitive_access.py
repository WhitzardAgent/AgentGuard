"""Detect signals related to access to sensitive resources.

This module only answers whether a sensitive resource was accessed or referenced. It does not decide whether the data was exfiltrated; that composition is handled by the network
detector.
"""

from __future__ import annotations

from ..lexicons import PERSONAL_DATA_MARKERS
from ..models import FeatureSet, Signal


def scan(features: FeatureSet) -> list[Signal]:
    signals: list[Signal] = []

    for hit in features.sensitive_paths:
        if hit.kind == "sensitive_file_read":
            # Explicit reads of sensitive files are strong behavioral signals and usually map more directly to AST01 than plain path references.
            signals.append(
                Signal(
                    signal_id="SA001_SENSITIVE_FILE_READ",
                    kind="sensitive_access",
                    severity=5,
                    confidence=0.85,
                    file_path=hit.file_path,
                    evidence=f"sensitive file read pattern `{hit.value}` observed{_line(hit)}",
                    tags=("sensitive_access", "read"),
                    line_number=hit.line_number,
                    snippet=hit.snippet,
                )
            )
            continue
        signals.append(
            # Plain mentions of paths such as `.ssh` or `.env` remain as weak signals so they can combine with other behavior.
            Signal(
                signal_id="SA002_SENSITIVE_PATH_REFERENCE",
                kind="sensitive_reference",
                severity=2,
                confidence=0.45,
                file_path=hit.file_path,
                evidence=f"sensitive resource reference `{hit.value}` ({hit.evidence}){_line(hit)}",
                tags=("sensitive_access", "reference"),
                line_number=hit.line_number,
                snippet=hit.snippet,
            )
        )

    for hit in features.secret_patterns:
        # Secrets were already redacted in the feature layer, so they are safe to include in evidence here.
        signals.append(
            Signal(
                signal_id="SA003_SECRET_LITERAL",
                kind="secret_literal",
                severity=4,
                confidence=0.8,
                file_path=hit.file_path,
                evidence=f"{hit.evidence}: `{hit.value}`{_line(hit)}",
                tags=("secret", "credential"),
                line_number=hit.line_number,
                snippet=hit.snippet,
            )
        )

    for hit in features.identity_files:
        signals.append(
            Signal(
                signal_id="SA004_IDENTITY_FILE",
                kind="identity_file_access",
                severity=4,
                confidence=0.75,
                file_path=hit.file_path,
                evidence=f"mentions agent identity or memory file `{hit.value}`{_line(hit)}",
                tags=("identity", "persistence"),
                line_number=hit.line_number,
                snippet=hit.snippet,
            )
        )

    for text_file in features.package.files:
        lower = text_file.content.lower()
        matched = [marker for marker in PERSONAL_DATA_MARKERS if marker in lower]
        if len(matched) >= 4:
            # Multiple health/personal-data domain terms together suggest the skill may request a broad data-access scope.
            signals.append(
                Signal(
                    signal_id="SA005_PERSONAL_DATA_SCOPE",
                    kind="personal_data_access",
                    severity=3,
                    confidence=0.7,
                    file_path=text_file.path,
                    evidence=f"broad personal/health data domain terms observed: {', '.join(matched[:5])}",
                    tags=("sensitive_access", "personal_data"),
                )
            )

    return signals


def _line(hit) -> str:
    return f" at line {hit.line_number}" if hit.line_number else ""

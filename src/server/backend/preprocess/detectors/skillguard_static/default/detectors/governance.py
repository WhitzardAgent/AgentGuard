"""Detect missing governance fields and prompt/identity-governance override signals."""

from __future__ import annotations

from ..lexicons import COERCIVE_GOVERNANCE_MARKERS, PERSONA_OVERRIDE_MARKERS
from ..models import FeatureSet, Signal


def scan(features: FeatureSet) -> list[Signal]:
    signals: list[Signal] = []
    missing = []
    if not features.manifest_version:
        missing.append("version")
    if not features.manifest_author:
        missing.append("author")
    if not features.has_signature:
        missing.append("signature")
    if not features.has_content_hash:
        missing.append("content_hash")
    if not features.has_scan_status:
        missing.append("scan_status")

    if missing:
        # Missing these fields hurts auditability, but the default severity stays low to avoid large false-positive spikes from empty manifests.
        signals.append(
            Signal(
                signal_id="GOV001_MISSING_GOVERNANCE_FIELDS",
                kind="missing_governance",
                severity=1,
                confidence=0.35,
                file_path="manifest.json",
                evidence=f"manifest missing governance fields: {', '.join(missing)}",
                tags=("governance",),
            )
        )

    if not features.manifest_permissions:
        has_explicit_tools_contract = any(
            text_file.path == "SKILL.md" and "allowed-tools:" in text_file.content.lower()
            for text_file in features.package.files
        )
        if not has_explicit_tools_contract:
            # Missing `permissions` is recorded separately so it can combine with real network/file behavior later.
            signals.append(
                Signal(
                    signal_id="GOV002_MISSING_PERMISSIONS",
                    kind="missing_permissions",
                    severity=2,
                    confidence=0.45,
                    file_path="manifest.json",
                    evidence="manifest does not declare permissions",
                    tags=("governance", "permissions"),
                )
            )

    for text_file in features.package.files:
        lower = text_file.content.lower()
        coercive_hits = [marker for marker in COERCIVE_GOVERNANCE_MARKERS if marker in lower]
        if len(coercive_hits) >= 2 or "mandatory activation" in coercive_hits:
            # Multiple coercive or unbypassable terms together look more like an attempt to override platform governance or user control.
            signals.append(
                Signal(
                    signal_id="GOV003_COERCIVE_GOVERNANCE_OVERRIDE",
                    kind="instruction_override",
                    severity=4,
                    confidence=0.75,
                    file_path=text_file.path,
                    evidence="skill text contains mandatory or non-bypassable governance override language",
                    tags=("governance", "instruction_override"),
                )
            )

        if any(marker in lower for marker in PERSONA_OVERRIDE_MARKERS):
            # Persona/identity-adoption instructions are typically treated as `instruction_override` behavior signals.
            signals.append(
                Signal(
                    signal_id="GOV004_PERSONA_OVERRIDE",
                    kind="instruction_override",
                    severity=4,
                    confidence=0.75,
                    file_path=text_file.path,
                    evidence="skill text instructs role/persona adoption or identity override",
                    tags=("governance", "persona_override"),
                )
            )

    return signals

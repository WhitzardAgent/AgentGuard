"""Detect security-metadata loss signals during cross-platform reuse or migration."""

from __future__ import annotations

from ..models import FeatureSet, Signal

PLATFORM_WORDS = ("openclaw", "claude", "cursor", "codex", "vscode", "gemini")
LOSS_WORDS = ("strip", "dropped", "lost", "without permissions", "without signature")


def scan(features: FeatureSet) -> list[Signal]:
    signals: list[Signal] = []

    if len(features.manifest_platforms) > 1 and (not features.has_signature or not features.manifest_permissions):
        # Multi-platform declarations are normal by themselves; treat them as migration/governance loss only when signature or permission metadata is missing.
        signals.append(
            Signal(
                signal_id="CP001_CROSS_PLATFORM_METADATA_LOSS",
                kind="cross_platform_metadata_loss",
                severity=2,
                confidence=0.55,
                file_path="manifest.json",
                evidence="multi-platform manifest lacks signature or permissions metadata",
                tags=("cross_platform", "metadata"),
            )
        )

    for text_file in features.package.files:
        lower = text_file.content.lower()
        if sum(1 for word in PLATFORM_WORDS if word in lower) >= 2:
            # Mentioning multiple agent platforms in the same text suggests the package may be a migrated or reused variant.
            signals.append(
                Signal(
                    signal_id="CP002_CROSS_PLATFORM_REUSE",
                    kind="cross_platform_reuse",
                    severity=1,
                    confidence=0.35,
                    file_path=text_file.path,
                    evidence="text mentions multiple agent platforms",
                    tags=("cross_platform",),
                )
            )
        has_platform_context = any(word in lower for word in PLATFORM_WORDS)
        if has_platform_context and any(word in lower for word in LOSS_WORDS):
            # Language like "permissions/signatures were lost during porting" is a stronger signal than simple multi-platform mentions.
            signals.append(
                Signal(
                    signal_id="CP003_SECURITY_PROPERTY_LOSS",
                    kind="cross_platform_metadata_loss",
                    severity=3,
                    confidence=0.65,
                    file_path=text_file.path,
                    evidence="text suggests security metadata may be stripped, dropped, or missing during porting",
                    tags=("cross_platform", "metadata_loss"),
                )
            )

    return signals

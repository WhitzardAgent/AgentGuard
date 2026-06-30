"""Detect missing metadata, declaration mismatches, and brand-impersonation signals."""

from __future__ import annotations

from ..models import FeatureSet, Signal

BRAND_NAMES = ("google", "microsoft", "openai", "anthropic", "solana", "youtube", "vercel")
NETWORK_PERMISSIONS = ("network", "network:true", "network:outbound")


def scan(features: FeatureSet) -> list[Signal]:
    signals: list[Signal] = []
    manifest = features.package.manifest
    has_top_level_skill = any(text_file.path == "SKILL.md" for text_file in features.package.files)

    if not manifest:
        # A missing manifest is a weak governance signal by itself and should not normally drive a sample to malicious on its own.
        signals.append(
            Signal(
                signal_id="META001_MISSING_MANIFEST",
                kind="missing_metadata",
                severity=1,
                confidence=0.3,
                file_path="manifest.json",
                evidence="manifest.json missing or invalid",
                tags=("metadata",),
            )
        )

    if not has_top_level_skill:
        # Benchmark inputs may include non-standard platform packages; record structural uncertainty when there is no top-level `SKILL.md`.
        signals.append(
            Signal(
                signal_id="META005_INVALID_PACKAGE_SHAPE",
                kind="package_shape_invalid",
                severity=4,
                confidence=0.8,
                file_path="SKILL.md",
                evidence="top-level SKILL.md is missing; package does not match expected skill shape",
                tags=("metadata", "package_shape"),
            )
        )

    name = features.manifest_name.lower()
    description = features.manifest_description.lower()
    if any(brand in name for brand in BRAND_NAMES) and "verified" not in description:
        # A high-value brand name in `name` without language such as `verified` in the description is treated as a weak impersonation signal.
        signals.append(
            Signal(
                signal_id="META002_BRAND_IMPERSONATION",
                kind="brand_impersonation",
                severity=2,
                confidence=0.45,
                file_path="manifest.json",
                evidence=f"skill name `{features.manifest_name}` contains high-value brand name",
                tags=("metadata", "brand"),
            )
        )

    declared_network = any(item.lower() in NETWORK_PERMISSIONS for item in features.manifest_permissions)
    if manifest and features.network_calls and not declared_network:
        # Declaration mismatches are only checked when a manifest exists; missing manifests are left to `missing_metadata` to avoid double amplification.
        signals.append(
            Signal(
                signal_id="META003_PERMISSION_MISMATCH_NETWORK",
                kind="permission_mismatch",
                severity=3,
                confidence=0.65,
                file_path="manifest.json",
                evidence="network operations observed but manifest does not clearly declare network permission",
                tags=("metadata", "permission_mismatch"),
            )
        )

    if features.manifest_risk_tier.lower() in {"l0", "safe", "low"} and (
        features.command_tokens or features.command_invocations or features.sensitive_paths or features.secret_patterns
    ):
        # Claiming low risk while containing execution or sensitive-access behavior counts as risk understatement.
        signals.append(
            Signal(
                signal_id="META004_RISK_UNDERSTATEMENT",
                kind="risk_understatement",
                severity=3,
                confidence=0.6,
                file_path="manifest.json",
                evidence="low risk tier declared while execution or sensitive-access features exist",
                tags=("metadata", "risk_tier"),
            )
        )

    return signals

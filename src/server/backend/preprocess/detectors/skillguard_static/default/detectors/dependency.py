"""Detect dependency, install-time execution, and version-drift signals."""

from __future__ import annotations

from ..models import FeatureSet, Signal

FLOATING_MARKERS = (">=", "*", "latest", "git+", "http://", "https://")


def scan(features: FeatureSet) -> list[Signal]:
    signals: list[Signal] = []

    for hit in features.dependency_files:
        text_file = next((item for item in features.package.files if item.path == hit.file_path), None)
        content = text_file.content.lower() if text_file else ""
        if any(marker in content for marker in FLOATING_MARKERS) and "--hash=" not in content:
            # Floating versions or remote dependencies without hashes make future builds non-reproducible and indicate supply-chain drift risk.
            signals.append(
                Signal(
                    signal_id="DEP001_FLOATING_DEPENDENCY",
                    kind="floating_dependency",
                    severity=2,
                    confidence=0.5,
                    file_path=hit.file_path,
                    evidence="dependency file appears to use floating or remote versions without hashes",
                    tags=("dependency", "update_drift"),
                )
            )

    for hook in features.install_hooks:
        # `install`/`preinstall`/`postinstall`/`prepare` in `package.json` run automatically during installation.
        signals.append(
            Signal(
                signal_id="DEP002_INSTALL_HOOK",
                kind="install_time_execution",
                severity=4,
                confidence=0.75,
                file_path=hook.file_path,
                evidence=f"package.json defines `{hook.value}` hook: {hook.evidence}",
                tags=("dependency", "install_hook"),
            )
        )

    for hit in features.hidden_code_files:
        # Hidden script files reduce audit visibility and are kept as independent weak-to-medium supply-chain signals.
        signals.append(
            Signal(
                signal_id="DEP003_HIDDEN_CODE_FILE",
                kind="hidden_code_file",
                severity=3,
                confidence=0.65,
                file_path=hit.file_path,
                evidence=f"hidden executable/script file `{hit.value}` is included",
                tags=("dependency", "hidden_file"),
            )
        )

    for url in features.urls:
        if any(dep.file_path == url.file_path for dep in features.dependency_files):
            # URLs inside dependency configuration are closer to the real installation surface than URLs in a README.
            signals.append(
                Signal(
                    signal_id="DEP004_REMOTE_DEPENDENCY",
                    kind="remote_dependency",
                    severity=3,
                    confidence=0.65,
                    file_path=url.file_path,
                    evidence=f"dependency file references remote URL `{url.value}`",
                    tags=("dependency", "remote"),
                )
            )

    return signals

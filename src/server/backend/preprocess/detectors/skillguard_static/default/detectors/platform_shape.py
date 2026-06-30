"""Detect platform-specific package shapes that the scanner cannot fully interpret.

This module improves completion rate and robustness: when an input does not look like a standard Skill but clearly contains Claude
Code, VS Code, or similar platform configuration, it emits uncertainty signals instead of silently classifying the sample as benign.
"""

from __future__ import annotations

import json
from pathlib import PurePosixPath
from typing import Any

from ..models import FeatureSet, Signal, TextFile

CLAUDE_CONFIG_FILES = {".claude/settings.json", ".claude/settings.local.json"}
CLAUDE_HOOK_FILES = {".claude/hooks.json", ".claude/hooks.yaml", ".claude/hooks.yml"}
EXTENSION_ENTRY_FILES = {"extension.js", "extension.ts", "src/extension.js", "src/extension.ts", "out/extension.js"}
SCRIPT_PREFIXES = ("scripts/", "handlers/", "commands/", "src/")
SCRIPT_SUFFIXES = (".py", ".sh", ".js", ".ts", ".mjs", ".cjs")
REFERENCE_PREFIXES = ("references/", "docs/", "examples/", "assets/")
SCRIPT_IGNORE_NAMES = ("example.", ".test.", ".spec.")


def scan(features: FeatureSet) -> list[Signal]:
    signals: list[Signal] = []
    files = {text_file.path: text_file for text_file in features.package.files}
    paths = set(files)

    if features.package.load_errors:
        # The loader already made a best effort; convert read/parse failures here into explainable `scanner_uncertain` signals.
        signals.append(
            Signal(
                signal_id="PLAT001_LOAD_UNCERTAIN",
                kind="scanner_uncertain",
                severity=3,
                confidence=0.8,
                file_path="<loader>",
                evidence="loader could not fully read or parse the package: " + "; ".join(features.package.load_errors[:2]),
                tags=("platform", "uncertain", "loader"),
            )
        )

    if _has_claude_config(paths) and not _has_skill_entry(paths):
        # Claude Code configuration may take effect through settings/hooks, so the behavioral surface is incomplete without a skill entry point.
        signals.append(
            Signal(
                signal_id="PLAT002_CLAUDE_CONFIG_WITHOUT_SKILL_ENTRY",
                kind="platform_uncertain",
                severity=3,
                confidence=0.75,
                file_path=_first_existing(paths, CLAUDE_CONFIG_FILES | CLAUDE_HOOK_FILES) or ".claude/",
                evidence="Claude Code settings/hooks observed without a top-level SKILL.md or skill.json",
                tags=("platform", "claude_code", "unknown_shape"),
            )
        )

    for path in sorted((CLAUDE_CONFIG_FILES | {"skill.json", "package.json"}) & paths):
        data, error = _json_file(files[path])
        if error:
            # Invalid JSON in key platform configuration means the scanner cannot interpret that platform behavior reliably.
            signals.append(_bad_config_signal(path, error))
            continue
        if path == "skill.json" and not _valid_skill_json(data):
            # The presence of `skill.json` alone is not enough; at minimum the scanner should recognize `name` or `description`.
            signals.append(
                Signal(
                    signal_id="PLAT004_SKILL_JSON_INCOMPLETE",
                    kind="platform_uncertain",
                    severity=3,
                    confidence=0.75,
                    file_path=path,
                    evidence="skill.json exists but does not contain basic name/description metadata",
                    tags=("platform", "skill_json", "unknown_shape"),
                )
            )
        if path == "package.json" and _looks_like_vscode_extension(data) and not _has_vscode_entry(data, paths):
            # A VS Code extension without a `main`/extension entry may be truncated or non-standard.
            signals.append(
                Signal(
                    signal_id="PLAT005_VSCODE_EXTENSION_INCOMPLETE",
                    kind="platform_uncertain",
                    severity=3,
                    confidence=0.75,
                    file_path=path,
                    evidence="VS Code extension package lacks a main entry and no extension source entry was loaded",
                    tags=("platform", "vscode", "unknown_shape"),
                )
            )

    if features.package.manifest and not _has_skill_entry(paths) and not _has_script_entry(paths):
        # If a manifest exists but no recognizable entry point exists, treat it as platform-shape uncertainty.
        signals.append(
            Signal(
                signal_id="PLAT006_MANIFEST_WITHOUT_HANDLER",
                kind="platform_uncertain",
                severity=3,
                confidence=0.7,
                file_path="manifest.json",
                evidence="manifest.json exists without SKILL.md, skill.json, or obvious handler/script entry",
                tags=("platform", "manifest", "unknown_shape"),
            )
        )

    if (
        "SKILL.md" in paths
        and _has_script_entry(paths)
        and not features.package.manifest
        and not _has_structured_skill_frontmatter(files.get("SKILL.md"))
        and not _looks_like_documented_helper_skill(features.package.files)
        and not _looks_like_reference_heavy_single_script_skill(features.package.files)
    ):
        # A multi-file package with `SKILL.md` plus script/command directories but no manifest usually has a larger behavioral surface than a single-file tutorial.
        # These packages are often treated as attention/suspicious in the benchmark, so first raise them to AST09 as platform-shape uncertainty.
        signals.append(
            Signal(
                signal_id="PLAT007_MULTIFILE_SKILL_WITHOUT_MANIFEST",
                kind="platform_uncertain",
                severity=3,
                confidence=0.72,
                file_path="SKILL.md",
                evidence="multi-file skill includes executable script/command entries but no manifest metadata was loaded",
                tags=("platform", "multifile_skill", "unknown_shape"),
            )
        )

    return signals


def _has_skill_entry(paths: set[str]) -> bool:
    return "SKILL.md" in paths or "skill.json" in paths


def _has_claude_config(paths: set[str]) -> bool:
    return bool(paths & (CLAUDE_CONFIG_FILES | CLAUDE_HOOK_FILES)) or any(path.startswith(".claude/hooks/") for path in paths)


def _json_file(text_file: TextFile) -> tuple[Any, str]:
    try:
        return json.loads(text_file.content), ""
    except json.JSONDecodeError as exc:
        return None, str(exc)


def _bad_config_signal(path: str, error: str) -> Signal:
    return Signal(
        signal_id="PLAT003_KEY_CONFIG_UNPARSEABLE",
        kind="scanner_uncertain",
        severity=3,
        confidence=0.8,
        file_path=path,
        evidence=f"key platform config is not parseable JSON: {error}",
        tags=("platform", "uncertain", "parse_error"),
    )


def _valid_skill_json(data: Any) -> bool:
    return isinstance(data, dict) and bool(data.get("name") or data.get("description"))


def _looks_like_vscode_extension(data: Any) -> bool:
    if not isinstance(data, dict):
        return False
    engines = data.get("engines")
    return (
        isinstance(engines, dict)
        and "vscode" in engines
        or "activationEvents" in data
        or "contributes" in data
    )


def _has_vscode_entry(package_json: Any, paths: set[str]) -> bool:
    if isinstance(package_json, dict) and isinstance(package_json.get("main"), str):
        return True
    return bool(paths & EXTENSION_ENTRY_FILES)


def _has_script_entry(paths: set[str]) -> bool:
    return bool(_script_candidate_paths(paths))


def _first_existing(paths: set[str], candidates: set[str]) -> str:
    return next((path for path in sorted(candidates) if path in paths), "")


def _has_structured_skill_frontmatter(skill_file: TextFile | None) -> bool:
    if skill_file is None:
        return False
    text = skill_file.content.lstrip()
    if not text.startswith("---"):
        return False
    lines = text.splitlines()
    if len(lines) < 3:
        return False
    header_lines: list[str] = []
    for line in lines[1:80]:
        if line.strip() == "---":
            break
        header_lines.append(line)
    if not header_lines:
        return False
    lower = "\n".join(header_lines).lower()
    if "name:" not in lower or "description:" not in lower:
        return False
    all_markers = ("allowed-tools:", "version:", "license:", "compatibility:", "metadata:")
    non_tool_markers = ("version:", "license:", "compatibility:", "metadata:")
    if sum(1 for marker in non_tool_markers if marker in lower) >= 1:
        return True
    return sum(1 for marker in all_markers if marker in lower) >= 2


def _looks_like_reference_heavy_single_script_skill(files: list[TextFile]) -> bool:
    if not files:
        return False
    paths = [text_file.path for text_file in files]
    script_paths = _script_candidate_paths(paths)
    if len(script_paths) != 1:
        return False
    passive_paths = [path for path in paths if path.startswith(REFERENCE_PREFIXES)]
    if len(passive_paths) < 2:
        return False
    if len(paths) > 12:
        return False
    skill_file = next((text_file for text_file in files if text_file.path == "SKILL.md"), None)
    if skill_file is None:
        return False
    lower = skill_file.content[:1500].lower()
    return any(marker in lower for marker in ("reference", "references", "guide", "documentation", "docs"))


def _looks_like_documented_helper_skill(files: list[TextFile]) -> bool:
    if not files:
        return False
    paths = [text_file.path for text_file in files]
    skill_file = next((text_file for text_file in files if text_file.path == "SKILL.md"), None)
    if skill_file is None:
        return False
    script_paths = _script_candidate_paths(paths)
    if not (1 <= len(script_paths) <= 2):
        return False
    passive_paths = [path for path in paths if path.startswith(REFERENCE_PREFIXES)]
    head = skill_file.content[:1800].lower()
    guide_markers = (
        "guide",
        "guidance",
        "reference",
        "references",
        "documentation",
        "docs",
        "workflow",
        "tutorial",
        "example",
        "examples",
    )
    helper_markers = (
        "analyz",
        "validate",
        "check",
        "search",
        "review",
        "generate",
        "scaffold",
        "format",
        "fetch",
        "translate",
        "sync",
        "confidence",
    )
    script_names = [PurePosixPath(path).name.lower() for path in script_paths]
    if _has_command_pack_markers(head):
        return False
    return (
        sum(1 for marker in guide_markers if marker in head) >= 2
        and (
            len(passive_paths) >= 1
            or all(any(marker in name for marker in helper_markers) for name in script_names)
            or "for reference" in head
            or "implementation is available" in head
        )
        and all(any(marker in name for marker in helper_markers) for name in script_names)
    )


def _script_candidate_paths(paths: list[str] | set[str]) -> list[str]:
    candidates: list[str] = []
    for path in paths:
        lower = path.lower()
        if not lower.endswith(SCRIPT_SUFFIXES):
            continue
        if not (lower.startswith(SCRIPT_PREFIXES) or "/" not in lower):
            continue
        name = PurePosixPath(lower).name
        if any(marker in name for marker in SCRIPT_IGNORE_NAMES):
            continue
        candidates.append(path)
    return sorted(candidates)


def _has_command_pack_markers(head: str) -> bool:
    if any(marker in head for marker in ("command:", "commands:", "gates:", "emoji:")):
        return True
    return "/alert" in head or "/mcp " in head or "/index " in head or "/opportunit" in head

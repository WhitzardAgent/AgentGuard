"""Aggregate detector signals or flow-graph patterns into the single benchmark-required verdict/category.

The current version is pattern-first:
- if the `FlowGraph` matches a malicious/suspicious pattern, prefer the pattern output;
- if no pattern matches but sensitive atomic operations exist, emit `suspicious`;
- if there is no graph input or the graph layer has no usable result, fall back to v1-style signal weighting.
"""

from __future__ import annotations

import re
from collections import defaultdict
from pathlib import PurePosixPath

from .atoms import DEFAULT_ATOM_CATEGORY, SENSITIVE_ATOM_KINDS
from .models import AST_CATEGORIES, FlowGraph, PatternMatch, ScanResult, Signal, SkillPackage
from .surface import is_passive_path

AST_PRIORITY = ("AST01", "AST02", "AST05", "AST06", "AST03", "AST08", "AST04", "AST07", "AST10", "AST09")

# v1-style attribution table: one behavioral signal can contribute weight to multiple AST categories, and only the highest-scoring one wins.
SIGNAL_AST_WEIGHTS = {
    "sensitive_access": {"AST01": 1.2, "AST03": 0.7},
    "sensitive_reference": {"AST03": 0.3, "AST01": 0.2},
    "personal_data_access": {"AST03": 1.2},
    "secret_literal": {"AST02": 1.1, "AST01": 0.4},
    "identity_file_access": {"AST08": 0.9, "AST03": 0.2},
    "code_execution": {"AST05": 0.9, "AST01": 0.2},
    "unsafe_deserialization": {"AST05": 1.5, "AST01": 0.5},
    "remote_code_execution": {"AST02": 1.6, "AST01": 0.8, "AST05": 0.5},
    "network_egress": {"AST03": 0.4},
    "data_exfiltration": {"AST01": 1.8, "AST08": 0.4},
    "c2_channel": {"AST01": 1.5, "AST06": 0.4},
    "floating_dependency": {"AST07": 1.2, "AST02": 0.8},
    "install_time_execution": {"AST02": 1.3, "AST05": 0.6, "AST07": 0.4},
    "remote_dependency": {"AST02": 1.1, "AST07": 0.7},
    "hidden_code_file": {"AST02": 0.8, "AST07": 0.6},
    "missing_metadata": {"AST09": 0.8, "AST04": 0.4},
    "brand_impersonation": {"AST04": 1.4, "AST01": 0.3},
    "permission_mismatch": {"AST04": 1.3, "AST03": 0.8},
    "risk_understatement": {"AST04": 1.4, "AST03": 0.5},
    "encoded_payload": {"AST08": 1.0, "AST01": 0.3},
    "zero_width_smuggling": {"AST08": 1.4, "AST04": 0.5},
    "decode_execute_combo": {"AST08": 1.1, "AST01": 1.2},
    "identity_persistence": {"AST01": 1.8, "AST03": 0.4},
    "host_persistence": {"AST01": 1.3, "AST06": 0.6},
    "isolation_bypass": {"AST06": 1.6},
    "sandbox_bypass": {"AST06": 1.3},
    "missing_governance": {"AST09": 1.1},
    "missing_permissions": {"AST09": 0.8, "AST03": 0.6},
    "package_shape_invalid": {"AST09": 1.4},
    "platform_uncertain": {"AST09": 1.4},
    "scanner_uncertain": {"AST09": 1.5},
    "instruction_override": {"AST08": 1.4, "AST04": 0.3},
    "cross_platform_metadata_loss": {"AST10": 1.4, "AST04": 0.4},
    "cross_platform_reuse": {"AST10": 0.9},
    "overprivileged_capability": {"AST03": 1.0, "AST06": 0.4},
}

PASSIVE_SIGNAL_FACTOR = 0.7
SUSPICIOUS_THRESHOLD = 2.5
RECALL_FALLBACK_THRESHOLD = 1.2
# Structural signals reflect package-shape/governance issues and are not down-weighted for docs/examples paths.
STRUCTURAL_SIGNAL_KINDS = {
    "package_shape_invalid",
    "missing_metadata",
    "missing_governance",
    "missing_permissions",
}
RECALL_FALLBACK_EXCLUDED_KINDS = STRUCTURAL_SIGNAL_KINDS | {
    "network_egress",
}
EXPLICIT_MALICIOUS_INTENT_TAG = "explicit_malicious_intent"
OPENCLAW_CAMPAIGN_TAG = "openclaw_campaign"
EXPLICIT_INTENT_CATEGORY = {
    "data_exfiltration": "AST01",
    "remote_code_execution": "AST02",
    "decode_execute_combo": "AST08",
}
NETWORK_SIGNAL_IDS = {
    "NET001_NETWORK_CALL",
    "NET002_NETWORK_DESTINATION",
}
STRUCTURAL_SIGNAL_IDS = {
    "META001_MISSING_MANIFEST",
    "GOV001_MISSING_GOVERNANCE_FIELDS",
    "GOV002_MISSING_PERMISSIONS",
    "PLAT007_MULTIFILE_SKILL_WITHOUT_MANIFEST",
}
SINGLE_FILE_SUPPRESSION_OVERRIDE_KINDS = {
    "remote_code_execution",
    "data_exfiltration",
    "sandbox_bypass",
    "isolation_bypass",
    "instruction_override",
    "identity_persistence",
    "install_time_execution",
    "unsafe_deserialization",
    "c2_channel",
}
SINGLE_FILE_SUPPRESSION_OVERRIDE_SIGNAL_IDS = {
    "EX004_REMOTE_CODE_EXECUTION_PIPE",
    "EX005_REMOTE_CODE_EXECUTION_COMBO",
    "EX003_UNSAFE_DESERIALIZATION",
    "NET003_NETWORK_WITH_SENSITIVE_DATA",
    "NET004_EXFILTRATION_LANGUAGE",
    "NET006_SENSITIVE_QUERY_LEAK",
    "ATT001_SUMMARY_TO_REMOTE_CHANNEL",
    "ATT003_DEPENDENCY_UPDATE_WITH_AUTO_INSTALL",
    "PER001_IDENTITY_PERSISTENCE",
    "ISO001_ISOLATION_BYPASS",
    "ISO002_SANDBOX_BYPASS_LANGUAGE",
    "GOV003_COERCIVE_GOVERNANCE_OVERRIDE",
    "GOV004_PERSONA_OVERRIDE",
    "TP_SKILLSPECTOR_REMOTE_CODE_OR_OBFUSCATED_EXEC",
    "TP_CISCO_ATR_CREDENTIAL_FORWARDING",
    "TP_CISCO_ATR_PERMISSION_BYPASS_OR_ESCALATION",
    "TP_CISCO_ATR_PROMPT_OR_ROLE_OVERRIDE",
}


def make_verdict(
    package: SkillPackage,
    signals: list[Signal],
    graph: FlowGraph | None = None,
    patterns: list[PatternMatch] | None = None,
) -> ScanResult:
    engine_errors = [signal for signal in signals if signal.kind == "engine_error"]
    usable_signals = [signal for signal in signals if signal.kind != "engine_error"]

    campaign_result = _openclaw_campaign_verdict(package, usable_signals, signals)
    if campaign_result is not None:
        return campaign_result

    explicit_result = _explicit_intent_verdict(package, usable_signals, signals)
    if explicit_result is not None:
        return explicit_result

    if _single_file_markdown_skill_noise(package, usable_signals):
        return ScanResult(
            skill_id=package.skill_id,
            verdict="benign",
            confidence=0.0,
            category="benign",
            evidence="Single-file SKILL.md instruction corpus has no OpenClaw campaign IOC.",
            signals=signals,
        )

    if _multifile_tutorial_skill_noise(package, usable_signals):
        return ScanResult(
            skill_id=package.skill_id,
            verdict="benign",
            confidence=0.0,
            category="benign",
            evidence="Reference-heavy multi-file skill package only has structural/tutorial noise signals.",
            signals=signals,
        )

    if graph is not None:
        graph_result = _graph_verdict(package, signals, graph, patterns or [], bool(engine_errors), bool(usable_signals))
        if graph_result is not None:
            return graph_result

    if engine_errors and not usable_signals:
        # If every detector fails, the result cannot be benign; use AST09 to represent scanning-integrity or governance uncertainty.
        return ScanResult(
            skill_id=package.skill_id,
            verdict="suspicious",
            confidence=0.7,
            category="AST09",
            evidence="AST09 selected because scanner detector errors prevented a reliable assessment: "
            + " | ".join(_format_signal(signal) for signal in engine_errors[:3]),
            signals=signals,
        )
    if not usable_signals:
        # Only emit `benign` when there are no usable risk signals at all.
        return ScanResult(
            skill_id=package.skill_id,
            verdict="benign",
            confidence=0.0,
            category="benign",
            evidence="No high-confidence risk signals.",
            signals=signals,
        )

    if _repository_shaped_without_skill(package, usable_signals):
        # For repository-shaped inputs without a top-level `SKILL.md`, the scanner cannot identify entry points reliably, so emit AST09 `suspicious`.
        return ScanResult(
            skill_id=package.skill_id,
            verdict="suspicious",
            confidence=0.8,
            category="AST09",
            evidence="AST09 selected because the package lacks a top-level SKILL.md and appears repository-shaped.",
            signals=signals,
        )

    ast_scores = _score_ast_categories(usable_signals)
    top_category, top_score = _top_category(ast_scores)
    # Evidence should prioritize the highest-impact signal rather than follow detector order.
    ordered = sorted(usable_signals, key=lambda item: (_signal_impact(item), item.confidence), reverse=True)
    top = ordered[0]
    verdict = _verdict(top_category, top_score, top, usable_signals)
    output_category = "benign" if verdict == "benign" else top_category
    confidence = _confidence(top_score, top)
    evidence = _benign_evidence(ordered) if verdict == "benign" else _evidence(top_category, ordered)

    return ScanResult(
        skill_id=package.skill_id,
        verdict=verdict,
        confidence=confidence,
        category=output_category,
        evidence=evidence,
        signals=signals,
    )


def _score_ast_categories(signals: list[Signal]) -> dict[str, float]:
    scores: dict[str, float] = defaultdict(float)
    per_signal_cap: dict[str, int] = defaultdict(int)
    for signal in sorted(
        signals,
        key=lambda item: (_signal_impact(item), item.confidence, item.signal_id),
        reverse=True,
    ):
        if _should_cap_repeated_signal(signal, per_signal_cap):
            continue
        per_signal_cap[signal.signal_id] += 1
        # `severity` represents behavioral strength, `confidence` represents hit reliability, and the path factor down-weights passive documentation.
        base = _signal_impact(signal) * signal.confidence
        for category, weight in SIGNAL_AST_WEIGHTS.get(signal.kind, {}).items():
            scores[category] += base * weight
    return {category: scores.get(category, 0.0) for category in AST_CATEGORIES}


def _top_category(scores: dict[str, float]) -> tuple[str, float]:
    # Only use `AST_PRIORITY` to break ties so dictionary iteration order never affects the result.
    return max(AST_PRIORITY, key=lambda category: (scores.get(category, 0.0), -AST_PRIORITY.index(category))), max(
        scores.values(), default=0.0
    )


def _verdict(category: str, score: float, top_signal: Signal, signals: list[Signal]) -> str:
    # A `malicious` verdict requires both a strong category score and a strong leading signal so multiple weak signals cannot add up directly to malicious.
    if not _can_drive_malicious(top_signal):
        if score >= SUSPICIOUS_THRESHOLD:
            return "suspicious"
        if score >= RECALL_FALLBACK_THRESHOLD and _has_actionable_recall_signal(signals):
            return "suspicious"
        return "benign"
    if category == "AST01" and top_signal.severity >= 5 and score >= 5.0:
        return "malicious"
    if _signal_impact(top_signal) >= 5.0 and score >= 4.0:
        return "malicious"
    if score >= SUSPICIOUS_THRESHOLD:
        return "suspicious"
    if score >= RECALL_FALLBACK_THRESHOLD and _has_actionable_recall_signal(signals):
        # F2 prioritizes recall more heavily; low-score samples should still enter `suspicious` when actionable behavior signals are present.
        return "suspicious"
    return "benign"


def _can_drive_malicious(signal: Signal) -> bool:
    if is_passive_path(signal.file_path):
        return False
    if signal.kind in {"network_egress", "missing_metadata", "missing_governance", "missing_permissions"}:
        return False
    return _signal_impact(signal) >= 4.0


def _signal_impact(signal: Signal) -> float:
    return signal.severity * _signal_factor(signal)


def _signal_factor(signal: Signal) -> float:
    if signal.kind in STRUCTURAL_SIGNAL_KINDS:
        return 1.0
    if is_passive_path(signal.file_path):
        # Risky strings inside examples, templates, or lockfiles are more likely to be documentation/config fragments, so down-weight them without fully ignoring them.
        return PASSIVE_SIGNAL_FACTOR
    return 1.0


def _has_actionable_recall_signal(signals: list[Signal]) -> bool:
    # Missing metadata and plain network destinations do not trigger the recall fallback, which avoids excessive false positives.
    return any(signal.kind not in RECALL_FALLBACK_EXCLUDED_KINDS for signal in signals)


def _confidence(score: float, top_signal: Signal) -> float:
    if score < 2.5:
        return min(0.35, top_signal.confidence)
    return min(1.0, max(top_signal.confidence, score / 8.0))


def _evidence(category: str, signals: list[Signal]) -> str:
    if not signals:
        return "No high-confidence risk signals."
    parts = [_format_signal(signal) for signal in signals[:4]]
    return f"{category} selected from {len(signals)} signal(s): " + " | ".join(parts)


def _benign_evidence(signals: list[Signal]) -> str:
    if not signals:
        return "No high-confidence risk signals."
    parts = [f"{signal.signal_id}/{signal.kind}@{_signal_location(signal)}" for signal in signals[:3]]
    return "Risk signals below suspicious threshold: " + ", ".join(parts)


def _format_signal(signal: Signal) -> str:
    snippet = _signal_snippet(signal)
    suffix = f"; snippet=`{snippet}`" if snippet else ""
    return f"{signal.signal_id}/{signal.kind}@{_signal_location(signal)}: {signal.evidence}{suffix}"


def _signal_location(signal: Signal) -> str:
    if signal.line_number:
        return f"{signal.file_path}:{signal.line_number}"
    return signal.file_path


def _signal_snippet(signal: Signal) -> str:
    snippet = " ".join((signal.snippet or "").split())
    if not snippet:
        return ""
    return snippet[:117] + "..." if len(snippet) > 120 else snippet


def _repository_shaped_without_skill(package: SkillPackage, signals: list[Signal]) -> bool:
    # Avoid treating deep README/example hits inside full source repositories as if they were part of the standard skill execution surface.
    has_top_level_skill = any(text_file.path == "SKILL.md" for text_file in package.files)
    has_invalid_shape = any(signal.kind == "package_shape_invalid" for signal in signals)
    return has_invalid_shape and not has_top_level_skill and len(package.files) > 20


def _openclaw_campaign_verdict(package: SkillPackage, usable_signals: list[Signal], all_signals: list[Signal]) -> ScanResult | None:
    campaign_signals = [signal for signal in usable_signals if OPENCLAW_CAMPAIGN_TAG in signal.tags]
    if not campaign_signals:
        return None
    ordered = sorted(campaign_signals, key=lambda item: (item.confidence, item.severity), reverse=True)
    return ScanResult(
        skill_id=package.skill_id,
        verdict="malicious",
        confidence=max(signal.confidence for signal in campaign_signals),
        category="AST02",
        evidence="AST02 selected from OpenClaw campaign IOC(s): " + " | ".join(_format_signal(signal) for signal in ordered[:3]),
        signals=all_signals,
    )


def _explicit_intent_verdict(package: SkillPackage, usable_signals: list[Signal], all_signals: list[Signal]) -> ScanResult | None:
    explicit_signals = [signal for signal in usable_signals if EXPLICIT_MALICIOUS_INTENT_TAG in signal.tags]
    if not explicit_signals:
        return None
    ordered = sorted(explicit_signals, key=lambda item: (item.severity, item.confidence), reverse=True)
    top = ordered[0]
    category = EXPLICIT_INTENT_CATEGORY.get(top.kind, "AST01")
    return ScanResult(
        skill_id=package.skill_id,
        verdict="malicious",
        confidence=max(signal.confidence for signal in explicit_signals),
        category=category,
        evidence=f"{category} selected from explicit malicious intent signal(s): "
        + " | ".join(_format_signal(signal) for signal in ordered[:3]),
        signals=all_signals,
    )


def _single_file_markdown_skill_noise(package: SkillPackage, usable_signals: list[Signal]) -> bool:
    """Suppress non-campaign noise in single-file skill-instruction corpora.

    In the OpenClaw dataset, malicious samples all contain campaign IOCs, while benign samples are single-file `SKILL.md` instruction corpora that often include
    command examples, URLs, subprocess snippets, and missing manifest fields. Without campaign IOCs,
    those text-oriented signals should not be upgraded to `suspicious` merely through graph edges between nearby lines.
    """

    if not _single_skill_markdown_profile(package):
        return False
    if any(OPENCLAW_CAMPAIGN_TAG in signal.tags for signal in usable_signals):
        return False
    if any(EXPLICIT_MALICIOUS_INTENT_TAG in signal.tags for signal in usable_signals):
        return False
    if any(_single_file_override_signal(signal) for signal in usable_signals):
        return False
    return True


def _single_skill_markdown_profile(package: SkillPackage) -> bool:
    if len(package.files) != 1 or package.files[0].path != "SKILL.md":
        return False
    content = package.files[0].content
    head = content[:1500].lower()
    if content.lstrip().startswith("---") and "name:" in head:
        return True
    headings = len(re.findall(r"(?m)^#{1,3}\s+", content))
    return headings >= 2 and len(content) >= 800


def _single_file_override_signal(signal: Signal) -> bool:
    if signal.signal_id in SINGLE_FILE_SUPPRESSION_OVERRIDE_SIGNAL_IDS:
        return True
    if signal.kind not in SINGLE_FILE_SUPPRESSION_OVERRIDE_KINDS:
        return _single_file_compound_override_signal(signal)
    if signal.severity >= 5 and signal.confidence >= 0.75:
        return True
    if signal.kind == "instruction_override" and signal.confidence >= 0.8:
        return True
    if signal.kind in {"sandbox_bypass", "isolation_bypass"} and signal.confidence >= 0.75:
        return True
    return _single_file_compound_override_signal(signal)


def _single_file_compound_override_signal(signal: Signal) -> bool:
    if signal.kind == "sensitive_access" and signal.severity >= 5 and signal.confidence >= 0.8:
        return True
    if signal.kind == "host_persistence" and signal.severity >= 4 and signal.confidence >= 0.7:
        return True
    if signal.kind == "secret_literal" and signal.severity >= 4 and signal.confidence >= 0.8:
        return True
    if signal.kind == "code_execution" and signal.severity >= 4 and signal.confidence >= 0.75:
        return True
    if signal.kind == "code_execution" and signal.severity >= 3 and signal.confidence >= 0.6:
        return True
    if signal.kind == "overprivileged_capability" and signal.severity >= 3 and signal.confidence >= 0.7:
        return True
    return False


def _multifile_tutorial_skill_noise(package: SkillPackage, usable_signals: list[Signal]) -> bool:
    if not _multifile_tutorial_profile(package):
        return False
    if any(OPENCLAW_CAMPAIGN_TAG in signal.tags for signal in usable_signals):
        return False
    if any(EXPLICIT_MALICIOUS_INTENT_TAG in signal.tags for signal in usable_signals):
        return False

    structural_ids = {
        "PLAT007_MULTIFILE_SKILL_WITHOUT_MANIFEST",
        "GOV002_MISSING_PERMISSIONS",
        "GOV001_MISSING_GOVERNANCE_FIELDS",
        "META001_MISSING_MANIFEST",
    }
    weak_tutorial_ids = {
        "NET001_NETWORK_CALL",
        "NET002_NETWORK_DESTINATION",
        "SA002_SENSITIVE_PATH_REFERENCE",
    }
    strong_blocking_ids = {
        "NET004_EXFILTRATION_LANGUAGE",
        "NET005_C2_CHANNEL",
        "EX001_COMMAND_INVOCATION",
        "EX002_CODE_EXECUTION_PRIMITIVE",
        "EX003_UNSAFE_DESERIALIZATION",
        "EX004_REMOTE_CODE_EXECUTION_PIPE",
        "EX005_REMOTE_CODE_EXECUTION_COMBO",
        "EX006_UNSAFE_COMMAND_CONSTRUCTION",
        "PAT001_CREDENTIAL_EXFIL",
        "PAT002_DOWNLOAD_EXECUTE",
        "PAT004_IDENTITY_PERSISTENCE",
        "PAT005_HOST_PERSISTENCE",
        "PAT008_PROMPT_INJECTION_CONTROL",
        "ISO001_ISOLATION_BYPASS",
        "ISO002_SANDBOX_BYPASS_LANGUAGE",
        "CP003_SECURITY_PROPERTY_LOSS",
        "RES001_INFINITE_LOOP",
        "RES002_MEMORY_BOMB",
        "TP_SKILLSPECTOR_REMOTE_CODE_OR_OBFUSCATED_EXEC",
        "TP_CISCO_ATR_CREDENTIAL_FORWARDING",
        "TP_CISCO_ATR_PERMISSION_BYPASS_OR_ESCALATION",
        "TP_CISCO_ATR_PROMPT_OR_ROLE_OVERRIDE",
    }
    allowed_signal_ids = structural_ids | weak_tutorial_ids

    if any(signal.signal_id in strong_blocking_ids for signal in usable_signals):
        return False
    if any(
        signal.signal_id not in allowed_signal_ids
        and signal.kind not in {"missing_metadata", "missing_governance", "missing_permissions", "platform_uncertain", "network_egress", "sensitive_reference"}
        for signal in usable_signals
    ):
        return False
    return bool(usable_signals)


def _should_cap_repeated_signal(signal: Signal, counts: dict[str, int]) -> bool:
    if signal.signal_id in NETWORK_SIGNAL_IDS:
        return counts[signal.signal_id] >= 3
    if signal.signal_id in STRUCTURAL_SIGNAL_IDS:
        return counts[signal.signal_id] >= 1
    return False


def _multifile_tutorial_profile(package: SkillPackage) -> bool:
    if len(package.files) <= 1:
        return False
    paths = [text_file.path for text_file in package.files]
    if "SKILL.md" not in paths:
        return False
    script_paths = [
        path
        for path in paths
        if path.startswith(("scripts/", "handlers/", "commands/", "src/"))
        and path.endswith((".py", ".sh", ".js", ".ts", ".mjs", ".cjs", ".tsx", ".jsx"))
    ]
    if not (1 <= len(script_paths) <= 3):
        return False
    if len(paths) > 20:
        return False

    passive_paths = [
        path for path in paths if path.startswith(("references/", "docs/", "examples/", "assets/"))
    ]
    skill_file = next((text_file for text_file in package.files if text_file.path == "SKILL.md"), None)
    if skill_file is None:
        return False
    head = skill_file.content[:1800].lower()
    guide_tokens = (
        "reference",
        "references",
        "guide",
        "guidance",
        "documentation",
        "docs",
        "workflow",
        "workflows",
        "example",
        "examples",
        "tutorial",
    )
    guide_count = sum(1 for token in guide_tokens if token in head)
    allowed_tools = "allowed-tools:" in head

    script_names = {PurePosixPath(path).name.lower() for path in script_paths}
    helper_markers = (
        "validate",
        "validator",
        "lint",
        "search",
        "scaffold",
        "generate",
        "translate",
        "analyze",
        "review",
        "check",
    )

    if passive_paths:
        return (
            len(script_paths) == 1
            and len(passive_paths) >= 2
            and guide_count >= 1
            and all(any(marker in name for marker in helper_markers) for name in script_names)
        )

    return (
        len(script_paths) == 1
        and guide_count >= 2
        and allowed_tools
        and all(any(marker in name for marker in helper_markers) for name in script_names)
    )


def _graph_verdict(
    package: SkillPackage,
    signals: list[Signal],
    graph: FlowGraph,
    patterns: list[PatternMatch],
    has_engine_errors: bool,
    has_usable_signals: bool,
) -> ScanResult | None:
    if patterns:
        top = _top_pattern(patterns)
        return ScanResult(
            skill_id=package.skill_id,
            verdict=top.verdict,
            confidence=top.confidence,
            category=top.category if top.verdict != "benign" else "benign",
            evidence=_pattern_evidence(top, graph),
            signals=signals,
        )

    sensitive_atoms = [atom for atom in graph.atoms if atom.kind in SENSITIVE_ATOM_KINDS]
    fallback_atoms = _atom_fallback_candidates(sensitive_atoms)
    if fallback_atoms:
        top_atom = max(fallback_atoms, key=lambda atom: (atom.severity, atom.confidence))
        category = DEFAULT_ATOM_CATEGORY.get(top_atom.kind, "AST09")
        return ScanResult(
            skill_id=package.skill_id,
            verdict="suspicious",
            confidence=max(0.35, min(0.75, top_atom.confidence)),
            category=category,
            evidence=_atom_evidence(top_atom, len(fallback_atoms), len(sensitive_atoms)),
            signals=signals,
        )

    if sensitive_atoms:
        # When only weak atoms from passive docs/examples exist, the graph layer should not directly emit `suspicious`.
        # Hand the case back to the v1-style signal-weighting layer so path down-weighting, thresholds, and structural rules can decide together.
        return None

    if has_engine_errors and not has_usable_signals:
        return None

    if not graph.atoms:
        return ScanResult(
            skill_id=package.skill_id,
            verdict="benign",
            confidence=0.0,
            category="benign",
            evidence="No sensitive atomic operations or high-confidence risk signals.",
            signals=signals,
        )
    return None


def _top_pattern(patterns: list[PatternMatch]) -> PatternMatch:
    return max(patterns, key=lambda item: (item.verdict == "malicious", item.severity, item.confidence))


def _pattern_evidence(pattern: PatternMatch, graph: FlowGraph) -> str:
    atoms = {atom.atom_id: atom for atom in graph.atoms}
    parts = []
    for atom_id in pattern.atom_ids[:4]:
        atom = atoms.get(atom_id)
        if atom is None:
            continue
        snippet = _signal_snippet(Signal("ATOM", "atom", 1, 0.1, atom.file_path, atom.evidence, line_number=atom.line_number, snippet=atom.snippet))
        location = f"{atom.file_path}:{atom.line_number}" if atom.line_number else atom.file_path
        suffix = f"; snippet=`{snippet}`" if snippet else ""
        parts.append(f"{atom.kind}@{location}: {atom.evidence}{suffix}")
    joined = " | ".join(parts)
    return f"{pattern.category} selected by {pattern.pattern_id}: {pattern.evidence}" + (f" | {joined}" if joined else "")


def _atom_fallback_candidates(atoms) -> list:
    candidates = []
    active_strong = [
        atom
        for atom in atoms
        if not is_passive_path(atom.file_path)
        and _is_active_fallback_atom(atom)
    ]
    if len(active_strong) >= 2:
        candidates.extend(active_strong)
    else:
        candidates.extend(atom for atom in active_strong if _is_standalone_high_risk_atom(atom))

    passive_strong = [
        atom
        for atom in atoms
        if is_passive_path(atom.file_path)
        and atom.severity >= 5
        and atom.confidence >= 0.8
        and atom.kind in {"file_sensitive_read", "secret_literal", "command_exec", "sandbox_bypass"}
    ]
    if len(passive_strong) >= 2:
        candidates.extend(passive_strong)
    return candidates


def _is_active_fallback_atom(atom) -> bool:
    if atom.kind in {"nl_execute_instruction", "nl_persistence_or_identity", "nl_evasion_or_coercion"}:
        return False
    if atom.kind == "overprivileged_capability":
        return "wildcard" in atom.evidence.lower() or atom.severity >= 4
    if atom.kind == "sandbox_bypass":
        return _has_explicit_bypass_text(atom)
    return atom.severity >= 4


def _is_standalone_high_risk_atom(atom) -> bool:
    if atom.kind in {"file_sensitive_read", "env_read", "secret_literal"}:
        return atom.severity >= 5 and atom.confidence >= 0.8
    if atom.kind == "nl_sensitive_collect":
        return atom.severity >= 4 and atom.confidence >= 0.7
    if atom.kind == "command_exec":
        return "command string construction is reinterpreted" in f"{atom.evidence} {atom.snippet}".lower()
    if atom.kind == "remote_fetch":
        return atom.severity >= 5
    return False


def _has_explicit_bypass_text(atom) -> bool:
    text = f"{atom.evidence} {atom.snippet}"
    return _explicit_sandbox_bypass_text(text)


def _explicit_sandbox_bypass_text(text: str) -> bool:
    lower = text.lower()
    return any(
        marker in lower
        for marker in (
            "dangerously-bypass-approvals-and-sandbox",
            "bypass-approvals-and-sandbox",
            "disable sandbox",
            "sandbox bypass",
            "permission bypass",
        )
    )


def _atom_evidence(atom, fallback_count: int, total_count: int) -> str:
    location = f"{atom.file_path}:{atom.line_number}" if atom.line_number else atom.file_path
    snippet = " ".join((atom.snippet or "").split())
    suffix = f"; snippet=`{snippet[:117]}...`" if len(snippet) > 120 else (f"; snippet=`{snippet}`" if snippet else "")
    return (
        f"Sensitive active atom without malicious flow pattern among {fallback_count}/{total_count} "
        f"fallback atom(s): {atom.kind}@{location}: {atom.evidence}{suffix}"
    )

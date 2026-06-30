"""Normalize existing features/signals into sensitive atomic operations.

The first flow-graph refactor does not rewrite every detector. `features.py` continues to own low-level hits, and detectors continue to emit
behavioral `Signal`s. This module folds both into `AtomicOperation` objects for `graph_builder` and `pattern_matcher`.
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

from .models import AtomicOperation, FeatureHit, FeatureSet, OperandRef, Signal
from .nl_intent import extract_llm_nl_atoms, extract_nl_atoms

SIGNAL_KIND_TO_ATOM_KIND = {
    "sensitive_access": "file_sensitive_read",
    "sensitive_reference": "file_sensitive_reference",
    "personal_data_access": "nl_sensitive_collect",
    "secret_literal": "secret_literal",
    "identity_file_access": "identity_file_reference",
    "code_execution": "command_exec",
    "unsafe_command_construction": "command_exec",
    "unsafe_deserialization": "unsafe_deserialization",
    "remote_code_execution": "remote_fetch",
    "network_egress": "network_destination",
    "data_exfiltration": "network_send",
    "c2_channel": "network_send",
    "floating_dependency": "remote_fetch",
    "install_time_execution": "install_hook",
    "remote_dependency": "remote_fetch",
    "hidden_code_file": "hidden_code",
    "encoded_payload": "encoded_blob",
    "zero_width_smuggling": "zero_width",
    "decode_execute_combo": "decode_operation",
    "identity_persistence": "identity_persistence",
    "host_persistence": "host_persistence",
    "isolation_bypass": "sandbox_bypass",
    "sandbox_bypass": "sandbox_bypass",
    "instruction_override": "nl_evasion_or_coercion",
    "overprivileged_capability": "overprivileged_capability",
    "missing_metadata": "missing_metadata",
    "missing_governance": "missing_metadata",
    "missing_permissions": "missing_metadata",
    "package_shape_invalid": "platform_uncertain",
    "platform_uncertain": "platform_uncertain",
    "scanner_uncertain": "platform_uncertain",
    "cross_platform_metadata_loss": "platform_uncertain",
    "cross_platform_reuse": "platform_uncertain",
}

SENSITIVE_ATOM_KINDS = {
    "file_sensitive_read",
    "file_sensitive_reference",
    "env_read",
    "secret_literal",
    "identity_file_reference",
    "command_exec",
    "unsafe_deserialization",
    "remote_fetch",
    "install_hook",
    "hidden_code",
    "encoded_blob",
    "decode_operation",
    "zero_width",
    "host_persistence",
    "identity_persistence",
    "sandbox_bypass",
    "overprivileged_capability",
    "nl_sensitive_collect",
    "nl_execute_instruction",
    "nl_persistence_or_identity",
    "nl_evasion_or_coercion",
}

DEFAULT_ATOM_CATEGORY = {
    "file_sensitive_read": "AST01",
    "file_sensitive_reference": "AST03",
    "env_read": "AST01",
    "secret_literal": "AST02",
    "identity_file_reference": "AST08",
    "command_exec": "AST05",
    "unsafe_deserialization": "AST05",
    "remote_fetch": "AST02",
    "network_send": "AST01",
    "network_destination": "AST03",
    "install_hook": "AST02",
    "hidden_code": "AST02",
    "encoded_blob": "AST08",
    "decode_operation": "AST08",
    "zero_width": "AST08",
    "host_persistence": "AST01",
    "identity_persistence": "AST08",
    "sandbox_bypass": "AST06",
    "overprivileged_capability": "AST03",
    "missing_metadata": "AST09",
    "platform_uncertain": "AST09",
    "nl_sensitive_collect": "AST01",
    "nl_external_send": "AST01",
    "nl_execute_instruction": "AST05",
    "nl_persistence_or_identity": "AST08",
    "nl_evasion_or_coercion": "AST08",
}

_URL_RE = re.compile(r"https?://[^\s)>'\"`]+", re.IGNORECASE)
_PATH_RE = re.compile(
    r"(?:~?/)?(?:\.ssh/[\w.-]+|\.aws/credentials|\.env(?:\.[\w-]+)?|\.docker/config\.json|"
    r"\.kube/config|SOUL\.md|MEMORY\.md|AGENTS\.md|/etc/(?:passwd|shadow|sudoers))",
    re.IGNORECASE,
)
_ENV_RE = re.compile(r"\b(?:os\.environ|os\.getenv|process\.env|printenv|dotenv)\b", re.IGNORECASE)
_COMMAND_RE = re.compile(r"\b(?:curl|wget|bash|sh|zsh|python3?|node|powershell|pwsh|npm|pip|docker|kubectl)\b", re.IGNORECASE)
CODE_SUFFIXES = (".py", ".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx", ".sh", ".bash")


def build_atoms(features: FeatureSet, signals: list[Signal]) -> list[AtomicOperation]:
    """Build a deduplicated atom list from features, signals, and natural-language rules."""

    atoms: list[AtomicOperation] = []
    atoms.extend(_feature_atoms(features))
    atoms.extend(_env_read_atoms(features))
    atoms.extend(_signal_atoms(signals))
    for text_file in features.package.files:
        atoms.extend(extract_nl_atoms(text_file, start_index=len(atoms)))
    atoms.extend(
        extract_llm_nl_atoms(
            llm_config=getattr(features.package, "static_llm_config", None),
            text_views=features.text_views,
            skill_id=features.package.skill_id,
            start_index=len(atoms),
        )
    )
    return _dedupe_atoms(atoms)


def _feature_atoms(features: FeatureSet) -> list[AtomicOperation]:
    atoms: list[AtomicOperation] = []
    for hit in features.sensitive_paths:
        kind = "file_sensitive_read" if hit.kind == "sensitive_file_read" else "file_sensitive_reference"
        atoms.append(_atom_from_hit("ATOM_FEATURE_SENSITIVE", kind, hit, severity=5 if kind.endswith("_read") else 2))
    for hit in features.secret_patterns:
        atoms.append(_atom_from_hit("ATOM_FEATURE_SECRET", "secret_literal", hit, severity=4))
    for hit in features.identity_files:
        atoms.append(_atom_from_hit("ATOM_FEATURE_IDENTITY", "identity_file_reference", hit, severity=4))
    for hit in features.command_invocations:
        if hit.value in {"dangerous", "risky"}:
            atoms.append(_atom_from_hit("ATOM_FEATURE_COMMAND", "command_exec", hit, severity=4 if hit.value == "dangerous" else 3))
    for hit in features.command_tokens:
        if "websocket" not in hit.value.lower():
            atoms.append(_atom_from_hit("ATOM_FEATURE_EXEC_TOKEN", "command_exec", hit, severity=4))
    for hit in features.network_calls:
        atoms.append(_atom_from_hit("ATOM_FEATURE_NETWORK_CALL", "network_send", hit, severity=3))
    for hit in features.urls + features.ips:
        atoms.append(_atom_from_hit("ATOM_FEATURE_NETWORK_DEST", "network_destination", hit, severity=2, confidence=0.35))
    for hit in features.install_hooks:
        atoms.append(_atom_from_hit("ATOM_FEATURE_INSTALL_HOOK", "install_hook", hit, severity=4))
    for hit in features.hidden_code_files:
        atoms.append(_atom_from_hit("ATOM_FEATURE_HIDDEN_CODE", "hidden_code", hit, severity=3))
    for hit in features.encoded_blobs:
        atoms.append(_atom_from_hit("ATOM_FEATURE_ENCODED", "encoded_blob", hit, severity=2, confidence=0.35))
    for hit in features.zero_width:
        atoms.append(_atom_from_hit("ATOM_FEATURE_ZERO_WIDTH", "zero_width", hit, severity=3, confidence=0.6))
    return atoms


def _env_read_atoms(features: FeatureSet) -> list[AtomicOperation]:
    atoms: list[AtomicOperation] = []
    for text_file in features.package.files:
        if not text_file.path.lower().endswith(CODE_SUFFIXES):
            continue
        for line_number, line in enumerate(text_file.content.splitlines(), start=1):
            if not _ENV_RE.search(line):
                continue
            atoms.append(
                AtomicOperation(
                    atom_id=f"ATOM_FEATURE_ENV_{text_file.path}_{line_number}",
                    kind="env_read",
                    file_path=text_file.path,
                    line_number=line_number,
                    severity=4,
                    confidence=0.7,
                    operands=_operands_from_text(line),
                    evidence="environment variable read observed",
                    snippet=line.strip()[:200],
                    tags=("env", "source"),
                )
            )
    return atoms


def _signal_atoms(signals: list[Signal]) -> list[AtomicOperation]:
    atoms = []
    for signal in signals:
        kind = SIGNAL_KIND_TO_ATOM_KIND.get(signal.kind)
        if not kind:
            continue
        atoms.append(
            AtomicOperation(
                atom_id=f"ATOM_SIGNAL_{signal.signal_id}_{len(atoms)}",
                kind=kind,
                file_path=signal.file_path,
                line_number=signal.line_number,
                severity=signal.severity,
                confidence=signal.confidence,
                operands=_operands_from_text(signal.evidence + " " + signal.snippet),
                evidence=signal.evidence,
                snippet=signal.snippet,
                tags=("signal",) + signal.tags,
            )
        )
    return atoms


def _atom_from_hit(
    prefix: str,
    kind: str,
    hit: FeatureHit,
    severity: int,
    confidence: float = 0.75,
) -> AtomicOperation:
    return AtomicOperation(
        atom_id=f"{prefix}_{hit.rule_id}_{hit.file_path}_{hit.line_number}",
        kind=kind,
        file_path=hit.file_path,
        line_number=hit.line_number,
        severity=severity,
        confidence=confidence,
        operands=_operands_from_text(" ".join((hit.value, hit.matched_text, hit.snippet))),
        evidence=hit.evidence,
        snippet=hit.snippet,
        tags=hit.tags,
    )


def _operands_from_text(text: str) -> tuple[OperandRef, ...]:
    operands: list[OperandRef] = []
    for url in _URL_RE.findall(text):
        operands.append(OperandRef("url", url, _normalize_url(url)))
    for path in _PATH_RE.findall(text):
        operands.append(OperandRef("path", path, _normalize_path(path)))
    for env in _ENV_RE.findall(text):
        operands.append(OperandRef("env", env, "env"))
    for command in _COMMAND_RE.findall(text):
        operands.append(OperandRef("command", command, command.lower()))
    return tuple(_dedupe_operands(operands))


def _dedupe_operands(operands: list[OperandRef]) -> list[OperandRef]:
    out = []
    seen = set()
    for operand in operands:
        key = (operand.role, operand.normalized or operand.value.lower())
        if key in seen:
            continue
        out.append(operand)
        seen.add(key)
    return out


def _normalize_url(url: str) -> str:
    try:
        parsed = urlparse(url)
    except ValueError:
        return url.lower()
    if not parsed.netloc:
        return url.lower()
    return f"{parsed.scheme.lower()}://{parsed.netloc.lower()}{parsed.path.rstrip('/')}"


def _normalize_path(path: str) -> str:
    return path.strip("`'\"").replace("\\", "/").lower()


def _dedupe_atoms(atoms: list[AtomicOperation]) -> list[AtomicOperation]:
    out = []
    seen = set()
    for atom in atoms:
        operand_key = tuple((operand.role, operand.normalized or operand.value.lower()) for operand in atom.operands)
        key = (atom.kind, atom.file_path, atom.line_number, atom.snippet, operand_key)
        if key in seen:
            continue
        out.append(atom)
        seen.add(key)
    return [
        AtomicOperation(
            atom_id=f"ATOM{i:04d}_{atom.kind}",
            kind=atom.kind,
            file_path=atom.file_path,
            line_number=atom.line_number,
            severity=atom.severity,
            confidence=atom.confidence,
            operands=atom.operands,
            evidence=atom.evidence,
            snippet=atom.snippet,
            tags=atom.tags,
        )
        for i, atom in enumerate(out, start=1)
    ]

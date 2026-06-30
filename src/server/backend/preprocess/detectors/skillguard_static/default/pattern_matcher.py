"""Match malicious or suspicious behavior patterns on the `FlowGraph`."""

from __future__ import annotations

from dataclasses import dataclass

from .models import AtomicOperation, FlowEdge, FlowGraph, PatternMatch
from .surface import is_active_path, is_passive_path


@dataclass(frozen=True)
class PatternSpec:
    pattern_id: str
    source_kinds: frozenset[str]
    sink_kinds: frozenset[str]
    verdict: str
    category: str
    severity: int
    evidence: str


PATTERNS = (
    PatternSpec(
        "PAT001_CREDENTIAL_EXFIL",
        frozenset({"file_sensitive_read", "env_read", "secret_literal", "nl_sensitive_collect"}),
        frozenset({"network_send", "nl_external_send"}),
        "malicious",
        "AST01",
        5,
        "sensitive data source is connected to network/external send sink",
    ),
    PatternSpec(
        "PAT002_DOWNLOAD_EXECUTE",
        frozenset({"remote_fetch", "network_destination"}),
        frozenset({"command_exec", "install_hook", "nl_execute_instruction"}),
        "malicious",
        "AST02",
        5,
        "remote download/destination is connected to execution or install sink",
    ),
    PatternSpec(
        "PAT003_DECODE_EXECUTE",
        frozenset({"encoded_blob", "decode_operation"}),
        frozenset({"command_exec", "unsafe_deserialization"}),
        "malicious",
        "AST08",
        4,
        "encoded or decoded payload is connected to execution sink",
    ),
    PatternSpec(
        "PAT004_IDENTITY_PERSISTENCE",
        frozenset({"identity_file_reference", "nl_persistence_or_identity"}),
        frozenset({"identity_persistence", "nl_evasion_or_coercion"}),
        "malicious",
        "AST08",
        5,
        "agent identity or memory operation is connected to persistence/evasion behavior",
    ),
    PatternSpec(
        "PAT005_HOST_PERSISTENCE",
        frozenset({"host_persistence"}),
        frozenset({"command_exec", "network_send"}),
        "malicious",
        "AST01",
        4,
        "host persistence marker is connected to command or network behavior",
    ),
    PatternSpec(
        "PAT006_SANDBOX_ESCAPE",
        frozenset({"sandbox_bypass"}),
        frozenset({"command_exec", "overprivileged_capability"}),
        "malicious",
        "AST06",
        4,
        "sandbox bypass is connected to command execution or broad capability",
    ),
    PatternSpec(
        "PAT007_MALICIOUS_INSTALL_HOOK",
        frozenset({"install_hook"}),
        frozenset({"remote_fetch", "network_destination", "command_exec"}),
        "malicious",
        "AST02",
        5,
        "install hook is connected to remote fetch, network destination, or command execution",
    ),
    PatternSpec(
        "PAT008_PROMPT_INJECTION_CONTROL",
        frozenset({"nl_evasion_or_coercion"}),
        frozenset({"nl_execute_instruction", "overprivileged_capability"}),
        "suspicious",
        "AST08",
        4,
        "natural-language evasion/coercion is connected to execution or broad capability",
    ),
)

EDGE_KINDS_FOR_PATTERNS = {
    "near_lines",
    "shared_operand",
    "mentions_artifact",
    "source_to_sink",
    "light_dataflow",
    "light_exec_pipeline",
    "light_code_context",
}

def match_patterns(graph: FlowGraph) -> list[PatternMatch]:
    atoms_by_id = {atom.atom_id: atom for atom in graph.atoms}
    matches: list[PatternMatch] = []
    matches.extend(_match_global_atom_patterns(graph.atoms))
    for edge in graph.edges:
        if edge.kind not in EDGE_KINDS_FOR_PATTERNS:
            continue
        left = atoms_by_id.get(edge.src_atom_id)
        right = atoms_by_id.get(edge.dst_atom_id)
        if left is None or right is None:
            continue
        for pattern in PATTERNS:
            match = _match_pair(pattern, left, right, edge)
            if match:
                matches.append(match)
    return _dedupe_matches(matches)


def _match_global_atom_patterns(atoms: list[AtomicOperation]) -> list[PatternMatch]:
    matches: list[PatternMatch] = []
    matches.extend(_match_direct_install_hook(atoms))
    matches.extend(_match_direct_sandbox_bypass(atoms))
    matches.extend(_match_active_atom_combo(atoms, "PAT011_PRIVILEGED_EXFIL", "AST01", _is_net003_atom, _is_privileged_atom))
    matches.extend(_match_active_atom_combo(atoms, "PAT012_ISOLATION_COMMAND", "AST06", _is_unsafe_command_atom, _is_isolation_atom))
    matches.extend(_match_active_atom_combo(atoms, "PAT013_SANDBOX_PRIVILEGE", "AST06", _is_cisco_sandbox_atom, _is_privileged_atom))
    return matches


def _match_direct_install_hook(atoms: list[AtomicOperation]) -> list[PatternMatch]:
    return [
        PatternMatch(
            pattern_id="PAT009_INSTALL_HOOK_EXECUTION",
            verdict="malicious",
            category="AST02",
            severity=5,
            confidence=min(0.9, atom.confidence + 0.1),
            atom_ids=(atom.atom_id,),
            evidence=f"package install lifecycle hook executes code: {_atom_label(atom)}",
        )
        for atom in atoms
        if atom.kind == "install_hook" and is_active_path(atom.file_path)
    ]


def _match_direct_sandbox_bypass(atoms: list[AtomicOperation]) -> list[PatternMatch]:
    return [
        PatternMatch(
            pattern_id="PAT010_EXPLICIT_SANDBOX_BYPASS",
            verdict="malicious",
            category="AST06",
            severity=5,
            confidence=min(0.9, atom.confidence + 0.15),
            atom_ids=(atom.atom_id,),
            evidence=f"explicit sandbox or approval bypass instruction is present: {_atom_label(atom)}",
        )
        for atom in atoms
        if atom.kind == "sandbox_bypass" and is_active_path(atom.file_path) and _is_explicit_sandbox_bypass_atom(atom)
    ]


def _match_active_atom_combo(
    atoms: list[AtomicOperation],
    pattern_id: str,
    category: str,
    left_predicate,
    right_predicate,
) -> list[PatternMatch]:
    left_atoms = [atom for atom in atoms if is_active_path(atom.file_path) and left_predicate(atom)]
    right_atoms = [atom for atom in atoms if is_active_path(atom.file_path) and right_predicate(atom)]
    matches: list[PatternMatch] = []
    for left in left_atoms:
        for right in right_atoms:
            if left.atom_id == right.atom_id:
                continue
            confidence = min(0.9, max(left.confidence, right.confidence) + 0.05)
            matches.append(
                PatternMatch(
                    pattern_id=pattern_id,
                    verdict="malicious",
                    category=category,
                    severity=5,
                    confidence=confidence,
                    atom_ids=(left.atom_id, right.atom_id),
                    evidence=f"active skill surface combines {_atom_label(left)} with {_atom_label(right)}",
                )
            )
    return matches


def _match_pair(pattern: PatternSpec, left: AtomicOperation, right: AtomicOperation, edge: FlowEdge) -> PatternMatch | None:
    if left.kind in pattern.source_kinds and right.kind in pattern.sink_kinds:
        source, sink = left, right
    elif right.kind in pattern.source_kinds and left.kind in pattern.sink_kinds:
        source, sink = right, left
    else:
        return None
    if not _allows_pair_match(pattern, source, sink, edge):
        return None
    verdict, severity, confidence_cap = _pattern_strength(pattern, source, sink, edge)
    if verdict == "malicious" and not _allows_malicious_pair(pattern, source, sink, edge):
        verdict = "suspicious"
        severity = min(severity, 4)
        confidence_cap = min(confidence_cap, 0.75)
    confidence = min(confidence_cap, max(source.confidence, sink.confidence, edge.confidence) + 0.05)
    return PatternMatch(
        pattern_id=pattern.pattern_id,
        verdict=verdict,
        category=pattern.category,
        severity=severity,
        confidence=confidence,
        atom_ids=(source.atom_id, sink.atom_id),
        evidence=f"{pattern.evidence}: {_atom_label(source)} -> {_atom_label(sink)}",
    )


def _allows_pair_match(pattern: PatternSpec, source: AtomicOperation, sink: AtomicOperation, edge: FlowEdge) -> bool:
    if pattern.pattern_id == "PAT008_PROMPT_INJECTION_CONTROL":
        return _allows_pat008_pair(source, sink, edge)
    if pattern.pattern_id != "PAT002_DOWNLOAD_EXECUTE":
        return True
    if edge.kind in {"light_dataflow", "light_exec_pipeline"}:
        return True
    if source.kind == "remote_fetch" and sink.kind == "command_exec":
        if is_passive_path(source.file_path) or is_passive_path(sink.file_path):
            return False
        return edge.kind in {"source_to_sink", "light_dataflow", "light_exec_pipeline", "shared_operand"}
    if source.kind == "network_destination":
        if is_passive_path(source.file_path) or is_passive_path(sink.file_path):
            return False
        if _looks_like_function_signature(source):
            return False
        if edge.kind not in {"near_lines", "shared_operand", "source_to_sink"}:
            return False
        if _looks_like_reference_network_context(source, sink):
            return False
        if sink.kind == "nl_execute_instruction" and edge.kind != "near_lines":
            return False
    return True


def _allows_pat008_pair(source: AtomicOperation, sink: AtomicOperation, edge: FlowEdge) -> bool:
    source_passive = is_passive_path(source.file_path)
    sink_passive = is_passive_path(sink.file_path)
    if source_passive and sink_passive:
        return False
    if source_passive:
        if sink.kind == "overprivileged_capability":
            return False
        return edge.kind in {"near_lines", "source_to_sink"} and _has_strong_evasion_text(source)
    if sink_passive:
        return False
    if sink.kind == "overprivileged_capability":
        return _has_strong_evasion_text(source)
    return True


def _pattern_strength(
    pattern: PatternSpec,
    source: AtomicOperation,
    sink: AtomicOperation,
    edge: FlowEdge,
) -> tuple[str, int, float]:
    if pattern.pattern_id != "PAT002_DOWNLOAD_EXECUTE":
        return pattern.verdict, pattern.severity, 1.0
    if edge.kind in {"light_dataflow", "light_exec_pipeline"}:
        return pattern.verdict, pattern.severity, 1.0
    if source.kind == "network_destination" or edge.kind in {"mentions_artifact", "light_code_context"}:
        # Ordinary URLs, documentation links, or cross-artifact mentions only indicate that a remote destination and execution context exist,
        # not that a downloaded payload was executed. Keep the recall, but avoid over-upgrading to `malicious`.
        return "suspicious", min(pattern.severity, 4), 0.75
    return pattern.verdict, pattern.severity, 1.0


def _allows_malicious_pair(
    pattern: PatternSpec,
    source: AtomicOperation,
    sink: AtomicOperation,
    edge: FlowEdge,
) -> bool:
    # Source/sink combinations in passive docs/examples are often security tutorials, test templates, or vulnerability examples.
    # Only direct code dataflow, an explicit download-execute pipeline, or an artifact reference that connects to an active script should immediately become `malicious`.
    if edge.kind in {"light_dataflow", "light_exec_pipeline"}:
        return True
    if is_passive_path(source.file_path) or is_passive_path(sink.file_path):
        return False
    if edge.kind == "mentions_artifact" and pattern.pattern_id != "PAT001_CREDENTIAL_EXFIL":
        return False
    if pattern.pattern_id == "PAT001_CREDENTIAL_EXFIL":
        # Natural-language `collect/send` pairs can chain from proximity alone, but a plain `secret_literal + network_send` pair
        # requires a stronger relationship to avoid false positives on security docs and example code.
        if source.kind == "nl_sensitive_collect" or sink.kind == "nl_external_send":
            return edge.kind in {"near_lines", "shared_operand", "source_to_sink", "mentions_artifact"}
        if source.kind == "env_read" and sink.kind == "network_send":
            return edge.kind in {"shared_operand", "source_to_sink"} and _looks_like_credential_forwarding(source, sink)
        return edge.kind in {"shared_operand", "source_to_sink", "light_code_context"}
    if pattern.pattern_id == "PAT002_DOWNLOAD_EXECUTE":
        if source.kind != "remote_fetch":
            return False
        if edge.kind in {"light_dataflow", "light_exec_pipeline"}:
            return True
        if edge.kind != "source_to_sink":
            return False
        return not _looks_like_installer_bootstrap(source, sink)
    return edge.kind in {"shared_operand", "source_to_sink", "light_code_context"}


def _is_net003_atom(atom: AtomicOperation) -> bool:
    return (
        atom.kind == "network_send"
        and atom.severity >= 5
        and "same file combines network operation with sensitive file read or credential material" in atom.evidence.lower()
    )


def _is_privileged_atom(atom: AtomicOperation) -> bool:
    return atom.kind == "overprivileged_capability" and atom.severity >= 3


def _is_unsafe_command_atom(atom: AtomicOperation) -> bool:
    text = f"{atom.evidence} {atom.snippet}".lower()
    return atom.kind == "command_exec" and (
        "command string construction is reinterpreted" in text
        or ("bash -c" in text and "$" in text)
        or ("sh -c" in text and "$" in text)
        or "eval $" in text
    )


def _is_isolation_atom(atom: AtomicOperation) -> bool:
    return atom.kind == "sandbox_bypass" and "host/privileged/docker socket isolation marker" in atom.evidence.lower()


def _is_cisco_sandbox_atom(atom: AtomicOperation) -> bool:
    return atom.kind == "sandbox_bypass" and "permission bypass or privilege escalation language" in atom.evidence.lower()


def _is_explicit_sandbox_bypass_atom(atom: AtomicOperation) -> bool:
    text = f"{atom.evidence} {atom.snippet}".lower()
    return any(
        marker in text
        for marker in (
            "dangerously-bypass-approvals-and-sandbox",
            "bypass-approvals-and-sandbox",
            "disable sandbox",
            "no sandbox",
            "sandbox bypass",
            "ignore approval",
            "permission bypass",
        )
    )


def _atom_label(atom: AtomicOperation) -> str:
    location = f"{atom.file_path}:{atom.line_number}" if atom.line_number else atom.file_path
    return f"{atom.kind}@{location}"


def _looks_like_credential_forwarding(source: AtomicOperation, sink: AtomicOperation) -> bool:
    text = " ".join(
        part.lower()
        for part in (
            source.evidence,
            source.snippet,
            sink.evidence,
            sink.snippet,
        )
        if part
    )
    if any(marker in text for marker in ("authorization", "bearer", "x-api-key", "api_key", "apikey", "auth token")):
        return False
    return any(
        marker in text
        for marker in (
            "send token",
            "forward token",
            "share token",
            "upload token",
            "relay token",
            "transfer token",
            "send secret",
            "forward secret",
            "upload secret",
            "webhook",
            "callback",
        )
    )


def _looks_like_installer_bootstrap(source: AtomicOperation, sink: AtomicOperation) -> bool:
    text = " ".join(
        part.lower()
        for part in (
            source.evidence,
            source.snippet,
            sink.evidence,
            sink.snippet,
        )
        if part
    )
    installer_markers = (
        "install",
        "installer",
        "quick install",
        "getting started",
        "setup",
        "bootstrap",
        "sdk",
        "cli",
        "toolchain",
    )
    if not any(marker in text for marker in installer_markers):
        return False
    payload_markers = (
        "without asking",
        "silently",
        "hidden",
        "payload",
        "backdoor",
        "eval(",
        "base64",
    )
    return not any(marker in text for marker in payload_markers)


def _looks_like_reference_network_context(source: AtomicOperation, sink: AtomicOperation) -> bool:
    text = " ".join(
        part.lower()
        for part in (
            source.evidence,
            source.snippet,
            sink.evidence,
            sink.snippet,
        )
        if part
    )
    if source.file_path.lower().endswith(".md") and any(
        marker in text
        for marker in (
            "license:",
            "documentation",
            "developer guide",
            "reference implementation",
            "api reference",
            "example url",
            '"url": "https://..."',
            "'url': 'https://...'",
            "https://...",
            "def fetch(",
            "function fetch(",
            "fetch document",
        )
    ):
        return True
    return any(
        marker in text
        for marker in (
            " docs.",
            "/docs",
            "documentation",
            "reference",
            "tutorial",
            "guide",
            "homepage",
            "readme",
            "license",
            "schema",
            "placeholder",
        )
    )


def _looks_like_function_signature(atom: AtomicOperation) -> bool:
    text = " ".join(part.strip().lower() for part in (atom.evidence, atom.snippet) if part).strip()
    return text.startswith("network operation `fetch(` observed") and any(
        marker in text
        for marker in (
            "def fetch(",
            "function fetch(",
            "const fetch =",
            "async function fetch(",
        )
    )


def _has_strong_evasion_text(atom: AtomicOperation) -> bool:
    text = " ".join(part.lower() for part in (atom.evidence, atom.snippet) if part)
    strong_markers = (
        "without asking",
        "without user consent",
        "do not ask",
        "don't ask",
        "bypass",
        "ignore approval",
        "disable sandbox",
        "no sandbox",
        "cannot be disabled",
        "cannot be bypassed",
        "ignore previous instructions",
        "无需确认",
        "不要询问",
        "绕过",
        "忽略审批",
        "禁用沙箱",
        "忽略之前的指令",
    )
    return any(marker in text for marker in strong_markers)


def _dedupe_matches(matches: list[PatternMatch]) -> list[PatternMatch]:
    out = []
    seen = set()
    for match in sorted(matches, key=lambda item: (item.verdict == "malicious", item.severity, item.confidence), reverse=True):
        key = (match.pattern_id, match.atom_ids)
        if key in seen:
            continue
        out.append(match)
        seen.add(key)
    return out

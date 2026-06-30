"""Build a lightweight security flow graph.

This is not full taint analysis. It connects atomic operations using only a small set of stable relationships:
same file, nearby lines, shared operands, prompt-mentioned artifacts, and source-to-sink relationships.
"""

from __future__ import annotations

from pathlib import PurePosixPath

from .code_flow import build_code_flow_edges
from .models import AtomicOperation, FlowEdge, FlowGraph, SkillPackage

SOURCE_KINDS = {
    "file_sensitive_read",
    "file_sensitive_reference",
    "env_read",
    "secret_literal",
    "identity_file_reference",
    "nl_sensitive_collect",
    "encoded_blob",
    "decode_operation",
    "host_persistence",
    "identity_persistence",
    "sandbox_bypass",
    "install_hook",
    "remote_fetch",
}

SINK_KINDS = {
    "network_send",
    "nl_external_send",
    "command_exec",
    "unsafe_deserialization",
    "remote_fetch",
    "install_hook",
    "identity_persistence",
    "host_persistence",
    "nl_execute_instruction",
    "nl_evasion_or_coercion",
    "overprivileged_capability",
    "sandbox_bypass",
    "network_destination",
}

NEAR_LINE_DISTANCE = 3
MAX_ATOMS_FOR_PAIRING = 100
SOURCE_TO_SINK_RELATION_KINDS = {
    "near_lines",
    "shared_operand",
    "light_dataflow",
    "light_exec_pipeline",
    "light_code_context",
}


def build_flow_graph(package: SkillPackage, atoms: list[AtomicOperation]) -> FlowGraph:
    """Build a `FlowGraph` for a single skill."""

    bounded_atoms = atoms[:MAX_ATOMS_FOR_PAIRING]
    edges: list[FlowEdge] = []
    edges.extend(_local_edges(bounded_atoms))
    edges.extend(_artifact_mention_edges(package, bounded_atoms))
    edges.extend(build_code_flow_edges(package, bounded_atoms, SOURCE_KINDS, SINK_KINDS))
    edges.extend(_source_to_sink_edges(bounded_atoms, edges))
    return FlowGraph(atoms=atoms, edges=_dedupe_edges(edges))


def _local_edges(atoms: list[AtomicOperation]) -> list[FlowEdge]:
    edges = []
    for file_atoms in _atoms_by_file(atoms).values():
        for index, left in enumerate(file_atoms):
            for right in file_atoms[index + 1 :]:
                edges.append(FlowEdge(left.atom_id, right.atom_id, "same_file", 0.35, "atoms appear in the same file"))
                if left.line_number and right.line_number and abs(left.line_number - right.line_number) <= NEAR_LINE_DISTANCE:
                    edges.append(FlowEdge(left.atom_id, right.atom_id, "near_lines", 0.75, "atoms appear within a small line window"))
                if _shared_operand(left, right):
                    edges.append(FlowEdge(left.atom_id, right.atom_id, "shared_operand", 0.8, "atoms share a normalized operand"))
    return edges


def _atoms_by_file(atoms: list[AtomicOperation]) -> dict[str, list[AtomicOperation]]:
    by_file: dict[str, list[AtomicOperation]] = {}
    for atom in atoms:
        by_file.setdefault(atom.file_path, []).append(atom)
    return by_file


def _source_to_sink_edges(atoms: list[AtomicOperation], existing_edges: list[FlowEdge]) -> list[FlowEdge]:
    relation = {(edge.src_atom_id, edge.dst_atom_id): edge for edge in existing_edges}
    relation.update({(edge.dst_atom_id, edge.src_atom_id): edge for edge in existing_edges})
    sources = [atom for atom in atoms if atom.kind in SOURCE_KINDS]
    sinks = [atom for atom in atoms if atom.kind in SINK_KINDS]
    edges = []
    for src in sources:
        for dst in sinks:
            if src.atom_id == dst.atom_id:
                continue
            rel = relation.get((src.atom_id, dst.atom_id))
            if rel is None:
                continue
            if rel.kind not in SOURCE_TO_SINK_RELATION_KINDS:
                continue
            confidence = min(0.9, max(src.confidence, dst.confidence, rel.confidence))
            edges.append(
                FlowEdge(
                    src.atom_id,
                    dst.atom_id,
                    "source_to_sink",
                    confidence,
                    f"{src.kind} is connected to {dst.kind} via {rel.kind}",
                )
            )
    return edges


def _artifact_mention_edges(package: SkillPackage, atoms: list[AtomicOperation]) -> list[FlowEdge]:
    edges = []
    paths = {text_file.path for text_file in package.files}
    prompt_atoms = [atom for atom in atoms if atom.file_path.lower().endswith((".md", ".yaml", ".yml", ".json"))]
    by_path = {}
    for atom in atoms:
        by_path.setdefault(atom.file_path, []).append(atom)

    for text_file in package.files:
        if not text_file.path.lower().endswith((".md", ".yaml", ".yml", ".json")):
            continue
        mentioned = _mentioned_paths(text_file.content, paths)
        if not mentioned:
            continue
        from_atoms = [atom for atom in prompt_atoms if atom.file_path == text_file.path]
        for mentioned_path in mentioned:
            for src in from_atoms:
                for dst in by_path.get(mentioned_path, []):
                    edges.append(
                        FlowEdge(
                            src.atom_id,
                            dst.atom_id,
                            "mentions_artifact",
                            0.65,
                            f"{text_file.path} mentions {mentioned_path}",
                        )
                    )
    return edges


def _shared_operand(left: AtomicOperation, right: AtomicOperation) -> bool:
    left_values = {(operand.role, operand.normalized or operand.value.lower()) for operand in left.operands}
    right_values = {(operand.role, operand.normalized or operand.value.lower()) for operand in right.operands}
    if left_values & right_values:
        return True
    left_norms = {value for _, value in left_values if value}
    right_norms = {value for _, value in right_values if value}
    return bool(left_norms & right_norms)


def _mentioned_paths(content: str, paths: set[str]) -> set[str]:
    lower = content.lower()
    mentioned = set()
    for path in paths:
        name = PurePosixPath(path).name.lower()
        if not name or name in {"skill.md", "manifest.json", "package.json"}:
            continue
        if path.lower() in lower or name in lower:
            mentioned.add(path)
    return mentioned


def _dedupe_edges(edges: list[FlowEdge]) -> list[FlowEdge]:
    out = []
    seen = set()
    for edge in edges:
        key = (edge.src_atom_id, edge.dst_atom_id, edge.kind)
        if key in seen:
            continue
        out.append(edge)
        seen.add(key)
    return out

"""Lightweight code relationship analysis.

This module replaces external static-analysis tooling with stdlib parsing plus limited text scanning to add code-level relationship edges. It is not full
static analysis; it only covers high-yield same-file patterns: simple Python variable flow, shell download-and-execute pipelines, and shared JS/TS block
context.
"""

from __future__ import annotations

import ast
import re
import shlex
from collections.abc import Collection
from pathlib import PurePosixPath

from .models import AtomicOperation, FlowEdge, SkillPackage, TextFile

CODE_SUFFIXES = {".py", ".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx", ".sh", ".bash"}
PY_SOURCE_NAMES = {"open", "read_text", "read_bytes", "getenv"}
PY_NETWORK_NAMES = {"post", "put", "patch", "request", "urlopen", "create_connection"}
PY_EXEC_NAMES = {"system", "popen", "run", "call", "Popen", "check_output", "check_call", "eval", "exec"}
SHELL_DOWNLOAD_RE = re.compile(r"\b(?:curl|wget)\b[^\n]{0,260}https?://", re.IGNORECASE)
SHELL_EXEC_RE = re.compile(r"(?:\|\s*(?:sudo\s+)?(?:bash|sh|zsh|python3?|node)\b|&&\s*(?:bash|sh|zsh|python3?|node)\b)", re.IGNORECASE)
JS_SOURCE_RE = re.compile(r"\b(?:process\.env|fs\.(?:readFileSync|createReadStream)|fs\.promises\.readFile)\b")
JS_SINK_RE = re.compile(r"\b(?:fetch|axios\.(?:post|put|request)|https?\.request|child_process\.(?:exec|execSync)|eval)\s*\(")
MAX_CODE_BYTES = 2 * 1024 * 1024
JS_BLOCK_LINE_WINDOW = 40


def build_code_flow_edges(
    package: SkillPackage,
    atoms: list[AtomicOperation],
    source_kinds: Collection[str],
    sink_kinds: Collection[str],
) -> list[FlowEdge]:
    """Add lightweight code-semantic edges for existing atoms."""

    atoms_by_file: dict[str, list[AtomicOperation]] = {}
    for atom in atoms:
        atoms_by_file.setdefault(atom.file_path, []).append(atom)

    edges: list[FlowEdge] = []
    for text_file in package.files:
        if text_file.size > MAX_CODE_BYTES:
            continue
        file_atoms = atoms_by_file.get(text_file.path, [])
        if not file_atoms or not _has_source_and_sink(file_atoms, source_kinds, sink_kinds):
            continue
        suffix = PurePosixPath(text_file.path).suffix.lower()
        if suffix == ".py":
            edges.extend(_python_edges(text_file, file_atoms, source_kinds, sink_kinds))
        elif suffix in {".sh", ".bash"}:
            edges.extend(_shell_edges(text_file, file_atoms, source_kinds, sink_kinds))
        elif suffix in {".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx"}:
            edges.extend(_js_edges(text_file, file_atoms, source_kinds, sink_kinds))
    return _dedupe_edges(edges)


def _has_source_and_sink(
    atoms: list[AtomicOperation],
    source_kinds: Collection[str],
    sink_kinds: Collection[str],
) -> bool:
    return any(atom.kind in source_kinds for atom in atoms) and any(atom.kind in sink_kinds for atom in atoms)


def _python_edges(
    text_file: TextFile,
    atoms: list[AtomicOperation],
    source_kinds: Collection[str],
    sink_kinds: Collection[str],
) -> list[FlowEdge]:
    try:
        tree = ast.parse(text_file.content)
    except SyntaxError:
        return []

    source_vars: set[str] = set()
    source_lines: set[int] = set()
    sink_var_lines: set[int] = set()
    context_lines: set[int] = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and _expr_has_source(node.value):
            source_lines.add(getattr(node, "lineno", 0))
            for target in node.targets:
                source_vars.update(_target_names(target))
        elif isinstance(node, ast.AnnAssign) and node.value is not None and _expr_has_source(node.value):
            source_lines.add(getattr(node, "lineno", 0))
            source_vars.update(_target_names(node.target))
        elif isinstance(node, ast.Call):
            line = getattr(node, "lineno", 0)
            if _is_sink_call(node):
                context_lines.add(line)
                if _call_uses_names(node, source_vars):
                    sink_var_lines.add(line)
            if _expr_has_source(node):
                source_lines.add(line)

    edges = []
    if sink_var_lines:
        edges.extend(_connect_atoms(atoms, source_kinds, sink_kinds, "light_dataflow", 0.88, "python source variable reaches sink"))
    elif source_lines and context_lines:
        edges.extend(_connect_near_lines(atoms, source_kinds, sink_kinds, context_lines, "light_code_context", 0.78, "python source and sink share code context"))
    return edges


def _expr_has_source(node: ast.AST) -> bool:
    for child in ast.walk(node):
        if isinstance(child, ast.Call):
            name = _call_name(child)
            if name in PY_SOURCE_NAMES or name.endswith((".read_text", ".read_bytes", ".getenv")):
                return True
        elif isinstance(child, ast.Subscript) and _node_name(child.value).endswith("os.environ"):
            return True
        elif isinstance(child, ast.Attribute) and _node_name(child).endswith("process.env"):
            return True
    return False


def _is_sink_call(node: ast.Call) -> bool:
    name = _call_name(node)
    base = name.rsplit(".", 1)[-1]
    return base in PY_NETWORK_NAMES or base in PY_EXEC_NAMES


def _call_name(node: ast.Call) -> str:
    return _node_name(node.func)


def _node_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = _node_name(node.value)
        return f"{parent}.{node.attr}" if parent else node.attr
    return ""


def _target_names(node: ast.AST) -> set[str]:
    if isinstance(node, ast.Name):
        return {node.id}
    if isinstance(node, (ast.Tuple, ast.List)):
        out = set()
        for item in node.elts:
            out.update(_target_names(item))
        return out
    return set()


def _call_uses_names(node: ast.Call, names: set[str]) -> bool:
    if not names:
        return False
    for child in ast.walk(node):
        if isinstance(child, ast.Name) and child.id in names:
            return True
    return False


def _shell_edges(
    text_file: TextFile,
    atoms: list[AtomicOperation],
    source_kinds: Collection[str],
    sink_kinds: Collection[str],
) -> list[FlowEdge]:
    edges = []
    for line_number, line in enumerate(text_file.content.splitlines(), start=1):
        if not SHELL_DOWNLOAD_RE.search(line) or not SHELL_EXEC_RE.search(line):
            continue
        if _looks_like_shell_command(line):
            edges.extend(_connect_near_lines(atoms, source_kinds, sink_kinds, {line_number}, "light_exec_pipeline", 0.9, "shell download is piped to interpreter"))
    return edges


def _looks_like_shell_command(line: str) -> bool:
    try:
        tokens = shlex.split(line, comments=True, posix=True)
    except ValueError:
        return True
    return bool(tokens and tokens[0] in {"curl", "wget", "sudo", "bash", "sh", "zsh", "python", "python3", "node"})


def _js_edges(
    text_file: TextFile,
    atoms: list[AtomicOperation],
    source_kinds: Collection[str],
    sink_kinds: Collection[str],
) -> list[FlowEdge]:
    edges = []
    lines = text_file.content.splitlines()
    source_lines = {index for index, line in enumerate(lines, start=1) if JS_SOURCE_RE.search(line)}
    sink_lines = {index for index, line in enumerate(lines, start=1) if JS_SINK_RE.search(line)}
    for source_line in source_lines:
        nearby_sinks = {line for line in sink_lines if abs(line - source_line) <= JS_BLOCK_LINE_WINDOW}
        if nearby_sinks:
            edges.extend(_connect_near_lines(atoms, source_kinds, sink_kinds, nearby_sinks | {source_line}, "light_code_context", 0.78, "javascript source and sink share a small code block"))
    return edges


def _connect_atoms(
    atoms: list[AtomicOperation],
    source_kinds: Collection[str],
    sink_kinds: Collection[str],
    kind: str,
    confidence: float,
    evidence: str,
) -> list[FlowEdge]:
    sources = [atom for atom in atoms if atom.kind in source_kinds]
    sinks = [atom for atom in atoms if atom.kind in sink_kinds]
    return [FlowEdge(src.atom_id, dst.atom_id, kind, confidence, evidence) for src in sources for dst in sinks if src.atom_id != dst.atom_id]


def _connect_near_lines(
    atoms: list[AtomicOperation],
    source_kinds: Collection[str],
    sink_kinds: Collection[str],
    lines: set[int],
    kind: str,
    confidence: float,
    evidence: str,
) -> list[FlowEdge]:
    if not lines:
        return []
    sources = [atom for atom in atoms if atom.kind in source_kinds and _near_any_line(atom.line_number, lines)]
    sinks = [atom for atom in atoms if atom.kind in sink_kinds and _near_any_line(atom.line_number, lines)]
    return [FlowEdge(src.atom_id, dst.atom_id, kind, confidence, evidence) for src in sources for dst in sinks if src.atom_id != dst.atom_id]


def _near_any_line(line_number: int, lines: set[int], window: int = 6) -> bool:
    return bool(line_number and any(abs(line_number - line) <= window for line in lines))


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

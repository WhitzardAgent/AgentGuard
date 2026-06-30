"""Internal data contracts for the scanner.

This module only defines structures and carries no rule logic. All detectors, the verdicter, and output code pass data through these
dataclasses so modules do not depend on each other's implementation details.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

AST_CATEGORIES = tuple(f"AST{i:02d}" for i in range(1, 11))
VALID_CATEGORIES = AST_CATEGORIES + ("benign",)
VALID_VERDICTS = ("benign", "suspicious", "malicious")


@dataclass(frozen=True)
class TextFile:
    """A text file loaded by the loader.

    `path` is relative to the skill package so evidence never leaks absolute host paths.
    """

    path: str
    content: str
    size: int


@dataclass(frozen=True)
class TextView:
    """A derived text representation used for bounded normalization/deobfuscation."""

    file_path: str
    content: str
    view_kind: str
    source_line_map: tuple[int, ...]
    derivation: str = ""

    def line_number_for_offset(self, index: int) -> int:
        if index < 0:
            return 1
        line = self.content.count("\n", 0, index)
        if line >= len(self.source_line_map):
            return self.source_line_map[-1] if self.source_line_map else 1
        return self.source_line_map[line]


@dataclass
class SkillPackage:
    """The complete input view of a skill to be scanned.

    `load_errors` do not abort scanning directly; they are converted into uncertainty signals later so malformed samples still
    produce one result row.
    """

    skill_id: str
    root: Path
    manifest: dict[str, Any] = field(default_factory=dict)
    files: list[TextFile] = field(default_factory=list)
    load_errors: list[str] = field(default_factory=list)
    static_llm_config: Any = None


@dataclass(frozen=True)
class FeatureHit:
    """A raw hit from the feature-extraction stage.

    `FeatureHit` preserves location, snippets, and rule IDs as much as possible so detectors can compose signals and
    the final evidence remains explainable.
    """

    rule_id: str
    kind: str
    value: str
    file_path: str
    evidence: str = ""
    tags: tuple[str, ...] = ()
    line_number: int = 0
    matched_text: str = ""
    snippet: str = ""
    view_kind: str = "raw"
    derived_from: str = ""


@dataclass
class FeatureSet:
    """The reusable fact set extracted for one skill.

    `features.py` normalizes text, manifest data, and file listings into this structure; detectors only consume these
    facts instead of rescanning every file.
    """

    package: SkillPackage
    text_views: list[TextView] = field(default_factory=list)
    urls: list[FeatureHit] = field(default_factory=list)
    ips: list[FeatureHit] = field(default_factory=list)
    network_calls: list[FeatureHit] = field(default_factory=list)
    sensitive_paths: list[FeatureHit] = field(default_factory=list)
    secret_patterns: list[FeatureHit] = field(default_factory=list)
    identity_files: list[FeatureHit] = field(default_factory=list)
    command_tokens: list[FeatureHit] = field(default_factory=list)
    command_invocations: list[FeatureHit] = field(default_factory=list)
    encoded_blobs: list[FeatureHit] = field(default_factory=list)
    zero_width: list[FeatureHit] = field(default_factory=list)
    dependency_files: list[FeatureHit] = field(default_factory=list)
    install_hooks: list[FeatureHit] = field(default_factory=list)
    hidden_code_files: list[FeatureHit] = field(default_factory=list)
    manifest_permissions: tuple[str, ...] = ()
    manifest_name: str = ""
    manifest_description: str = ""
    manifest_version: str = ""
    manifest_author: str = ""
    manifest_risk_tier: str = ""
    manifest_platforms: tuple[str, ...] = ()
    has_signature: bool = False
    has_content_hash: bool = False
    has_scan_status: bool = False


@dataclass(frozen=True)
class Signal:
    """A behavioral signal emitted by a detector.

    A `Signal` is still not the final AST category. One signal may contribute weight to multiple AST categories in the verdicter,
    but only one benchmark-required category is emitted in the end.
    """

    signal_id: str
    kind: str
    severity: int
    confidence: float
    file_path: str
    evidence: str
    tags: tuple[str, ...] = ()
    line_number: int = 0
    snippet: str = ""

    def __post_init__(self) -> None:
        # Validate at the data boundary so invalid scores do not leak into aggregation and create hard-to-debug behavior.
        if not 1 <= self.severity <= 5:
            raise ValueError(f"severity must be 1..5: {self.severity}")
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError(f"confidence must be 0.0..1.0: {self.confidence}")


@dataclass(frozen=True)
class OperandRef:
    """A security-relevant object associated with an atomic operation.

    `role` describes the semantic position of the object in the operation, such as path/url/command/payload.
    `normalized` is used for graph edges; equivalent URLs, paths, or variable names are normalized into stable strings when possible.
    """

    role: str
    value: str
    normalized: str = ""


@dataclass(frozen=True)
class AtomicOperation:
    """A sensitive atomic operation inside the flow graph.

    An atom only states that a security-relevant action occurred; it does not imply maliciousness by itself. Maliciousness is decided when
    `pattern_matcher` establishes relationships between atoms.
    """

    atom_id: str
    kind: str
    file_path: str
    line_number: int
    severity: int
    confidence: float
    operands: tuple[OperandRef, ...] = ()
    evidence: str = ""
    snippet: str = ""
    tags: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not 1 <= self.severity <= 5:
            raise ValueError(f"severity must be 1..5: {self.severity}")
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError(f"confidence must be 0.0..1.0: {self.confidence}")


@dataclass(frozen=True)
class FlowEdge:
    """A lightweight relationship edge between atomic operations."""

    src_atom_id: str
    dst_atom_id: str
    kind: str
    confidence: float
    evidence: str = ""

    def __post_init__(self) -> None:
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError(f"confidence must be 0.0..1.0: {self.confidence}")


@dataclass(frozen=True)
class FlowGraph:
    """A lightweight security flow graph for one skill."""

    atoms: list[AtomicOperation] = field(default_factory=list)
    edges: list[FlowEdge] = field(default_factory=list)


@dataclass(frozen=True)
class PatternMatch:
    """A malicious or suspicious behavior pattern matched on the `FlowGraph`."""

    pattern_id: str
    verdict: str
    category: str
    severity: int
    confidence: float
    atom_ids: tuple[str, ...]
    evidence: str

    def __post_init__(self) -> None:
        if self.verdict not in VALID_VERDICTS:
            raise ValueError(f"invalid verdict: {self.verdict}")
        if self.category not in VALID_CATEGORIES:
            raise ValueError(f"invalid category: {self.category}")
        if not 1 <= self.severity <= 5:
            raise ValueError(f"severity must be 1..5: {self.severity}")
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError(f"confidence must be 0.0..1.0: {self.confidence}")


@dataclass(frozen=True)
class ScanResult:
    """The internal representation eventually written to `results.jsonl`."""

    skill_id: str
    verdict: str
    confidence: float
    category: str
    evidence: str
    signals: list[Signal] = field(default_factory=list)

    def __post_init__(self) -> None:
        # The output schema is part of the benchmark interface, so enforce one final validation barrier here.
        if self.verdict not in VALID_VERDICTS:
            raise ValueError(f"invalid verdict: {self.verdict}")
        if self.category not in VALID_CATEGORIES:
            raise ValueError(f"invalid category: {self.category}")
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError(f"confidence must be 0.0..1.0: {self.confidence}")

"""Scanner entry point.

Responsibilities:
- read input/output directories from environment variables;
- scan skills in a streaming manner and write results immediately;
- isolate exceptions from individual detectors or skills to maximize completion rate.
"""

from __future__ import annotations

from .atoms import build_atoms
from .detectors.registry import DETECTORS
from .features import extract_features
from .graph_builder import build_flow_graph
from .loader import input_dir_from_env, iter_skills
from .models import ScanResult, Signal, SkillPackage
from .output import ResultWriter, output_dir_from_env
from .pattern_matcher import match_patterns
from .verdicter import make_verdict


def main() -> int:
    input_dir = input_dir_from_env()
    output_dir = output_dir_from_env()
    with ResultWriter(output_dir) as writer:
        # `iter_skills` is a generator; do not preload the whole benchmark and let malformed large inputs blow up memory.
        for package in iter_skills(input_dir):
            try:
                writer.write(_scan_package(package))
            except Exception as exc:
                writer.write(_fallback_result(package, exc))
    return 0


def _scan_package(package: SkillPackage) -> ScanResult:
    features = extract_features(package)
    signals: list[Signal] = []
    for detector in DETECTORS:
        try:
            signals.extend(detector(features))
        except Exception as exc:
            # Detectors must be isolated from each other: one crashing rule may only reduce confidence for that skill,
            # never make the whole scanning process or that skill's output row disappear.
            signals.append(
                Signal(
                    signal_id="ENGINE_DETECTOR_ERROR",
                    kind="engine_error",
                    severity=1,
                    confidence=0.1,
                    file_path="<engine>",
                    evidence=f"Detector failed and was skipped: {exc}",
                    tags=("engine_error",),
                )
            )
    atoms = build_atoms(features, signals)
    graph = build_flow_graph(package, atoms)
    patterns = match_patterns(graph)
    return make_verdict(package, signals, graph, patterns)


def _fallback_result(package: SkillPackage, exc: Exception) -> ScanResult:
    # This fallback covers feature extraction, aggregation, or other package-level exceptions. Exceptional samples must not silently become benign.
    return ScanResult(
        skill_id=package.skill_id,
        verdict="suspicious",
        confidence=0.5,
        category="AST09",
        evidence=f"AST09 selected because scanner could not complete package analysis: {exc}",
    )


if __name__ == "__main__":
    raise SystemExit(main())

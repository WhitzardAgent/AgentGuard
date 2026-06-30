"""Write the JSONL output required by the benchmark.

The current official sample uses four fields: `skill_id`, `verdict`, `engine_category`, and `evidence_text`.
This module centralizes field trimming and category fallback so internal detector/verdicter fields never leak into submission output.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import TextIO

from .models import AST_CATEGORIES, ScanResult

DEFAULT_OUTPUT_DIR = Path("/output")
OUTPUT_ENV = "SKILLSEC_OUTPUT_DIR"


def output_dir_from_env() -> Path:
    return Path(os.environ.get(OUTPUT_ENV, str(DEFAULT_OUTPUT_DIR)))


def write_results(results: list[ScanResult], output_dir: Path | None = None) -> Path:
    # Keep compatibility with the old batch API; the main flow uses `ResultWriter` for streaming output.
    out_dir = output_dir or output_dir_from_env()
    with ResultWriter(out_dir) as writer:
        for result in results:
            writer.write(result)
        return writer.path


class ResultWriter:
    def __init__(self, output_dir: Path | None = None) -> None:
        self.output_dir = output_dir or output_dir_from_env()
        self.path = self.output_dir / "results.jsonl"
        self._handle: TextIO | None = None

    def __enter__(self) -> ResultWriter:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self._handle = self.path.open("w", encoding="utf-8")
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if self._handle is not None:
            self._handle.close()

    def write(self, result: ScanResult) -> None:
        if self._handle is None:
            raise RuntimeError("ResultWriter must be opened before use")
        self._handle.write(json.dumps(_to_row(result), ensure_ascii=False) + "\n")
        # Flush after every skill to reduce the chance that a mid-run crash loses the entire batch.
        self._handle.flush()


def _to_row(result: ScanResult) -> dict[str, object]:
    return {
        "skill_id": result.skill_id,
        "verdict": result.verdict,
        "engine_category": _engine_category(result),
        "evidence_text": result.evidence,
    }


def _engine_category(result: ScanResult) -> str:
    if result.category in AST_CATEGORIES:
        return result.category.lower()
    # `benign` or any invalid category is normalized to `benign` to keep the schema valid.
    return "benign"

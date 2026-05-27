"""Rule loading utilities: file, directory, raw DSL text."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Iterable

from agentguard.policy.dsl.compiler import CompiledRule, compile_rules

log = logging.getLogger(__name__)


def load_rules(
    source: str | Path | Iterable[str] | None,
    *,
    _is_builtin: bool = False,
) -> list[CompiledRule]:
    if source is None:
        return []
    texts: list[str] = []
    if isinstance(source, (str, Path)):
        texts.extend(_read_source(str(source)))
    else:
        for s in source:
            texts.extend(_read_source(str(s)))
    try:
        return compile_rules(*texts)
    except Exception as e:
        if _is_builtin:
            log.error("failed to load builtin rules: %s", e)
            return []
        raise


def _read_source(s: str) -> list[str]:
    """Accept 'file://path', 'path/to/file_or_dir', or raw DSL text."""
    if s.startswith("file://"):
        s = s[len("file://"):]
    if "\n" in s and "RULE" in s:
        return [s]
    p = Path(s)
    if p.is_dir():
        return [f.read_text(encoding="utf-8") for f in sorted(p.rglob("*.rules"))]
    if p.is_file():
        return [p.read_text(encoding="utf-8")]
    # If the string looks like a file path but the file doesn't exist,
    # raise a clear error instead of silently treating it as DSL text.
    if "/" in s or s.endswith(".rules"):
        raise FileNotFoundError(
            f"Policy file or directory not found: {s!r}\n"
            f"  Current working directory: {Path.cwd()}\n"
            f"  Use an absolute path or ensure the file exists."
        )
    return [s]

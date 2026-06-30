"""Detect obfuscation, invisible-character, and decode-plus-execute signals."""

from __future__ import annotations

import re

from ..models import FeatureSet, Signal

DECODE_EXECUTE_RE = re.compile(
    r"(?:base64\.(?:b64decode|decodebytes)|atob\s*\(|fromhex\s*\()[^\n]{0,160}"
    r"(?:eval\s*\(|exec\s*\(|os\.system\s*\(|subprocess\.)|"
    r"(?:eval\s*\(|exec\s*\(|os\.system\s*\(|subprocess\.)[^\n]{0,160}"
    r"(?:base64\.(?:b64decode|decodebytes)|atob\s*\(|fromhex\s*\()",
    re.IGNORECASE,
)
ARCHIVE_PASSWORD_EXEC_RE = re.compile(
    r"\b(?:zip|unzip|7z|tar|archive|password|passphrase)\b[^\n]{0,180}"
    r"\b(?:bash|sh|zsh|python3?|node|eval|exec|os\.system|subprocess\.)\b",
    re.IGNORECASE,
)
SPECIAL_CHAR_SMUGGLING_RE = re.compile(
    r"(?i)(?:c[\W_]{1,3}u[\W_]{1,3}r[\W_]{1,3}l|"
    r"w[\W_]{1,3}g[\W_]{1,3}e[\W_]{1,3}t|"
    r"b[\W_]{1,3}a[\W_]{1,3}s[\W_]{1,3}h|"
    r"h[\W_]{1,3}t[\W_]{1,3}t[\W_]{1,3}p)",
)


def scan(features: FeatureSet) -> list[Signal]:
    signals: list[Signal] = []
    seen: set[tuple[str, str, int, str]] = set()

    for hit in features.encoded_blobs:
        # Large encoded blobs alone have limited confidence and are mainly meant to combine with execution primitives.
        _append_signal(
            signals,
            seen,
            Signal(
                signal_id="OBF001_ENCODED_BLOB",
                kind="encoded_payload",
                severity=2,
                confidence=0.35 if hit.view_kind == "raw" else 0.55,
                file_path=hit.file_path,
                evidence=f"large encoded-looking string observed ({hit.kind}, view={hit.view_kind}){_line(hit)}",
                tags=("obfuscation", hit.view_kind),
                line_number=hit.line_number,
                snippet=hit.snippet,
            ),
        )

    for hit in features.zero_width:
        # Zero-width characters may be used for prompt smuggling or hiding instructions.
        _append_signal(
            signals,
            seen,
            Signal(
                signal_id="OBF002_ZERO_WIDTH",
                kind="zero_width_smuggling",
                severity=3,
                confidence=0.6,
                file_path=hit.file_path,
                evidence=f"zero-width character observed in text ({hit.value}){_line(hit)}",
                tags=("obfuscation", "smuggling"),
                line_number=hit.line_number,
                snippet=hit.snippet,
            ),
        )

    for view in features.text_views:
        match = DECODE_EXECUTE_RE.search(view.content)
        if match:
            # Nearby `decode`/`base64` plus `eval`/`exec`/`os.system` is stronger than an encoded blob by itself.
            line_number = view.content.count("\n", 0, match.start()) + 1
            _append_signal(
                signals,
                seen,
                Signal(
                    signal_id="OBF003_DECODE_EXECUTE_COMBO",
                    kind="decode_execute_combo",
                    severity=4,
                    confidence=0.8 if view.view_kind == "raw" else 0.88,
                    file_path=view.file_path,
                    evidence=f"decode/base64 language appears near execution primitive at line {line_number} (view={view.view_kind})",
                    tags=("obfuscation", "execution", view.view_kind),
                    line_number=line_number,
                ),
            )
        password_exec = ARCHIVE_PASSWORD_EXEC_RE.search(view.content)
        if password_exec:
            line_number = view.content.count("\n", 0, password_exec.start()) + 1
            _append_signal(
                signals,
                seen,
                Signal(
                    signal_id="OBF004_ARCHIVE_PASSWORD_EXECUTE",
                    kind="decode_execute_combo",
                    severity=4,
                    confidence=0.75,
                    file_path=view.file_path,
                    evidence=f"archive/password language appears near execution primitive at line {line_number} (view={view.view_kind})",
                    tags=("obfuscation", "execution", "archive_password", view.view_kind),
                    line_number=line_number,
                ),
            )
        smuggling = SPECIAL_CHAR_SMUGGLING_RE.search(view.content)
        if smuggling and view.view_kind == "raw":
            line_number = view.content.count("\n", 0, smuggling.start()) + 1
            _append_signal(
                signals,
                seen,
                Signal(
                    signal_id="OBF005_SPECIAL_CHAR_SMUGGLING",
                    kind="zero_width_smuggling",
                    severity=3,
                    confidence=0.65,
                    file_path=view.file_path,
                    evidence=f"fragmented command/network token suggests special-character smuggling at line {line_number}",
                    tags=("obfuscation", "smuggling", "fragmented_token"),
                    line_number=line_number,
                    snippet=smuggling.group(0)[:200],
                ),
            )

    return signals


def _line(hit) -> str:
    return f" at line {hit.line_number}" if hit.line_number else ""


def _append_signal(
    target: list[Signal],
    seen: set[tuple[str, str, int, str]],
    signal: Signal,
) -> None:
    key = (signal.kind, signal.file_path, signal.line_number, signal.evidence)
    if key in seen:
        return
    target.append(signal)
    seen.add(key)

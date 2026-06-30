"""Bounded text normalization and lightweight deobfuscation views."""

from __future__ import annotations

import base64
import binascii
import re
import unicodedata
from collections.abc import Iterable
from urllib.parse import unquote

from .models import SkillPackage, TextFile, TextView

_ZERO_WIDTH_RE = re.compile("[\u200b\u200c\u200d\ufeff]")
_PERCENT_ESCAPE_RE = re.compile(r"%(?:[0-9a-fA-F]{2})")
_BASE64_CANDIDATE_RE = re.compile(r"\b[A-Za-z0-9+/]{80,}={0,2}\b")
_HEX_CANDIDATE_RE = re.compile(r"(?:\\x[0-9a-fA-F]{2}){20,}|(?:0x[0-9a-fA-F]{2},?\s*){20,}")
_SPLIT_TOKEN_RE = re.compile(
    r"(?i)(?:c[\W_]{0,3}u[\W_]{0,3}r[\W_]{0,3}l|"
    r"w[\W_]{0,3}g[\W_]{0,3}e[\W_]{0,3}t|"
    r"b[\W_]{0,3}a[\W_]{0,3}s[\W_]{0,3}h|"
    r"e[\W_]{0,3}v[\W_]{0,3}a[\W_]{0,3}l|"
    r"r[\W_]{0,3}e[\W_]{0,3}q[\W_]{0,3}u[\W_]{0,3}e[\W_]{0,3}s[\W_]{0,3}t[\W_]{0,3}s|"
    r"h[\W_]{0,3}t[\W_]{0,3}t[\W_]{0,3}p[\W_]{0,3}s?)"
)
_JOINER_CHARS = " \t\r\n`'\"()[]{}<>|,:;._-+/\\"
_CONFUSABLES = str.maketrans(
    {
        "／": "/",
        "．": ".",
        "：": ":",
        "－": "-",
        "＿": "_",
        "ａ": "a",
        "ｂ": "b",
        "ｃ": "c",
        "ｅ": "e",
        "ｈ": "h",
        "ｉ": "i",
        "ｌ": "l",
        "ｍ": "m",
        "ｎ": "n",
        "ｏ": "o",
        "ｐ": "p",
        "ｒ": "r",
        "ｓ": "s",
        "ｔ": "t",
        "ｕ": "u",
        "ｖ": "v",
        "ｗ": "w",
        "х": "x",
        "а": "a",
        "е": "e",
        "о": "o",
        "р": "p",
        "с": "c",
        "у": "y",
        "і": "i",
    }
)
_MAX_DERIVED_VIEWS_PER_FILE = 6
_MAX_VIEW_CHARS = 16000


def build_text_views(package: SkillPackage) -> list[TextView]:
    views: list[TextView] = []
    for text_file in package.files:
        views.extend(_views_for_file(text_file))
    return views


def _views_for_file(text_file: TextFile) -> list[TextView]:
    raw = _make_view(text_file, text_file.content[:_MAX_VIEW_CHARS], "raw", "raw")
    views = [raw]
    derived: list[TextView] = []

    normalized = unicodedata.normalize("NFKC", text_file.content).translate(_CONFUSABLES)
    if normalized != text_file.content:
        derived.append(_make_view(text_file, normalized[:_MAX_VIEW_CHARS], "nfkc", "unicode_normalized"))

    zero_width_stripped = _ZERO_WIDTH_RE.sub("", normalized)
    if zero_width_stripped != normalized:
        derived.append(_make_view(text_file, zero_width_stripped[:_MAX_VIEW_CHARS], "zero_width_stripped", "zero_width_removed"))

    if _PERCENT_ESCAPE_RE.search(text_file.content):
        decoded = _safe_percent_decode(text_file.content)
        if decoded and decoded != text_file.content:
            derived.append(_make_view(text_file, decoded[:_MAX_VIEW_CHARS], "percent_decoded", "percent_unquoted"))

    if _HEX_CANDIDATE_RE.search(text_file.content):
        decoded_hex = _decode_hex_blob(text_file.content)
        if decoded_hex:
            derived.append(_make_view(text_file, decoded_hex[:_MAX_VIEW_CHARS], "hex_decoded", "hex_decoded"))

    if _BASE64_CANDIDATE_RE.search(text_file.content):
        decoded_b64 = _decode_base64_blob(text_file.content)
        if decoded_b64:
            derived.append(_make_view(text_file, decoded_b64[:_MAX_VIEW_CHARS], "base64_decoded", "base64_decoded"))

    rejoined = _rejoin_split_tokens(text_file.content)
    if rejoined != text_file.content:
        derived.append(_make_view(text_file, rejoined[:_MAX_VIEW_CHARS], "token_rejoined", "fragment_rejoined"))

    deduped = _dedupe_views(derived)
    views.extend(deduped[:_MAX_DERIVED_VIEWS_PER_FILE])
    return views


def text_view_iter(package: SkillPackage) -> Iterable[TextView]:
    views = build_text_views(package)
    if views:
        return views
    return [_make_view(TextFile(path="", content="", size=0), "", "raw", "raw")]


def _make_view(text_file: TextFile, content: str, view_kind: str, derivation: str) -> TextView:
    line_count = max(1, content.count("\n") + 1)
    return TextView(
        file_path=text_file.path,
        content=content,
        view_kind=view_kind,
        source_line_map=tuple(range(1, line_count + 1)),
        derivation=derivation,
    )


def _dedupe_views(views: list[TextView]) -> list[TextView]:
    out: list[TextView] = []
    seen: set[tuple[str, str]] = set()
    for view in views:
        key = (view.view_kind, view.content)
        if key in seen or not view.content.strip():
            continue
        out.append(view)
        seen.add(key)
    return out


def _safe_percent_decode(text: str) -> str:
    try:
        decoded = unquote(text)
    except Exception:
        return ""
    return decoded if _printable_ratio(decoded) >= 0.85 else ""


def _decode_base64_blob(text: str) -> str:
    candidate = _largest_match(_BASE64_CANDIDATE_RE, text)
    if not candidate:
        return ""
    padded = candidate + "=" * (-len(candidate) % 4)
    try:
        raw = base64.b64decode(padded, validate=False)
    except (binascii.Error, ValueError):
        return ""
    decoded = raw.decode("utf-8", errors="replace")
    return decoded if _printable_ratio(decoded) >= 0.85 else ""


def _decode_hex_blob(text: str) -> str:
    candidate = _largest_match(_HEX_CANDIDATE_RE, text)
    if not candidate:
        return ""
    collapsed = candidate.replace("\\x", "").replace("0x", "")
    collapsed = re.sub(r"[^0-9a-fA-F]", "", collapsed)
    if len(collapsed) < 40 or len(collapsed) % 2 != 0:
        return ""
    try:
        raw = bytes.fromhex(collapsed)
    except ValueError:
        return ""
    decoded = raw.decode("utf-8", errors="replace")
    return decoded if _printable_ratio(decoded) >= 0.85 else ""


def _rejoin_split_tokens(text: str) -> str:
    def replace(match: re.Match[str]) -> str:
        token = match.group(0)
        compact = "".join(ch for ch in token if ch not in _JOINER_CHARS)
        return compact if len(compact) >= 4 else token

    return _SPLIT_TOKEN_RE.sub(replace, text)


def _largest_match(pattern: re.Pattern[str], text: str) -> str:
    best = ""
    for match in pattern.finditer(text):
        value = match.group(0)
        if len(value) > len(best):
            best = value
    return best


def _printable_ratio(text: str) -> float:
    if not text:
        return 0.0
    printable = sum(1 for ch in text if ch.isprintable() or ch in "\r\n\t")
    return printable / len(text)

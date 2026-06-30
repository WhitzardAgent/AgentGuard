"""Detect explicit malicious-behavior combinations in single-file instructional text.

These rules target natural language and Markdown code blocks without relying on OpenClaw campaign IOCs. They only emit high-confidence signals when a single line or
a short window contains a strong source/sink/action combination, which avoids misclassifying ordinary tutorial commands.

"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ..context_filters import should_suppress_match
from ..models import FeatureSet, Signal, TextFile


@dataclass(frozen=True)
class ExplicitIntentRule:
    rule_id: str
    kind: str
    category_hint: str
    severity: int
    confidence: float
    description: str
    pattern: re.Pattern


EXPLICIT_INTENT_RULES = (
    ExplicitIntentRule(
        "EMI001_BASE64_DECODE_EXEC",
        "decode_execute_combo",
        "AST08",
        5,
        0.92,
        "base64 decode output is piped into a shell or interpreter",
        re.compile(r"\bbase64\s+-[dD]\b[^\n]{0,160}\\?\|\s*(?:bash|sh|zsh|python3?|node)\b", re.IGNORECASE),
    ),
    ExplicitIntentRule(
        "EMI002_CREDENTIAL_EXFIL_FORWARD",
        "data_exfiltration",
        "AST01",
        5,
        0.9,
        "sensitive credential collection is connected to external send/upload language",
        re.compile(
            r"\b(?:steal|exfiltrat\w*|harvest|dump|collect|extract|read|copy)\b"
            r"[^\n]{0,220}\b(?:credentials?|secrets?|tokens?|api[_ -]?keys?|passwords?|cookies?|wallets?|"
            r"private[_ -]?keys?|ssh[_ -]?keys?|\.ssh|\.env|login data|keychain)\b"
            r"[^\n]{0,260}\b(?:send|upload|post|transmit|forward|webhook|https?://)\b",
            re.IGNORECASE,
        ),
    ),
    ExplicitIntentRule(
        "EMI003_CREDENTIAL_EXFIL_REVERSE",
        "data_exfiltration",
        "AST01",
        5,
        0.9,
        "external send/upload language is connected to sensitive credential material",
        re.compile(
            r"\b(?:send|upload|post|transmit|forward)\b"
            r"[^\n]{0,220}\b(?:credentials?|secrets?|tokens?|api[_ -]?keys?|passwords?|cookies?|wallets?|"
            r"private[_ -]?keys?|ssh[_ -]?keys?|\.ssh|\.env|login data|keychain)\b"
            r"[^\n]{0,220}\b(?:to|into|via|https?://|webhook)\b",
            re.IGNORECASE,
        ),
    ),
    ExplicitIntentRule(
        "EMI004_PASSWORD_ARCHIVE_EXECUTE",
        "remote_code_execution",
        "AST02",
        5,
        0.9,
        "downloaded archive/binary with password is followed by run/execute instruction",
        re.compile(
            r"\b(?:download|fetch)\b[^\n]{0,260}\b(?:zip|archive|dmg|pkg|exe|msi)\b"
            r"[^\n]{0,260}\bpass(?:word)?\b[^\n]{0,260}\b(?:run|execute|launch|open)\b",
            re.IGNORECASE,
        ),
    ),
)

MAX_SIGNALS = 6
NEGATIVE_INTENT_RE = re.compile(
    r"\b(?:do not|don't|never|avoid|forbid|forbidden|must not|should not|without collecting|without sending)\b|"
    r"(?:不要|禁止|避免|不得|不能)",
    re.IGNORECASE,
)


def scan(features: FeatureSet) -> list[Signal]:
    signals: list[Signal] = []
    for text_file in features.package.files:
        signals.extend(_scan_file(text_file, remaining=MAX_SIGNALS - len(signals)))
        if len(signals) >= MAX_SIGNALS:
            break
    return signals


def _scan_file(text_file: TextFile, remaining: int) -> list[Signal]:
    if remaining <= 0 or not _supported_text_file(text_file.path):
        return []
    signals: list[Signal] = []
    for rule in EXPLICIT_INTENT_RULES:
        for match in rule.pattern.finditer(text_file.content):
            line_number = _line_number(text_file.content, match.start())
            snippet = _line_snippet(text_file.content, line_number)
            if _should_skip_match(text_file.path, snippet, match.group(0)):
                continue
            signals.append(
                Signal(
                    signal_id=rule.rule_id,
                    kind=rule.kind,
                    severity=rule.severity,
                    confidence=rule.confidence,
                    file_path=text_file.path,
                    evidence=f"explicit malicious intent matched: {rule.description}",
                    tags=("explicit_malicious_intent", rule.category_hint.lower()),
                    line_number=line_number,
                    snippet=snippet,
                )
            )
            if len(signals) >= remaining:
                return signals
    return signals


def _supported_text_file(path: str) -> bool:
    lower = path.lower()
    return lower.endswith((".md", ".txt", ".yaml", ".yml", ".json", ".toml", ".sh", ".py", ".js", ".ts"))


def _should_skip_match(file_path: str, snippet: str, matched_text: str) -> bool:
    if NEGATIVE_INTENT_RE.search(snippet):
        return True
    return should_suppress_match(snippet, file_path, matched_text)


def _line_number(content: str, index: int) -> int:
    return content.count("\n", 0, index) + 1


def _line_snippet(content: str, line_number: int) -> str:
    lines = content.splitlines()
    if not 1 <= line_number <= len(lines):
        return ""
    return lines[line_number - 1].strip()[:200]

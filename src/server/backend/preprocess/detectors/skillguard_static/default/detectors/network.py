"""Detect network access, data-exfiltration, and C2-style communication signals."""

from __future__ import annotations

import re

from ..lexicons import EXFIL_LANGUAGE, SENSITIVE_CONTEXT_WORDS
from ..models import FeatureSet, Signal

SENSITIVE_QUERY_RE = re.compile(
    r"(?i)[?&](?:"
    r"pw|pass|password|passwd|pwd|passcode|passphrase|"
    r"token|auth[_-]?token|access[_-]?token|refresh[_-]?token|id[_-]?token|jwt|"
    r"api[_-]?key|apikey|key|secret|credential|creds|cookie|session|private[_-]?key|"
    r"mima|password_cn|contrasena|contrase%C3%B1a"
    r")="
)
FILE_UPLOAD_MARKERS = ("files=", "multipart/form-data", "form-data")
LOCAL_DESTINATION_RE = re.compile(r"(?i)\b(?:localhost|127\.0\.0\.1|0\.0\.0\.0|::1)\b")
INCIDENT_RESPONSE_CONTEXT_RE = re.compile(
    r"(?i)\b(?:incident|forensic|forensics|triage|containment|eradication|recovery|investigation|ioc|indicator(?:s)? of compromise|playbook|timeline)\b"
)
DETECTION_VERBS_RE = re.compile(
    r"(?i)\b(?:detect|identify|determine|analyze|investigate|review|monitor|classify|document|preserve|block)\b"
)
BENIGN_WEBSOCKET_CONTEXT_RE = re.compile(
    r"(?i)\b(?:real[- ]?time|realtime|live update|activity feed|notification feed|timeline|calendar|chat|streaming|sse|server-sent events)\b"
)


def scan(features: FeatureSet) -> list[Signal]:
    signals: list[Signal] = []

    for hit in features.network_calls:
        # Real network calls are stronger than plain URLs, but still do not equal exfiltration without sensitive context.
        signals.append(
            Signal(
                signal_id="NET001_NETWORK_CALL",
                kind="network_egress",
                severity=3,
                confidence=0.65,
                file_path=hit.file_path,
                evidence=f"network operation `{hit.value}` observed{_line(hit)}",
                tags=("network", "call"),
                line_number=hit.line_number,
                snippet=hit.snippet,
            )
        )

    for hit in features.urls + features.ips:
        # URLs/IPs are kept as weak network-destination signals so they can combine with execution or exfiltration language.
        signals.append(
            Signal(
                signal_id="NET002_NETWORK_DESTINATION",
                kind="network_egress",
                severity=2,
                confidence=0.35,
                file_path=hit.file_path,
                evidence=f"network destination `{hit.value}` observed{_line(hit)}",
                tags=("network",),
                line_number=hit.line_number,
                snippet=hit.snippet,
            )
        )

    sensitive_files = {
        hit.file_path
        for hit in features.sensitive_paths
        if hit.kind == "sensitive_file_read"
    }
    secret_files = {hit.file_path for hit in features.secret_patterns}
    network_files = {hit.file_path for hit in features.network_calls}
    for file_path in sorted(network_files & (sensitive_files | secret_files)):
        # Network operations plus sensitive reads/credentials in the same file form the clearest exfiltration combination.
        signals.append(
            Signal(
                signal_id="NET003_NETWORK_WITH_SENSITIVE_DATA",
                kind="data_exfiltration",
                severity=5,
                confidence=0.9,
                file_path=file_path,
                evidence="same file combines network operation with sensitive file read or credential material",
                tags=("network", "exfiltration"),
            )
        )

    for url in features.urls:
        if SENSITIVE_QUERY_RE.search(url.value):
            # Query strings that directly carry `token`/`password`/`key` style fields are treated as exfiltration.
            signals.append(
                Signal(
                    signal_id="NET006_SENSITIVE_QUERY_LEAK",
                    kind="data_exfiltration",
                    severity=5,
                    confidence=0.9,
                    file_path=url.file_path,
                    evidence=f"external URL contains sensitive-looking query parameter `{url.value}`{_line(url)}",
                    tags=("network", "exfiltration", "url_query"),
                    line_number=url.line_number,
                    snippet=url.snippet,
                )
            )

    for text_file in features.package.files:
        lower = text_file.content.lower()
        has_network = _has_external_network(features, text_file.path)
        if (
            has_network
            and _has_exfil_language(lower)
            and _has_sensitive_context(features, text_file.path, lower)
            and not _looks_like_incident_response_analysis(lower)
        ):
            # Natural-language exfiltration intent still requires both an external destination and sensitive context to avoid false positives on ordinary API docs.
            signals.append(
                Signal(
                    signal_id="NET004_EXFILTRATION_LANGUAGE",
                    kind="data_exfiltration",
                    severity=5,
                    confidence=0.8,
                    file_path=text_file.path,
                    evidence="file combines network operation/destination with exfiltration language",
                    tags=("network", "exfiltration"),
                )
            )
        if any(hit.file_path == text_file.path for hit in features.network_calls) and _has_file_upload_shape(lower):
            # `multipart`/`files=` combined with `requests.post` and similar calls is a common file-upload exfiltration pattern.
            signals.append(
                Signal(
                    signal_id="NET007_FILE_UPLOAD_EGRESS",
                    kind="data_exfiltration",
                    severity=5,
                    confidence=0.75,
                    file_path=text_file.path,
                    evidence="file combines network operation with multipart/file upload shape",
                    tags=("network", "exfiltration", "file_upload"),
                )
            )
        if (
            ("websocket" in lower or "command and control" in lower or "c2 server" in lower)
            and not _looks_like_incident_response_analysis(lower)
            and not _looks_like_benign_realtime_context(lower)
        ):
            # C2/WebSocket control channels are kept as independent network-risk signals.
            signals.append(
                Signal(
                    signal_id="NET005_C2_CHANNEL",
                    kind="c2_channel",
                    severity=4,
                    confidence=0.75,
                    file_path=text_file.path,
                    evidence="C2/WebSocket-style control channel language observed",
                    tags=("network", "c2"),
                )
            )

    return signals


def _has_exfil_language(lower: str) -> bool:
    return any(word in lower for word in EXFIL_LANGUAGE)


def _has_sensitive_context(features: FeatureSet, file_path: str, lower: str) -> bool:
    if any(hit.file_path == file_path for hit in features.sensitive_paths + features.secret_patterns):
        return True
    return any(word in lower for word in SENSITIVE_CONTEXT_WORDS)


def _has_file_upload_shape(lower: str) -> bool:
    return any(marker in lower for marker in FILE_UPLOAD_MARKERS) and ("post(" in lower or "requests.post" in lower)


def _has_external_network(features: FeatureSet, file_path: str) -> bool:
    for hit in features.network_calls + features.urls + features.ips:
        if hit.file_path == file_path and not LOCAL_DESTINATION_RE.search(hit.value):
            return True
    return False


def _line(hit) -> str:
    return f" at line {hit.line_number}" if hit.line_number else ""


def _looks_like_incident_response_analysis(lower: str) -> bool:
    return bool(INCIDENT_RESPONSE_CONTEXT_RE.search(lower) and DETECTION_VERBS_RE.search(lower))


def _looks_like_benign_realtime_context(lower: str) -> bool:
    return bool(BENIGN_WEBSOCKET_CONTEXT_RE.search(lower))

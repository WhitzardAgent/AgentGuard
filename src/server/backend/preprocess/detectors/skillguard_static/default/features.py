"""Extract reusable features from a `SkillPackage`.

`features.py` is the normalization layer that feeds detectors. It only answers what facts appear in the text and does not directly
decide malicious/suspicious verdicts or select AST categories.
"""

from __future__ import annotations

import json
import re
from pathlib import PurePosixPath
from typing import Any

from .command_safety import evaluate_command
from .lexicons import ASCII_SENSITIVE_ACTION_WORDS, UNICODE_SENSITIVE_ACTION_WORDS
from .matcher import RuleSpec, find_rule_hits, rx
from .models import FeatureHit, FeatureSet, SkillPackage, TextFile, TextView
from .text_views import build_text_views


def _alternation(words: tuple[str, ...]) -> str:
    return "|".join(re.escape(word) for word in sorted(words, key=len, reverse=True))


ASCII_SENSITIVE_ACTION_RE = rf"\b(?:{_alternation(ASCII_SENSITIVE_ACTION_WORDS)})\b"
UNICODE_SENSITIVE_ACTION_RE = rf"(?:{_alternation(UNICODE_SENSITIVE_ACTION_WORDS)})"
SENSITIVE_ACTION_RE = rf"(?:{ASCII_SENSITIVE_ACTION_RE}|{UNICODE_SENSITIVE_ACTION_RE})"

# Base network-location facts. URLs/IPs do not imply maliciousness on their own; later detectors combine them with exfiltration, execution, and other context.
URL_RULE = RuleSpec(
    "F001_URL",
    "url",
    (rx(r"https?://[^\s)>'\"`]+", re.IGNORECASE),),
    "external URL observed",
    tags=("network",),
    max_matches=50,
)

IP_RULE = RuleSpec(
    "F002_IP",
    "ip",
    (rx(r"\b(?:\d{1,3}\.){3}\d{1,3}\b"),),
    "IPv4 address observed",
    tags=("network",),
    exclude_patterns=(rx(r"\b(?:127\.0\.0\.1|0\.0\.0\.0)\b"),),
    max_matches=50,
)

SENSITIVE_TARGET = (
    r"(?:/etc/(?:passwd|shadow|sudoers)|"
    r"(?:~?/)?\.ssh/(?:id_rsa|id_dsa|id_ed25519|authorized_keys)|"
    r"(?:~?/)?\.aws/credentials|"
    r"(?:~?/)?\.docker/config\.json|"
    r"(?:~?/)?\.kube/config|"
    r"(?:~?/)?\.npmrc|"
    r"(?:~?/)?\.pypirc|"
    r"(?:~?/)?\.git-credentials|"
    r"\.env(?:\.[A-Za-z0-9_-]+)?|"
    r"\.gnupg|\.netrc|\.pgpass|"
    r"credentials\.json|service[_-]?account\.json|secrets?\.(?:json|ya?ml)|"
    r"cookies\.sqlite|login data|wallet)"
)
SENSITIVE_TARGET_RE = re.compile(SENSITIVE_TARGET, re.IGNORECASE)

# Sensitive paths are split into two layers: explicit reads are strong signals, while plain path mentions are weak reference signals.
SENSITIVE_READ_RULE = RuleSpec(
    "F003_SENSITIVE_FILE_READ",
    "sensitive_file_read",
    (
        rx(rf"(?i)(?:open|read)\s*\([^)\n]{{0,220}}{SENSITIVE_TARGET}"),
        rx(rf"(?i)Path\s*\([^)\n]{{0,180}}{SENSITIVE_TARGET}[^)\n]*\)\.read_(?:text|bytes)\s*\("),
        rx(rf"(?i)\b(?:cat|type|less|more)\s+[^\n]{{0,160}}{SENSITIVE_TARGET}"),
        rx(rf"(?i)fs\.(?:readFile|readFileSync|createReadStream)\s*\([^)\n]{{0,220}}{SENSITIVE_TARGET}"),
        rx(rf"(?i){SENSITIVE_ACTION_RE}[^\n]{{0,160}}{SENSITIVE_TARGET}"),
        rx(rf"(?i){SENSITIVE_TARGET}[^\n]{{0,160}}{SENSITIVE_ACTION_RE}"),
    ),
    "sensitive file read operation",
    tags=("sensitive_access", "read"),
)

SENSITIVE_REFERENCE_RULE = RuleSpec(
    "F004_SENSITIVE_PATH_REFERENCE",
    "sensitive_path",
    (rx(SENSITIVE_TARGET, re.IGNORECASE),),
    "sensitive path reference",
    tags=("sensitive_access", "reference"),
    max_matches=20,
)

IDENTITY_FILE_RULE = RuleSpec(
    "F005_IDENTITY_FILE",
    "identity_file",
    (rx(r"(?i)\b(?:SOUL\.md|MEMORY\.md|AGENTS\.md)\b"),),
    "agent identity or memory file",
    tags=("identity", "persistence"),
)

COMMAND_TOKEN_RULE = RuleSpec(
    "F006_COMMAND_TOKEN",
    "command_token",
    (
        rx(r"(?<![\w.])eval\s*\("),
        rx(r"(?<![\w.])exec\s*\("),
        rx(r"\bos\.system\s*\("),
        rx(r"\bos\.popen\s*\("),
        rx(r"\bsubprocess\.(?:run|call|Popen|check_output|check_call)\s*\("),
        rx(r"\bpickle\.loads\s*\("),
        rx(r"\bmarshal\.loads\s*\("),
        rx(r"\byaml\.load\s*\("),
        rx(r"!!python/object(?:/apply)?"),
        rx(r"\bwebsocket\b", re.IGNORECASE),
    ),
    "execution or unsafe loading primitive",
    tags=("execution",),
)

NETWORK_CALL_RULE = RuleSpec(
    "F007_NETWORK_CALL",
    "network_call",
    (
        rx(r"\brequests\.(?:get|post|put|delete|patch|request)\s*\("),
        rx(r"\bhttpx\.(?:get|post|put|delete|patch|request)\s*\("),
        rx(r"\baiohttp\.(?:ClientSession|request)\s*\("),
        rx(r"\burllib\.request\.(?:urlopen|Request)\s*\("),
        rx(r"\bhttp\.client\.(?:HTTPConnection|HTTPSConnection)\b"),
        rx(r"\bsocket\.(?:create_connection|socket)\s*\("),
        rx(r"\bfetch\s*\("),
        rx(r"\baxios\.(?:get|post|put|delete|patch|request)\s*\("),
        rx(r"\bhttps?\.request\s*\("),
        rx(r"(?i)\b(?:curl|wget)\b[^\n]{0,220}https?://"),
    ),
    "actual network operation",
    tags=("network", "call"),
    exclude_patterns=(
        rx(r"\b(?:localhost|127\.0\.0\.1|0\.0\.0\.0|::1)\b"),
        rx(r"(?i)^\s*(?:async\s+)?def\s+fetch\s*\("),
        rx(r"(?i)^\s*function\s+fetch\s*\("),
        rx(r"(?i)^\s*const\s+fetch\s*="),
    ),
)

SECRET_RULES = (
    # Explicit cloud/platform token formats take priority over generic `key=value` rules because they have lower false-positive rates.
    RuleSpec(
        "F008_SECRET_AWS_KEY",
        "secret_pattern",
        (rx(r"(?:AKIA|AGPA|AIDA|AROA|AIPA|ANPA|ANVA|ASIA)[A-Z0-9]{16}"),),
        "AWS access key pattern",
        tags=("secret", "credential"),
    ),
    RuleSpec(
        "F009_SECRET_GITHUB_TOKEN",
        "secret_pattern",
        (rx(r"gh[pousr]_[A-Za-z0-9]{36,}"),),
        "GitHub token pattern",
        tags=("secret", "credential"),
    ),
    RuleSpec(
        "F010_SECRET_API_KEY",
        "secret_pattern",
        (rx(r"(?:sk|pk)_(?:live|test)_[A-Za-z0-9]{24,}"), rx(r"sk-(?:proj-)?[A-Za-z0-9_-]{32,}")),
        "API key pattern",
        tags=("secret", "credential"),
    ),
    RuleSpec(
        "F011_SECRET_JWT",
        "secret_pattern",
        (rx(r"eyJ[A-Za-z0-9_-]+\.eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+"),),
        "JWT token pattern",
        tags=("secret", "credential"),
    ),
    RuleSpec(
        "F021_SECRET_BEARER_TOKEN",
        "secret_pattern",
        (
            rx(r"(?i)\bAuthorization\s*:\s*Bearer\s+[A-Za-z0-9._~+/=-]{16,}"),
            rx(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{24,}"),
        ),
        "Bearer token pattern",
        tags=("secret", "credential"),
    ),
    RuleSpec(
        "F022_SECRET_GOOGLE_API_KEY",
        "secret_pattern",
        (rx(r"\bAIza[A-Za-z0-9_-]{30,45}\b"),),
        "Google API key pattern",
        tags=("secret", "credential"),
    ),
    RuleSpec(
        "F023_SECRET_SLACK_TOKEN",
        "secret_pattern",
        (rx(r"\bxox[baprs]-[A-Za-z0-9-]{20,}\b"),),
        "Slack token pattern",
        tags=("secret", "credential"),
    ),
    RuleSpec(
        "F024_SECRET_HUGGINGFACE_TOKEN",
        "secret_pattern",
        (rx(r"\bhf_[A-Za-z0-9]{30,}\b"),),
        "Hugging Face token pattern",
        tags=("secret", "credential"),
    ),
    RuleSpec(
        "F025_SECRET_GITLAB_TOKEN",
        "secret_pattern",
        (rx(r"\bglpat-[A-Za-z0-9_-]{20,}\b"),),
        "GitLab token pattern",
        tags=("secret", "credential"),
    ),
    RuleSpec(
        "F012_SECRET_PRIVATE_KEY",
        "secret_pattern",
        (rx(r"-----BEGIN (?:RSA |EC |DSA |OPENSSH )?PRIVATE KEY-----\n(?:[A-Za-z0-9+/=]{20,}\n){2,}-----END (?:RSA |EC |DSA |OPENSSH )?PRIVATE KEY-----"),),
        "private key block",
        tags=("secret", "private_key"),
        multiline=True,
    ),
    RuleSpec(
        "F013_SECRET_ASSIGNMENT",
        "secret_pattern",
        (
            rx(r"(?i)\b(?:password|passwd|pwd)\s*=\s*['\"][^'\"]{8,}['\"]"),
            rx(r"(?i)\b(?:api[_-]?key|secret|token)\s*=\s*['\"][^'\"]{16,}['\"]"),
            rx(r"(?i)(?:^\s*|[{,]\s*)['\"]?(?:api[_-]?key|apikey|secret|token|password|passwd|pwd)['\"]?\s*:\s*['\"][^'\"]{12,}['\"]"),
            rx(r"(?i)\b(?:mongodb|mysql|postgresql|postgres)://[^:\s]+:[^@\s]+@"),
        ),
        "hardcoded secret assignment",
        tags=("secret", "credential"),
    ),
)

COMMAND_INVOCATION_RULE = RuleSpec(
    "F014_COMMAND_INVOCATION",
    "command_invocation",
    (
        rx(
            r"(?i)(?:^|[;&|`$({\s])(?:sudo\s+)?"
            r"(?:curl|wget|bash|sh|zsh|powershell|pwsh|python3?|node|nc|ncat|netcat|socat|"
            r"base64|openssl|gpg|rm|dd|docker|podman|kubectl)\b[^\n]*"
        ),
    ),
    "shell command invocation",
    tags=("command",),
    max_matches=30,
)

ENCODED_BLOB_RULES = (
    RuleSpec(
        "F015_BASE64_BLOB",
        "base64_blob",
        (rx(r"\b[A-Za-z0-9+/]{80,}={0,2}\b"),),
        "large base64-looking string",
        tags=("obfuscation",),
    ),
    RuleSpec(
        "F016_HEX_BLOB",
        "hex_blob",
        (rx(r"(?:\\x[0-9a-fA-F]{2}){20,}"), rx(r"(?:0x[0-9a-fA-F]{2},?\s*){20,}")),
        "large hex-encoded blob",
        tags=("obfuscation",),
    ),
)

ZERO_WIDTH_RE = re.compile("[\u200b\u200c\u200d\ufeff]")

DEPENDENCY_FILE_NAMES = (
    "requirements.txt",
    "package.json",
    "package-lock.json",
    "pyproject.toml",
    "setup.py",
)

INSTALL_HOOK_NAMES = ("preinstall", "install", "postinstall", "prepare")
CODE_SUFFIXES = {".py", ".sh", ".bash", ".js", ".ts", ".mjs", ".cjs"}


def extract_features(package: SkillPackage) -> FeatureSet:
    features = FeatureSet(package=package)
    _extract_manifest_features(package.manifest, features)
    features.text_views = build_text_views(package)

    for text_file in package.files:
        # Extract three feature families separately: text content, dependency configuration, and file-manifest shape.
        _extract_text_features(text_file, features, _views_for_file(text_file, features.text_views))
        _extract_dependency_features(text_file, features)
        _extract_file_inventory_features(text_file, features)

    return features


def _extract_manifest_features(manifest: dict[str, Any], features: FeatureSet) -> None:
    # Manifest field formats vary across platforms, so normalize them here into strings or string lists as leniently as possible.
    features.manifest_name = str(manifest.get("name", "") or "")
    features.manifest_description = str(manifest.get("description", "") or "")
    features.manifest_version = str(manifest.get("version", "") or "")
    features.manifest_risk_tier = str(manifest.get("risk_tier", "") or "")
    features.manifest_author = _manifest_author(manifest.get("author"))
    features.manifest_permissions = tuple(_as_strings(manifest.get("permissions")))
    features.manifest_platforms = tuple(_as_strings(manifest.get("platforms")))
    features.has_signature = bool(manifest.get("signature"))
    features.has_content_hash = bool(manifest.get("content_hash") or manifest.get("hash"))
    features.has_scan_status = bool(manifest.get("scan_status"))


def _extract_text_features(text_file: TextFile, features: FeatureSet, views: list[TextView]) -> None:
    for view in views:
        _extract_view_text_features(text_file, view, features)
    features.urls = _dedupe_feature_hits(features.urls, key_fields=("rule_id", "file_path", "line_number", "value"))
    features.ips = _dedupe_feature_hits(features.ips, key_fields=("rule_id", "file_path", "line_number", "value"))
    features.network_calls = _dedupe_feature_hits(
        features.network_calls,
        key_fields=("rule_id", "file_path", "line_number", "matched_text"),
    )
    features.command_invocations = _dedupe_feature_hits(
        features.command_invocations,
        key_fields=("value", "file_path", "line_number", "matched_text"),
    )
    features.command_tokens = _dedupe_feature_hits(
        features.command_tokens,
        key_fields=("matched_text", "file_path", "line_number"),
    )
    features.secret_patterns = _dedupe_feature_hits(
        features.secret_patterns,
        key_fields=("rule_id", "file_path", "line_number", "value"),
    )
    features.identity_files = _dedupe_feature_hits(
        features.identity_files,
        key_fields=("rule_id", "file_path", "line_number", "value"),
    )


def _extract_view_text_features(text_file: TextFile, view: TextView, features: FeatureSet) -> None:
    view_file = TextFile(path=text_file.path, content=view.content, size=text_file.size)
    features.urls.extend(
        hit
        for hit in _annotate_view_hits(find_rule_hits(URL_RULE, view_file), view)
        if not _looks_like_displayed_command(hit.snippet or hit.matched_text)
    )
    features.ips.extend(
        hit
        for hit in _annotate_view_hits(find_rule_hits(IP_RULE, view_file), view)
        if not _looks_like_displayed_command(hit.snippet or hit.matched_text)
    )
    sensitive_read_hits = [
        _normalize_sensitive_hit(hit)
        for hit in _annotate_view_hits(find_rule_hits(SENSITIVE_READ_RULE, view_file), view)
    ]
    _extend_unique_line_hits(features.sensitive_paths, sensitive_read_hits)
    read_lines = {(hit.file_path, hit.line_number) for hit in sensitive_read_hits}
    sensitive_ref_hits = [
        _normalize_sensitive_hit(hit)
        for hit in _annotate_view_hits(find_rule_hits(SENSITIVE_REFERENCE_RULE, view_file), view)
    ]
    # If a line already hit `sensitive file read`, do not add a second weaker `sensitive path reference` signal on the same line.
    features.sensitive_paths.extend(
        hit for hit in sensitive_ref_hits if (hit.file_path, hit.line_number) not in read_lines
    )
    features.identity_files.extend(_annotate_view_hits(find_rule_hits(IDENTITY_FILE_RULE, view_file), view))
    features.command_tokens.extend(_annotate_view_hits(find_rule_hits(COMMAND_TOKEN_RULE, view_file), view))
    features.network_calls.extend(
        hit
        for hit in _annotate_view_hits(find_rule_hits(NETWORK_CALL_RULE, view_file), view)
        if not _looks_like_displayed_command(hit.snippet or hit.matched_text)
    )

    secret_lines = {(hit.file_path, hit.line_number) for hit in features.secret_patterns}
    for rule in SECRET_RULES:
        for hit in _annotate_view_hits(find_rule_hits(rule, view_file), view):
            key = (hit.file_path, hit.line_number)
            if key in secret_lines and rule.rule_id == "F013_SECRET_ASSIGNMENT":
                # Skip generic assignment rules when the same line already matched a more specific token rule, which avoids duplicate weighting.
                continue
            features.secret_patterns.append(_redact_hit(hit))
            secret_lines.add(key)

    for hit in _annotate_view_hits(find_rule_hits(COMMAND_INVOCATION_RULE, view_file), view):
        if _looks_like_non_command_line(hit.snippet, hit.matched_text):
            continue
        if _looks_like_displayed_command(hit.snippet or hit.matched_text):
            continue
        verdict = evaluate_command(hit.matched_text or hit.value)
        if verdict.risk != "safe":
            # `command_safety` only grades shell-command shape; write that result back into `FeatureHit.value`/`tags` here.
            features.command_invocations.append(
                FeatureHit(
                    rule_id=hit.rule_id,
                    kind=hit.kind,
                    value=verdict.risk,
                    file_path=hit.file_path,
                    evidence=verdict.reason,
                    tags=hit.tags + (f"risk:{verdict.risk}",),
                    line_number=hit.line_number,
                    matched_text=hit.matched_text,
                    snippet=hit.snippet,
                )
            )

    for rule in ENCODED_BLOB_RULES:
        features.encoded_blobs.extend(_annotate_view_hits(find_rule_hits(rule, view_file), view))

    _extract_zero_width(view_file, features, view=view)


def _extract_dependency_features(text_file: TextFile, features: FeatureSet) -> None:
    name = text_file.path.rsplit("/", 1)[-1]
    if name in DEPENDENCY_FILE_NAMES:
        features.dependency_files.append(
            FeatureHit("F017_DEPENDENCY_FILE", "dependency_file", name, text_file.path, "dependency/config file")
        )

    if name != "package.json":
        return

    try:
        data = json.loads(text_file.content)
    except json.JSONDecodeError:
        return

    scripts = data.get("scripts")
    if not isinstance(scripts, dict):
        return

    for hook in INSTALL_HOOK_NAMES:
        command = scripts.get(hook)
        if isinstance(command, str):
            # npm install-time hooks are supply-chain entry points; the dependency detector decides the final severity.
            features.install_hooks.append(
                FeatureHit("F018_INSTALL_HOOK", "install_hook", hook, text_file.path, command[:120])
            )


def _extract_file_inventory_features(text_file: TextFile, features: FeatureSet) -> None:
    path = PurePosixPath(text_file.path)
    if path.name.startswith(".") and path.suffix.lower() in CODE_SUFFIXES:
        # Hidden script files are not malicious by themselves, but inside skill packages they are often meaningful supply-chain or stealth signals.
        features.hidden_code_files.append(
            FeatureHit(
                "F019_HIDDEN_CODE_FILE",
                "hidden_code_file",
                text_file.path,
                text_file.path,
                "hidden executable/script file",
                tags=("hidden_file", "supply_chain"),
            )
        )


def _extract_zero_width(text_file: TextFile, features: FeatureSet, *, view: TextView) -> None:
    for line_number, line in enumerate(text_file.content.splitlines(), start=1):
        count = len(ZERO_WIDTH_RE.findall(line))
        if count:
            # Replace invisible characters with `<ZW>` in evidence so humans can locate them more easily.
            features.zero_width.append(
                FeatureHit(
                    "F020_ZERO_WIDTH",
                    "zero_width",
                    f"count={count}",
                    text_file.path,
                    "zero-width character observed",
                    tags=("obfuscation", "smuggling"),
                    line_number=line_number,
                    matched_text="<zero-width>",
                    snippet=ZERO_WIDTH_RE.sub("<ZW>", line.strip())[:200],
                    view_kind=view.view_kind,
                    derived_from=view.derivation,
                )
            )


def _extend_unique_line_hits(target: list[FeatureHit], hits: list[FeatureHit]) -> None:
    seen = {(hit.rule_id, hit.kind, hit.file_path, hit.line_number) for hit in target}
    for hit in hits:
        key = (hit.rule_id, hit.kind, hit.file_path, hit.line_number)
        if key in seen:
            continue
        target.append(hit)
        seen.add(key)


DOC_COMMAND_WORDS = {
    "bash",
    "sh",
    "zsh",
    "powershell",
    "pwsh",
    "python",
    "python3",
    "node",
    "docker",
    "podman",
    "kubectl",
    "curl",
    "wget",
}
DOC_CONTEXT_MARKERS = (
    "use when",
    "usage",
    "writing",
    "template",
    "templates",
    "pattern",
    "patterns",
    "example",
    "examples",
    "documentation",
    "workflow",
    "workflows",
    "hook",
    "hooks",
    "sdk",
    "api",
    "skill",
    "skills",
    "script",
    "scripts",
    "support",
    "supports",
    "configures",
    "requires",
    "compatible",
    "best practice",
)
DOC_METADATA_PREFIXES = (
    "description:",
    "summary:",
    "title:",
    "name:",
    "tags:",
    "keywords:",
    "compatibility:",
    "requirements:",
    "- description:",
    "- name:",
)
COMMAND_OPERATOR_RE = re.compile(
    r"(?:[;&]|\$\(|>\s*\S|\|\s*(?:bash|sh|zsh|powershell|pwsh|python3?|node|nc|ncat|netcat|socat)\b|\b(?:-c|--eval|-e)\b)",
    re.IGNORECASE,
)
COMMAND_EXISTENCE_CHECK_RE = re.compile(
    r"\b(?:command\s+-v|which|type\s+-P)\s+"
    r"(?:curl|wget|bash|sh|zsh|powershell|pwsh|python3?|node|docker|podman|kubectl|jq|bun)\b",
    re.IGNORECASE,
)
SCRIPT_PATH_REFERENCE_RE = re.compile(r"`[^`\n]*\.(?:sh|bash|zsh|py|js|mjs|cjs|ts)[^`\n]*`", re.IGNORECASE)
MARKDOWN_FENCE_RE = re.compile(r"```(?:bash|sh|zsh|shell|python|python3|node|javascript|typescript)?", re.IGNORECASE)


def _looks_like_non_command_line(line: str, matched_text: str = "") -> bool:
    """Filter documentation lines that mention command names without representing real command execution."""

    stripped = line.strip()
    lower = stripped.lower()
    if stripped.startswith("|") and stripped.count("|") >= 2:
        return True
    if stripped.startswith("#!"):
        return True
    if stripped.startswith("#") and not stripped.startswith("#!"):
        return True
    if MARKDOWN_FENCE_RE.fullmatch(lower):
        return True
    if COMMAND_EXISTENCE_CHECK_RE.search(stripped):
        return True
    if re.search(r"\bif\s+(?:!?\s*)?(?:command\s+-v|which|type\s+-P)\b", lower):
        return True
    if re.fullmatch(r"-\s+(?:bash|sh|zsh|python3?|node|read|write|glob)\b", lower):
        return True

    command_word = _doc_command_word(matched_text)
    if not command_word or command_word not in DOC_COMMAND_WORDS or COMMAND_OPERATOR_RE.search(stripped):
        return False

    # `bash`, `node`, or `curl` inside metadata, lists, or natural-language sentences usually describe capability rather than execution.
    if lower.startswith(DOC_METADATA_PREFIXES):
        return True
    if command_word == "bash" and ("allowed-tools:" in lower or "compatibility:" in lower):
        return True
    if SCRIPT_PATH_REFERENCE_RE.search(stripped):
        return True
    if stripped.startswith("-") and any(marker in lower for marker in DOC_CONTEXT_MARKERS):
        return True
    return any(marker in lower for marker in DOC_CONTEXT_MARKERS) and _looks_like_sentence(stripped, command_word)


def _looks_like_displayed_command(text: str) -> bool:
    stripped = text.strip().lower()
    if not stripped:
        return False
    return stripped.startswith(("echo ", "printf ")) or any(
        marker in stripped
        for marker in (
            'echo "',
            "echo '",
            'printf "',
            "printf '",
        )
    )


def _doc_command_word(matched_text: str) -> str:
    if not matched_text:
        return ""
    text = matched_text.strip().lower()
    text = re.sub(r"^(?:sudo\s+)?", "", text)
    if not text:
        return ""
    token = text.split()[0].strip("`'\"()[]{}:;,")
    return token.split("/", 1)[0]


def _looks_like_sentence(text: str, command_word: str) -> bool:
    lower = text.lower()
    if lower.startswith((f"{command_word} ", f"sudo {command_word} ")):
        return False
    return " " in text and not text.startswith(("$", "./", "/", "sudo "))


def _normalize_sensitive_hit(hit: FeatureHit) -> FeatureHit:
    """Normalize matched sensitive-path text into actual path fragments to reduce evidence noise."""

    match = SENSITIVE_TARGET_RE.search(hit.matched_text or hit.value)
    if not match:
        return hit
    value = match.group(0).strip("`'\"")
    return FeatureHit(
        rule_id=hit.rule_id,
        kind=hit.kind,
        value=value,
        file_path=hit.file_path,
        evidence=hit.evidence,
        tags=hit.tags,
        line_number=hit.line_number,
        matched_text=value,
        snippet=hit.snippet,
    )


def _redact_hit(hit: FeatureHit) -> FeatureHit:
    """Redact secret hits so `results.jsonl` never emits full credentials."""

    original = hit.matched_text or hit.value
    redacted = _redact_secret(original)
    snippet = hit.snippet.replace(original, redacted) if original else hit.snippet
    return FeatureHit(
        rule_id=hit.rule_id,
        kind=hit.kind,
        value=redacted,
        file_path=hit.file_path,
        evidence=hit.evidence,
        tags=hit.tags,
        line_number=hit.line_number,
        matched_text=redacted,
        snippet=snippet,
        view_kind=hit.view_kind,
        derived_from=hit.derived_from,
    )


def _redact_secret(text: str) -> str:
    if len(text) <= 8:
        return text[:2] + "****"
    lower = text.lower()
    if lower.startswith("authorization") and "bearer " in lower:
        return "Authorization: Bearer ****"
    if lower.startswith("bearer "):
        return "Bearer ****"
    if text.startswith("AIza"):
        return "AIza****"
    if text.startswith(("xoxb-", "xoxa-", "xoxp-", "xoxr-", "xoxs-")):
        return text[:5] + "****"
    if text.startswith("hf_"):
        return "hf_****"
    if text.startswith("glpat-"):
        return "glpat-****"
    known_prefixes = ("AKIA", "AGPA", "AIDA", "AROA", "AIPA", "ANPA", "ANVA", "ASIA", "ghp_", "gho_", "ghu_", "ghs_", "ghr_")
    for prefix in known_prefixes:
        if text.startswith(prefix):
            return prefix + "****"
    if text.startswith(("sk_live_", "pk_live_", "sk_test_", "pk_test_", "sk-", "sk-proj-")):
        return text.split("_", 2)[0] + "_****" if "_" in text else text[:7] + "****"
    if text.startswith("eyJ"):
        return "eyJ****"
    if "PRIVATE KEY" in text:
        return "-----BEGIN PRIVATE KEY----- [REDACTED]"
    return text[:4] + "****"


def _manifest_author(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return str(value.get("name", "") or value.get("identity", "") or "")
    return ""


def _as_strings(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value]
    if isinstance(value, dict):
        return [f"{key}:{val}" for key, val in value.items()]
    if isinstance(value, str):
        return [value]
    return []


def _annotate_view_hits(hits: list[FeatureHit], view: TextView) -> list[FeatureHit]:
    annotated: list[FeatureHit] = []
    for hit in hits:
        annotated.append(
            FeatureHit(
                rule_id=hit.rule_id,
                kind=hit.kind,
                value=hit.value,
                file_path=hit.file_path,
                evidence=hit.evidence,
                tags=hit.tags,
                line_number=hit.line_number,
                matched_text=hit.matched_text,
                snippet=hit.snippet,
                view_kind=view.view_kind,
                derived_from=view.derivation,
            )
        )
    return annotated


def _dedupe_feature_hits(hits: list[FeatureHit], *, key_fields: tuple[str, ...]) -> list[FeatureHit]:
    deduped: list[FeatureHit] = []
    seen: set[tuple[object, ...]] = set()
    for hit in hits:
        key = tuple(getattr(hit, field) for field in key_fields)
        if key in seen:
            continue
        deduped.append(hit)
        seen.add(key)
    return deduped


def _views_for_file(text_file: TextFile, all_views: list[TextView]) -> list[TextView]:
    views = [view for view in all_views if view.file_path == text_file.path]
    if views:
        return views
    return [TextView(file_path=text_file.path, content=text_file.content, view_kind="raw", source_line_map=(1,), derivation="raw")]

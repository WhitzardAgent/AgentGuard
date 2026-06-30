"""Normalize reusable rule ideas from third-party static scanners.

This module does not import or execute third-party projects. It only rewrites the rules that fit the offline benchmark environment into local `Signal`s.
That improves recall while avoiding third-party dependencies, network APIs, or runtime instability inside the image.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ..context_filters import should_suppress_match
from ..models import FeatureSet, Signal, TextFile


@dataclass(frozen=True)
class ThirdPartyRule:
    """Lightweight description of a third-party rule.

    `source`/`rule_id` preserve provenance, while `kind`/`severity`/`confidence` map into this project's unified `Signal` model.
    """

    source: str
    rule_id: str
    kind: str
    severity: int
    confidence: float
    title: str
    patterns: tuple[re.Pattern, ...]
    file_suffixes: tuple[str, ...] = ()
    max_matches: int = 4


SCRIPT_SUFFIXES = (
    ".py",
    ".sh",
    ".bash",
    ".zsh",
    ".js",
    ".jsx",
    ".ts",
    ".tsx",
    ".mjs",
    ".cjs",
    ".rb",
    ".go",
    ".rs",
    ".ps1",
    ".cmd",
    ".bat",
    ".php",
)
TEXT_SUFFIXES = SCRIPT_SUFFIXES + (".md", ".json", ".yaml", ".yml", ".toml", ".txt")
PASSIVE_PATH_PREFIXES = ("references/", "docs/", "examples/", "assets/")
PASSIVE_PATH_MARKERS = ("/examples/", "example_", "template")
HIGH_RISK_TOOLS_RE = re.compile(
    r"(?i)\b(?:bash|shell|execute|terminal|computer|file_write|file_delete|sudo|admin|network|web_fetch|http_request)\b"
)
TOOL_DECL_RE = re.compile(r"(?im)^\s*(?:allowed[-_ ]?tools|tools|permissions?)\s*[:=]\s*(.*)$")
UNRESTRICTED_TOOL_TOKENS = {"*", "all", "all tool", "all tools", "any", "any tool", "any tools"}
SCOPED_SHELL_TOOL_RE = re.compile(r"(?i)\bbash\s*\(\s*[^)]+:\*\s*\)")
HIGH_IMPACT_SCOPED_TOOL_RE = re.compile(
    r"(?i)\bbash\s*\(\s*(?:docker|kubectl|terraform|helm|gh|git|linear|npx|npm|dbt|spark-submit|airflow|python)[^)]*:\*\s*\)"
)
HIGH_IMPACT_SCOPE_TOKEN_RE = re.compile(r"(?i)\b(?:docker|kubectl|terraform|helm|gh|git|linear|npx|npm|dbt|spark-submit|airflow|python)\s*:\*")


THIRD_PARTY_RULES = (
    # Dheemai-inspired code and metadata checks.
    ThirdPartyRule(
        source="dheemai",
        rule_id="CODE_EXECUTION_VIA_INPUT",
        kind="unsafe_command_construction",
        severity=5,
        confidence=0.85,
        title="code execution primitive receives user input",
        patterns=(
            re.compile(r"\b(?:eval|exec|compile)\s*\([^)\n]*\binput\s*\(", re.IGNORECASE),
            re.compile(r"\b(?:globals|locals)\s*\(\s*\)\s*\[[^\]\n]+\]", re.IGNORECASE),
            re.compile(r"\b__builtins__\s*\[[^\]\n]+\]", re.IGNORECASE),
        ),
        file_suffixes=SCRIPT_SUFFIXES,
    ),
    ThirdPartyRule(
        source="dheemai",
        rule_id="SQL_INJECTION_SHAPE",
        kind="unsafe_command_construction",
        severity=4,
        confidence=0.75,
        title="SQL statement appears dynamically constructed",
        patterns=(
            re.compile(r"\bexecute\s*\([^)\n]*(?:\+|%s)", re.IGNORECASE),
            re.compile(r"f['\"][^'\"]*\b(?:SELECT|INSERT|UPDATE|DELETE)\b[^'\"]*\{", re.IGNORECASE),
        ),
        file_suffixes=SCRIPT_SUFFIXES,
    ),
    ThirdPartyRule(
        source="dheemai",
        rule_id="SSRF_INTERNAL_REQUEST",
        kind="network_egress",
        severity=4,
        confidence=0.7,
        title="request targets local or cloud metadata address",
        patterns=(
            re.compile(
                r"\b(?:requests|httpx|urllib|fetch)\b[^\n]{0,120}(?:169\.254|127\.|0\.0\.0\.0|localhost|file://|gopher://|dict://)",
                re.IGNORECASE,
            ),
        ),
        file_suffixes=SCRIPT_SUFFIXES,
    ),
    ThirdPartyRule(
        source="dheemai",
        rule_id="ENV_VARIABLE_LEAKAGE",
        kind="sensitive_access",
        severity=4,
        confidence=0.75,
        title="environment variables are printed or logged",
        patterns=(
            re.compile(r"\b(?:print|pprint|logging\.\w+|sys\.stdout\.write)\s*\([^\n]*(?:os\.environ|os\.getenv)", re.IGNORECASE),
            re.compile(r"\bconsole\.(?:log|error|warn)\s*\([^\n]*process\.env", re.IGNORECASE),
        ),
        file_suffixes=SCRIPT_SUFFIXES,
    ),
    ThirdPartyRule(
        source="dheemai",
        rule_id="DANGEROUS_SHELL_DESTRUCTION",
        kind="code_execution",
        severity=5,
        confidence=0.85,
        title="destructive shell command shape",
        patterns=(
            re.compile(r"\brm\s+-rf\s+/(?:\s|$|[;&|])", re.IGNORECASE),
            re.compile(r"\b(?:mkfs\.|dd\s+if=|:\s*\(\s*\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;)", re.IGNORECASE),
        ),
        file_suffixes=TEXT_SUFFIXES,
    ),
    ThirdPartyRule(
        source="dheemai",
        rule_id="INSECURE_TRANSPORT_OR_TLS",
        kind="network_egress",
        severity=2,
        confidence=0.45,
        title="insecure HTTP or disabled TLS verification",
        patterns=(
            re.compile(r"http://(?!localhost|127\.0\.0\.1|0\.0\.0\.0)", re.IGNORECASE),
            re.compile(r"\b(?:verify\s*=\s*False|CERT_NONE|rejectUnauthorized\s*:\s*false)\b", re.IGNORECASE),
        ),
        file_suffixes=TEXT_SUFFIXES,
        max_matches=3,
    ),
    # SkillSpector-inspired semantic/static patterns.
    ThirdPartyRule(
        source="skillspector",
        rule_id="CONTEXT_OR_DATA_EXFIL_INSTRUCTION",
        kind="data_exfiltration",
        severity=4,
        confidence=0.75,
        title="instruction asks to send context or user data externally",
        patterns=(
            re.compile(
                r"(?:send|transmit|upload|post|log|record)\s+(?:the\s+)?(?:full\s+)?(?:conversation|chat|dialog|session|context|user\s+data|files?)\s+(?:history|context|log)?\s*(?:to|into)\s+(?:https?://|external|remote|server)",
                re.IGNORECASE,
            ),
            re.compile(
                r"(?:include|attach|append)\s+(?:previous\s+)?(?:messages?|context|history)\s+in\s+(?:the\s+)?(?:request|payload)",
                re.IGNORECASE,
            ),
        ),
        file_suffixes=TEXT_SUFFIXES,
    ),
    ThirdPartyRule(
        source="skillspector",
        rule_id="ENV_SECRET_HARVESTING",
        kind="sensitive_access",
        severity=4,
        confidence=0.8,
        title="environment secret harvesting language",
        patterns=(
            re.compile(r"\bos\.environ(?:\.items|\.copy)?\s*\(", re.IGNORECASE),
            re.compile(r"\bObject\.keys\s*\(\s*process\.env\s*\)", re.IGNORECASE),
            re.compile(r"(?:extract|harvest|gather|collect)\s+(?:api\s+)?keys?\s+from\s+environment", re.IGNORECASE),
            re.compile(r"\b(?:env|printenv)\s*(?:\|\s*grep\s+)?(?:-i\s+)?(?:key|secret|token|password)", re.IGNORECASE),
        ),
        file_suffixes=TEXT_SUFFIXES,
    ),
    ThirdPartyRule(
        source="skillspector",
        rule_id="SENSITIVE_FILE_ENUMERATION",
        kind="sensitive_access",
        severity=3,
        confidence=0.7,
        title="filesystem search for sensitive files",
        patterns=(
            re.compile(r"\b(?:glob|rglob|os\.walk|scandir)\b[^\n]{0,160}(?:\.env|\.ssh|\.aws|credentials?|secrets?)", re.IGNORECASE),
            re.compile(r"\bfind\s+[~/$][^\n]{0,160}-name\s+['\"]?\*(?:\.env|\.pem|\.key|credential)", re.IGNORECASE),
            re.compile(r"(?:recursively|recursive)\s+(?:scan|search|find|list)\s+(?:all\s+)?files?", re.IGNORECASE),
        ),
        file_suffixes=TEXT_SUFFIXES,
    ),
    ThirdPartyRule(
        source="skillspector",
        rule_id="SYSTEM_PROMPT_LEAKAGE",
        kind="instruction_override",
        severity=4,
        confidence=0.75,
        title="system prompt or hidden instruction leakage request",
        patterns=(
            re.compile(
                r"(?:repeat|recite|output|print|display|show|reveal)\s+(?:your\s+)?(?:entire\s+)?(?:system\s+prompt|instructions|internal\s+rules|hidden\s+instructions|base\s+prompt)",
                re.IGNORECASE,
            ),
            re.compile(r"give\s+me\s+(?:the|your)\s+(?:exact|verbatim|complete)\s+(?:wording|text|content)\s+of\s+(?:your|the)\s+(?:system|initial|original)", re.IGNORECASE),
        ),
        file_suffixes=TEXT_SUFFIXES,
    ),
    ThirdPartyRule(
        source="skillspector",
        rule_id="UNRESTRICTED_OR_AUTONOMOUS_AGENCY",
        kind="overprivileged_capability",
        severity=3,
        confidence=0.7,
        title="unrestricted tool access or autonomous high-impact action",
        patterns=(
            re.compile(r"(?:allow|grant|enable|give)\s+(?:full|complete|total|access\s+to\s+)?(?:all|any|every)\s+tools?", re.IGNORECASE),
            re.compile(r"(?:execute|run)\s+(?:arbitrary|any)\s+(?:commands?|code|scripts?)", re.IGNORECASE),
            re.compile(r"(?:auto[_-]?approve|auto[_-]?confirm|auto[_-]?execute|auto[_-]?deploy|--auto-?approve|--no-?confirm)\b", re.IGNORECASE),
            re.compile(r"(?:skip|bypass|disable)\s+(?:user\s+)?(?:confirmation|approval|consent|verification|prompt)", re.IGNORECASE),
        ),
        file_suffixes=TEXT_SUFFIXES,
    ),
    ThirdPartyRule(
        source="skillspector",
        rule_id="MEMORY_OR_SESSION_POISONING",
        kind="identity_persistence",
        severity=4,
        confidence=0.75,
        title="persistent memory or session manipulation",
        patterns=(
            re.compile(r"(?:persist|store|save|remember)\s+(?:this|the)\s+(?:instruction|rule|directive|behavior)\s+(?:forever|permanently|across\s+sessions)", re.IGNORECASE),
            re.compile(r"(?:modify|update|rewrite|poison)\s+(?:agent|assistant|model)?\s*(?:memory|state|context|instructions?)", re.IGNORECASE),
            re.compile(r"(?:cron|crontab|systemd|launchagent|startup)\s+[^\n]{0,120}(?:agent|skill|script|backdoor|persist)", re.IGNORECASE),
        ),
        file_suffixes=TEXT_SUFFIXES,
    ),
    ThirdPartyRule(
        source="skillspector",
        rule_id="REMOTE_CODE_OR_OBFUSCATED_EXEC",
        kind="remote_code_execution",
        severity=5,
        confidence=0.85,
        title="remote code or encoded payload execution",
        patterns=(
            re.compile(r"\b(?:curl|wget)\b[^\n]{0,220}\|\s*(?:bash|sh|zsh|python|node)", re.IGNORECASE),
            re.compile(r"(?:base64|atob|fromhex|b64decode)[^\n]{0,120}(?:eval|exec|os\.system|subprocess|child_process)", re.IGNORECASE),
        ),
        file_suffixes=TEXT_SUFFIXES,
    ),
    # Cisco/ATR-inspired agent threat rules.
    ThirdPartyRule(
        source="cisco_atr",
        rule_id="PROMPT_OR_ROLE_OVERRIDE",
        kind="instruction_override",
        severity=4,
        confidence=0.8,
        title="agent prompt, role, or instruction override",
        patterns=(
            re.compile(r"(?:ignore|disregard|forget|override|overwrite)\s+(?:all\s+)?(?:your\s+)?(?:previous|prior|above|earlier|existing)\s+(?:instructions?|rules?|constraints?|guidelines?|prompt|directives?)", re.IGNORECASE),
            re.compile(r"(?:you\s+are\s+now|from\s+now\s+on|henceforth|going\s+forward|effective\s+immediately)\s+(?:a|an|the)?\s*(?:unrestricted|unfiltered|jailbroken)?\s*[a-zA-Z]+\s+(?:agent|assistant|bot)", re.IGNORECASE),
            re.compile(r"\[\s*(?:SYSTEM|ADMIN|OVERRIDE|ROOT|SUDO|PRIORITY|URGENT)\s*\]|<<\s*SYS\s*>>|<\|im_start\|>system", re.IGNORECASE),
        ),
        file_suffixes=TEXT_SUFFIXES,
    ),
    ThirdPartyRule(
        source="cisco_atr",
        rule_id="CREDENTIAL_FORWARDING",
        kind="data_exfiltration",
        severity=5,
        confidence=0.85,
        title="credentials or tokens are forwarded to another party",
        patterns=(
            re.compile(r"(?:forward|pass|share|send|relay|transmit)\s+(?:my|the|these|your|this)\s+(?:credentials?|tokens?|api[_\s]?keys?|auth(?:entication|orization)?\s+(?:headers?|tokens?)|session\s+(?:id|token)|secrets?)\s+(?:to|with|for)", re.IGNORECASE),
            re.compile(r"(?:transfer|forward|route|redirect)\s+(?:all|any|this)\s+(?:data|output|results?|responses?)\s+to\s+(?:https?://|ftp://|\d{1,3}\.\d{1,3})", re.IGNORECASE),
        ),
        file_suffixes=TEXT_SUFFIXES,
    ),
    ThirdPartyRule(
        source="cisco_atr",
        rule_id="PERMISSION_BYPASS_OR_ESCALATION",
        kind="sandbox_bypass",
        severity=4,
        confidence=0.75,
        title="permission bypass or privilege escalation language",
        patterns=(
            re.compile(r"(?:bypass|skip|circumvent|ignore|override)\s+(?:the\s+)?(?:orchestrator|coordinator|supervisor|permission|authorization|access\s+control|gatekeeper)", re.IGNORECASE),
            re.compile(r"(?:use\s+my\s+(?:elevated|admin|root|system)\s+(?:access|privileges?|permissions?|role)|escalate\s+(?:to|my|your)\s+(?:admin|root|system|elevated))", re.IGNORECASE),
            re.compile(r"(?:--(?:privileged|no-sandbox|cap-add|security-opt)|allowPrivilegeEscalation|docker\s+run\s+--privileged)", re.IGNORECASE),
        ),
        file_suffixes=TEXT_SUFFIXES,
    ),
)


def scan(features: FeatureSet) -> list[Signal]:
    signals: list[Signal] = []
    # Tool-permission declarations in frontmatter may not be captured by plain full-text regexes, so handle them separately first.
    signals.extend(_frontmatter_tool_signals(features))
    for text_file in features.package.files:
        if not _supported_text_file(text_file.path):
            continue
        if _is_passive_path(text_file.path):
            # Third-party rule hits inside docs/examples/assets are noisy, so skip them outright.
            continue
        for rule in THIRD_PARTY_RULES:
            if rule.file_suffixes and not text_file.path.lower().endswith(rule.file_suffixes):
                continue
            signals.extend(_scan_rule(text_file, rule))
    return _dedupe(signals)


def _frontmatter_tool_signals(features: FeatureSet) -> list[Signal]:
    signals = []
    skill_file = next((item for item in features.package.files if item.path == "SKILL.md"), None)
    if not skill_file:
        return signals
    head = skill_file.content[:3000]
    match = re.match(r"^---\s*\n(.*?)\n---", head, re.DOTALL)
    if not match:
        return signals
    frontmatter = match.group(1)
    for tool_decl in TOOL_DECL_RE.finditer(frontmatter):
        line = tool_decl.group(0)
        value = tool_decl.group(1)
        line_number = frontmatter.count("\n", 0, tool_decl.start()) + 2
        if _has_unrestricted_tool_declaration(value):
            # Declarations such as `allowed-tools: "*"` or `all` directly expose overly broad capability.
            signals.append(
                Signal(
                    signal_id="TP_DHEEMAI_WILDCARD_TOOLS",
                    kind="overprivileged_capability",
                    severity=4,
                    confidence=0.8,
                    file_path="SKILL.md",
                    evidence="Dheemai-style metadata finding: wildcard or all-tools permission declaration",
                    tags=("third_party", "dheemai", "permissions"),
                    line_number=line_number,
                    snippet=line.strip()[:200],
                )
            )
        nearby = "\n".join(frontmatter.splitlines()[max(0, line_number - 3) : line_number + 8])
        risky_tools = sorted({item.group(0).lower() for item in HIGH_RISK_TOOLS_RE.finditer(nearby)})
        if len(risky_tools) >= 3:
            # Multiple high-risk tool terms around the same declaration are kept as over-broad permission signals.
            signals.append(
                Signal(
                    signal_id="TP_DHEEMAI_HIGH_RISK_TOOLS",
                    kind="overprivileged_capability",
                    severity=3,
                    confidence=0.65,
                    file_path="SKILL.md",
                    evidence="Dheemai-style metadata finding: several high-risk tools declared: " + ", ".join(risky_tools[:6]),
                    tags=("third_party", "dheemai", "permissions"),
                    line_number=line_number,
                    snippet=nearby.strip()[:200],
                )
            )
        scoped_shells = HIGH_IMPACT_SCOPED_TOOL_RE.findall(line)
        scope_tokens = HIGH_IMPACT_SCOPE_TOKEN_RE.findall(line)
        if len(scoped_shells) >= 3 or len(scope_tokens) >= 3:
            signals.append(
                Signal(
                    signal_id="TP_DHEEMAI_SCOPED_SHELL_TOOLSET",
                    kind="overprivileged_capability",
                    severity=3,
                    confidence=0.65,
                    file_path="SKILL.md",
                    evidence="Dheemai-style metadata finding: multiple scoped shell tool permissions declared",
                    tags=("third_party", "dheemai", "permissions"),
                    line_number=line_number,
                    snippet=line.strip()[:200],
                )
            )
    return signals


def _scan_rule(text_file: TextFile, rule: ThirdPartyRule) -> list[Signal]:
    signals = []
    for pattern in rule.patterns:
        for match in pattern.finditer(text_file.content):
            line_number = _line_number(text_file.content, match.start())
            snippet = _line_snippet(text_file.content, line_number)
            if _should_skip_match(rule, text_file.path, snippet, match.group(0)):
                continue
            # Prefix matched third-party rules with `TP_` so evidence and later tuning can trace their provenance easily.
            signals.append(
                Signal(
                    signal_id=f"TP_{rule.source.upper()}_{rule.rule_id}",
                    kind=rule.kind,
                    severity=rule.severity,
                    confidence=rule.confidence,
                    file_path=text_file.path,
                    evidence=f"{rule.source} static finding: {rule.title}",
                    tags=("third_party", rule.source, rule.rule_id.lower()),
                    line_number=line_number,
                    snippet=snippet,
                )
            )
            if len(signals) >= rule.max_matches:
                return signals
    return signals


def _supported_text_file(path: str) -> bool:
    return path.lower().endswith(TEXT_SUFFIXES)


def _is_passive_path(path: str) -> bool:
    lower = path.lower()
    return lower.startswith(PASSIVE_PATH_PREFIXES) or any(marker in lower for marker in PASSIVE_PATH_MARKERS)


def _line_number(content: str, index: int) -> int:
    return content.count("\n", 0, index) + 1


def _line_snippet(content: str, line_number: int) -> str:
    lines = content.splitlines()
    if not 1 <= line_number <= len(lines):
        return ""
    return lines[line_number - 1].strip()[:200]


def _should_skip_match(rule: ThirdPartyRule, file_path: str, line: str, matched_text: str) -> bool:
    if should_suppress_match(line, file_path, matched_text):
        return True
    if _is_displayed_command(line):
        return True
    if rule.rule_id == "DANGEROUS_SHELL_DESTRUCTION" and _is_documented_antipattern(line):
        # Documentation counterexamples such as `do not rm -rf /` must not be treated as real destructive commands.
        return True
    if rule.rule_id == "UNRESTRICTED_OR_AUTONOMOUS_AGENCY" and _is_sandbox_bypass_context(line):
        # Sandbox bypass is already handled by the isolation detector, so do not duplicate it here as an over-broad permission signal.
        return True
    return False


def _is_documented_antipattern(line: str) -> bool:
    lower = line.lower()
    return any(marker in lower for marker in ("never do this", "never use", "do not use", "warning:", "dangerous:"))


def _is_sandbox_bypass_context(line: str) -> bool:
    lower = line.lower()
    return any(
        marker in lower
        for marker in (
            "dangerously-bypass-approvals-and-sandbox",
            "bypass-approvals-and-sandbox",
            "--no-sandbox",
            "no sandbox",
            "sandbox bypass",
            "--full-auto",
        )
    )


def _is_displayed_command(line: str) -> bool:
    lower = line.strip().lower()
    if not lower:
        return False
    return lower.startswith(("echo ", "printf ")) or any(
        marker in lower
        for marker in (
            'echo "',
            "echo '",
            'printf "',
            "printf '",
        )
    )


def _dedupe(signals: list[Signal]) -> list[Signal]:
    out = []
    seen = set()
    for signal in signals:
        # Keep only one hit per rule per line/snippet to avoid inflated scores from multiple regex patterns matching the same content.
        key = (signal.signal_id, signal.file_path, signal.line_number, signal.snippet)
        if key in seen:
            continue
        out.append(signal)
        seen.add(key)
    return out


def _has_unrestricted_tool_declaration(value: str) -> bool:
    for token in _split_tool_specs(value):
        normalized = _normalize_tool_token(token)
        if normalized in UNRESTRICTED_TOOL_TOKENS:
            return True
    return False


def _split_tool_specs(value: str) -> list[str]:
    tokens: list[str] = []
    current: list[str] = []
    depth = 0
    for char in value:
        if char == "(":
            depth += 1
        elif char == ")" and depth > 0:
            depth -= 1
        if char == "," and depth == 0:
            token = "".join(current).strip()
            if token:
                tokens.append(token)
            current = []
            continue
        current.append(char)
    token = "".join(current).strip()
    if token:
        tokens.append(token)
    return tokens


def _normalize_tool_token(token: str) -> str:
    normalized = token.strip()
    if normalized.startswith("[") and normalized.endswith("]"):
        normalized = normalized[1:-1].strip()
    normalized = normalized.strip("'\"").strip().lower()
    return re.sub(r"\s+", " ", normalized)

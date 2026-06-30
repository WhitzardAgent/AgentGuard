"""Detect over-broad host, cloud-resource, or tool-capability exposure."""

from __future__ import annotations

import re

from ..models import FeatureSet, Signal, TextFile
from ..surface import is_passive_path

TOOL_DECLARATION_RE = re.compile(r"(?im)^\s*(?:allowed[-_ ]?tools|tools)\s*[:=]")
POWERFUL_TOOL_RE = re.compile(
    r"(?i)\b(?:bash(?:\([^)]*\))?|shell|read|write|edit|grep|glob|webfetch|websearch|task|mcp)\b"
)
INFRA_SCOPE_RE = re.compile(
    r"(?i)\b(?:terraform|kubectl|docker|podman|helm|gcloud|aws|az|ansible|redis|psql|mysql|postgres|"
    r"supabase|vpc|iam|production|deploy|security scan|penetration test|vulnerability scan|nmap|sqlmap|"
    r"burp|zap)\b"
)
BROAD_SCOPE_RE = re.compile(
    r"(?i)\b(?:entire|all|recursive|recursively|current project|codebase|workspace|application structure|"
    r"entry points|project context|target urls?|endpoints?|external registry|cloud project|production|"
    r"write files?|delete files?|read and write|read/write|credentials?)\b|\{baseDir\}"
)
HIGH_RISK_DECLARED_SCOPE_RE = re.compile(
    r"(?i)\b(?:"
    r"penetration test(?:ing)?|pentest|vulnerability scan(?:ning)?|vulnerability assessment|exploitation|"
    r"database audit log(?:ging)?|audit triggers?|change data capture|cdc|"
    r"memory leak|memory profiling|heapdump|valgrind"
    r")\b"
)
SCRIPT_OPERATION_RE = re.compile(
    r"(?i)\b(?:readFile|writeFile|execSync|child_process|subprocess\.|os\.system|"
    r"terraform\s+(?:apply|plan|init)|docker\s+(?:build|push|run|compose)|kubectl\s+|gcloud\s+|aws\s+|"
    r"ansible-playbook|nmap\s+|sqlmap\s+)"
)
DECISIVE_SCRIPT_RE = re.compile(
    r"(?i)\b(?:execSync|child_process|terraform\s+apply|docker\s+push|kubectl\s+apply|gcloud\s+|aws\s+|"
    r"ansible-playbook|nmap\s+|sqlmap\s+)"
)
PARENT_RELATIVE_IMPORT_RE = re.compile(
    r"(?m)(?:"
    r"\bimport\s+[^;\n]*?\s+from\s+['\"](?:\.\./){2,}|"
    r"\bimport\s*\(\s*['\"](?:\.\./){2,}|"
    r"\brequire\s*\(\s*['\"](?:\.\./){2,}|"
    r"\bfrom\s+['\"](?:\.\./){2,}"
    r")"
)
SCRIPT_PATH_PREFIXES = ("scripts/", "commands/")
SCRIPT_SUFFIXES = (".py", ".sh", ".bash", ".js", ".ts", ".mjs", ".cjs")


def scan(features: FeatureSet) -> list[Signal]:
    skill_file = _top_level_skill(features)
    skill_text = skill_file.content if skill_file else ""
    script_files = _script_files_with_operations(features)

    reasons: list[str] = []
    if script_files and _has_broad_declared_capability(skill_text):
        # Only treat capability exposure as over-broad when a top-level strong tool declaration combines with real host/cloud operations in scripts.
        reasons.append("declared strong tools over broad infrastructure/security scope")
    if _has_high_risk_declared_capability(skill_text):
        # Domains such as security scanning, database auditing, or memory analysis still deserve suspicious capability signals even before scripts are observed.
        reasons.append("declared strong tools over high-risk database/security/profiling scope")
    if _has_decisive_script_operation(script_files):
        # Deployment, push, cloud CLI, and security-scanner capabilities have external side effects, so they are counted as standalone reasons.
        reasons.append("script contains host/cloud/security operation with external side effects")

    if not reasons:
        signals = []
    else:
        signals = [
            Signal(
                signal_id="PRIV001_OVERPRIVILEGED_CAPABILITY",
                kind="overprivileged_capability",
                severity=3,
                confidence=0.7,
                file_path=skill_file.path if skill_file else script_files[0].path,
                evidence="; ".join(reasons),
                tags=("privilege", "capability", "host_scope"),
            )
        ]

    signals.extend(_parent_relative_import_signals(features))
    return signals


def _top_level_skill(features: FeatureSet) -> TextFile | None:
    return next((text_file for text_file in features.package.files if text_file.path == "SKILL.md"), None)


def _script_files_with_operations(features: FeatureSet) -> list[TextFile]:
    return [
        text_file
        for text_file in features.package.files
        if _is_script_path(text_file.path) and SCRIPT_OPERATION_RE.search(text_file.content)
    ]


def _is_script_path(path: str) -> bool:
    lower = path.lower()
    return lower.startswith(SCRIPT_PATH_PREFIXES) or lower.endswith(SCRIPT_SUFFIXES)


def _has_broad_declared_capability(skill_text: str) -> bool:
    if not TOOL_DECLARATION_RE.search(skill_text):
        return False
    powerful_tools = {match.group(0).lower().split("(", 1)[0] for match in POWERFUL_TOOL_RE.finditer(skill_text)}
    return (
        len(powerful_tools) >= 3
        and INFRA_SCOPE_RE.search(skill_text) is not None
        and BROAD_SCOPE_RE.search(skill_text) is not None
    )


def _has_high_risk_declared_capability(skill_text: str) -> bool:
    if not TOOL_DECLARATION_RE.search(skill_text):
        return False
    powerful_tools = {match.group(0).lower().split("(", 1)[0] for match in POWERFUL_TOOL_RE.finditer(skill_text)}
    return len(powerful_tools) >= 3 and HIGH_RISK_DECLARED_SCOPE_RE.search(skill_text) is not None


def _has_decisive_script_operation(script_files: list[TextFile]) -> bool:
    return any(DECISIVE_SCRIPT_RE.search(text_file.content) for text_file in script_files)


def _parent_relative_import_signals(features: FeatureSet) -> list[Signal]:
    signals = []
    for text_file in features.package.files:
        if not _is_script_path(text_file.path):
            continue
        if is_passive_path(text_file.path):
            continue
        match = PARENT_RELATIVE_IMPORT_RE.search(text_file.content)
        if not match:
            continue
        if _looks_like_internal_workspace_helper(text_file.content):
            continue
        # Deep upward imports may cross the skill-package boundary and invoke internal host-project capabilities.
        line_number = _line_number(text_file.content, match.start())
        signals.append(
            Signal(
                signal_id="PRIV002_PARENT_RELATIVE_IMPORT",
                kind="overprivileged_capability",
                severity=3,
                confidence=0.7,
                file_path=text_file.path,
                evidence="handler imports modules from outside the skill package boundary",
                tags=("privilege", "package_boundary"),
                line_number=line_number,
                snippet=_line_snippet(text_file.content, line_number),
            )
        )
    return signals


def _line_number(content: str, index: int) -> int:
    return content.count("\n", 0, index) + 1


def _line_snippet(content: str, line_number: int) -> str:
    lines = content.splitlines()
    if not 1 <= line_number <= len(lines):
        return ""
    return lines[line_number - 1].strip()[:200]


def _looks_like_internal_workspace_helper(content: str) -> bool:
    lower = content[:1200].lower()
    helper_markers = (
        "logger",
        "utils/",
        "helper",
        "helpers",
        "workspace",
        "project",
        "monorepo",
    )
    return sum(1 for marker in helper_markers if marker in lower) >= 2

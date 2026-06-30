"""Detect code execution, remote download-and-execute, and unsafe deserialization signals."""

from __future__ import annotations

import re

from ..models import FeatureSet, Signal

EXECUTION_TOKENS = {"eval(", "exec(", "os.system(", "os.popen(", "subprocess.run(", "subprocess.call(", "subprocess.popen("}
DESERIALIZATION_TOKENS = {"pickle.loads(", "marshal.loads(", "yaml.load(", "!!python/object", "!!python/object/apply"}
# Remote execution is not just about `curl`/`wget`; the key question is whether downloaded content is consumed by an interpreter such as shell/python/node.
REMOTE_EXEC_PIPE_RE = re.compile(r"\|\s*(?:sudo\s+)?(?:bash|sh|zsh|powershell|pwsh|python3?|node|eval|exec)\b")
DOWNLOAD_TO_SCRIPT_RE = re.compile(
    r"\b(?:curl|wget)\b[^\n]*(?:"
    r"(?:-o|--output|--output-document)\s+\S+\.(?:sh|bash|zsh|py|js|mjs|cjs)|"
    r">\s*\S+\.(?:sh|bash|zsh|py|js|mjs|cjs)"
    r")"
)
POWERSHELL_REMOTE_EXEC_RE = re.compile(r"\b(?:iex|invoke-expression|downloadstring|downloadfile|frombase64string)\b")
UNSAFE_COMMAND_CONSTRUCTION_RE = re.compile(
    r"(?i)(?:"
    r"\beval\s+['\"]?\$|"
    r"\b(?:bash|sh|zsh)\s+-c\s+['\"]?\$|"
    r"\bexecSync\s*\(\s*`[^`]*\$\{|"
    r"\bchild_process\.exec\s*\(\s*`[^`]*\$\{|"
    r"\bos\.system\s*\(\s*f[\"']|"
    r"\bsubprocess\.[^(]+\([^)\n]{0,220}\bshell\s*=\s*True|"
    r"\b(?:docker\s+exec|ssh|timeout)\b[^\n]{0,180}\b(?:bash|sh)\s+-c\s+[\"']?\$|"
    r"\brun_command\s+[\"']?\\?\$"
    r")"
)


def scan(features: FeatureSet) -> list[Signal]:
    signals: list[Signal] = []

    for hit in features.command_invocations:
        risk = hit.value
        if risk not in {"dangerous", "risky"}:
            continue
        # `command_safety` already removed documentation-only and safe commands; this layer turns the remaining commands into execution signals.
        signals.append(
            Signal(
                signal_id="EX001_COMMAND_INVOCATION",
                kind="code_execution",
                severity=4 if risk == "dangerous" else 3,
                confidence=0.75 if risk == "dangerous" else 0.6,
                file_path=hit.file_path,
                evidence=f"{hit.evidence}{_line(hit)}",
                tags=("execution", "command", risk),
                line_number=hit.line_number,
                snippet=hit.snippet,
            )
        )

    for hit in features.command_tokens:
        token = hit.value.lower()
        if token in EXECUTION_TOKENS or token.startswith(("eval", "exec", "os.system", "os.popen", "subprocess.")):
            # Primitives such as `eval`/`exec`/`subprocess` are language-agnostic indicators of execution capability.
            signals.append(
                Signal(
                    signal_id="EX002_CODE_EXECUTION_PRIMITIVE",
                    kind="code_execution",
                    severity=4,
                    confidence=0.7,
                    file_path=hit.file_path,
                    evidence=f"execution primitive `{hit.value}` observed{_line(hit)}",
                    tags=("execution",),
                    line_number=hit.line_number,
                    snippet=hit.snippet,
                )
            )
        if token in DESERIALIZATION_TOKENS or token.startswith(("pickle.loads", "marshal.loads", "yaml.load")):
            # Unsafe deserialization deserves its own signal so it is not reduced to generic code execution.
            signals.append(
                Signal(
                    signal_id="EX003_UNSAFE_DESERIALIZATION",
                    kind="unsafe_deserialization",
                    severity=4,
                    confidence=0.75,
                    file_path=hit.file_path,
                    evidence=f"unsafe deserialization token `{hit.value}` observed{_line(hit)}",
                    tags=("deserialization", "load_time"),
                    line_number=hit.line_number,
                    snippet=hit.snippet,
                )
            )

    for hit in features.command_invocations:
        lower = hit.matched_text.lower()
        if ("curl" in lower or "wget" in lower) and ("| bash" in lower or "| sh" in lower or " bash -c" in lower or " sh -c" in lower):
            # An explicit download-to-shell pipeline is a strong remote-execution signal.
            signals.append(
                Signal(
                    signal_id="EX004_REMOTE_CODE_EXECUTION_PIPE",
                    kind="remote_code_execution",
                    severity=5,
                    confidence=0.9,
                    file_path=hit.file_path,
                    evidence=f"download-to-shell command shape observed{_line(hit)}",
                    tags=("execution", "remote", "download_pipe"),
                    line_number=hit.line_number,
                    snippet=hit.snippet,
                )
            )

    command_files = {
        hit.file_path
        for hit in features.command_invocations
        if hit.value in {"dangerous", "risky"} and _remote_command_anchor(hit.matched_text)
    }
    network_files = {hit.file_path for hit in features.network_calls + features.urls}
    for file_path in sorted(command_files & network_files):
        # Some samples separate URLs and dangerous commands within the same file; this combines them at file scope.
        signals.append(
            Signal(
                signal_id="EX005_REMOTE_CODE_EXECUTION_COMBO",
                kind="remote_code_execution",
                severity=5,
                confidence=0.75,
                file_path=file_path,
                evidence="same file contains network operation/destination plus risky command execution",
                tags=("execution", "remote"),
            )
        )

    for text_file in features.package.files:
        match = UNSAFE_COMMAND_CONSTRUCTION_RE.search(text_file.content)
        if not match:
            continue
        # Patterns such as `shell=True` or `execSync(`...${var}`)` lean toward command-injection risk and keep a dedicated signal kind.
        line_number = _line_number(text_file.content, match.start())
        signals.append(
            Signal(
                signal_id="EX006_UNSAFE_COMMAND_CONSTRUCTION",
                kind="unsafe_command_construction",
                severity=4,
                confidence=0.8,
                file_path=text_file.path,
                evidence="command string construction is reinterpreted by a shell/eval-like primitive",
                tags=("execution", "command_injection"),
                line_number=line_number,
                snippet=_line_snippet(text_file.content, line_number),
            )
        )

    return signals


def _line(hit) -> str:
    return f" at line {hit.line_number}" if hit.line_number else ""


def _remote_command_anchor(text: str) -> bool:
    lower = text.lower()
    if "powershell" in lower or "pwsh" in lower:
        # PowerShell remote execution commonly appears through keywords such as `IEX`, `DownloadString`, or `FromBase64String`.
        return bool(POWERSHELL_REMOTE_EXEC_RE.search(lower))
    if "curl" not in lower and "wget" not in lower:
        return False
    return bool(REMOTE_EXEC_PIPE_RE.search(lower) or DOWNLOAD_TO_SCRIPT_RE.search(lower))


def _line_number(content: str, index: int) -> int:
    return content.count("\n", 0, index) + 1


def _line_snippet(content: str, line_number: int) -> str:
    lines = content.splitlines()
    if not 1 <= line_number <= len(lines):
        return ""
    return lines[line_number - 1].strip()[:200]

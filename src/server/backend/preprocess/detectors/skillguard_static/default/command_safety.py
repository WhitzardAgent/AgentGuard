"""Lightweight shell command risk grading.

This module only classifies command shape and does not emit `Signal`s directly. `features.py` extracts candidate commands first, then uses this module to
grade them as safe/caution/risky/dangerous before detectors decide whether to emit risk signals.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

SAFE_COMMANDS = frozenset(
    {
        "cat",
        "head",
        "tail",
        "wc",
        "ls",
        "grep",
        "rg",
        "find",
        "echo",
        "printf",
        "pwd",
        "whoami",
        "id",
        "date",
    }
)

CAUTION_COMMANDS = frozenset({"cp", "mv", "chmod", "chown", "git", "npm", "pip", "pip3", "python", "python3", "node"})
RISKY_COMMANDS = frozenset({"rm", "dd", "ssh", "scp", "rsync", "docker", "podman", "kubectl", "nc", "ncat", "netcat", "socat"})
DANGEROUS_COMMANDS = frozenset({"curl", "wget", "bash", "sh", "zsh", "powershell", "pwsh", "eval", "exec", "base64", "openssl"})
SAFE_PIPE_CONSUMERS = frozenset({"grep", "rg", "jq", "sed", "awk", "head", "tail", "wc", "cut", "tr", "sort", "uniq", "html2markdown"})

# These shapes matter more than a single command name: `curl` alone may only fetch text, while `curl | bash` is remote execution.
DANGEROUS_ARG_PATTERNS = (
    re.compile(r"\|\s*(?:bash|sh|zsh|powershell|pwsh|python|python3|node|eval|exec)\b"),
    re.compile(r"\b(?:bash|sh|zsh)\s+-c\b"),
    re.compile(r"\bpython[23]?\s+.*-c\s"),
    re.compile(r"\bnode\s+.*(?:-e|--eval)\s"),
    re.compile(r"\bfind\s+.*-exec(?:dir)?\s"),
    re.compile(r"/dev/tcp/"),
    re.compile(r">\s*/dev/null\s*2>&1\s*&"),
)
OUTPUT_ARG_RE = re.compile(r"\s(?:-o|--output|--output-document)\s+\S+")
REDIRECT_RE = re.compile(r"(?:^|\s)>\s*\S")


@dataclass(frozen=True)
class CommandVerdict:
    risk: str
    reason: str


def evaluate_command(raw_command: str) -> CommandVerdict:
    command = raw_command.strip()
    if not command:
        return CommandVerdict("safe", "empty command")

    lower = command.lower()
    if _looks_like_displayed_command(lower):
        return CommandVerdict("safe", "displayed example command, not executed")
    for pattern in DANGEROUS_ARG_PATTERNS:
        if pattern.search(lower):
            return CommandVerdict("dangerous", f"dangerous command shape: {pattern.pattern}")

    base = _base_command(lower)
    if base in DANGEROUS_COMMANDS:
        if base in {"bash", "sh", "zsh"} and _shell_runs_local_script(lower):
            # Running a local script is common in benign skills; without remote input, redirects, or pipes, downgrade it to `caution`.
            return CommandVerdict("caution", f"shell runs local script with `{base}`")
        if base in {"curl", "wget"} and _pipes_only_to_safe_consumers(lower):
            # `curl | grep/jq` style text processing is not equivalent to download-and-execute.
            return CommandVerdict("caution", f"network command `{base}` piped only to text processors")
        if base in {"curl", "wget"} and ("|" not in lower and not REDIRECT_RE.search(lower) and not OUTPUT_ARG_RE.search(lower)):
            # A plain URL fetch should not carry the same weight as writing to disk or piping into execution.
            return CommandVerdict("caution", f"network command `{base}` without execution or file output")
        if base == "base64" and "|" not in lower:
            return CommandVerdict("caution", "base64 without pipeline")
        return CommandVerdict("dangerous", f"dangerous command `{base}`")

    if base in RISKY_COMMANDS:
        return CommandVerdict("risky", f"risky command `{base}`")

    if base in CAUTION_COMMANDS:
        if "|" in lower or "$(" in lower or "`" in lower:
            return CommandVerdict("risky", f"context-dependent command `{base}` with pipeline/subshell")
        if base == "find" and not re.search(r"-(?:exec|execdir|delete)\b", lower):
            return CommandVerdict("safe", "find without exec/delete")
        return CommandVerdict("caution", f"context-dependent command `{base}`")

    if base in SAFE_COMMANDS:
        if re.search(r"\|\s*(?:curl|wget|bash|sh|zsh|nc|ncat|netcat|socat)\b", lower):
            return CommandVerdict("dangerous", f"safe command `{base}` piped to risky command")
        return CommandVerdict("safe", f"safe command `{base}`")

    return CommandVerdict("caution", f"unknown command `{base}`")


def _base_command(command: str) -> str:
    """Extract the effective command name while skipping sudo/env/timeout and VAR=value prefixes."""

    tokens = command.split()
    prefixes = {"sudo", "su", "doas", "env", "nohup", "time", "timeout"}
    for token in tokens:
        if "=" in token:
            continue
        base = token.rsplit("/", 1)[-1]
        if base in prefixes:
            continue
        return base
    return tokens[0].rsplit("/", 1)[-1] if tokens else ""


def _shell_runs_local_script(command: str) -> bool:
    """Check whether bash/sh/zsh is only executing a local script file."""

    if any(marker in command for marker in ("|", ">", "$(", "`", "http://", "https://")):
        return False
    tokens = command.split()
    for index, token in enumerate(tokens):
        base = token.rsplit("/", 1)[-1]
        if base not in {"bash", "sh", "zsh"}:
            continue
        if index + 1 >= len(tokens):
            return False
        script = tokens[index + 1]
        if script.startswith("-"):
            return False
        return script.endswith((".sh", ".bash", ".zsh"))
    return False


def _pipes_only_to_safe_consumers(command: str) -> bool:
    """Check whether every command on the right side of a pipe is only doing text processing."""

    if "|" not in command or REDIRECT_RE.search(command) or "$(" in command or "`" in command:
        return False
    consumers = []
    for segment in re.split(r"(?<!\\)\|", command)[1:]:
        token = _base_command(segment.strip())
        if not token:
            return False
        consumers.append(token)
    return bool(consumers) and all(consumer in SAFE_PIPE_CONSUMERS for consumer in consumers)


def _looks_like_displayed_command(command: str) -> bool:
    stripped = command.strip()
    if not stripped:
        return False
    if stripped.startswith(("echo ", "printf ")):
        return True
    return any(
        marker in stripped
        for marker in (
            'echo "',
            "echo '",
            'printf "',
            "printf '",
        )
    )

"""Detect attention/suspicious workflow semantics that are common in benchmark skills."""

from __future__ import annotations

import re

from ..models import FeatureSet, Signal

SUMMARY_TO_REMOTE_RE = re.compile(
    r"(?is)(?:summar(?:y|ize)|总结|概括|要点|current conversation|chat summary|session summary)[^\n]{0,220}"
    r"(?:send|post|upload|notify|通知|发送|推送|webhook|lark|飞书|slack|discord|telegram)"
)
AUTO_ROUTER_RE = re.compile(
    r"(?is)(?:automatically|auto[- ]|智能|自动)[^\n]{0,180}"
    r"(?:dispatch|route|select|load|activate|orchestrat|delegate|分发|路由|调度|加载|激活)"
    r"[^\n]{0,180}(?:agents?|skills?|plugins?|tools?)"
)
HANDOFF_DELEGATION_RE = re.compile(
    r"(?is)(?:handoff|delegate|delegation|dispatch|transfer|移交|委派|分发)[^\n]{0,180}"
    r"(?:gemini|claude|codex|cursor|agent|agents|assistant)"
)
AUTO_INSTALL_UPDATE_RE = re.compile(
    r"(?is)(?:update|upgrade|bump|keep[^\n]{0,40}up[- ]to[- ]date|更新|升级)[^\n]{0,220}"
    r"(?:dependenc|package|packages|依赖|包)[^\n]{0,220}"
    r"(?:auto(?:matic)?(?:ally)?[^\n]{0,80})?"
    r"(?:install|npm install|yarn install|pnpm install|uv sync|安装)"
)


def scan(features: FeatureSet) -> list[Signal]:
    signals: list[Signal] = []
    for text_file in features.package.files:
        if not _is_supported_path(text_file.path):
            continue
        content = text_file.content

        summary_match = SUMMARY_TO_REMOTE_RE.search(content)
        if summary_match:
            line_number = _line_number(content, summary_match.start())
            signals.append(
                Signal(
                    signal_id="ATT001_SUMMARY_TO_REMOTE_CHANNEL",
                    kind="data_exfiltration",
                    severity=4,
                    confidence=0.78,
                    file_path=text_file.path,
                    evidence="skill instructs the agent to summarize current conversation/session content and send it to an external channel",
                    tags=("attention_semantics", "conversation_egress"),
                    line_number=line_number,
                    snippet=_line_snippet(content, line_number),
                )
            )

        router_match = AUTO_ROUTER_RE.search(content)
        if router_match:
            line_number = _line_number(content, router_match.start())
            signals.append(
                Signal(
                    signal_id="ATT002_AUTOMATED_ROUTER_OR_DISPATCH",
                    kind="overprivileged_capability",
                    severity=3,
                    confidence=0.72,
                    file_path=text_file.path,
                    evidence="skill declares automatic routing/dispatch of agents, skills, plugins, or tools",
                    tags=("attention_semantics", "orchestration"),
                    line_number=line_number,
                    snippet=_line_snippet(content, line_number),
                )
            )

        handoff_match = HANDOFF_DELEGATION_RE.search(content)
        if handoff_match:
            line_number = _line_number(content, handoff_match.start())
            signals.append(
                Signal(
                    signal_id="ATT004_CROSS_AGENT_HANDOFF",
                    kind="overprivileged_capability",
                    severity=3,
                    confidence=0.7,
                    file_path=text_file.path,
                    evidence="skill describes cross-agent handoff or delegation to another agent/model surface",
                    tags=("attention_semantics", "delegation"),
                    line_number=line_number,
                    snippet=_line_snippet(content, line_number),
                )
            )

        update_match = AUTO_INSTALL_UPDATE_RE.search(content)
        if update_match:
            line_number = _line_number(content, update_match.start())
            signals.append(
                Signal(
                    signal_id="ATT003_DEPENDENCY_UPDATE_WITH_AUTO_INSTALL",
                    kind="install_time_execution",
                    severity=4,
                    confidence=0.76,
                    file_path=text_file.path,
                    evidence="skill describes dependency updates that automatically trigger package installation/execution",
                    tags=("attention_semantics", "dependency_update"),
                    line_number=line_number,
                    snippet=_line_snippet(content, line_number),
                )
            )
    return _dedupe(signals)


def _is_supported_path(path: str) -> bool:
    return path.lower().endswith((".md", ".txt", ".json", ".yaml", ".yml", ".js", ".ts", ".mjs", ".cjs"))


def _line_number(content: str, index: int) -> int:
    return content.count("\n", 0, index) + 1


def _line_snippet(content: str, line_number: int) -> str:
    lines = content.splitlines()
    if not 1 <= line_number <= len(lines):
        return ""
    return lines[line_number - 1].strip()[:200]


def _dedupe(signals: list[Signal]) -> list[Signal]:
    out: list[Signal] = []
    seen: set[tuple[str, str, int, str]] = set()
    for signal in signals:
        key = (signal.signal_id, signal.file_path, signal.line_number, signal.evidence)
        if key in seen:
            continue
        out.append(signal)
        seen.add(key)
    return out

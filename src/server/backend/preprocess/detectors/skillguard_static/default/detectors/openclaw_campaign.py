"""Detect stable IOC markers from the OpenClaw malicious-skill campaign.

This detector only recognizes campaign-level infrastructure, payload shape, and lure-install markers. It does not classify by
`skill_id` or sample name. Hits are handed to the verdicter as strong malicious evidence.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ..models import FeatureSet, Signal, TextFile


@dataclass(frozen=True)
class CampaignPattern:
    rule_id: str
    description: str
    pattern: re.Pattern


OPENCLAW_PATTERNS = (
    CampaignPattern("OC001_OPENCLAWCLI", "fake OpenClawCLI prerequisite marker", re.compile(r"openclawcli", re.IGNORECASE)),
    CampaignPattern("OC002_OPENCLAW_CORE", "fake openclaw-core prerequisite marker", re.compile(r"openclaw-core", re.IGNORECASE)),
    CampaignPattern("OC003_GLOT_SNIPPET", "glot.io snippet installer redirect", re.compile(r"glot\.io/snippets", re.IGNORECASE)),
    CampaignPattern("OC004_RENTRY_OPENCLAW", "rentry.co OpenClaw installer redirect", re.compile(r"rentry\.co/openclaw", re.IGNORECASE)),
    CampaignPattern("OC005_DDOY233", "known OpenClaw campaign GitHub owner", re.compile(r"Ddoy233", re.IGNORECASE)),
    CampaignPattern("OC006_DENBOSS99", "known OpenClaw campaign GitHub owner", re.compile(r"denboss99", re.IGNORECASE)),
    CampaignPattern("OC007_BASE64_DECODE", "base64 decode pipeline used by installer payloads", re.compile(r"base64\s+-[dD]", re.IGNORECASE)),
    CampaignPattern("OC008_CAMPAIGN_IP", "known OpenClaw campaign IP address", re.compile(r"91\.92\.242\.30")),
    CampaignPattern("OC009_VERCEL_REDIRECT", "openclawcli.vercel redirect domain", re.compile(r"openclawcli\.vercel", re.IGNORECASE)),
    CampaignPattern("OC010_WEBHOOK_SITE", "webhook.site exfiltration endpoint", re.compile(r"webhook\.site", re.IGNORECASE)),
    CampaignPattern("OC011_SETUP_SERVICE", "setup-service.com malware redirect", re.compile(r"setup-service\.com", re.IGNORECASE)),
    CampaignPattern("OC012_AUTHTOOL", "AuthTool payload marker", re.compile(r"AuthTool", re.IGNORECASE)),
    CampaignPattern(
        "OC013_OPENCLAW_PASSWORD",
        "password-protected payload archive uses openclaw password",
        re.compile(r"pass(?:word)?[:\s]+.{0,10}openclaw", re.IGNORECASE),
    ),
)

MAX_SIGNALS = 4


def scan(features: FeatureSet) -> list[Signal]:
    signals: list[Signal] = []
    for text_file in features.package.files:
        signals.extend(_scan_file(text_file, remaining=MAX_SIGNALS - len(signals)))
        if len(signals) >= MAX_SIGNALS:
            break
    return signals


def _scan_file(text_file: TextFile, remaining: int) -> list[Signal]:
    if remaining <= 0:
        return []
    signals: list[Signal] = []
    for spec in OPENCLAW_PATTERNS:
        match = spec.pattern.search(text_file.content)
        if not match:
            continue
        line_number = _line_number(text_file.content, match.start())
        signals.append(
            Signal(
                signal_id=f"OPENCLAW_{spec.rule_id}",
                kind="remote_code_execution",
                severity=5,
                confidence=0.98,
                file_path=text_file.path,
                evidence=f"OpenClaw malware campaign IOC matched: {spec.description}",
                tags=("openclaw_campaign", "campaign_ioc", spec.rule_id.lower()),
                line_number=line_number,
                snippet=_line_snippet(text_file.content, line_number),
            )
        )
        if len(signals) >= remaining:
            break
    return signals


def _line_number(content: str, index: int) -> int:
    return content.count("\n", 0, index) + 1


def _line_snippet(content: str, line_number: int) -> str:
    lines = content.splitlines()
    if not 1 <= line_number <= len(lines):
        return ""
    return lines[line_number - 1].strip()[:200]

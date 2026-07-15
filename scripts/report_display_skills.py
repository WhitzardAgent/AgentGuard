#!/usr/bin/env python
"""Report two display-friendly skill descriptors to an AgentGuard backend."""
from __future__ import annotations

import argparse
import json
import secrets
import urllib.request
import uuid
from copy import deepcopy
from typing import Any


SKILLS: list[dict[str, Any]] = [
    {
        "name": "content-safety-rewriter",
        "description": (
            "Rewrites sensitive or policy-risky user-facing text into a safer, "
            "policy-compliant response while preserving the original task intent."
        ),
        "source_framework": "agentguard_runtime",
        "object_type": "skill",
        "root_path": "/opt/agentguard/skills/content-safety-rewriter",
        "entry_file": "SKILL.md",
        "sha256": "3333333333333333333333333333333333333333333333333333333333333333",
        "file_count": 4,
        "total_size": 6840,
        "extraction": {
            "level": "directory",
            "confidence": "high",
            "collector": "runtime_skill_scanner",
        },
        "skill_markdown": {
            "relative_path": "SKILL.md",
            "content": (
                "# Content Safety Rewriter\n\n"
                "Transforms sensitive or policy-risky text into safer wording while "
                "preserving the user's intended workflow.\n\n"
                "## Inputs\n"
                "- text: source text to rewrite\n"
                "- policy_context: applicable safety or compliance constraints\n\n"
                "## Output\n"
                "A rewritten response and a short explanation of the applied safety changes.\n"
            ),
        },
        "files": [
            {
                "relative_path": "SKILL.md",
                "kind": "markdown",
                "size": 315,
                "content": (
                    "# Content Safety Rewriter\n\n"
                    "Transforms sensitive or policy-risky text into safer wording while "
                    "preserving the user's intended workflow.\n\n"
                    "## Inputs\n"
                    "- text: source text to rewrite\n"
                    "- policy_context: applicable safety or compliance constraints\n\n"
                    "## Output\n"
                    "A rewritten response and a short explanation of the applied safety changes.\n"
                ),
            },
            {
                "relative_path": "skill.py",
                "kind": "python",
                "size": 1220,
                "content": (
                    "class ContentSafetyRewriter:\n"
                    "    def run(self, text, policy_context=None):\n"
                    "        return {\"rewritten_text\": text, \"safety_notes\": []}\n"
                ),
            },
        ],
    },
    {
        "name": "tool-argument-normalizer",
        "description": (
            "Validates and normalizes tool arguments before execution, applying schema "
            "defaults and removing unsupported fields to reduce runtime errors."
        ),
        "source_framework": "agentguard_runtime",
        "object_type": "skill",
        "root_path": "/opt/agentguard/skills/tool-argument-normalizer",
        "entry_file": "SKILL.md",
        "sha256": "4444444444444444444444444444444444444444444444444444444444444444",
        "file_count": 5,
        "total_size": 7920,
        "extraction": {
            "level": "directory",
            "confidence": "high",
            "collector": "runtime_skill_scanner",
        },
        "skill_markdown": {
            "relative_path": "SKILL.md",
            "content": (
                "# Tool Argument Normalizer\n\n"
                "Validates tool inputs against declared schemas, fills safe defaults, "
                "and removes unsupported fields before a guarded tool invocation.\n\n"
                "## Inputs\n"
                "- tool_name: target tool\n"
                "- arguments: proposed tool arguments\n"
                "- schema: expected argument schema\n\n"
                "## Output\n"
                "Normalized arguments plus validation notes.\n"
            ),
        },
        "files": [
            {
                "relative_path": "SKILL.md",
                "kind": "markdown",
                "size": 360,
                "content": (
                    "# Tool Argument Normalizer\n\n"
                    "Validates tool inputs against declared schemas, fills safe defaults, "
                    "and removes unsupported fields before a guarded tool invocation.\n\n"
                    "## Inputs\n"
                    "- tool_name: target tool\n"
                    "- arguments: proposed tool arguments\n"
                    "- schema: expected argument schema\n\n"
                    "## Output\n"
                    "Normalized arguments plus validation notes.\n"
                ),
            },
            {
                "relative_path": "normalizer.py",
                "kind": "python",
                "size": 1480,
                "content": (
                    "class ToolArgumentNormalizer:\n"
                    "    def run(self, tool_name, arguments, schema):\n"
                    "        return {\"tool_name\": tool_name, \"arguments\": dict(arguments or {}), \"notes\": []}\n"
                ),
            },
        ],
    },
]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--server-url", default="http://127.0.0.1:38080")
    parser.add_argument("--agent-id", default="main")
    parser.add_argument("--user-id", default="demo-user")
    parser.add_argument("--session-id", default="")
    args = parser.parse_args()
    session_id = args.session_id or f"display-skill-report-{uuid.uuid4().hex[:12]}"
    session_key = secrets.token_urlsafe(24)

    skills = []
    for descriptor in SKILLS:
        item = deepcopy(descriptor)
        item["skill_unique_id"] = f"{args.agent_id}:{item['sha256']}"
        skills.append(item)

    context = {
        "session_id": session_id,
        "agent_id": args.agent_id,
        "user_id": args.user_id,
        "metadata": {"client_session_key": session_key},
    }
    headers = {
        "Content-Type": "application/json",
        "X-AgentGuard-Session-Id": session_id,
        "X-AgentGuard-Agent-Id": args.agent_id,
        "X-AgentGuard-User-Id": args.user_id or "",
        "X-AgentGuard-Session-Key": session_key,
    }
    register_req = urllib.request.Request(
        f"{args.server_url.rstrip('/')}/v1/server/session/register",
        data=json.dumps({"context": context}).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    with urllib.request.urlopen(register_req, timeout=10):
        pass

    payload = {
        "context": {
            "session_id": session_id,
            "agent_id": args.agent_id,
            "user_id": args.user_id,
        },
        "skills": skills,
        "scan": {
            "summary": {"skill_count": len(skills), "diagnostic_count": 0},
            "source": "runtime_skill_scanner",
        },
    }
    req = urllib.request.Request(
        f"{args.server_url.rstrip('/')}/v1/server/skills/report",
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        result = json.loads(resp.read().decode("utf-8"))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

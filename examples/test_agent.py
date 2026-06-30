"""Register a local test agent with tools and skill resources.

This example is intentionally small and dependency-light so the frontend can be
tested without installing LangChain or OpenClaw. It follows the quick-start
remote-server flow: create an AgentGuard client, register tools, then report
OpenClaw-compatible skill descriptors to the server.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

import _bootstrap  # noqa: F401
from agentguard import AgentGuard
from agentguard.tools.metadata import ToolMetadata

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SKILL_ROOT = ROOT / "examples" / "test_agent_skills"
DEFAULT_SERVER_URL = "http://127.0.0.1:38080"
TEXT_EXTENSIONS = {
    ".cjs",
    ".css",
    ".csv",
    ".html",
    ".ini",
    ".js",
    ".json",
    ".md",
    ".mjs",
    ".py",
    ".sh",
    ".toml",
    ".ts",
    ".txt",
    ".yaml",
    ".yml",
}


def retrieve_doc(doc_id: int) -> str:
    """Retrieve a document by integer id."""
    return f"DOC#{doc_id}: This is a mocked document body."


def send_email_to(doc: str, addr: str) -> str:
    """Send a document to an email address."""
    return f"Email sent to {addr}: {doc}"


def main() -> int:
    args = parse_args()
    server_url = args.server_url.rstrip("/")
    api_key = args.api_key or None

    wait_for_server(server_url, api_key=api_key)
    guard = AgentGuard(
        session_id=args.session_id,
        user_id=args.user_id,
        agent_id=args.agent_id,
        server_url=server_url,
        api_key=api_key,
        policy=args.policy,
        sandbox="noop",
    )

    try:
        register_quickstart_tools(guard)
        skills = scan_skill_roots(args.skill_root)
        if not skills:
            print(f"No skills found under {args.skill_root}", file=sys.stderr)
            return 2

        report_payload = report_skills(
            server_url,
            guard=guard,
            skills=skills,
            skill_root=args.skill_root,
            api_key=api_key,
        )
        registered_skills = report_payload.get("skills") or []
        print(f"Registered agent: {guard.context.agent_id}")
        print("Registered tools: retrieve_doc, send_email_to")
        print(f"Registered skills: {len(registered_skills)}")
        for item in registered_skills:
            print(
                "  - {name} [{skill_id}] files={file_count} bytes={total_size}".format(
                    name=item.get("name") or "<unnamed>",
                    skill_id=item.get("skill_unique_id") or "<missing-id>",
                    file_count=item.get("file_count") or 0,
                    total_size=item.get("total_size") or 0,
                )
            )

        if args.detect:
            skill_ids = [
                str(item.get("skill_unique_id") or "").strip()
                for item in registered_skills
                if str(item.get("skill_unique_id") or "").strip()
            ]
            detect_payload = detect_skills(
                server_url,
                agent_id=guard.context.agent_id,
                skill_unique_ids=skill_ids,
                use_llm=args.use_llm,
                api_key=api_key,
            )
            print("Detection results:")
            for item in detect_payload.get("results") or []:
                result = item.get("detect_result") or {}
                print(
                    "  - {name}: {label} ({reason})".format(
                        name=item.get("name") or item.get("skill_unique_id") or "<unknown>",
                        label=result.get("label") or "none",
                        reason=result.get("reason") or "no reason",
                    )
                )

        print()
        print("Open frontend: http://127.0.0.1:8008/agents.html")
        print(f"Select agent: {guard.context.agent_id}")
        print("Then open the Skills entry to verify skill listing and detection.")
        return 0
    finally:
        guard.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--server-url",
        default=os.environ.get("AGENTGUARD_SERVER_URL", DEFAULT_SERVER_URL),
        help="AgentGuard server URL. Defaults to AGENTGUARD_SERVER_URL or localhost:38080.",
    )
    parser.add_argument(
        "--api-key",
        default=os.environ.get("AGENTGUARD_API_KEY", ""),
        help="Backend API key if the server requires one.",
    )
    parser.add_argument(
        "--agent-id",
        default=os.environ.get("AGENTGUARD_TEST_AGENT_ID", "test-skill-agent"),
        help="Agent id shown in the frontend.",
    )
    parser.add_argument(
        "--session-id",
        default=os.environ.get("AGENTGUARD_TEST_SESSION_ID", "test-skill-agent-session"),
        help="Runtime session id used for server registration.",
    )
    parser.add_argument(
        "--user-id",
        default=os.environ.get("AGENTGUARD_TEST_USER_ID", "test-user"),
        help="User id used for server registration.",
    )
    parser.add_argument(
        "--policy",
        default=os.environ.get("AGENTGUARD_TEST_POLICY", "enterprise_default"),
        help="Policy name passed to the AgentGuard client.",
    )
    parser.add_argument(
        "--skill-root",
        type=Path,
        default=DEFAULT_SKILL_ROOT,
        help="Directory containing one or more skill directories with SKILL.md.",
    )
    parser.add_argument(
        "--detect",
        action="store_true",
        help="Run the backend skill detect API after registration.",
    )
    parser.add_argument(
        "--use-llm",
        action="store_true",
        help="Ask the backend detect API to run optional LLM review.",
    )
    return parser.parse_args()


def register_quickstart_tools(guard: AgentGuard) -> None:
    retrieve_metadata = ToolMetadata.infer(
        retrieve_doc,
        capabilities=["read_document"],
        metadata={
            "boundary": "internal",
            "sensitivity": "medium",
            "integrity": "trusted",
            "tags": ["quickstart", "document"],
        },
    )
    send_metadata = ToolMetadata.infer(
        send_email_to,
        capabilities=["external_send"],
        metadata={
            "boundary": "external",
            "sensitivity": "medium",
            "integrity": "trusted",
            "tags": ["quickstart", "email"],
        },
    )
    guard.register_tool(retrieve_doc, metadata=retrieve_metadata)
    guard.register_tool(send_email_to, metadata=send_metadata)


def wait_for_server(server_url: str, *, api_key: str | None, timeout_s: float = 15.0) -> None:
    deadline = time.time() + timeout_s
    last_error: Exception | None = None
    while time.time() < deadline:
        try:
            get_json(f"{server_url}/v1/backend/health", api_key=api_key, timeout_s=3.0)
            return
        except Exception as exc:
            last_error = exc
            time.sleep(0.5)
    raise SystemExit(f"AgentGuard server is not reachable at {server_url}: {last_error}")


def scan_skill_roots(root: Path) -> list[dict[str, Any]]:
    if not root.exists():
        return []
    skill_dirs = sorted(
        path for path in root.rglob("SKILL.md") if path.is_file()
    )
    return [build_skill_descriptor(path.parent) for path in skill_dirs]


def build_skill_descriptor(skill_dir: Path) -> dict[str, Any]:
    root = skill_dir.resolve()
    files = collect_skill_files(root)
    skill_file = next((item for item in files if item["relative_path"] == "SKILL.md"), None)
    skill_text = str((skill_file or {}).get("content") or "")
    frontmatter, body = parse_frontmatter(skill_text)
    manifest_file = next((item for item in files if item["kind"] == "manifest"), None)
    manifest = parse_json_object(str((manifest_file or {}).get("content") or ""))
    total_size = sum(int(item.get("size") or 0) for item in files)
    sha256 = hash_file_records(files)

    missing = []
    if not any(item["kind"] == "prompt" for item in files):
        missing.append("prompt")
    if not manifest:
        missing.append("manifest")

    skill_markdown = skill_file or None
    return {
        "object_type": "skill",
        "source_framework": "openclaw_compatible",
        "name": frontmatter.get("name") or first_markdown_heading(body) or root.name,
        "description": frontmatter.get("description")
        or first_markdown_paragraph(body)
        or "",
        "root_path": str(root),
        "entry_file": "SKILL.md",
        "sha256": sha256,
        "frontmatter": frontmatter,
        "manifest": manifest,
        "skill_markdown": skill_markdown,
        "files": files,
        "assets": [item for item in files if item["kind"] == "asset"],
        "skipped": [],
        "file_count": len(files),
        "total_size": total_size,
        "extraction": {
            "level": "directory",
            "confidence": "high",
            "missing": missing,
            "truncated": any(item.get("content_omitted") for item in files),
        },
    }


def collect_skill_files(root: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for file_path in sorted(path for path in root.rglob("*") if path.is_file()):
        rel = file_path.relative_to(root).as_posix()
        raw = file_path.read_bytes()
        record: dict[str, Any] = {
            "relative_path": rel,
            "path": str(file_path),
            "kind": classify_file(rel),
            "size": len(raw),
            "sha256": hashlib.sha256(raw).hexdigest(),
        }
        if is_text_file(file_path):
            try:
                record["content"] = raw.decode("utf-8")
                record["binary"] = False
            except UnicodeDecodeError:
                record["binary"] = True
                record["content_omitted"] = "binary"
        else:
            record["binary"] = True
            record["content_omitted"] = "non_text_extension"
        if record["kind"] == "manifest" and isinstance(record.get("content"), str):
            record["parsed_json"] = parse_json_object(record["content"])
        records.append(record)
    return records


def classify_file(relative_path: str) -> str:
    name = Path(relative_path).name.lower()
    normalized = relative_path.replace("\\", "/").lower()
    if name == "skill.md":
        return "skill_markdown"
    if name in {"prompt.md", "prompts.md", "system_prompt.md"} or normalized.startswith("prompts/"):
        return "prompt"
    if name in {"manifest.json", "skill.json", "package.json"}:
        return "manifest"
    if normalized.startswith("assets/"):
        return "asset"
    if normalized.startswith("scripts/") or Path(relative_path).suffix.lower() in {".py", ".sh", ".js"}:
        return "script"
    return "text"


def is_text_file(path: Path) -> bool:
    return path.name == "SKILL.md" or path.suffix.lower() in TEXT_EXTENSIONS


def hash_file_records(files: list[dict[str, Any]]) -> str:
    digest = hashlib.sha256()
    for item in files:
        digest.update(str(item.get("relative_path") or "").encode("utf-8"))
        digest.update(b"\0")
        if isinstance(item.get("content"), str):
            digest.update(item["content"].encode("utf-8", errors="replace"))
        else:
            digest.update(str(item.get("sha256") or "").encode("utf-8"))
        digest.update(b"\0")
    return digest.hexdigest()


def parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    if not text.startswith("---\n"):
        return {}, text
    lines = text.splitlines()
    end_index = None
    for index, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            end_index = index
            break
    if end_index is None:
        return {}, text

    frontmatter: dict[str, str] = {}
    for line in lines[1:end_index]:
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        frontmatter[key.strip()] = value.strip().strip('"').strip("'")
    return frontmatter, "\n".join(lines[end_index + 1:])


def first_markdown_heading(text: str) -> str:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            return stripped.lstrip("#").strip()
    return ""


def first_markdown_paragraph(text: str) -> str:
    for block in text.split("\n\n"):
        stripped = " ".join(line.strip() for line in block.splitlines()).strip()
        if stripped and not stripped.startswith("#"):
            return stripped
    return ""


def parse_json_object(text: str) -> dict[str, Any] | None:
    if not text.strip():
        return None
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def report_skills(
    server_url: str,
    *,
    guard: AgentGuard,
    skills: list[dict[str, Any]],
    skill_root: Path,
    api_key: str | None,
) -> dict[str, Any]:
    payload = {
        "context": guard.context.to_dict(),
        "skills": skills,
        "scan": {
            "source_framework": "openclaw_compatible",
            "summary": {
                "roots": [str(skill_root.resolve())],
                "skill_count": len(skills),
                "diagnostic_count": 0,
            },
            "diagnostics": [],
        },
    }
    return post_json(
        f"{server_url}/v1/server/skills/report",
        payload,
        headers=session_headers(guard, api_key=api_key),
    )


def detect_skills(
    server_url: str,
    *,
    agent_id: str,
    skill_unique_ids: list[str],
    use_llm: bool,
    api_key: str | None,
) -> dict[str, Any]:
    quoted_agent = urllib.parse.quote(agent_id, safe="")
    return post_json(
        f"{server_url}/v1/backend/agents/{quoted_agent}/skills/detect",
        {"skill_unique_ids": skill_unique_ids, "use_llm": use_llm},
        headers=backend_headers(api_key),
        timeout_s=60.0 if use_llm else 15.0,
    )


def session_headers(guard: AgentGuard, *, api_key: str | None) -> dict[str, str]:
    headers = {
        "X-AgentGuard-Session-Id": guard.context.session_id,
        "X-AgentGuard-Agent-Id": guard.context.agent_id,
        "X-AgentGuard-Session-Key": guard.session_key,
    }
    if guard.context.user_id:
        headers["X-AgentGuard-User-Id"] = guard.context.user_id
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    return headers


def backend_headers(api_key: str | None) -> dict[str, str]:
    return {"X-API-Key": api_key} if api_key else {}


def get_json(url: str, *, api_key: str | None, timeout_s: float = 5.0) -> dict[str, Any]:
    request = urllib.request.Request(url, headers=backend_headers(api_key), method="GET")
    with urllib.request.urlopen(request, timeout=timeout_s) as response:
        return json.loads(response.read().decode("utf-8"))


def post_json(
    url: str,
    payload: dict[str, Any],
    *,
    headers: dict[str, str] | None = None,
    timeout_s: float = 10.0,
) -> dict[str, Any]:
    all_headers = {"Content-Type": "application/json", "Accept": "application/json"}
    all_headers.update(headers or {})
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers=all_headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_s) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise SystemExit(f"POST {url} failed with HTTP {exc.code}: {body}") from exc


if __name__ == "__main__":
    raise SystemExit(main())

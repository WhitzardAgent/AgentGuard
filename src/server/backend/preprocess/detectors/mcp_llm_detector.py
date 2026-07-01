"""LLM-only detection for MCP server descriptors."""
from __future__ import annotations

import json
import os
import time
from typing import Any

from backend.console.mcp_record import McpRecord
from backend.llm import LLMClient
from backend.preprocess.detectors.base import BaseDetector, DetectionResult

McpDetectionLabel = str


class MCPLLMDetector(BaseDetector):
    object_type = "mcp"

    def __init__(
        self,
        *,
        llm_client_factory: Any | None = None,
        env: dict[str, Any] | None = None,
    ) -> None:
        self._custom_llm_client_factory = llm_client_factory is not None
        self._llm_client_factory = llm_client_factory or (lambda config: LLMClient(config=config))
        self._env = env if env is not None else os.environ

    def detect(
        self,
        record: McpRecord,
        *,
        llm_config: dict[str, Any] | None = None,
    ) -> DetectionResult:
        prompt = _llm_review_prompt(record)
        config = _llm_reviewer_config(llm_config or {}, self._env)
        started = time.time()
        if not self._custom_llm_client_factory and not _has_llm_config(config, self._env):
            return DetectionResult(
                object_id=record.mcp_unique_id,
                object_type="mcp",
                name=record.name,
                risk_labels=["suspicious"],
                risk_level="medium",
                label="suspicious",
                reason="LLM review was requested but no MCP LLM endpoint/backend is configured.",
                agent_id=record.agent_id,
                user_id=record.user_id,
                session_id=record.session_id,
                metadata={
                    "llm_review": {
                        "skipped": True,
                        "reason": "LLM review was requested but no MCP LLM endpoint/backend is configured.",
                    }
                },
            )

        try:
            completion = self._llm_client_factory(config).complete(
                prompt,
                temperature=0,
                max_tokens=500,
            )
            parsed = _parse_llm_review(completion)
            label = parsed.get("label") or "suspicious"
            reason = str(parsed.get("reason") or completion or "").strip()
            return DetectionResult(
                object_id=record.mcp_unique_id,
                object_type="mcp",
                name=record.name,
                risk_labels=[str(label)],
                risk_level=_risk_level_for_label(str(label)),
                label=str(label),
                reason=reason,
                agent_id=record.agent_id,
                user_id=record.user_id,
                session_id=record.session_id,
                metadata={
                    "llm_review": {
                        "skipped": False,
                        "response": str(completion or ""),
                        "latency_ms": round((time.time() - started) * 1000, 2),
                        "parsed": parsed,
                    }
                },
            )
        except Exception as exc:
            return DetectionResult(
                object_id=record.mcp_unique_id,
                object_type="mcp",
                name=record.name,
                risk_labels=["suspicious"],
                risk_level="medium",
                label="suspicious",
                reason=f"LLM review failed: {exc}",
                agent_id=record.agent_id,
                user_id=record.user_id,
                session_id=record.session_id,
                metadata={
                    "llm_review": {
                        "skipped": False,
                        "error": str(exc),
                        "latency_ms": round((time.time() - started) * 1000, 2),
                    }
                },
            )


def _llm_reviewer_config(llm_config: dict[str, Any], env: dict[str, Any]) -> dict[str, Any]:
    config = dict(llm_config or {})
    env_map = {
        "backend": "AGENTGUARD_MCP_LLM_BACKEND",
        "model": "AGENTGUARD_MCP_LLM_MODEL",
        "base_url": "AGENTGUARD_MCP_LLM_BASE_URL",
        "api_key": "AGENTGUARD_MCP_LLM_API_KEY",
        "timeout_s": "AGENTGUARD_MCP_LLM_TIMEOUT_S",
    }
    for key, env_key in env_map.items():
        if key not in config and env.get(env_key):
            config[key] = env.get(env_key)
    if "base_url" not in config and env.get("OPENAI_API_KEY") and not env.get("OPENAI_BASE_URL"):
        config["base_url"] = "https://api.openai.com/v1"
    return config


def _has_llm_config(config: dict[str, Any], env: dict[str, Any]) -> bool:
    backend = str(config.get("backend") or env.get("AGENTGUARD_LLM_BACKEND") or "").strip().lower()
    if backend in {"heuristic", "offline"}:
        return True
    return bool(
        config.get("base_url")
        or env.get("AGENTGUARD_MCP_LLM_BASE_URL")
        or env.get("OPENAI_BASE_URL")
    )


def _llm_review_prompt(record: McpRecord) -> str:
    payload = {
        "agent_id": record.agent_id,
        "mcp_unique_id": record.mcp_unique_id,
        "name": record.name,
        "description": record.description,
        "transport": record.transport,
        "remote": record.remote,
        "root_path": record.root_path,
        "entry_file": record.entry_file,
        "url": record.url,
        "tool_count": record.tool_count,
        "file_count": record.file_count,
        "total_size": record.total_size,
        "extraction": record.extraction,
        "mcp_resource": record.mcp_resource.to_dict(),
    }
    return "\n".join(
        [
            "You are AgentGuard's MCP security reviewer.",
            "Review the MCP server descriptor and full extracted MCP resource.",
            "Return compact JSON only: {\"label\":\"benign|suspicious|malicious\",\"reason\":\"...\"}.",
            "Use malicious for clear command execution, secrets exfiltration, destructive behavior, or remote code execution risk.",
            "Use suspicious for remote transport, broad filesystem or network access, privileged command launch, or incomplete source recovery.",
            f"Metadata: {json.dumps(payload, ensure_ascii=True, default=str)}",
            "MCP resource:",
            json.dumps(record.mcp_resource.to_dict(), ensure_ascii=True, default=str),
        ]
    )


def _parse_llm_review(text: Any) -> dict[str, Any]:
    raw = str(text or "").strip()
    payload_text = raw
    if "{" in raw and "}" in raw:
        payload_text = raw[raw.find("{"): raw.rfind("}") + 1]
    try:
        payload = json.loads(payload_text)
        label = _normalize_label(
            payload.get("label") or payload.get("verdict") or payload.get("result")
        )
        reason = str(payload.get("reason") or raw).strip()
        return {"label": label, "reason": reason}
    except Exception:
        lowered = raw.lower()
        if "malicious" in lowered:
            return {"label": "malicious", "reason": raw}
        if "suspicious" in lowered:
            return {"label": "suspicious", "reason": raw}
        if "benign" in lowered or "safe" in lowered:
            return {"label": "benign", "reason": raw}
        return {"reason": raw or "LLM reviewer returned an empty response."}


def _normalize_label(value: Any) -> str:
    normalized = str(value or "").strip().lower()
    if normalized in {"malicious", "confirmed", "deny", "blocked"}:
        return "malicious"
    if normalized in {"suspicious", "warning", "review", "human_check", "inconclusive"}:
        return "suspicious"
    return "benign"


def _risk_level_for_label(label: str) -> str:
    if label == "malicious":
        return "high"
    if label == "suspicious":
        return "medium"
    return "low"

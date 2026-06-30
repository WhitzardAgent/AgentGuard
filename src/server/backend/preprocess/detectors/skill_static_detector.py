"""Static security detection for registered skill resources."""
from __future__ import annotations

import json
import os
import tempfile
import time
from collections.abc import Callable, Iterable
from pathlib import Path, PurePosixPath
from typing import Any, Literal

from backend.console.skill_record import SkillRecord
from backend.llm import LLMClient
from backend.preprocess.detectors.base import DetectionResult

MAX_MATERIALIZED_FILES = 500
MAX_MATERIALIZED_BYTES = 5_000_000
MAX_LLM_CHARS = 24_000
SkillDetectionLabel = Literal["benign", "suspicious", "malicious"]


class SkillStaticDetector:
    """Run rule-based static detection and optional LLM review for a skill."""

    def __init__(
        self,
        *,
        llm_client_factory: Callable[[dict[str, Any]], Any] | None = None,
        env: dict[str, Any] | None = None,
    ) -> None:
        self._custom_llm_client_factory = llm_client_factory is not None
        self._llm_client_factory = llm_client_factory or (lambda config: LLMClient(config=config))
        self._env = env if env is not None else os.environ

    def detect(
        self,
        record: SkillRecord,
        *,
        use_llm: bool = False,
        llm_config: dict[str, Any] | None = None,
    ) -> DetectionResult:
        rule_based = self._rule_based_scan(record)
        label = _normalize_label(rule_based.get("label"))
        reason = str(rule_based.get("reason") or "")
        metadata: dict[str, Any] = {"rule_based": rule_based}

        if use_llm:
            llm_review = self._review_with_llm(record, rule_based, llm_config or {})
            metadata["llm_review"] = llm_review
            if llm_review.get("label") in {"benign", "suspicious", "malicious"}:
                label = llm_review["label"]  # type: ignore[assignment]
                reason = str(llm_review.get("reason") or reason)

        return DetectionResult(
            object_id=record.skill_unique_id,
            object_type="skill",
            name=record.name,
            risk_labels=[label],
            policy_targets=["skill_static_scan", "skill_run"],
            risk_level=_risk_level_for_label(label),
            label=label,
            reason=reason,
            agent_id=record.agent_id,
            user_id=record.user_id,
            session_id=record.session_id,
            skill_unique_id=record.skill_unique_id,
            metadata=metadata,
        )

    def _rule_based_scan(self, record: SkillRecord) -> dict[str, Any]:
        return self._try_vendored_skillguard_scan(record)

    def _try_vendored_skillguard_scan(self, record: SkillRecord) -> dict[str, Any]:
        started = time.time()
        resource = record.skill_resource.to_dict()
        with tempfile.TemporaryDirectory(prefix="agentguard-skill-scan-") as temp:
            temp_root = Path(temp)
            skill_root = temp_root / "skill"
            skill_root.mkdir(parents=True, exist_ok=True)
            materialized = _materialize_skill_resource(resource, skill_root)
            if materialized["file_count"] <= 0:
                return {
                    "source": "agentguard.skillguard_static",
                    "status": "skipped",
                    "label": "benign",
                    "reason": "No text files were available in skill_resource for SkillGuard scanning.",
                    "materialized": materialized,
                }
            try:
                from backend.preprocess.detectors.skillguard_static import scan_skill_path

                result = scan_skill_path(skill_root)
            except Exception as exc:
                return {
                    "source": "agentguard.skillguard_static",
                    "status": "failed",
                    "label": "suspicious",
                    "reason": f"Vendored SkillGuard rule-based scan failed: {exc}",
                    "error": str(exc),
                    "materialized": materialized,
                }

        parsed_summary = _json_safe(getattr(result, "parsed_summary", {}) or {})
        label = _normalize_label(getattr(result, "verdict", ""))
        status_value = _enum_value(getattr(result, "status", ""))
        raw_output = str(getattr(result, "raw_output", "") or "")
        reason = raw_output or _summary_reason(parsed_summary) or "Vendored SkillGuard rule-based scan completed."
        return {
            "source": "agentguard.skillguard_static",
            "status": status_value or "success",
            "label": label,
            "reason": reason,
            "scanner_name": str(
                getattr(result, "scanner_name", "agentguard.skillguard_static")
                or "agentguard.skillguard_static"
            ),
            "finding_count": int(getattr(result, "finding_count", 0) or 0),
            "verdict": str(getattr(result, "verdict", "") or ""),
            "category": str(getattr(result, "category", "") or ""),
            "confidence": float(getattr(result, "confidence", 0.0) or 0.0),
            "parsed_summary": parsed_summary,
            "materialized": materialized,
            "latency_ms": round((time.time() - started) * 1000, 2),
        }

    def _review_with_llm(
        self,
        record: SkillRecord,
        rule_based: dict[str, Any],
        llm_config: dict[str, Any],
    ) -> dict[str, Any]:
        config = _llm_reviewer_config(llm_config, self._env)
        if not self._custom_llm_client_factory and not _has_llm_config(config, self._env):
            return {
                "skipped": True,
                "reason": "LLM review was requested but no LLM endpoint/backend is configured.",
            }

        prompt = _llm_review_prompt(record, rule_based)
        started = time.time()
        try:
            completion = self._llm_client_factory(config).complete(
                prompt,
                temperature=0,
                max_tokens=500,
            )
            parsed = _parse_llm_review(completion)
            parsed.update(
                {
                    "skipped": False,
                    "response": str(completion or ""),
                    "latency_ms": round((time.time() - started) * 1000, 2),
                }
            )
            return parsed
        except Exception as exc:
            return {
                "skipped": False,
                "error": str(exc),
                "reason": f"LLM review failed: {exc}",
                "latency_ms": round((time.time() - started) * 1000, 2),
            }


def _materialize_skill_resource(resource: dict[str, Any], root: Path) -> dict[str, Any]:
    file_count = 0
    byte_count = 0
    skipped: list[dict[str, Any]] = []
    seen: set[str] = set()

    for item in _iter_text_files(resource):
        relative_path = item["relative_path"]
        if relative_path in seen:
            skipped.append({"relative_path": relative_path, "reason": "duplicate"})
            continue
        if file_count >= MAX_MATERIALIZED_FILES:
            skipped.append({"relative_path": relative_path, "reason": "max_files_exceeded"})
            continue
        content = item["content"]
        encoded = content.encode("utf-8", errors="replace")
        if byte_count + len(encoded) > MAX_MATERIALIZED_BYTES:
            skipped.append({"relative_path": relative_path, "reason": "max_total_bytes_exceeded"})
            continue
        target = _safe_output_path(root, relative_path)
        if target is None:
            skipped.append({"relative_path": relative_path, "reason": "unsafe_path"})
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8", errors="replace")
        seen.add(relative_path)
        file_count += 1
        byte_count += len(encoded)

    return {
        "file_count": file_count,
        "byte_count": byte_count,
        "skipped": skipped[:50],
    }


def _iter_text_files(resource: dict[str, Any]) -> Iterable[dict[str, str]]:
    emitted: set[str] = set()
    for raw in resource.get("files") or []:
        if not isinstance(raw, dict):
            continue
        relative_path = _safe_relative_path(raw.get("relative_path") or raw.get("path"))
        content = raw.get("content")
        if relative_path and isinstance(content, str):
            emitted.add(relative_path)
            yield {"relative_path": relative_path, "content": content}

    skill_markdown = resource.get("skill_markdown")
    if isinstance(skill_markdown, dict):
        relative_path = _safe_relative_path(skill_markdown.get("relative_path") or "SKILL.md")
        content = skill_markdown.get("content")
        if relative_path and relative_path not in emitted and isinstance(content, str):
            yield {"relative_path": relative_path, "content": content}


def _safe_relative_path(value: Any) -> str:
    raw = str(value or "").replace("\\", "/").strip()
    if not raw:
        return ""
    path = PurePosixPath(raw)
    if path.is_absolute():
        return ""
    parts = [part for part in path.parts if part not in ("", ".")]
    if not parts or any(part == ".." or ":" in part for part in parts):
        return ""
    return "/".join(parts)


def _safe_output_path(root: Path, relative_path: str) -> Path | None:
    candidate = (root / relative_path).resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError:
        return None
    return candidate


def _llm_reviewer_config(llm_config: dict[str, Any], env: dict[str, Any]) -> dict[str, Any]:
    config = dict(llm_config or {})
    env_map = {
        "backend": "AGENTGUARD_SKILL_LLM_BACKEND",
        "model": "AGENTGUARD_SKILL_LLM_MODEL",
        "base_url": "AGENTGUARD_SKILL_LLM_BASE_URL",
        "api_key": "AGENTGUARD_SKILL_LLM_API_KEY",
        "timeout_s": "AGENTGUARD_SKILL_LLM_TIMEOUT_S",
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
        or env.get("AGENTGUARD_LLM_BASE_URL")
        or env.get("OPENAI_BASE_URL")
    )


def _llm_review_prompt(record: SkillRecord, rule_based: dict[str, Any]) -> str:
    skill_text = _skill_text_for_llm(record.skill_resource.to_dict(), max_chars=MAX_LLM_CHARS)
    payload = {
        "agent_id": record.agent_id,
        "skill_unique_id": record.skill_unique_id,
        "skill_name": record.name,
        "description": record.description,
        "rule_based_result": rule_based,
    }
    return "\n".join(
        [
            "You are AgentGuard's static skill security reviewer.",
            "Review the rule-based result and the full skill content excerpt.",
            "Return compact JSON only: {\"label\":\"benign|suspicious|malicious\",\"reason\":\"...\"}.",
            "Use malicious for clear credential theft, destructive behavior, persistence, reverse shell, or remote code execution.",
            "Use suspicious for risky network, filesystem, prompt-injection, or ambiguous privileged behavior.",
            f"Metadata: {json.dumps(payload, ensure_ascii=True, default=str)}",
            "Skill content:",
            skill_text,
        ]
    )


def _skill_text_for_llm(resource: dict[str, Any], *, max_chars: int) -> str:
    chunks: list[str] = []
    used = 0
    for item in _iter_text_files(resource):
        header = f"\n### FILE: {item['relative_path']}\n"
        content = item["content"]
        remaining = max_chars - used - len(header)
        if remaining <= 0:
            break
        excerpt = content[:remaining]
        chunks.append(header + excerpt)
        used += len(header) + len(excerpt)
    return "".join(chunks) or "(no text content available)"


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


def _normalize_label(value: Any) -> SkillDetectionLabel:
    normalized = str(value or "").strip().lower()
    if normalized in {"malicious", "confirmed", "deny", "blocked"}:
        return "malicious"
    if normalized in {"suspicious", "warning", "review", "human_check", "inconclusive"}:
        return "suspicious"
    return "benign"


def _risk_level_for_label(label: SkillDetectionLabel) -> str:
    if label == "malicious":
        return "high"
    if label == "suspicious":
        return "medium"
    return "low"


def _summary_reason(summary: Any) -> str:
    if not isinstance(summary, dict):
        return ""
    evidence = summary.get("evidence_text")
    if isinstance(evidence, str) and evidence.strip():
        return evidence.strip()
    signals = summary.get("signals")
    if isinstance(signals, list) and signals:
        first = signals[0]
        if isinstance(first, dict):
            return str(first.get("evidence") or "").strip()
    return ""


def _enum_value(value: Any) -> str:
    return str(getattr(value, "value", value) or "")


def _json_safe(value: Any) -> Any:
    try:
        return json.loads(json.dumps(value, ensure_ascii=True, default=str))
    except Exception:
        return str(value)

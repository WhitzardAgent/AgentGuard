"""Shared Qwen3Guard API adapter for server-side LLM safety plugins."""
from __future__ import annotations

import json
import re
import urllib.request
from typing import Any

from shared.schemas.context import RuntimeContext
from shared.schemas.decisions import GuardDecision
from shared.schemas.events import RuntimeEvent

from backend.runtime.plugins.base import BasePlugin, CheckResult

DEFAULT_MODEL = "Qwen3Guard-Gen-8B"


class Qwen3GuardPluginBase(BasePlugin):
    policy_scope = "llm"

    def _messages_for_event(self, event: RuntimeEvent) -> list[dict[str, str]]:
        raise NotImplementedError

    def check(
        self,
        event: RuntimeEvent,
        context: RuntimeContext,
        trajectory_window: list[RuntimeEvent] | None = None,
    ) -> CheckResult:
        messages = self._messages_for_event(event)
        if not messages:
            return CheckResult.empty()

        api_url = str(getattr(self, "api_url", "") or "").strip()
        api_key = str(getattr(self, "api_key", "") or "").strip()
        if not api_url or not api_key:
            return CheckResult(
                is_final=False,
                metadata={"qwen3guard": {"error": "missing_api_config"}},
            )

        try:
            content = self._post_chat_completion(messages)
        except Exception as exc:
            return CheckResult(
                is_final=False,
                metadata={"qwen3guard": {"error": str(exc)}},
            )

        parsed = _parse_qwen3guard_content(content)
        metadata = {
            "qwen3guard": {
                "safety": parsed["safety"],
                "categories": parsed["categories"],
                "raw_content": content,
                "scope": self.policy_scope,
            }
        }
        safety = parsed["safety"]
        if safety == "safe":
            return CheckResult(is_final=False, metadata=metadata)
        if safety == "unsafe":
            signal = "qwen3guard_unsafe"
            return CheckResult(
                decision_candidate=GuardDecision.deny(
                    f"Qwen3Guard classified {self.policy_scope} content as unsafe.",
                    policy_id=f"server:qwen3guard:{self.policy_scope}:unsafe",
                    risk_signals=[signal],
                    metadata=metadata,
                ),
                risk_signals=[signal],
                is_final=True,
                metadata=metadata,
            )

        signal = "qwen3guard_controversial" if safety == "controversial" else "qwen3guard_unknown"
        return CheckResult(
            decision_candidate=GuardDecision.human_check(
                f"Qwen3Guard classified {self.policy_scope} content as {safety}; human review required.",
                policy_id=f"server:qwen3guard:{self.policy_scope}:{safety}",
                risk_signals=[signal],
                metadata=metadata,
            ),
            risk_signals=[signal],
            is_final=True,
            metadata=metadata,
        )

    def _post_chat_completion(self, messages: list[dict[str, str]]) -> str:
        api_url = str(getattr(self, "api_url", "") or "").strip()
        api_key = str(getattr(self, "api_key", "") or "").strip()
        model = str(getattr(self, "model", "") or DEFAULT_MODEL).strip()
        timeout_s = float(getattr(self, "timeout_s", 20.0) or 20.0)
        body = json.dumps({"model": model, "messages": messages}).encode("utf-8")
        request = urllib.request.Request(
            api_url,
            data=body,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
            },
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=timeout_s) as response:
            payload = json.loads(response.read().decode("utf-8"))
        return str(payload["choices"][0]["message"]["content"])


def _parse_qwen3guard_content(content: str) -> dict[str, Any]:
    safety_match = re.search(r"(?im)^Safety\s*:\s*([A-Za-z_-]+)", content or "")
    safety = safety_match.group(1).strip().lower() if safety_match else "unknown"
    categories_match = re.search(r"(?im)^Categories\s*:\s*(.+)$", content or "")
    raw_categories = categories_match.group(1).strip() if categories_match else ""
    categories = [
        item.strip()
        for item in re.split(r"[,;]", raw_categories)
        if item.strip() and item.strip().lower() != "none"
    ]
    if safety not in {"safe", "unsafe", "controversial"}:
        safety = "unknown"
    return {"safety": safety, "categories": categories}

"""Run skills on the server via /v1/skills/run."""
from __future__ import annotations

import urllib.error
import urllib.request
from typing import Any

from agentguard.utils.errors import SkillError
from agentguard.utils.json import safe_dumps, safe_loads


class RemoteSkillRunner:
    def __init__(self, server_url: str | None, *, api_key: str | None = None, timeout_s: float = 10.0) -> None:
        self.server_url = (server_url or "").rstrip("/")
        self.api_key = api_key
        self.timeout_s = timeout_s

    @property
    def enabled(self) -> bool:
        return bool(self.server_url)

    def run(self, skill_name: str, input_data: dict[str, Any]) -> dict[str, Any]:
        if not self.enabled:
            raise SkillError("no server_url configured for remote skills")
        body = safe_dumps({"skill_name": skill_name, "input": input_data}).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        req = urllib.request.Request(
            f"{self.server_url}/v1/skills/run", data=body, headers=headers, method="POST"
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:
                raw = resp.read().decode("utf-8")
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise SkillError(f"remote skill call failed: {exc}") from exc
        return safe_loads(raw, fallback={}) or {}

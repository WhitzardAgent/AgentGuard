"""Run skills on the server via /v1/server/skills/run."""
from __future__ import annotations

import urllib.error
import urllib.request
from typing import Any

from agentguard.utils.errors import SkillError
from agentguard.utils.json import safe_dumps, safe_loads


class RemoteSkillRunner:
    def __init__(
        self,
        server_url: str | None,
        *,
        api_key: str | None = None,
        session_id: str | None = None,
        agent_id: str | None = None,
        user_id: str | None = None,
        session_key: str | None = None,
        user_ticket: str | None = None,
        session_token: str | None = None,
        dpop_proof_factory: Any | None = None,
        use_dpop_auth: bool = False,
        timeout_s: float = 10.0,
    ) -> None:
        self.server_url = (server_url or "").rstrip("/")
        self.api_key = api_key
        self.session_id = session_id
        self.agent_id = agent_id
        self.user_id = user_id
        self.session_key = session_key
        self.user_ticket = user_ticket
        self.session_token = session_token
        self.dpop_proof_factory = dpop_proof_factory
        self.use_dpop_auth = use_dpop_auth
        self.timeout_s = timeout_s

    @property
    def enabled(self) -> bool:
        return bool(self.server_url)

    def run(self, skill_name: str, input_data: dict[str, Any]) -> dict[str, Any]:
        if not self.enabled:
            raise SkillError("no server_url configured for remote skills")
        if not self.use_dpop_auth or not self.session_token or not callable(self.dpop_proof_factory):
            raise SkillError(
                "remote skills require a runtime-auth session_token and DPoP proof factory"
            )
        body = safe_dumps({"skill_name": skill_name, "input": input_data}).encode("utf-8")
        url = f"{self.server_url}/v1/server/skills/run"
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Authorization": f"DPoP {self.session_token}",
            "DPoP": str(self.dpop_proof_factory("POST", url, self.session_token)),
        }
        if self.user_ticket:
            headers["X-AgentGuard-User-Ticket"] = self.user_ticket
        req = urllib.request.Request(
            url, data=body, headers=headers, method="POST"
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:
                raw = resp.read().decode("utf-8")
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise SkillError(f"remote skill call failed: {exc}") from exc
        return safe_loads(raw, fallback={}) or {}

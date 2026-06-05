"""HTTP client to the remote PDP, using only the standard library.

The client is *optional*: when no ``base_url`` is configured it reports itself
as disabled, and the PEP falls back to local evaluation. This keeps the Harness
fully functional offline while still supporting a centralised PDP when present.
"""

from __future__ import annotations

import logging
import urllib.error
import urllib.request

from agentguard.pdp_client.auth import AuthProvider
from agentguard.pdp_client.bridge import from_server_decision, to_server_event
from agentguard.pdp_client.retry import RetryPolicy
from agentguard.pdp_client.schema import PDPRequest, PDPResponse
from agentguard.schemas.context import RuntimeContext
from agentguard.schemas.decision import Decision
from agentguard.schemas.events import RuntimeEvent
from agentguard.utils.json import safe_dumps, safe_loads

log = logging.getLogger("agentguard.pdp")


class PDPUnavailable(RuntimeError):
    """Raised when the PDP cannot be reached after retries."""


class PDPClient:
    def __init__(
        self,
        base_url: str | None = None,
        *,
        api_key: str = "",
        bearer_token: str = "",
        timeout: float = 5.0,
        retry: RetryPolicy | None = None,
        evaluate_path: str = "/v1/evaluate",
        version_path: str = "/rules/version",
    ) -> None:
        self.base_url = base_url.rstrip("/") if base_url else None
        self._auth = AuthProvider(api_key=api_key, bearer_token=bearer_token)
        self._timeout = timeout
        self._retry = retry or RetryPolicy()
        self._evaluate_path = evaluate_path
        self._version_path = version_path

    @property
    def enabled(self) -> bool:
        return self.base_url is not None

    # ── dual-path slow lane: ask the real server PDP ────────────────────
    def decide(self, event: RuntimeEvent, context: RuntimeContext) -> Decision:
        """Escalate one Harness event to the remote PDP and return a Decision.

        Bridges to/from the server-side (v1) schema. Raises
        :class:`PDPUnavailable` on transport failure so the caller can apply its
        fallback policy.
        """
        if not self.enabled:
            raise PDPUnavailable("no PDP base_url configured")
        server_event = to_server_event(event, context)
        body = safe_dumps(server_event.model_dump(mode="json")).encode("utf-8")
        raw = self._retry.run(lambda: self._post(self._evaluate_path, body))
        payload = safe_loads(raw, fallback={}) or {}
        return from_server_decision(payload)

    def policy_version(self) -> dict[str, Any]:
        """Fetch the server's rule-set version/etag (for policy sync)."""
        if not self.enabled:
            raise PDPUnavailable("no PDP base_url configured")
        raw = self._retry.run(lambda: self._get(self._version_path))
        return safe_loads(raw, fallback={}) or {}

    # ── low-level HTTP helpers ──────────────────────────────────────────
    def _post(self, path: str, body: bytes) -> str:
        url = f"{self.base_url}{path}"
        req = urllib.request.Request(url, data=body, method="POST")
        req.add_header("Content-Type", "application/json")
        for key, value in self._auth.headers().items():
            req.add_header(key, value)
        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                return resp.read().decode("utf-8")
        except (urllib.error.URLError, OSError, TimeoutError) as exc:
            raise PDPUnavailable(str(exc)) from exc

    def _get(self, path: str) -> str:
        url = f"{self.base_url}{path}"
        req = urllib.request.Request(url, method="GET")
        for key, value in self._auth.headers().items():
            req.add_header(key, value)
        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                return resp.read().decode("utf-8")
        except (urllib.error.URLError, OSError, TimeoutError) as exc:
            raise PDPUnavailable(str(exc)) from exc

    def evaluate(self, request: PDPRequest) -> PDPResponse:
        if not self.enabled:
            raise PDPUnavailable("no PDP base_url configured")
        url = f"{self.base_url}{self._evaluate_path}"
        body = safe_dumps(request.to_payload()).encode("utf-8")

        def _do_request() -> PDPResponse:
            req = urllib.request.Request(url, data=body, method="POST")
            req.add_header("Content-Type", "application/json")
            for key, value in self._auth.headers().items():
                req.add_header(key, value)
            try:
                with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                    raw = resp.read().decode("utf-8")
            except (urllib.error.URLError, OSError, TimeoutError) as exc:
                raise PDPUnavailable(str(exc)) from exc
            payload = safe_loads(raw, fallback={}) or {}
            return PDPResponse.from_payload(payload)

        try:
            return self._retry.run(_do_request)
        except PDPUnavailable:
            raise
        except Exception as exc:  # noqa: BLE001
            raise PDPUnavailable(str(exc)) from exc

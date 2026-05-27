"""Remote Guard client — sends RuntimeEvent to a standalone AgentGuard Runtime
over HTTP and returns a Decision. Uses only Python stdlib (urllib + json).

Usage (automatic, via Guard):
    guard = Guard(remote_url="http://runtime-host:38080", api_key="secret")

Usage (manual):
    client = RemoteGuardClient("http://localhost:38080", api_key="secret")
    decision = client.evaluate(event)
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from typing import Any

from agentguard.models.decisions import Action, Decision
from agentguard.models.events import RuntimeEvent
from agentguard.models.tool_catalog import ToolCatalogEntry

log = logging.getLogger(__name__)

_FAIL_OPEN_DECISION = Decision(
    action=Action.ALLOW,
    reason="runtime_unreachable_fail_open",
    risk_score=0.0,
)
_FAIL_CLOSED_DECISION = Decision(
    action=Action.DENY,
    reason="runtime_unreachable_fail_closed",
    risk_score=1.0,
)


class RemoteGuardClient:
    """Synchronous HTTP client for the AgentGuard Runtime /v1/evaluate endpoint.

    Parameters
    ----------
    base_url:
        HTTP base URL of the runtime server, e.g. ``http://runtime.internal:38080``.
    api_key:
        Value for the ``X-Api-Key`` header. Leave empty if auth is disabled.
    timeout:
        Per-request timeout in seconds. Default 10 s.
    fail_open:
        If True (default), allow the tool call when the runtime is unreachable.
        Set False for strict fail-closed behaviour.
    """

    def __init__(
        self,
        base_url: str = "http://localhost:38080",
        *,
        api_key: str = "",
        timeout: float = 10.0,
        fail_open: bool = True,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key  = api_key
        self._timeout  = timeout
        self._fail_open = fail_open

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def evaluate(self, event: RuntimeEvent) -> Decision:
        """Submit one event and return the Decision. Blocking.

        The request body is the RuntimeEvent JSON directly (FastAPI body param).
        """
        payload = json.dumps(event.model_dump(mode="json")).encode()
        try:
            resp = self._post("/v1/evaluate", payload)
        except urllib.error.HTTPError as e:
            log.warning("RemoteGuardClient: HTTP %s from %s — %s",
                        e.code, self._base_url, e.reason)
            # A 4xx/5xx from the server means the request was received; treat
            # as evaluation error rather than "unreachable".
            return _FAIL_OPEN_DECISION if self._fail_open else _FAIL_CLOSED_DECISION
        except (urllib.error.URLError, OSError, TimeoutError) as e:
            log.warning("RemoteGuardClient: runtime unreachable (%s) — %s",
                        self._base_url, e)
            return _FAIL_OPEN_DECISION if self._fail_open else _FAIL_CLOSED_DECISION

        try:
            body: dict[str, Any] = json.loads(resp)
            decision_data = body.get("decision") or {}
            decision = Decision.model_validate(decision_data)
            # Prefer the server-resolved client_action when provided
            if "client_action" in decision_data and decision.client_action is None:
                from agentguard.models.decisions import ClientAction as CA
                try:
                    decision = decision.model_copy(
                        update={"client_action": CA(decision_data["client_action"])}
                    )
                except ValueError:
                    pass
            return decision
        except Exception as e:
            log.warning("RemoteGuardClient: bad response (%s)", e)
            return _FAIL_OPEN_DECISION if self._fail_open else _FAIL_CLOSED_DECISION

    def evaluate_batch(self, events: list[RuntimeEvent]) -> list[Decision]:
        """Submit a list of events in a single HTTP round-trip."""
        payload = json.dumps({
            "events": [e.model_dump(mode="json") for e in events]
        }).encode()
        try:
            resp = self._post("/v1/evaluate/batch", payload)
        except (urllib.error.HTTPError, urllib.error.URLError, OSError, TimeoutError) as e:
            log.warning("RemoteGuardClient: batch error (%s)", e)
            fallback = _FAIL_OPEN_DECISION if self._fail_open else _FAIL_CLOSED_DECISION
            return [fallback] * len(events)

        try:
            body: dict[str, Any] = json.loads(resp)
            results = body.get("results", [])
            decisions = []
            for r in results:
                if r.get("ok"):
                    decisions.append(Decision.model_validate(r["decision"]))
                else:
                    fallback = _FAIL_OPEN_DECISION if self._fail_open else _FAIL_CLOSED_DECISION
                    decisions.append(fallback)
            return decisions
        except Exception as e:
            log.warning("RemoteGuardClient: batch parse error (%s)", e)
            fallback = _FAIL_OPEN_DECISION if self._fail_open else _FAIL_CLOSED_DECISION
            return [fallback] * len(events)

    def health(self) -> dict[str, Any]:
        """Check runtime health. Raises on error."""
        try:
            resp = self._get("/health")
            return json.loads(resp)
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def upsert_tool(self, entry: ToolCatalogEntry | dict[str, Any]) -> bool:
        """Register or update one tool definition on the remote runtime."""
        payload_obj = (
            entry.model_dump(mode="json")
            if isinstance(entry, ToolCatalogEntry)
            else dict(entry)
        )
        payload = json.dumps(payload_obj).encode()
        try:
            resp = self._post("/tools", payload)
        except urllib.error.HTTPError as e:
            log.warning(
                "RemoteGuardClient: tool upsert HTTP %s from %s - %s",
                e.code,
                self._base_url,
                e.reason,
            )
            return False
        except (urllib.error.URLError, OSError, TimeoutError) as e:
            log.warning("RemoteGuardClient: tool upsert failed (%s) - %s", self._base_url, e)
            return False

        try:
            body: dict[str, Any] = json.loads(resp)
        except Exception as e:
            log.warning("RemoteGuardClient: bad /tools response (%s)", e)
            return False
        return bool(body.get("ok", False))

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _headers(self) -> dict[str, str]:
        h = {"Content-Type": "application/json", "Accept": "application/json"}
        if self._api_key:
            h["X-Api-Key"] = self._api_key
        return h

    def _post(self, path: str, body: bytes) -> bytes:
        url = self._base_url + path
        req = urllib.request.Request(
            url, data=body, headers=self._headers(), method="POST"
        )
        with urllib.request.urlopen(req, timeout=self._timeout) as r:
            return r.read()

    def _get(self, path: str) -> bytes:
        url = self._base_url + path
        req = urllib.request.Request(url, headers=self._headers(), method="GET")
        with urllib.request.urlopen(req, timeout=self._timeout) as r:
            return r.read()

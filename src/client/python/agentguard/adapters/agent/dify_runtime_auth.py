"""Shared Dify runtime Auth Broker / DPoP client state."""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Any

from agentguard.u_guard.dpop import DPoPKey
from agentguard.u_guard.agent_keys import load_or_create_agent_key
from agentguard.u_guard.remote_client import RemoteGuardClient


@dataclass
class DifyRuntimeAuthState:
    agent_id: str
    cache_key: str
    external_session_id: str | None
    dpop_key: DPoPKey
    session_id: str | None = None
    session_token: str | None = None
    expires_at: int = 0
    canonical_user_id: str | None = None

    def proof(self, method: str, url: str, access_token: str | None = None) -> str:
        return self.dpop_key.proof(method, url, access_token)

    def token_valid(self, *, refresh_window_s: int = 60) -> bool:
        return bool(self.session_token and self.expires_at - int(time.time()) > refresh_window_s)


class DifyRuntimeAuthManager:
    def __init__(self) -> None:
        self._states: dict[tuple[str, str], DifyRuntimeAuthState] = {}
        self._lock = threading.Lock()

    def get(self, agent_id: str, cache_key: str) -> DifyRuntimeAuthState | None:
        with self._lock:
            return self._states.get((agent_id, cache_key))

    def ensure(
        self,
        *,
        server_url: str | None,
        api_key: str | None,
        agent_id: str,
        agent_identity_key_id: str | None,
        external_session_id: str | None,
        account_email: str | None,
        external_user_id: str | None,
        metadata: dict[str, Any],
        timeout_s: float,
        retries: int,
        cache_key: str | None = None,
    ) -> DifyRuntimeAuthState | None:
        if not server_url or not account_email:
            return None
        state = self._state(agent_id, cache_key or external_session_id, external_session_id=external_session_id)
        if state.token_valid():
            return state
        if state.session_token:
            try:
                self._refresh(state, server_url=server_url, api_key=api_key, timeout_s=timeout_s, retries=retries)
                if state.token_valid(refresh_window_s=0):
                    return state
            except Exception:
                state.session_token = None
                state.expires_at = 0
        self._create(
            state,
            server_url=server_url,
            api_key=api_key,
            agent_identity_key_id=agent_identity_key_id,
            account_email=account_email,
            external_user_id=external_user_id,
            metadata=metadata,
            timeout_s=timeout_s,
            retries=retries,
        )
        return state if state.session_token else None

    def _state(
        self,
        agent_id: str,
        cache_key: str | None,
        *,
        external_session_id: str | None,
    ) -> DifyRuntimeAuthState:
        cache_key = _optional_text(cache_key) or f"agentguard-internal:{time.time_ns()}"
        external_session_id = _optional_text(external_session_id)
        with self._lock:
            state = self._states.get((agent_id, cache_key))
            if state is None:
                state = DifyRuntimeAuthState(
                    agent_id=agent_id,
                    cache_key=cache_key,
                    external_session_id=external_session_id,
                    dpop_key=DPoPKey(),
                )
                self._states[(agent_id, cache_key)] = state
            return state

    def _client(
        self,
        state: DifyRuntimeAuthState,
        *,
        server_url: str,
        api_key: str | None,
        timeout_s: float,
        retries: int,
    ) -> RemoteGuardClient:
        return RemoteGuardClient(
            server_url,
            api_key=api_key,
            session_token=state.session_token,
            dpop_proof_factory=state.proof,
            use_dpop_auth=True,
            legacy_identity_headers=False,
            timeout_s=timeout_s,
            retries=retries,
        )

    def _create(
        self,
        state: DifyRuntimeAuthState,
        *,
        server_url: str,
        api_key: str | None,
        agent_identity_key_id: str | None,
        account_email: str,
        external_user_id: str | None,
        metadata: dict[str, Any],
        timeout_s: float,
        retries: int,
    ) -> None:
        client = self._client(
            state,
            server_url=server_url,
            api_key=api_key,
            timeout_s=timeout_s,
            retries=retries,
        )
        body = {
            "provider": "dify",
            "agent_id": state.agent_id,
            "account_email": account_email,
            "external_user_id": external_user_id,
            "metadata": metadata,
        }
        if state.external_session_id:
            body["external_session_id"] = state.external_session_id
        result = client.create_runtime_session(
            body,
            extra_headers_factory=self._agent_identity_headers_factory(
                state,
                agent_identity_key_id=agent_identity_key_id,
            ),
        )
        self._update_state(state, result)

    def _agent_identity_headers_factory(
        self,
        state: DifyRuntimeAuthState,
        *,
        agent_identity_key_id: str | None,
    ):
        key_id = _optional_text(agent_identity_key_id)
        if not key_id:
            return None
        agent_key = load_or_create_agent_key(key_id)

        def _headers(method: str, url: str, body: dict[str, Any]) -> dict[str, str]:
            return {
                "X-AgentGuard-Agent-Proof": agent_key.sign_session_create_proof(
                    agent_id=state.agent_id,
                    method=method,
                    url=url,
                    body=body,
                    dpop_jkt=state.dpop_key.thumbprint,
                )
            }

        return _headers

    def _refresh(
        self,
        state: DifyRuntimeAuthState,
        *,
        server_url: str,
        api_key: str | None,
        timeout_s: float,
        retries: int,
    ) -> None:
        client = self._client(
            state,
            server_url=server_url,
            api_key=api_key,
            timeout_s=timeout_s,
            retries=retries,
        )
        self._update_state(state, client.refresh_runtime_session())

    def _update_state(self, state: DifyRuntimeAuthState, result: dict[str, Any]) -> None:
        state.session_id = _optional_text(result.get("session_id"))
        state.session_token = _optional_text(result.get("session_token"))
        state.canonical_user_id = _optional_text(result.get("user_id"))
        try:
            state.expires_at = int(result.get("expires_at") or 0)
        except (TypeError, ValueError):
            state.expires_at = 0


manager = DifyRuntimeAuthManager()


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None

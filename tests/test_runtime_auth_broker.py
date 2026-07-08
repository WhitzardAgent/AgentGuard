from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import pytest

from agentguard.u_guard.dpop import DPoPKey
from backend.auth.broker import DifyAuthBroker, RuntimeAuthForbidden, RuntimeAuthUnauthorized
from backend.auth.models import RuntimeSession, RuntimeToken
from backend.auth.replay_store import DPoPReplayError
from backend.auth.token_service import RuntimeTokenService
from backend.user.store import ExternalAccountMapping


CREATE_URL = "http://agentguard.test/v1/server/session/create"
REFRESH_URL = "http://agentguard.test/v1/server/session/refresh"
CLOSE_URL = "http://agentguard.test/v1/server/session/close"


class FakeUserStore:
    def __init__(self, *, bound: bool = True) -> None:
        self.bound = bound

    def external_account_by_provider_email(self, *, provider: str, account_email: str):
        if not self.bound:
            return None
        return ExternalAccountMapping(
            id=1,
            user_id=7,
            provider=provider,
            account_email=account_email,
        )


class FakeReplayStore:
    def __init__(self) -> None:
        self.seen: set[tuple[str, str]] = set()

    def remember(self, *, jkt: str, jti: str, ttl_seconds: int = 300) -> None:
        key = (jkt, jti)
        if key in self.seen:
            raise DPoPReplayError("DPoP proof replay detected")
        self.seen.add(key)


class FakeRuntimeSessionStore:
    def __init__(self) -> None:
        self.sessions: dict[str, RuntimeSession] = {}
        self.tokens: dict[str, RuntimeToken] = {}
        self.next_id = 1

    def find_active_external_session(self, *, provider: str, external_session_id: str, agent_id: str):
        for session in self.sessions.values():
            if (
                session.provider == provider
                and session.external_session_id == external_session_id
                and session.agent_id == agent_id
                and session.status == "active"
            ):
                return session
        return None

    def create_session(
        self,
        *,
        agent_id: str,
        user_id: int,
        provider: str,
        external_session_id: str,
        external_account_email: str,
        dpop_jkt: str,
        metadata: dict[str, Any] | None = None,
    ):
        session = RuntimeSession(
            session_id=f"ags_dify_test_{self.next_id}",
            agent_id=agent_id,
            user_id=user_id,
            provider=provider,
            external_session_id=external_session_id,
            external_account_email=external_account_email,
            dpop_jkt=dpop_jkt,
            status="active",
        )
        self.next_id += 1
        self.sessions[session.session_id] = session
        return session

    def get_session(self, session_id: str):
        return self.sessions.get(session_id)

    def touch_session(self, session_id: str) -> None:
        pass

    def close_session(self, session_id: str) -> None:
        session = self.sessions[session_id]
        self.sessions[session_id] = RuntimeSession(
            session_id=session.session_id,
            agent_id=session.agent_id,
            user_id=session.user_id,
            provider=session.provider,
            external_session_id=session.external_session_id,
            external_account_email=session.external_account_email,
            dpop_jkt=session.dpop_jkt,
            status="closed",
        )
        for token in list(self.tokens.values()):
            if token.session_id == session_id:
                self.revoke_token(token.token_jti)

    def create_token(self, *, token_jti: str, session_id: str, expires_at_epoch: int, cnf_jkt: str):
        token = RuntimeToken(
            token_jti=token_jti,
            session_id=session_id,
            expires_at=datetime.fromtimestamp(expires_at_epoch, timezone.utc),
            cnf_jkt=cnf_jkt,
            status="active",
        )
        self.tokens[token_jti] = token
        return token

    def get_token(self, token_jti: str):
        return self.tokens.get(token_jti)

    def revoke_token(self, token_jti: str) -> None:
        token = self.tokens[token_jti]
        self.tokens[token_jti] = RuntimeToken(
            token_jti=token.token_jti,
            session_id=token.session_id,
            expires_at=token.expires_at,
            cnf_jkt=token.cnf_jkt,
            status="revoked",
        )


def _broker(*, bound: bool = True, ttl_seconds: int = 900):
    store = FakeRuntimeSessionStore()
    replay = FakeReplayStore()
    broker = DifyAuthBroker(
        session_store=store,
        replay_store=replay,
        user_store=FakeUserStore(bound=bound),
        token_service=RuntimeTokenService(secret="test-secret", ttl_seconds=ttl_seconds),
    )
    return broker, store


def _create(broker: DifyAuthBroker, key: DPoPKey):
    return broker.create_session(
        provider="dify",
        external_session_id="conversation-1",
        agent_id="dify-agent-chat:app-1",
        account_email="alice@example.com",
        external_user_id="dify-user-1",
        metadata={"app_id": "app-1"},
        dpop_proof=key.proof("POST", CREATE_URL),
        method="POST",
        url=CREATE_URL,
    )


def test_dify_session_create_issues_runtime_token_for_bound_email():
    broker, _ = _broker()
    issue = _create(broker, DPoPKey())

    assert issue.session.session_id.startswith("ags_dify_test_")
    assert issue.session.user_id == 7
    assert issue.session_token


def test_dify_session_create_rejects_unbound_email():
    broker, _ = _broker(bound=False)

    with pytest.raises(RuntimeAuthForbidden):
        _create(broker, DPoPKey())


def test_dify_session_create_is_idempotent_for_same_external_session():
    broker, _ = _broker()
    key = DPoPKey()
    first = _create(broker, key)
    second = broker.create_session(
        provider="dify",
        external_session_id="conversation-1",
        agent_id="dify-agent-chat:app-1",
        account_email="alice@example.com",
        external_user_id="dify-user-1",
        metadata={},
        dpop_proof=key.proof("POST", CREATE_URL),
        method="POST",
        url=CREATE_URL,
    )

    assert second.session.session_id == first.session.session_id
    assert second.session_token != first.session_token


def test_dpop_proof_replay_is_rejected():
    broker, _ = _broker()
    key = DPoPKey()
    proof = key.proof("POST", CREATE_URL)
    payload = {
        "provider": "dify",
        "external_session_id": "conversation-1",
        "agent_id": "dify-agent-chat:app-1",
        "account_email": "alice@example.com",
        "external_user_id": "dify-user-1",
        "metadata": {},
        "dpop_proof": proof,
        "method": "POST",
        "url": CREATE_URL,
    }
    broker.create_session(**payload)
    with pytest.raises(RuntimeAuthUnauthorized):
        broker.create_session(**payload)


def test_dpop_method_mismatch_is_rejected():
    broker, _ = _broker()
    key = DPoPKey()

    with pytest.raises(RuntimeAuthUnauthorized):
        broker.create_session(
            provider="dify",
            external_session_id="conversation-1",
            agent_id="dify-agent-chat:app-1",
            account_email="alice@example.com",
            external_user_id="dify-user-1",
            metadata={},
            dpop_proof=key.proof("GET", CREATE_URL),
            method="POST",
            url=CREATE_URL,
        )


def test_refresh_keeps_session_and_revokes_old_token():
    broker, store = _broker()
    key = DPoPKey()
    issue = _create(broker, key)
    refreshed = broker.refresh_session(
        token=issue.session_token,
        dpop_proof=key.proof("POST", REFRESH_URL, issue.session_token),
        method="POST",
        url=REFRESH_URL,
    )

    assert refreshed.session.session_id == issue.session.session_id
    assert store.get_token(issue.token_jti).status == "revoked"
    assert store.get_token(refreshed.token_jti).status == "active"


def test_close_revokes_runtime_session():
    broker, _ = _broker()
    key = DPoPKey()
    issue = _create(broker, key)
    auth = broker.close_session(
        token=issue.session_token,
        dpop_proof=key.proof("POST", CLOSE_URL, issue.session_token),
        method="POST",
        url=CLOSE_URL,
    )

    assert auth.session_id == issue.session.session_id
    with pytest.raises(RuntimeAuthUnauthorized):
        broker.authenticate_runtime_request(
            token=issue.session_token,
            dpop_proof=key.proof("POST", "http://agentguard.test/v1/server/guard/decide", issue.session_token),
            method="POST",
            url="http://agentguard.test/v1/server/guard/decide",
        )


def test_expired_runtime_token_is_rejected():
    broker, _ = _broker(ttl_seconds=-1)
    key = DPoPKey()
    issue = _create(broker, key)

    with pytest.raises(RuntimeAuthUnauthorized):
        broker.authenticate_runtime_request(
            token=issue.session_token,
            dpop_proof=key.proof("POST", "http://agentguard.test/v1/server/guard/decide", issue.session_token),
            method="POST",
            url="http://agentguard.test/v1/server/guard/decide",
        )

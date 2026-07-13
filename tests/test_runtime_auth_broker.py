from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from agentguard.u_guard.agent_keys import AgentIdentityKey
from agentguard.u_guard.dpop import DPoPKey
from backend.auth.broker import DifyAuthBroker, RuntimeAuthForbidden, RuntimeAuthUnauthorized
from backend.auth.models import RuntimeSession, RuntimeToken
from backend.auth.replay_store import DPoPReplayError
from backend.auth.token_service import RuntimeTokenService
from backend.user.store import ExternalAccountMapping, InvalidUserTicket, UserTicketIdentity


CREATE_URL = "http://agentguard.test/v1/server/session/create"
REFRESH_URL = "http://agentguard.test/v1/server/session/refresh"
CLOSE_URL = "http://agentguard.test/v1/server/session/close"
AGENT_ID = "dify-agent-chat:app-1"
AGENT_KEY = AgentIdentityKey(Ed25519PrivateKey.generate())


class FakeAgentStore:
    def __init__(
        self,
        *,
        active: bool = True,
        bound: bool = True,
        credential_active: bool = True,
    ) -> None:
        self.active = active
        self.bound = bound
        self.credential_active = credential_active
        self.registered: dict[str, Any] = {}
        self.bindings: list[dict[str, Any]] = []

    def get_agent(self, agent_id: str):
        if agent_id in self.registered:
            return self.registered[agent_id]
        if agent_id != AGENT_ID and agent_id != "ag_workflow":
            return None
        return dataclass_record(
            agent_id=agent_id,
            status="active" if self.active else "disabled",
            public_key_jwk=json.dumps(AGENT_KEY.public_jwk, sort_keys=True, separators=(",", ":")),
            public_key_thumbprint=AGENT_KEY.thumbprint,
        )

    def agent_ids_for_user(self, user_id: int) -> set[str]:
        if not self.bound:
            return set()
        return {AGENT_ID, "ag_workflow"}

    def get_active_credential(self, *, agent_id: str, public_key_thumbprint: str):
        if not self.credential_active or public_key_thumbprint != AGENT_KEY.thumbprint:
            return None
        if agent_id != AGENT_ID and agent_id != "ag_workflow":
            return None
        return dataclass_record(
            credential_id="agcred_test",
            agent_id=agent_id,
            public_key_jwk=json.dumps(AGENT_KEY.public_jwk, sort_keys=True, separators=(",", ":")),
            public_key_thumbprint=AGENT_KEY.thumbprint,
            issuer="agentguard-local",
            status="active",
        )

    def register_agent(self, **kwargs):
        agent_id = f"ag_langchain_{len(self.registered) + 1}"
        agent = dataclass_record(
            agent_id=agent_id,
            agent_identity_code=f"agic_{agent_id}",
            provider=kwargs.get("provider"),
            external_agent_id=kwargs.get("external_agent_id"),
            agent_type=kwargs.get("agent_type"),
            status="active",
            public_key_jwk=json.dumps(kwargs.get("public_key_jwk"), sort_keys=True, separators=(",", ":")),
            public_key_thumbprint="langchain-thumbprint",
        )
        credential = dataclass_record(
            credential_id=f"agcred_{agent_id}",
            agent_id=agent_id,
            public_key_jwk=agent.public_key_jwk,
            public_key_thumbprint=agent.public_key_thumbprint,
            status="active",
        )
        self.registered[agent_id] = agent
        return dataclass_record(
            agent=agent,
            credential=credential,
            user_id=None,
            account_email=None,
            user_binding_created=False,
            user_binding_updated=False,
        )

    def bind_agent_to_user(self, **kwargs):
        self.bindings.append(dict(kwargs))
        return True, False


def dataclass_record(**kwargs):
    return type("Record", (), kwargs)()


class FakeUserStore:
    def __init__(self, *, bound: bool = True) -> None:
        self.bound = bound
        self.ticket = UserTicketIdentity(
            user_id=7,
            username="alice",
            ticket_id=99,
            ticket_prefix="agt_fake_ticket",
            expires_at=datetime.now(timezone.utc),
        )
        self.consumed: list[dict[str, str]] = []

    def external_account_by_provider_email(self, *, provider: str, account_email: str):
        if not self.bound:
            return None
        return ExternalAccountMapping(
            id=1,
            user_id=7,
            provider=provider,
            account_email=account_email,
        )

    def resolve_ticket(self, ticket: str | None):
        if ticket != "agt_valid_ticket" or self.consumed:
            raise InvalidUserTicket("invalid or expired user ticket")
        return self.ticket

    def consume_ticket(self, ticket: str | None, *, agent_id: str, session_id: str):
        if ticket != "agt_valid_ticket" or self.consumed:
            raise InvalidUserTicket("user ticket has already been consumed")
        self.consumed.append({"agent_id": agent_id, "session_id": session_id})
        return self.ticket


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
        provider_prefix = str(provider or "dify")
        session = RuntimeSession(
            session_id=f"ags_{provider_prefix}_test_{self.next_id}",
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


def _broker(
    *,
    bound: bool = True,
    agent_bound: bool = True,
    credential_active: bool = True,
    ttl_seconds: int = 900,
):
    store = FakeRuntimeSessionStore()
    replay = FakeReplayStore()
    broker = DifyAuthBroker(
        session_store=store,
        replay_store=replay,
        user_store=FakeUserStore(bound=bound),
        agent_store=FakeAgentStore(bound=agent_bound, credential_active=credential_active),
        token_service=RuntimeTokenService(secret="test-secret", ttl_seconds=ttl_seconds),
    )
    return broker, store


def _body(*, agent_id: str = AGENT_ID, external_session_id: str | None = "conversation-1"):
    body = {
        "provider": "dify",
        "agent_id": agent_id,
        "account_email": "alice@example.com",
        "external_user_id": "dify-user-1",
        "metadata": {"app_id": "app-1"},
    }
    if external_session_id:
        body["external_session_id"] = external_session_id
    return body


def _agent_proof(key: DPoPKey, body: dict[str, Any], *, agent_id: str | None = None, url: str = CREATE_URL):
    return AGENT_KEY.sign_session_create_proof(
        agent_id=agent_id or str(body["agent_id"]),
        method="POST",
        url=url,
        body=body,
        dpop_jkt=key.thumbprint,
    )


def _create(broker: DifyAuthBroker, key: DPoPKey):
    body = _body()
    return broker.create_session(
        **body,
        dpop_proof=key.proof("POST", CREATE_URL),
        agent_proof=_agent_proof(key, body),
        request_body=body,
        method="POST",
        url=CREATE_URL,
    )


def test_dify_session_create_issues_runtime_token_for_bound_email():
    broker, _ = _broker()
    issue = _create(broker, DPoPKey())

    assert issue.session.session_id.startswith("ags_dify_test_")
    assert issue.session.user_id == 7
    assert issue.session_token


def test_langchain_ticket_session_create_issues_dpop_runtime_session():
    session_store = FakeRuntimeSessionStore()
    replay = FakeReplayStore()
    user_store = FakeUserStore()
    agent_store = FakeAgentStore()
    broker = DifyAuthBroker(
        session_store=session_store,
        replay_store=replay,
        user_store=user_store,
        agent_store=agent_store,
        token_service=RuntimeTokenService(secret="test-secret", ttl_seconds=900),
    )
    key = DPoPKey()
    body = {
        "provider": "langchain",
        "user_ticket": "agt_valid_ticket",
        "metadata": {"name": "Ticket demo"},
    }

    issue = broker.create_langchain_ticket_session(
        user_ticket="agt_valid_ticket",
        metadata=body["metadata"],
        dpop_proof=key.proof("POST", CREATE_URL),
        request_body=body,
        method="POST",
        url=CREATE_URL,
    )

    assert issue.session.provider == "langchain"
    assert issue.session.session_id.startswith("ags_langchain_test_")
    assert issue.session.user_id == 7
    assert issue.session.external_account_email is None
    assert issue.session.agent_id in agent_store.registered
    assert agent_store.bindings == [
        {
            "user_id": 7,
            "agent_id": issue.session.agent_id,
            "provider": "langchain",
            "account_email": None,
            "source": "user_ticket",
            "metadata": {
                "name": "Ticket demo",
                "ticket_id": 99,
                "ticket_prefix": "agt_fake_ticket",
                "runtime_auth_provider": "langchain",
                "request_body_provider": "langchain",
            },
        }
    ]
    assert user_store.consumed == [
        {"agent_id": issue.session.agent_id, "session_id": issue.session.session_id}
    ]
    claims = broker.token_service.verify(issue.session_token)
    assert claims["sid"] == issue.session.session_id
    assert claims["sub"] == issue.session.agent_id
    assert claims["uid"] == "7"


def test_langchain_ticket_session_rejects_reused_ticket():
    broker, _ = _broker()
    key = DPoPKey()
    body = {"provider": "langchain", "user_ticket": "agt_valid_ticket", "metadata": {}}

    broker.create_langchain_ticket_session(
        user_ticket="agt_valid_ticket",
        metadata={},
        dpop_proof=key.proof("POST", CREATE_URL),
        request_body=body,
        method="POST",
        url=CREATE_URL,
    )
    with pytest.raises(RuntimeAuthUnauthorized):
        broker.create_langchain_ticket_session(
            user_ticket="agt_valid_ticket",
            metadata={},
            dpop_proof=key.proof("POST", CREATE_URL),
            request_body=body,
            method="POST",
            url=CREATE_URL,
        )


def test_dify_session_create_allows_missing_external_session_id():
    broker, _ = _broker()
    key = DPoPKey()
    body = _body(agent_id="ag_workflow", external_session_id=None)
    issue = broker.create_session(
        **body,
        external_session_id=None,
        dpop_proof=key.proof("POST", CREATE_URL),
        agent_proof=_agent_proof(key, body),
        request_body=body,
        method="POST",
        url=CREATE_URL,
    )

    assert issue.session.external_session_id is None
    claims = broker.token_service.verify(issue.session_token)
    assert "external_session_id" not in claims


def test_dify_session_create_rejects_unbound_email():
    broker, _ = _broker(bound=False)

    with pytest.raises(RuntimeAuthForbidden):
        _create(broker, DPoPKey())


def test_dify_session_create_is_idempotent_for_same_external_session():
    broker, _ = _broker()
    key = DPoPKey()
    first = _create(broker, key)
    body = _body()
    second = broker.create_session(
        **body,
        dpop_proof=key.proof("POST", CREATE_URL),
        agent_proof=_agent_proof(key, body),
        request_body=body,
        method="POST",
        url=CREATE_URL,
    )

    assert second.session.session_id == first.session.session_id
    assert second.session_token != first.session_token


def test_dpop_proof_replay_is_rejected():
    broker, _ = _broker()
    key = DPoPKey()
    body = _body()
    proof = key.proof("POST", CREATE_URL)
    payload = {
        **body,
        "dpop_proof": proof,
        "agent_proof": _agent_proof(key, body),
        "request_body": body,
        "method": "POST",
        "url": CREATE_URL,
    }
    broker.create_session(**payload)
    with pytest.raises(RuntimeAuthUnauthorized):
        broker.create_session(**payload)


def test_dpop_method_mismatch_is_rejected():
    broker, _ = _broker()
    key = DPoPKey()
    body = _body()

    with pytest.raises(RuntimeAuthUnauthorized):
        broker.create_session(
            **body,
            dpop_proof=key.proof("GET", CREATE_URL),
            agent_proof=_agent_proof(key, body),
            request_body=body,
            method="POST",
            url=CREATE_URL,
        )


def test_missing_agent_identity_proof_is_rejected():
    broker, _ = _broker()
    key = DPoPKey()
    body = _body()

    with pytest.raises(RuntimeAuthUnauthorized):
        broker.create_session(
            **body,
            dpop_proof=key.proof("POST", CREATE_URL),
            agent_proof=None,
            request_body=body,
            method="POST",
            url=CREATE_URL,
        )


def test_agent_identity_proof_dpop_binding_mismatch_is_rejected():
    broker, _ = _broker()
    key = DPoPKey()
    other_key = DPoPKey()
    body = _body()

    with pytest.raises(RuntimeAuthUnauthorized):
        broker.create_session(
            **body,
            dpop_proof=key.proof("POST", CREATE_URL),
            agent_proof=_agent_proof(other_key, body),
            request_body=body,
            method="POST",
            url=CREATE_URL,
        )


def test_agent_identity_proof_body_hash_mismatch_is_rejected():
    broker, _ = _broker()
    key = DPoPKey()
    body = _body()
    signed_body = {**body, "account_email": "mallory@example.com"}

    with pytest.raises(RuntimeAuthUnauthorized):
        broker.create_session(
            **body,
            dpop_proof=key.proof("POST", CREATE_URL),
            agent_proof=_agent_proof(key, signed_body),
            request_body=body,
            method="POST",
            url=CREATE_URL,
        )


def test_agent_not_available_to_user_is_rejected():
    broker, _ = _broker(agent_bound=False)
    key = DPoPKey()
    body = _body()

    with pytest.raises(RuntimeAuthForbidden):
        broker.create_session(
            **body,
            dpop_proof=key.proof("POST", CREATE_URL),
            agent_proof=_agent_proof(key, body),
            request_body=body,
            method="POST",
            url=CREATE_URL,
        )


def test_inactive_agent_credential_is_rejected():
    broker, _ = _broker(credential_active=False)
    key = DPoPKey()
    body = _body()

    with pytest.raises(RuntimeAuthForbidden):
        broker.create_session(
            **body,
            dpop_proof=key.proof("POST", CREATE_URL),
            agent_proof=_agent_proof(key, body),
            request_body=body,
            method="POST",
            url=CREATE_URL,
        )


def test_agent_identity_proof_replay_is_rejected():
    broker, _ = _broker()
    key = DPoPKey()
    body = _body()
    agent_proof = _agent_proof(key, body)

    broker.create_session(
        **body,
        dpop_proof=key.proof("POST", CREATE_URL),
        agent_proof=agent_proof,
        request_body=body,
        method="POST",
        url=CREATE_URL,
    )
    with pytest.raises(RuntimeAuthUnauthorized):
        broker.create_session(
            **body,
            dpop_proof=key.proof("POST", CREATE_URL),
            agent_proof=agent_proof,
            request_body=body,
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

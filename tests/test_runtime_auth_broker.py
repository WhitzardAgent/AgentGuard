from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import pytest
from agentguard.u_guard.agent_keys import AgentIdentityKey
from agentguard.u_guard.dpop import DPoPKey
from backend.auth.broker import DifyAuthBroker, RuntimeAuthForbidden, RuntimeAuthUnauthorized
from backend.auth.models import RuntimeSession, RuntimeToken
from backend.auth.replay_store import DPoPReplayError
from backend.auth.token_service import RuntimeTokenService
from backend.user.store import ExternalAccountMapping, InvalidUserTicket, UserTicketIdentity
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

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
        self.credentials: dict[str, Any] = {}
        self.user_agent_ids: set[str] = set()
        self.bindings: list[dict[str, Any]] = []
        self.syncs: list[dict[str, Any]] = []

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
        return {AGENT_ID, "ag_workflow", *self.user_agent_ids}

    def user_ids_for_agent(self, agent_id: str) -> set[int]:
        if not self.bound:
            return set()
        if agent_id in {AGENT_ID, "ag_workflow", *self.user_agent_ids}:
            return {7}
        return set()

    def get_active_credential(self, *, agent_id: str, public_key_thumbprint: str):
        stored = self.credentials.get(agent_id)
        if stored is not None:
            if not self.credential_active or stored.public_key_thumbprint != public_key_thumbprint:
                return None
            return stored
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
        provider = str(kwargs.get("provider") or "langchain")
        external_agent_id = str(kwargs.get("external_agent_id") or len(self.registered) + 1)
        agent_id = f"ag_{provider}_{external_agent_id.replace(':', '_')}"
        thumbprint = (
            (kwargs.get("metadata") or {}).get("agent_public_key_thumbprint")
            or "langchain-thumbprint"
        )
        agent = dataclass_record(
            agent_id=agent_id,
            agent_identity_code=f"agic_{agent_id}",
            provider=provider,
            external_agent_id=external_agent_id,
            agent_type=kwargs.get("agent_type"),
            name=kwargs.get("name"),
            description=kwargs.get("description"),
            metadata=kwargs.get("metadata"),
            status="active",
            public_key_jwk=json.dumps(kwargs.get("public_key_jwk"), sort_keys=True, separators=(",", ":")),
            public_key_thumbprint=thumbprint,
        )
        credential = dataclass_record(
            credential_id=f"agcred_{agent_id}",
            agent_id=agent_id,
            public_key_jwk=agent.public_key_jwk,
            public_key_thumbprint=agent.public_key_thumbprint,
            status="active",
        )
        self.registered[agent_id] = agent
        self.credentials[agent_id] = credential
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
        self.user_agent_ids.add(str(kwargs["agent_id"]))
        return True, False

    def sync_provider_agents(self, **kwargs):
        self.syncs.append(dict(kwargs))
        return {"deactivated_count": 0}


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
            expires_at=datetime.now(UTC),
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

    def find_external_session(self, *, provider: str, external_session_id: str, agent_id: str):
        candidates = [
            session
            for session in self.sessions.values()
            if (
                session.provider == provider
                and session.external_session_id == external_session_id
                and session.agent_id == agent_id
            )
        ]
        candidates.sort(key=lambda session: 0 if session.status == "closed" else 1)
        for session in candidates:
            if (
                session.provider == provider
                and session.external_session_id == external_session_id
                and session.agent_id == agent_id
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

    def close_external_sessions(self, *, provider: str, external_session_id: str, agent_id: str) -> int:
        closed = 0
        for session in list(self.sessions.values()):
            if (
                session.provider == provider
                and session.external_session_id == external_session_id
                and session.agent_id == agent_id
                and session.status == "active"
            ):
                self.close_session(session.session_id)
                closed += 1
        return closed

    def create_token(self, *, token_jti: str, session_id: str, expires_at_epoch: int, cnf_jkt: str):
        token = RuntimeToken(
            token_jti=token_jti,
            session_id=session_id,
            expires_at=datetime.fromtimestamp(expires_at_epoch, UTC),
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


def test_n8n_session_create_uses_bound_email_and_agent_proof():
    broker, _ = _broker()
    key = DPoPKey()
    body = {
        "provider": "n8n",
        "agent_id": "ag_workflow",
        "account_email": "alice@example.com",
        "external_user_id": "n8n-user-1",
        "external_session_id": "execution-1",
        "metadata": {"workflow_id": "wf-1"},
    }

    issue = broker.create_session(
        **body,
        dpop_proof=key.proof("POST", CREATE_URL),
        agent_proof=_agent_proof(key, body),
        request_body=body,
        method="POST",
        url=CREATE_URL,
    )

    assert issue.session.provider == "n8n"
    assert issue.session.session_id.startswith("ags_n8n_test_")
    assert issue.session.agent_id == "ag_workflow"
    assert issue.session.user_id == 7
    assert issue.session.external_session_id == "execution-1"
    assert issue.session.external_account_email == "alice@example.com"


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


def test_openclaw_ticket_session_create_binds_agentguard_user():
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
        "provider": "openclaw",
        "user_ticket": "agt_valid_ticket",
        "metadata": {
            "openclaw_agent_id": "main",
            "openclaw_session_id": "session-1",
            "openclaw_session_key": "agent:main:session-1",
        },
    }

    issue = broker.create_ticket_session(
        provider="openclaw",
        user_ticket="agt_valid_ticket",
        metadata=body["metadata"],
        dpop_proof=key.proof("POST", CREATE_URL),
        request_body=body,
        method="POST",
        url=CREATE_URL,
    )

    assert issue.session.provider == "openclaw"
    assert issue.session.session_id.startswith("ags_openclaw_test_")
    assert issue.session.user_id == 7
    assert issue.session.external_account_email is None
    registered = agent_store.registered[issue.session.agent_id]
    assert registered.provider == "openclaw"
    assert agent_store.bindings == [
        {
            "user_id": 7,
            "agent_id": issue.session.agent_id,
            "provider": "openclaw",
            "account_email": None,
            "source": "user_ticket",
            "metadata": {
                "openclaw_agent_id": "main",
                "openclaw_session_id": "session-1",
                "openclaw_session_key": "agent:main:session-1",
                "ticket_id": 99,
                "ticket_prefix": "agt_fake_ticket",
                "runtime_auth_provider": "openclaw",
                "request_body_provider": "openclaw",
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


def test_opencode_ticket_session_create_binds_agentguard_user():
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
        "provider": "opencode",
        "user_ticket": "agt_valid_ticket",
        "metadata": {
            "opencode_session_id": "session-1",
            "opencode_agent": "agentguard",
            "opencode_directory": "/repo",
        },
    }

    issue = broker.create_ticket_session(
        provider="opencode",
        user_ticket="agt_valid_ticket",
        metadata=body["metadata"],
        dpop_proof=key.proof("POST", CREATE_URL),
        request_body=body,
        method="POST",
        url=CREATE_URL,
    )

    assert issue.session.provider == "opencode"
    assert issue.session.session_id.startswith("ags_opencode_test_")
    assert issue.session.user_id == 7
    assert issue.session.external_account_email is None
    registered = agent_store.registered[issue.session.agent_id]
    assert registered.provider == "opencode"
    assert registered.external_agent_id == "opencode:agentguard"
    assert registered.agent_type == "agent"
    assert registered.name == "OpenCode agentguard"
    assert agent_store.bindings == [
        {
            "user_id": 7,
            "agent_id": issue.session.agent_id,
            "provider": "opencode",
            "account_email": None,
            "source": "user_ticket",
            "metadata": {
                "opencode_session_id": "session-1",
                "opencode_agent": "agentguard",
                "opencode_directory": "/repo",
                "external_agent_id": "opencode:agentguard",
                "display_agent_id": "opencode:agentguard",
                "agent_type": "agent",
                "ticket_id": 99,
                "ticket_prefix": "agt_fake_ticket",
                "runtime_auth_provider": "opencode",
                "request_body_provider": "opencode",
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


def test_openclaw_bootstrap_registers_catalog_and_consumes_ticket_once():
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
    main_key = AgentIdentityKey(Ed25519PrivateKey.generate())
    helper_key = AgentIdentityKey(Ed25519PrivateKey.generate())

    identity, results = broker.bootstrap_openclaw_agents(
        user_ticket="agt_valid_ticket",
        provider_instance_id="local-openclaw",
        agents=[
            {
                "provider": "openclaw",
                "provider_instance_id": "local-openclaw",
                "external_agent_id": "main",
                "agent_type": "agent",
                "name": "main",
                "public_key_jwk": main_key.public_jwk,
                "metadata": {"agent_public_key_thumbprint": main_key.thumbprint},
            },
            {
                "provider": "openclaw",
                "provider_instance_id": "local-openclaw",
                "external_agent_id": "helper",
                "agent_type": "agent",
                "name": "helper",
                "public_key_jwk": helper_key.public_jwk,
                "metadata": {"agent_public_key_thumbprint": helper_key.thumbprint},
            },
        ],
        metadata={"bootstrap": True},
    )

    assert identity.user_id == 7
    assert [item.agent.external_agent_id for item in results] == ["main", "helper"]
    assert {item.agent.agent_id for item in results} <= agent_store.user_agent_ids
    assert len(agent_store.bindings) == 2
    assert agent_store.syncs == [
        {
            "provider": "openclaw",
            "provider_instance_id": "local-openclaw",
            "tenant_id": None,
            "agent_type": "agent",
            "external_agent_ids": ["helper", "main"],
            "metadata": {
                "bootstrap": True,
                "ticket_id": 99,
                "ticket_prefix": "agt_fake_ticket",
                "runtime_auth_provider": "openclaw",
                "sync_source": "openclaw_bootstrap",
            },
        }
    ]
    assert user_store.consumed == [
        {"agent_id": results[0].agent.agent_id, "session_id": "openclaw-bootstrap:local-openclaw"}
    ]


def test_opencode_bootstrap_registers_catalog_and_consumes_ticket_once():
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
    main_key = AgentIdentityKey(Ed25519PrivateKey.generate())
    review_key = AgentIdentityKey(Ed25519PrivateKey.generate())

    identity, results = broker.bootstrap_opencode_agents(
        user_ticket="agt_valid_ticket",
        provider_instance_id="local-opencode",
        agents=[
            {
                "provider": "opencode",
                "provider_instance_id": "local-opencode",
                "external_agent_id": "opencode:agentguard",
                "agent_type": "agent",
                "name": "OpenCode agentguard",
                "public_key_jwk": main_key.public_jwk,
                "metadata": {"agent_public_key_thumbprint": main_key.thumbprint},
            },
            {
                "provider": "opencode",
                "provider_instance_id": "local-opencode",
                "external_agent_id": "opencode:reviewer",
                "agent_type": "agent",
                "name": "OpenCode reviewer",
                "public_key_jwk": review_key.public_jwk,
                "metadata": {"agent_public_key_thumbprint": review_key.thumbprint},
            },
        ],
        metadata={"bootstrap": True},
    )

    assert identity.user_id == 7
    assert [item.agent.external_agent_id for item in results] == ["opencode:agentguard", "opencode:reviewer"]
    assert {item.agent.agent_id for item in results} <= agent_store.user_agent_ids
    assert len(agent_store.bindings) == 2
    assert agent_store.syncs == [
        {
            "provider": "opencode",
            "provider_instance_id": "local-opencode",
            "tenant_id": None,
            "agent_type": "agent",
            "external_agent_ids": ["opencode:agentguard", "opencode:reviewer"],
            "metadata": {
                "bootstrap": True,
                "ticket_id": 99,
                "ticket_prefix": "agt_fake_ticket",
                "runtime_auth_provider": "opencode",
                "sync_source": "opencode_bootstrap",
            },
        }
    ]
    assert user_store.consumed == [
        {"agent_id": results[0].agent.agent_id, "session_id": "opencode-bootstrap:local-opencode"}
    ]


def test_openclaw_runtime_session_uses_canonical_agent_and_external_session():
    broker, store = _broker()
    agent_store = broker.agent_store
    user_store = broker.user_store
    agent_key = AgentIdentityKey(Ed25519PrivateKey.generate())
    _, bootstrap = broker.bootstrap_openclaw_agents(
        user_ticket="agt_valid_ticket",
        provider_instance_id="local-openclaw",
        agents=[
            {
                "provider": "openclaw",
                "provider_instance_id": "local-openclaw",
                "external_agent_id": "main",
                "agent_type": "agent",
                "name": "main",
                "public_key_jwk": agent_key.public_jwk,
                "metadata": {"agent_public_key_thumbprint": agent_key.thumbprint},
            }
        ],
    )
    canonical_agent_id = bootstrap[0].agent.agent_id
    key = DPoPKey()
    body = {
        "provider": "openclaw",
        "agent_id": canonical_agent_id,
        "external_session_id": "agent:main:session-1",
        "external_user_id": "openclaw-user",
        "metadata": {
            "openclaw_agent_id": "main",
            "openclaw_session_key": "agent:main:session-1",
        },
    }
    proof = agent_key.sign_session_create_proof(
        agent_id=canonical_agent_id,
        method="POST",
        url=CREATE_URL,
        body=body,
        dpop_jkt=key.thumbprint,
    )

    issue = broker.create_openclaw_session(
        external_session_id=body["external_session_id"],
        agent_id=canonical_agent_id,
        external_user_id=body["external_user_id"],
        metadata=body["metadata"],
        dpop_proof=key.proof("POST", CREATE_URL),
        agent_proof=proof,
        request_body=body,
        method="POST",
        url=CREATE_URL,
    )

    assert issue.session.provider == "openclaw"
    assert issue.session.agent_id == canonical_agent_id
    assert issue.session.user_id == 7
    assert issue.session.external_session_id == "agent:main:session-1"
    assert issue.session.external_account_email is None
    assert canonical_agent_id in agent_store.user_agent_ids
    assert user_store.consumed
    claims = broker.token_service.verify(issue.session_token)
    assert claims["sub"] == canonical_agent_id

    second_key = DPoPKey()
    second_body = {**body, "external_session_id": "agent:main:session-2"}
    second = broker.create_openclaw_session(
        external_session_id=second_body["external_session_id"],
        agent_id=canonical_agent_id,
        external_user_id=second_body["external_user_id"],
        metadata=second_body["metadata"],
        dpop_proof=second_key.proof("POST", CREATE_URL),
        agent_proof=agent_key.sign_session_create_proof(
            agent_id=canonical_agent_id,
            method="POST",
            url=CREATE_URL,
            body=second_body,
            dpop_jkt=second_key.thumbprint,
        ),
        request_body=second_body,
        method="POST",
        url=CREATE_URL,
    )

    assert second.session.agent_id == canonical_agent_id
    assert second.session.session_id != issue.session.session_id
    assert len(store.sessions) == 2


def test_opencode_runtime_session_uses_canonical_agent_and_external_session():
    broker, store = _broker()
    agent_store = broker.agent_store
    user_store = broker.user_store
    agent_key = AgentIdentityKey(Ed25519PrivateKey.generate())
    _, bootstrap = broker.bootstrap_opencode_agents(
        user_ticket="agt_valid_ticket",
        provider_instance_id="local-opencode",
        agents=[
            {
                "provider": "opencode",
                "provider_instance_id": "local-opencode",
                "external_agent_id": "opencode:agentguard",
                "agent_type": "agent",
                "name": "OpenCode agentguard",
                "public_key_jwk": agent_key.public_jwk,
                "metadata": {"agent_public_key_thumbprint": agent_key.thumbprint},
            }
        ],
    )
    canonical_agent_id = bootstrap[0].agent.agent_id
    key = DPoPKey()
    body = {
        "provider": "opencode",
        "agent_id": canonical_agent_id,
        "external_session_id": "session-1",
        "external_user_id": "opencode-user",
        "metadata": {
            "opencode_agent": "agentguard",
            "opencode_session_id": "session-1",
        },
    }
    proof = agent_key.sign_session_create_proof(
        agent_id=canonical_agent_id,
        method="POST",
        url=CREATE_URL,
        body=body,
        dpop_jkt=key.thumbprint,
    )

    issue = broker.create_opencode_session(
        external_session_id=body["external_session_id"],
        agent_id=canonical_agent_id,
        external_user_id=body["external_user_id"],
        metadata=body["metadata"],
        dpop_proof=key.proof("POST", CREATE_URL),
        agent_proof=proof,
        request_body=body,
        method="POST",
        url=CREATE_URL,
    )

    assert issue.session.provider == "opencode"
    assert issue.session.agent_id == canonical_agent_id
    assert issue.session.user_id == 7
    assert issue.session.external_session_id == "session-1"
    assert issue.session.external_account_email is None
    assert canonical_agent_id in agent_store.user_agent_ids
    assert user_store.consumed
    claims = broker.token_service.verify(issue.session_token)
    assert claims["sub"] == canonical_agent_id

    second_key = DPoPKey()
    second_body = {**body, "external_session_id": "session-2"}
    second = broker.create_opencode_session(
        external_session_id=second_body["external_session_id"],
        agent_id=canonical_agent_id,
        external_user_id=second_body["external_user_id"],
        metadata=second_body["metadata"],
        dpop_proof=second_key.proof("POST", CREATE_URL),
        agent_proof=agent_key.sign_session_create_proof(
            agent_id=canonical_agent_id,
            method="POST",
            url=CREATE_URL,
            body=second_body,
            dpop_jkt=second_key.thumbprint,
        ),
        request_body=second_body,
        method="POST",
        url=CREATE_URL,
    )

    assert second.session.agent_id == canonical_agent_id
    assert second.session.session_id != issue.session.session_id
    assert len(store.sessions) == 2


def test_openclaw_external_session_cannot_recreate_after_close():
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
    agent_key = AgentIdentityKey(Ed25519PrivateKey.generate())
    _, bootstrap = broker.bootstrap_openclaw_agents(
        user_ticket="agt_valid_ticket",
        provider_instance_id="local-openclaw",
        agents=[
            {
                "provider": "openclaw",
                "provider_instance_id": "local-openclaw",
                "external_agent_id": "main",
                "agent_type": "agent",
                "name": "main",
                "public_key_jwk": agent_key.public_jwk,
                "metadata": {"agent_public_key_thumbprint": agent_key.thumbprint},
            }
        ],
    )
    canonical_agent_id = bootstrap[0].agent.agent_id
    body = {
        "provider": "openclaw",
        "agent_id": canonical_agent_id,
        "external_session_id": "02cba761-9e90-4bb7-a962-f31b52491877",
        "external_user_id": "openclaw-user",
        "metadata": {
            "openclaw_agent_id": "main",
            "openclaw_session_key": "agent:main:main",
            "openclaw_session_id": "02cba761-9e90-4bb7-a962-f31b52491877",
        },
    }
    first_key = DPoPKey()
    first = broker.create_openclaw_session(
        external_session_id=body["external_session_id"],
        agent_id=canonical_agent_id,
        external_user_id=body["external_user_id"],
        metadata=body["metadata"],
        dpop_proof=first_key.proof("POST", CREATE_URL),
        agent_proof=agent_key.sign_session_create_proof(
            agent_id=canonical_agent_id,
            method="POST",
            url=CREATE_URL,
            body=body,
            dpop_jkt=first_key.thumbprint,
        ),
        request_body=body,
        method="POST",
        url=CREATE_URL,
    )
    broker.close_session(
        token=first.session_token,
        dpop_proof=first_key.proof("POST", CLOSE_URL, first.session_token),
        method="POST",
        url=CLOSE_URL,
    )

    second_key = DPoPKey()
    with pytest.raises(RuntimeAuthUnauthorized, match="OpenClaw external session is closed"):
        broker.create_openclaw_session(
            external_session_id=body["external_session_id"],
            agent_id=canonical_agent_id,
            external_user_id=body["external_user_id"],
            metadata=body["metadata"],
            dpop_proof=second_key.proof("POST", CREATE_URL),
            agent_proof=agent_key.sign_session_create_proof(
                agent_id=canonical_agent_id,
                method="POST",
                url=CREATE_URL,
                body=body,
                dpop_jkt=second_key.thumbprint,
            ),
            request_body=body,
            method="POST",
            url=CREATE_URL,
        )

    assert len(session_store.sessions) == 1
    assert session_store.find_external_session(
        provider="openclaw",
        external_session_id=body["external_session_id"],
        agent_id=canonical_agent_id,
    ).session_id == first.session.session_id


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


def test_dify_session_create_rejects_closed_external_session():
    broker, store = _broker()
    key = DPoPKey()
    first = _create(broker, key)
    store.close_session(first.session.session_id)
    body = _body()

    with pytest.raises(RuntimeAuthUnauthorized, match="dify external session is closed"):
        broker.create_session(
            **body,
            dpop_proof=key.proof("POST", CREATE_URL),
            agent_proof=_agent_proof(key, body),
            request_body=body,
            method="POST",
            url=CREATE_URL,
        )


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


def test_close_revokes_external_session_siblings():
    broker, store = _broker()
    key = DPoPKey()
    issue = _create(broker, key)
    sibling = store.create_session(
        agent_id=issue.session.agent_id,
        user_id=issue.session.user_id,
        provider=issue.session.provider,
        external_session_id=issue.session.external_session_id,
        external_account_email=issue.session.external_account_email,
        dpop_jkt=issue.session.dpop_jkt,
        metadata={},
    )
    sibling_token = broker.token_service.issue(
        session_id=sibling.session_id,
        agent_id=sibling.agent_id,
        user_id=sibling.user_id,
        dpop_jkt=sibling.dpop_jkt,
        provider=sibling.provider,
        external_session_id=sibling.external_session_id,
    )
    store.create_token(
        token_jti=sibling_token.token_jti,
        session_id=sibling.session_id,
        expires_at_epoch=sibling_token.expires_at,
        cnf_jkt=sibling.dpop_jkt,
    )

    broker.close_session(
        token=issue.session_token,
        dpop_proof=key.proof("POST", CLOSE_URL, issue.session_token),
        method="POST",
        url=CLOSE_URL,
    )

    assert store.get_session(issue.session.session_id).status == "closed"
    assert store.get_session(sibling.session_id).status == "closed"
    assert store.get_token(issue.token_jti).status == "revoked"
    assert store.get_token(sibling_token.token_jti).status == "revoked"


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

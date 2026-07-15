"""Auth Broker for Dify runtime sessions."""
from __future__ import annotations

import base64
import json
import secrets
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from backend.agents.store import AgentStore
from backend.auth.agent_identity import AgentIdentityProofError, verify_agent_identity_proof
from backend.auth.dpop import DPoPError, verify_dpop_proof
from backend.auth.models import AuthContext, RuntimeSession
from backend.auth.replay_store import DPoPReplayError, DPoPReplayStore, get_replay_store
from backend.auth.session_store import RuntimeSessionStore, get_runtime_session_store
from backend.auth.token_service import RuntimeTokenService, TokenError
from backend.user.store import InvalidUserTicket, UserStore


class AuthBrokerError(PermissionError):
    status_code = 403


class BadAuthRequest(AuthBrokerError):
    status_code = 400


class RuntimeAuthUnauthorized(AuthBrokerError):
    status_code = 401


class RuntimeAuthForbidden(AuthBrokerError):
    status_code = 403


@dataclass(frozen=True)
class RuntimeSessionIssue:
    session: RuntimeSession
    session_token: str
    token_jti: str
    issued_at: int
    expires_at: int


@dataclass(frozen=True)
class OpenClawAgentBootstrapResult:
    agent: Any
    credential: Any
    user_id: int
    user_binding_created: bool
    user_binding_updated: bool


class DifyAuthBroker:
    def __init__(
        self,
        *,
        session_store: RuntimeSessionStore | None = None,
        replay_store: DPoPReplayStore | None = None,
        token_service: RuntimeTokenService | None = None,
        user_store: UserStore | None = None,
        agent_store: AgentStore | None = None,
    ) -> None:
        self.session_store = session_store or get_runtime_session_store()
        self.replay_store = replay_store or get_replay_store()
        self.token_service = token_service or RuntimeTokenService()
        self.user_store = user_store or UserStore()
        self.agent_store = agent_store or AgentStore()

    def create_session(
        self,
        *,
        provider: str,
        external_session_id: str | None,
        agent_id: str,
        account_email: str,
        external_user_id: str | None,
        metadata: dict[str, Any] | None,
        dpop_proof: str | None,
        agent_proof: str | None,
        request_body: dict[str, Any],
        method: str,
        url: str,
    ) -> RuntimeSessionIssue:
        provider = _clean_provider(provider)
        if provider not in {"dify", "n8n"}:
            raise BadAuthRequest("only provider=dify or provider=n8n is supported for runtime session create")
        account_email = _clean_email(account_email)
        if not account_email:
            raise BadAuthRequest("account_email is required")
        external_session_id = _optional_text(external_session_id)
        agent_id = _required_text(agent_id, "agent_id")
        mapping = self.user_store.external_account_by_provider_email(
            provider=provider,
            account_email=account_email,
        )
        if mapping is None:
            raise RuntimeAuthForbidden(f"{provider} account is not bound to an AgentGuard user")
        verification = self._verify_proof(
            dpop_proof,
            method=method,
            url=url,
            access_token=None,
            expected_jkt=None,
        )
        self._verify_agent_identity_proof(
            agent_id=agent_id,
            user_id=mapping.user_id,
            proof=agent_proof,
            method=method,
            url=url,
            body=request_body,
            dpop_jkt=verification.jkt,
        )
        existing = (
            self.session_store.find_active_external_session(
                provider=provider,
                external_session_id=external_session_id,
                agent_id=agent_id,
            )
            if external_session_id
            else None
        )
        if existing is not None:
            if existing.user_id != mapping.user_id:
                raise RuntimeAuthForbidden(f"{provider} session belongs to another AgentGuard user")
            if existing.dpop_jkt != verification.jkt:
                raise RuntimeAuthForbidden(f"{provider} session is bound to another DPoP key")
            self.session_store.touch_session(existing.session_id)
            return self._issue_token(existing)
        session = self.session_store.create_session(
            agent_id=agent_id,
            user_id=mapping.user_id,
            provider=provider,
            external_session_id=external_session_id,
            external_account_email=account_email,
            dpop_jkt=verification.jkt,
            metadata={
                **(metadata or {}),
                "external_user_id": external_user_id,
            },
        )
        return self._issue_token(session)

    def bootstrap_openclaw_agents(
        self,
        *,
        user_ticket: str | None,
        agents: list[dict[str, Any]],
        provider_instance_id: str | None = None,
        tenant_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> tuple[Any, list[OpenClawAgentBootstrapResult]]:
        identity = self._resolve_user_ticket(user_ticket)
        if not agents:
            raise BadAuthRequest("agents are required")
        clean_metadata = dict(metadata or {})
        clean_metadata.update(
            {
                "ticket_id": identity.ticket_id,
                "ticket_prefix": identity.ticket_prefix,
                "runtime_auth_provider": "openclaw",
            }
        )
        results: list[OpenClawAgentBootstrapResult] = []
        catalog_scopes: dict[tuple[str | None, str | None, str], set[str]] = {}
        for item in agents:
            if not isinstance(item, dict):
                raise BadAuthRequest("agents entries must be objects")
            external_agent_id = _required_text(item.get("external_agent_id"), "external_agent_id")
            agent_type = _optional_text(item.get("agent_type")) or "agent"
            if agent_type != "agent":
                raise BadAuthRequest("OpenClaw bootstrap only supports agent_type=agent")
            item_provider_instance_id = _optional_text(item.get("provider_instance_id")) or _optional_text(provider_instance_id)
            item_tenant_id = _optional_text(item.get("tenant_id")) or _optional_text(tenant_id)
            item_metadata = {
                **clean_metadata,
                **(item.get("metadata") if isinstance(item.get("metadata"), dict) else {}),
                "external_agent_id": external_agent_id,
                "openclaw_agent_id": external_agent_id,
                "ticket_prefix": identity.ticket_prefix,
            }
            registration = self.agent_store.register_agent(
                provider="openclaw",
                provider_instance_id=item_provider_instance_id,
                tenant_id=item_tenant_id,
                external_agent_id=external_agent_id,
                agent_type=agent_type,
                name=_optional_text(item.get("name")) or external_agent_id,
                description=_optional_text(item.get("description")),
                public_key_jwk=item.get("public_key_jwk"),
                metadata=item_metadata,
            )
            created, updated = self.agent_store.bind_agent_to_user(
                user_id=identity.user_id,
                agent_id=registration.agent.agent_id,
                provider="openclaw",
                account_email=None,
                source="user_ticket_bootstrap",
                metadata=item_metadata,
            )
            results.append(
                OpenClawAgentBootstrapResult(
                    agent=registration.agent,
                    credential=registration.credential,
                    user_id=identity.user_id,
                    user_binding_created=created,
                    user_binding_updated=updated,
                )
            )
            catalog_scopes.setdefault((item_provider_instance_id, item_tenant_id, agent_type), set()).add(external_agent_id)
        sync_provider_agents = getattr(self.agent_store, "sync_provider_agents", None)
        if callable(sync_provider_agents):
            for (scope_provider_instance_id, scope_tenant_id, scope_agent_type), external_agent_ids in catalog_scopes.items():
                sync_provider_agents(
                    provider="openclaw",
                    provider_instance_id=scope_provider_instance_id,
                    tenant_id=scope_tenant_id,
                    agent_type=scope_agent_type,
                    external_agent_ids=sorted(external_agent_ids),
                    metadata={
                        **clean_metadata,
                        "sync_source": "openclaw_bootstrap",
                    },
                )
        consume_agent_id = results[0].agent.agent_id if results else "openclaw-bootstrap"
        self._consume_user_ticket(
            user_ticket,
            agent_id=consume_agent_id,
            session_id=f"openclaw-bootstrap:{_optional_text(provider_instance_id) or 'default'}",
        )
        return identity, results

    def create_openclaw_session(
        self,
        *,
        external_session_id: str | None,
        agent_id: str,
        external_user_id: str | None,
        metadata: dict[str, Any] | None,
        dpop_proof: str | None,
        agent_proof: str | None,
        request_body: dict[str, Any],
        method: str,
        url: str,
    ) -> RuntimeSessionIssue:
        external_session_id = _required_text(external_session_id, "external_session_id")
        agent_id = _required_text(agent_id, "agent_id")
        user_ids = self.agent_store.user_ids_for_agent(agent_id)
        if not user_ids:
            raise RuntimeAuthForbidden("OpenClaw agent is not bound to an AgentGuard user")
        if len(user_ids) > 1:
            raise RuntimeAuthForbidden("OpenClaw agent is bound to multiple AgentGuard users")
        user_id = next(iter(user_ids))
        verification = self._verify_proof(
            dpop_proof,
            method=method,
            url=url,
            access_token=None,
            expected_jkt=None,
        )
        self._verify_agent_identity_proof(
            agent_id=agent_id,
            user_id=user_id,
            proof=agent_proof,
            method=method,
            url=url,
            body=request_body,
            dpop_jkt=verification.jkt,
        )
        find_external_session = getattr(self.session_store, "find_external_session", None)
        existing = (
            find_external_session(
                provider="openclaw",
                external_session_id=external_session_id,
                agent_id=agent_id,
            )
            if callable(find_external_session)
            else self.session_store.find_active_external_session(
                provider="openclaw",
                external_session_id=external_session_id,
                agent_id=agent_id,
            )
        )
        if existing is not None:
            if existing.user_id != user_id:
                raise RuntimeAuthForbidden("OpenClaw session belongs to another AgentGuard user")
            if existing.status != "active":
                raise RuntimeAuthUnauthorized("OpenClaw external session is closed")
            if existing.dpop_jkt != verification.jkt:
                raise RuntimeAuthForbidden("OpenClaw session is bound to another DPoP key")
            self.session_store.touch_session(existing.session_id)
            return self._issue_token(existing)
        session = self.session_store.create_session(
            agent_id=agent_id,
            user_id=user_id,
            provider="openclaw",
            external_session_id=external_session_id,
            external_account_email=None,
            dpop_jkt=verification.jkt,
            metadata={
                **(metadata or {}),
                "external_user_id": external_user_id,
            },
        )
        return self._issue_token(session)

    def create_ticket_session(
        self,
        *,
        provider: str,
        user_ticket: str | None,
        metadata: dict[str, Any] | None,
        dpop_proof: str | None,
        request_body: dict[str, Any],
        method: str,
        url: str,
    ) -> RuntimeSessionIssue:
        provider = _clean_provider(provider)
        if provider not in {"langchain", "openclaw"}:
            raise BadAuthRequest("only provider=langchain or provider=openclaw is supported for ticket session create")
        identity = self._resolve_user_ticket(user_ticket)
        verification = self._verify_proof(
            dpop_proof,
            method=method,
            url=url,
            access_token=None,
            expected_jkt=None,
        )
        clean_metadata = dict(metadata or {})
        clean_metadata.update(
            {
                "ticket_id": identity.ticket_id,
                "ticket_prefix": identity.ticket_prefix,
                "runtime_auth_provider": provider,
                "request_body_provider": request_body.get("provider"),
            }
        )
        external_agent_id = f"ticket-{identity.ticket_id}-{secrets.token_urlsafe(12)}"
        registration = self.agent_store.register_agent(
            provider=provider,
            provider_instance_id=None,
            tenant_id=None,
            external_agent_id=external_agent_id,
            agent_type="runtime",
            name=_optional_text(clean_metadata.get("name")) or _runtime_agent_name(provider),
            description=_optional_text(clean_metadata.get("description")),
            public_key_jwk=verification.public_jwk,
            metadata={
                **clean_metadata,
                "external_agent_id": external_agent_id,
                "ticket_prefix": identity.ticket_prefix,
            },
        )
        self.agent_store.bind_agent_to_user(
            user_id=identity.user_id,
            agent_id=registration.agent.agent_id,
            provider=provider,
            account_email=None,
            source="user_ticket",
            metadata={
                **clean_metadata,
                "ticket_prefix": identity.ticket_prefix,
            },
        )
        session = self.session_store.create_session(
            agent_id=registration.agent.agent_id,
            user_id=identity.user_id,
            provider=provider,
            external_session_id=None,
            external_account_email=None,
            dpop_jkt=verification.jkt,
            metadata={
                **clean_metadata,
                "ticket_id": identity.ticket_id,
                "ticket_prefix": identity.ticket_prefix,
            },
        )
        self._consume_user_ticket(
            user_ticket,
            agent_id=registration.agent.agent_id,
            session_id=session.session_id,
        )
        return self._issue_token(session)

    def create_langchain_ticket_session(
        self,
        *,
        user_ticket: str | None,
        metadata: dict[str, Any] | None,
        dpop_proof: str | None,
        request_body: dict[str, Any],
        method: str,
        url: str,
    ) -> RuntimeSessionIssue:
        return self.create_ticket_session(
            provider="langchain",
            user_ticket=user_ticket,
            metadata=metadata,
            dpop_proof=dpop_proof,
            request_body=request_body,
            method=method,
            url=url,
        )

    def refresh_session(
        self,
        *,
        token: str,
        dpop_proof: str | None,
        method: str,
        url: str,
    ) -> RuntimeSessionIssue:
        auth = self.authenticate_runtime_request(
            token=token,
            dpop_proof=dpop_proof,
            method=method,
            url=url,
        )
        self.session_store.revoke_token(auth.token_jti)
        session = self._active_session(auth.session_id)
        return self._issue_token(session)

    def close_session(
        self,
        *,
        token: str,
        dpop_proof: str | None,
        method: str,
        url: str,
    ) -> AuthContext:
        auth = self.authenticate_runtime_request(
            token=token,
            dpop_proof=dpop_proof,
            method=method,
            url=url,
        )
        self.session_store.close_session(auth.session_id)
        return auth

    def authenticate_runtime_request(
        self,
        *,
        token: str,
        dpop_proof: str | None,
        method: str,
        url: str,
    ) -> AuthContext:
        try:
            claims = self.token_service.verify(token)
        except TokenError as exc:
            raise RuntimeAuthUnauthorized(str(exc)) from exc
        session_id = str(claims["sid"])
        session = self._active_session(session_id)
        token_record = self.session_store.get_token(str(claims["jti"]))
        if token_record is None or token_record.status != "active":
            raise RuntimeAuthUnauthorized("runtime token is not active")
        if _expired(token_record.expires_at):
            raise RuntimeAuthUnauthorized("runtime token expired")
        cnf = claims.get("cnf") if isinstance(claims.get("cnf"), dict) else {}
        expected_jkt = str(cnf.get("jkt") or "")
        if not expected_jkt or expected_jkt != session.dpop_jkt or expected_jkt != token_record.cnf_jkt:
            raise RuntimeAuthUnauthorized("runtime token DPoP binding mismatch")
        self._verify_proof(
            dpop_proof,
            method=method,
            url=url,
            access_token=token,
            expected_jkt=expected_jkt,
        )
        self.session_store.touch_session(session_id)
        return AuthContext(
            session_id=session.session_id,
            agent_id=session.agent_id,
            user_id=str(session.user_id),
            token_jti=str(claims["jti"]),
            dpop_jkt=expected_jkt,
            external_provider=session.provider,
            external_session_id=session.external_session_id,
            scope=list(claims.get("scope") or []),
            raw_claims=claims,
        )

    def _verify_proof(
        self,
        proof: str | None,
        *,
        method: str,
        url: str,
        access_token: str | None,
        expected_jkt: str | None,
    ):
        try:
            verification = verify_dpop_proof(
                proof,
                method=method,
                url=url,
                access_token=access_token,
                expected_jkt=expected_jkt,
            )
            self.replay_store.remember(jkt=verification.jkt, jti=verification.jti)
            return verification
        except DPoPReplayError as exc:
            raise RuntimeAuthUnauthorized(str(exc)) from exc
        except DPoPError as exc:
            raise RuntimeAuthUnauthorized(str(exc)) from exc

    def _verify_agent_identity_proof(
        self,
        *,
        agent_id: str,
        user_id: int,
        proof: str | None,
        method: str,
        url: str,
        body: dict[str, Any],
        dpop_jkt: str,
    ) -> None:
        agent = self.agent_store.get_agent(agent_id)
        if agent is None or agent.status != "active":
            raise RuntimeAuthForbidden("AgentGuard agent is not registered or active")
        if agent_id not in self.agent_store.agent_ids_for_user(int(user_id)):
            raise RuntimeAuthForbidden("AgentGuard agent is not available to this user")
        proof_kid = _agent_proof_kid(proof)
        if not proof_kid:
            raise RuntimeAuthUnauthorized("AgentGuard agent identity proof missing key id")
        credential = self.agent_store.get_active_credential(
            agent_id=agent_id,
            public_key_thumbprint=proof_kid,
        )
        if credential is None:
            raise RuntimeAuthForbidden("AgentGuard agent credential is not active")
        try:
            public_key_jwk = (
                dict(credential.public_key_jwk)
                if isinstance(credential.public_key_jwk, dict)
                else json.loads(credential.public_key_jwk)
            )
        except Exception as exc:
            raise RuntimeAuthForbidden("AgentGuard agent credential public key is invalid") from exc
        try:
            verification = verify_agent_identity_proof(
                proof,
                agent_id=agent_id,
                public_key_jwk=public_key_jwk,
                expected_kid=credential.public_key_thumbprint,
                method=method,
                url=url,
                body=body,
                dpop_jkt=dpop_jkt,
            )
            self.replay_store.remember(jkt=f"agent:{verification.kid}", jti=verification.jti)
        except DPoPReplayError as exc:
            raise RuntimeAuthUnauthorized(str(exc)) from exc
        except AgentIdentityProofError as exc:
            raise RuntimeAuthUnauthorized(str(exc)) from exc

    def _resolve_user_ticket(self, ticket: str | None):
        try:
            identity = self.user_store.resolve_ticket(ticket)
        except InvalidUserTicket as exc:
            raise RuntimeAuthUnauthorized(str(exc)) from exc
        if identity is None:
            raise RuntimeAuthUnauthorized("missing user ticket")
        return identity

    def _consume_user_ticket(
        self,
        ticket: str | None,
        *,
        agent_id: str,
        session_id: str,
    ) -> None:
        try:
            self.user_store.consume_ticket(
                ticket,
                agent_id=agent_id,
                session_id=session_id,
            )
        except InvalidUserTicket as exc:
            raise RuntimeAuthUnauthorized(str(exc)) from exc

    def _active_session(self, session_id: str) -> RuntimeSession:
        session = self.session_store.get_session(session_id)
        if session is None or session.status != "active":
            raise RuntimeAuthUnauthorized("runtime session is not active")
        return session

    def _issue_token(self, session: RuntimeSession) -> RuntimeSessionIssue:
        issued = self.token_service.issue(
            session_id=session.session_id,
            agent_id=session.agent_id,
            user_id=session.user_id,
            dpop_jkt=session.dpop_jkt,
            provider=session.provider,
            external_session_id=session.external_session_id,
        )
        self.session_store.create_token(
            token_jti=issued.token_jti,
            session_id=session.session_id,
            expires_at_epoch=issued.expires_at,
            cnf_jkt=session.dpop_jkt,
        )
        return RuntimeSessionIssue(
            session=session,
            session_token=issued.token,
            token_jti=issued.token_jti,
            issued_at=issued.issued_at,
            expires_at=issued.expires_at,
        )


def get_dify_auth_broker() -> DifyAuthBroker:
    return DifyAuthBroker()


def _required_text(value: Any, field: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise BadAuthRequest(f"{field} is required")
    return text


def _optional_text(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _clean_provider(value: str) -> str:
    return str(value or "").strip().lower()


def _clean_email(value: str) -> str:
    return str(value or "").strip().lower()


def _runtime_agent_name(provider: str) -> str:
    if provider == "openclaw":
        return "OpenClaw runtime agent"
    return "LangChain runtime agent"


def _expired(value: datetime) -> bool:
    dt = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).timestamp() <= datetime.now(timezone.utc).timestamp()


def _agent_proof_kid(proof: str | None) -> str | None:
    if not proof:
        return None
    try:
        header_b64 = proof.split(".", 1)[0]
        padding = "=" * (-len(header_b64) % 4)
        data = json.loads(base64.urlsafe_b64decode((header_b64 + padding).encode("ascii")).decode("utf-8"))
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    kid = str(data.get("kid") or "").strip()
    return kid or None

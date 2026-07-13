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
        if provider != "dify":
            raise BadAuthRequest("only provider=dify is supported for runtime session create")
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
            raise RuntimeAuthForbidden("Dify account is not bound to an AgentGuard user")
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
                raise RuntimeAuthForbidden("Dify session belongs to another AgentGuard user")
            if existing.dpop_jkt != verification.jkt:
                raise RuntimeAuthForbidden("Dify session is bound to another DPoP key")
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
                "runtime_auth_provider": "langchain",
                "request_body_provider": request_body.get("provider"),
            }
        )
        external_agent_id = f"ticket-{identity.ticket_id}-{secrets.token_urlsafe(12)}"
        registration = self.agent_store.register_agent(
            provider="langchain",
            provider_instance_id=None,
            tenant_id=None,
            external_agent_id=external_agent_id,
            agent_type="runtime",
            name=_optional_text(clean_metadata.get("name")) or "LangChain runtime agent",
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
            provider="langchain",
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
            provider="langchain",
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

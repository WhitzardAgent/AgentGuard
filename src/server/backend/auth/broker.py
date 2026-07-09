"""Auth Broker for Dify runtime sessions."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from backend.auth.dpop import DPoPError, verify_dpop_proof
from backend.auth.models import AuthContext, RuntimeSession
from backend.auth.replay_store import DPoPReplayError, DPoPReplayStore, get_replay_store
from backend.auth.session_store import RuntimeSessionStore, get_runtime_session_store
from backend.auth.token_service import RuntimeTokenService, TokenError
from backend.user.store import UserStore


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
    ) -> None:
        self.session_store = session_store or get_runtime_session_store()
        self.replay_store = replay_store or get_replay_store()
        self.token_service = token_service or RuntimeTokenService()
        self.user_store = user_store or UserStore()

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

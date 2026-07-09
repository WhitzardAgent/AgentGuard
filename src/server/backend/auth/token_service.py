"""Short-lived runtime session token service."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from dataclasses import dataclass
from typing import Any


TOKEN_SECRET_ENV = "AGENTGUARD_SESSION_TOKEN_SECRET"
TOKEN_TTL_ENV = "AGENTGUARD_RUNTIME_TOKEN_TTL_SECONDS"
DEFAULT_TOKEN_TTL_SECONDS = 900
TOKEN_AUDIENCE = "agentguard_runtime"
TOKEN_ISSUER = "agentguard"


class TokenError(PermissionError):
    pass


@dataclass(frozen=True)
class IssuedToken:
    token: str
    token_jti: str
    issued_at: int
    expires_at: int


class RuntimeTokenService:
    def __init__(self, secret: str | None = None, ttl_seconds: int | None = None) -> None:
        self.secret = (secret or os.getenv(TOKEN_SECRET_ENV) or "agentguard-dev-session-token-secret").encode(
            "utf-8"
        )
        self.ttl_seconds = ttl_seconds if ttl_seconds is not None else _int_env(
            TOKEN_TTL_ENV,
            DEFAULT_TOKEN_TTL_SECONDS,
        )

    def issue(
        self,
        *,
        session_id: str,
        agent_id: str,
        user_id: int | str,
        dpop_jkt: str,
        provider: str,
        external_session_id: str | None,
    ) -> IssuedToken:
        now = int(time.time())
        token_jti = f"rtok_{secrets.token_urlsafe(18)}"
        payload = {
            "iss": TOKEN_ISSUER,
            "aud": TOKEN_AUDIENCE,
            "iat": now,
            "exp": now + self.ttl_seconds,
            "jti": token_jti,
            "sid": session_id,
            "sub": agent_id,
            "uid": str(user_id),
            "scope": ["runtime"],
            "provider": provider,
            "cnf": {"jkt": dpop_jkt},
        }
        if external_session_id:
            payload["external_session_id"] = external_session_id
        return IssuedToken(
            token=_encode_jwt({"typ": "JWT", "alg": "HS256"}, payload, self.secret),
            token_jti=token_jti,
            issued_at=now,
            expires_at=payload["exp"],
        )

    def verify(self, token: str) -> dict[str, Any]:
        payload = _decode_jwt(token, self.secret)
        if payload.get("iss") != TOKEN_ISSUER:
            raise TokenError("invalid token issuer")
        if payload.get("aud") != TOKEN_AUDIENCE:
            raise TokenError("invalid token audience")
        exp = int(payload.get("exp") or 0)
        if exp <= int(time.time()):
            raise TokenError("runtime token expired")
        if not payload.get("sid") or not payload.get("sub") or not payload.get("jti"):
            raise TokenError("runtime token missing required claims")
        cnf = payload.get("cnf") if isinstance(payload.get("cnf"), dict) else {}
        if not cnf.get("jkt"):
            raise TokenError("runtime token missing cnf.jkt")
        return payload


def access_token_hash(token: str) -> str:
    return _b64url(hashlib.sha256(token.encode("utf-8")).digest())


def _encode_jwt(header: dict[str, Any], payload: dict[str, Any], secret: bytes) -> str:
    signing_input = ".".join(
        [
            _b64url_json(header),
            _b64url_json(payload),
        ]
    )
    signature = hmac.new(secret, signing_input.encode("ascii"), hashlib.sha256).digest()
    return f"{signing_input}.{_b64url(signature)}"


def _decode_jwt(token: str, secret: bytes) -> dict[str, Any]:
    try:
        header_b64, payload_b64, signature_b64 = token.split(".", 2)
    except ValueError as exc:
        raise TokenError("malformed runtime token") from exc
    signing_input = f"{header_b64}.{payload_b64}".encode("ascii")
    expected = hmac.new(secret, signing_input, hashlib.sha256).digest()
    provided = _b64url_decode(signature_b64)
    if not hmac.compare_digest(expected, provided):
        raise TokenError("invalid runtime token signature")
    header = _json_from_b64(header_b64)
    if header.get("alg") != "HS256":
        raise TokenError("unsupported runtime token algorithm")
    payload = _json_from_b64(payload_b64)
    if not isinstance(payload, dict):
        raise TokenError("invalid runtime token payload")
    return payload


def _b64url_json(value: dict[str, Any]) -> str:
    return _b64url(json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8"))


def _json_from_b64(value: str) -> Any:
    try:
        return json.loads(_b64url_decode(value).decode("utf-8"))
    except Exception as exc:
        raise TokenError("invalid runtime token json") from exc


def _b64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _b64url_decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode((value + padding).encode("ascii"))


def _int_env(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or str(raw).strip() == "":
        return default
    try:
        return max(1, int(raw))
    except ValueError:
        return default

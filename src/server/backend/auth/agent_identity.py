"""Agent long-term identity proof validation."""
from __future__ import annotations

import base64
import hashlib
import json
import time
from dataclasses import dataclass
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey


class AgentIdentityProofError(PermissionError):
    pass


@dataclass(frozen=True)
class AgentIdentityProofVerification:
    kid: str
    jti: str
    agent_id: str
    claims: dict[str, Any]


def verify_agent_identity_proof(
    proof_jwt: str | None,
    *,
    agent_id: str,
    public_key_jwk: dict[str, Any],
    expected_kid: str | None,
    method: str,
    url: str,
    body: dict[str, Any],
    dpop_jkt: str,
    max_age_seconds: int = 300,
) -> AgentIdentityProofVerification:
    if not proof_jwt:
        raise AgentIdentityProofError("missing AgentGuard agent identity proof")
    try:
        header_b64, payload_b64, signature_b64 = proof_jwt.split(".", 2)
    except ValueError as exc:
        raise AgentIdentityProofError("malformed AgentGuard agent identity proof") from exc
    header = _json_from_b64(header_b64)
    payload = _json_from_b64(payload_b64)
    if header.get("typ") != "agentguard-agent-proof+jwt":
        raise AgentIdentityProofError("invalid AgentGuard agent identity proof type")
    if header.get("alg") != "EdDSA":
        raise AgentIdentityProofError("unsupported AgentGuard agent identity proof algorithm")
    kid = str(header.get("kid") or "").strip()
    if expected_kid and kid != expected_kid:
        raise AgentIdentityProofError("AgentGuard agent identity proof key mismatch")
    if str(payload.get("iss") or "") != agent_id or str(payload.get("sub") or "") != agent_id:
        raise AgentIdentityProofError("AgentGuard agent identity proof subject mismatch")
    if payload.get("aud") != "agentguard:runtime-session-create":
        raise AgentIdentityProofError("AgentGuard agent identity proof audience mismatch")
    if str(payload.get("htm") or "").upper() != method.upper():
        raise AgentIdentityProofError("AgentGuard agent identity proof method mismatch")
    if str(payload.get("htu") or "") != url:
        raise AgentIdentityProofError("AgentGuard agent identity proof URL mismatch")
    if payload.get("body_sha256") != canonical_body_sha256(body):
        raise AgentIdentityProofError("AgentGuard agent identity proof body hash mismatch")
    if str(payload.get("dpop_jkt") or "") != dpop_jkt:
        raise AgentIdentityProofError("AgentGuard agent identity proof DPoP binding mismatch")
    jti = str(payload.get("jti") or "").strip()
    if not jti:
        raise AgentIdentityProofError("AgentGuard agent identity proof missing jti")
    now = int(time.time())
    try:
        iat = int(payload.get("iat") or 0)
        exp = int(payload.get("exp") or 0)
    except (TypeError, ValueError) as exc:
        raise AgentIdentityProofError("AgentGuard agent identity proof has invalid time claims") from exc
    if iat < now - max_age_seconds or iat > now + 60 or exp <= now:
        raise AgentIdentityProofError("AgentGuard agent identity proof outside allowed time window")
    _verify_eddsa(
        public_key_jwk,
        f"{header_b64}.{payload_b64}".encode("ascii"),
        _b64url_decode(signature_b64),
    )
    return AgentIdentityProofVerification(kid=kid, jti=jti, agent_id=agent_id, claims=payload)


def canonical_body_sha256(body: dict[str, Any]) -> str:
    canonical = json.dumps(
        _strip_none(body),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return _b64url(hashlib.sha256(canonical).digest())


def _verify_eddsa(jwk: dict[str, Any], signing_input: bytes, signature: bytes) -> None:
    if jwk.get("kty") != "OKP" or jwk.get("crv") != "Ed25519":
        raise AgentIdentityProofError("unsupported AgentGuard agent public key")
    x = str(jwk.get("x") or "")
    if not x:
        raise AgentIdentityProofError("incomplete AgentGuard agent public key")
    try:
        Ed25519PublicKey.from_public_bytes(_b64url_decode(x)).verify(signature, signing_input)
    except InvalidSignature as exc:
        raise AgentIdentityProofError("invalid AgentGuard agent identity proof signature") from exc


def _json_from_b64(value: str) -> dict[str, Any]:
    try:
        data = json.loads(_b64url_decode(value).decode("utf-8"))
    except Exception as exc:
        raise AgentIdentityProofError("invalid AgentGuard agent identity proof json") from exc
    if not isinstance(data, dict):
        raise AgentIdentityProofError("invalid AgentGuard agent identity proof json object")
    return data


def _strip_none(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _strip_none(item) for key, item in value.items() if item is not None}
    if isinstance(value, list):
        return [_strip_none(item) for item in value if item is not None]
    return value


def _b64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _b64url_decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode((value + padding).encode("ascii"))

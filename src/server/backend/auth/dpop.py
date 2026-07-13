"""DPoP proof validation."""
from __future__ import annotations

import base64
import hashlib
import json
import time
from dataclasses import dataclass
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric import ec, utils
from cryptography.hazmat.primitives.hashes import SHA256

from backend.auth.token_service import access_token_hash


class DPoPError(PermissionError):
    pass


@dataclass(frozen=True)
class DPoPVerification:
    jkt: str
    jti: str
    iat: int
    claims: dict[str, Any]
    public_jwk: dict[str, Any]


def verify_dpop_proof(
    proof_jwt: str | None,
    *,
    method: str,
    url: str,
    access_token: str | None = None,
    expected_jkt: str | None = None,
    max_age_seconds: int = 300,
) -> DPoPVerification:
    if not proof_jwt:
        raise DPoPError("missing DPoP proof")
    try:
        header_b64, payload_b64, signature_b64 = proof_jwt.split(".", 2)
    except ValueError as exc:
        raise DPoPError("malformed DPoP proof") from exc
    header = _json_from_b64(header_b64)
    payload = _json_from_b64(payload_b64)
    if header.get("typ") != "dpop+jwt":
        raise DPoPError("invalid DPoP proof type")
    if header.get("alg") != "ES256":
        raise DPoPError("unsupported DPoP algorithm")
    jwk = header.get("jwk")
    if not isinstance(jwk, dict):
        raise DPoPError("missing DPoP JWK")
    jkt = jwk_thumbprint(jwk)
    if expected_jkt and jkt != expected_jkt:
        raise DPoPError("DPoP key does not match runtime token")
    _verify_es256(jwk, f"{header_b64}.{payload_b64}".encode("ascii"), _b64url_decode(signature_b64))

    htm = str(payload.get("htm") or "").upper()
    htu = str(payload.get("htu") or "")
    if htm != method.upper():
        raise DPoPError("DPoP method mismatch")
    if htu != url:
        raise DPoPError("DPoP URL mismatch")
    jti = str(payload.get("jti") or "").strip()
    if not jti:
        raise DPoPError("DPoP proof missing jti")
    iat = int(payload.get("iat") or 0)
    now = int(time.time())
    if iat < now - max_age_seconds or iat > now + 60:
        raise DPoPError("DPoP proof outside allowed time window")
    if access_token is not None:
        expected_ath = access_token_hash(access_token)
        if payload.get("ath") != expected_ath:
            raise DPoPError("DPoP access-token hash mismatch")
    return DPoPVerification(jkt=jkt, jti=jti, iat=iat, claims=payload, public_jwk=dict(jwk))


def jwk_thumbprint(jwk: dict[str, Any]) -> str:
    if jwk.get("kty") != "EC" or jwk.get("crv") != "P-256":
        raise DPoPError("unsupported DPoP JWK")
    if not jwk.get("x") or not jwk.get("y"):
        raise DPoPError("incomplete DPoP JWK")
    canonical = json.dumps(
        {"crv": jwk["crv"], "kty": jwk["kty"], "x": jwk["x"], "y": jwk["y"]},
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return _b64url(hashlib.sha256(canonical).digest())


def _verify_es256(jwk: dict[str, Any], signing_input: bytes, raw_signature: bytes) -> None:
    if len(raw_signature) != 64:
        raise DPoPError("invalid DPoP signature length")
    x = int.from_bytes(_b64url_decode(str(jwk["x"])), "big")
    y = int.from_bytes(_b64url_decode(str(jwk["y"])), "big")
    public_key = ec.EllipticCurvePublicNumbers(x, y, ec.SECP256R1()).public_key()
    der_signature = utils.encode_dss_signature(
        int.from_bytes(raw_signature[:32], "big"),
        int.from_bytes(raw_signature[32:], "big"),
    )
    try:
        public_key.verify(der_signature, signing_input, ec.ECDSA(SHA256()))
    except InvalidSignature as exc:
        raise DPoPError("invalid DPoP signature") from exc


def _json_from_b64(value: str) -> dict[str, Any]:
    try:
        data = json.loads(_b64url_decode(value).decode("utf-8"))
    except Exception as exc:
        raise DPoPError("invalid DPoP json") from exc
    if not isinstance(data, dict):
        raise DPoPError("invalid DPoP json object")
    return data


def _b64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _b64url_decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode((value + padding).encode("ascii"))

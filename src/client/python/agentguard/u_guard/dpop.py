"""Client-side DPoP proof generation."""
from __future__ import annotations

import base64
import hashlib
import json
import secrets
import time
from typing import Any

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec, utils
from cryptography.hazmat.primitives.hashes import SHA256


class DPoPKey:
    def __init__(self, private_key: ec.EllipticCurvePrivateKey | None = None) -> None:
        self._private_key = private_key or ec.generate_private_key(ec.SECP256R1())

    @classmethod
    def from_private_pem(cls, data: bytes) -> DPoPKey:
        key = serialization.load_pem_private_key(data, password=None)
        if not isinstance(key, ec.EllipticCurvePrivateKey):
            raise ValueError("DPoP private key is not an EC key")
        return cls(key)

    def private_pem(self) -> bytes:
        return self._private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )

    @property
    def public_jwk(self) -> dict[str, str]:
        numbers = self._private_key.public_key().public_numbers()
        return {
            "kty": "EC",
            "crv": "P-256",
            "x": _b64url(numbers.x.to_bytes(32, "big")),
            "y": _b64url(numbers.y.to_bytes(32, "big")),
        }

    @property
    def thumbprint(self) -> str:
        jwk = self.public_jwk
        canonical = json.dumps(
            {"crv": jwk["crv"], "kty": jwk["kty"], "x": jwk["x"], "y": jwk["y"]},
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return _b64url(hashlib.sha256(canonical).digest())

    def proof(self, method: str, url: str, access_token: str | None = None) -> str:
        header = {
            "typ": "dpop+jwt",
            "alg": "ES256",
            "jwk": self.public_jwk,
        }
        payload: dict[str, Any] = {
            "jti": f"dpop_{secrets.token_urlsafe(18)}",
            "iat": int(time.time()),
            "htm": method.upper(),
            "htu": url,
        }
        if access_token:
            payload["ath"] = _b64url(hashlib.sha256(access_token.encode("utf-8")).digest())
        signing_input = f"{_b64url_json(header)}.{_b64url_json(payload)}"
        der_signature = self._private_key.sign(signing_input.encode("ascii"), ec.ECDSA(SHA256()))
        r, s = utils.decode_dss_signature(der_signature)
        raw_signature = r.to_bytes(32, "big") + s.to_bytes(32, "big")
        return f"{signing_input}.{_b64url(raw_signature)}"


def _b64url_json(value: dict[str, Any]) -> str:
    return _b64url(json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8"))


def _b64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")

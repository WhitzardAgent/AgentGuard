"""Local agent identity key management."""
from __future__ import annotations

import base64
import hashlib
import json
import os
import subprocess
from pathlib import Path
from typing import Any

try:  # pragma: no cover - exercised through the normal dependency path.
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
except ModuleNotFoundError:  # pragma: no cover - covered in Dify containers.
    serialization = None  # type: ignore[assignment]
    Ed25519PrivateKey = None  # type: ignore[assignment]


KEY_DIR_ENV = "AGENTGUARD_AGENT_KEY_DIR"


class AgentIdentityKey:
    def __init__(
        self,
        private_key: Any | None = None,
        *,
        private_pem: bytes | None = None,
        public_jwk: dict[str, str] | None = None,
    ) -> None:
        self._private_key = private_key
        self._private_pem = private_pem
        self._public_jwk = public_jwk

    @property
    def public_jwk(self) -> dict[str, str]:
        if self._public_jwk is not None:
            return dict(self._public_jwk)
        if self._private_key is None or serialization is None:
            raise RuntimeError("agent identity key has no public key material")
        public_bytes = self._private_key.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        return {"kty": "OKP", "crv": "Ed25519", "x": _b64url(public_bytes)}

    @property
    def thumbprint(self) -> str:
        canonical = json.dumps(
            self.public_jwk,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return _b64url(hashlib.sha256(canonical).digest())

    def private_pem(self) -> bytes:
        if self._private_pem is not None:
            return self._private_pem
        if self._private_key is None or serialization is None:
            raise RuntimeError("agent identity key has no private key material")
        return self._private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )


def load_or_create_agent_key(stable_id: str) -> AgentIdentityKey:
    key_path = _key_path(stable_id)
    if key_path.exists():
        data = key_path.read_bytes()
        if serialization is not None and Ed25519PrivateKey is not None:
            key = serialization.load_pem_private_key(data, password=None)
            if not isinstance(key, Ed25519PrivateKey):
                raise ValueError(f"agent identity key is not Ed25519: {key_path}")
            return AgentIdentityKey(key)
        return AgentIdentityKey(
            private_pem=data,
            public_jwk=_public_jwk_from_pem_with_openssl(key_path),
        )

    key_path.parent.mkdir(parents=True, exist_ok=True)
    if Ed25519PrivateKey is not None:
        key = AgentIdentityKey(Ed25519PrivateKey.generate())
    else:
        key = _generate_key_with_openssl(key_path)
        try:
            os.chmod(key_path, 0o600)
        except OSError:
            pass
        return key
    key_path.write_bytes(key.private_pem())
    try:
        os.chmod(key_path, 0o600)
    except OSError:
        pass
    return key


def build_agent_registration_payload(
    *,
    provider: str,
    external_agent_id: str,
    agent_type: str,
    provider_instance_id: str | None = None,
    tenant_id: str | None = None,
    account_email: str | None = None,
    name: str | None = None,
    description: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    key = load_or_create_agent_key(
        stable_agent_key_id(
            provider=provider,
            provider_instance_id=provider_instance_id,
            tenant_id=tenant_id,
            external_agent_id=external_agent_id,
            agent_type=agent_type,
        )
    )
    return {
        "provider": _optional_text(provider),
        "provider_instance_id": _optional_text(provider_instance_id),
        "tenant_id": _optional_text(tenant_id),
        "external_agent_id": _optional_text(external_agent_id),
        "agent_type": _optional_text(agent_type),
        "name": _optional_text(name),
        "description": _optional_text(description),
        "account_email": _optional_text(account_email),
        "public_key_jwk": key.public_jwk,
        "metadata": {
            **dict(metadata or {}),
            "agent_public_key_thumbprint": key.thumbprint,
        },
    }


def stable_agent_key_id(
    *,
    provider: str,
    external_agent_id: str,
    agent_type: str,
    provider_instance_id: str | None = None,
    tenant_id: str | None = None,
) -> str:
    return "\x1f".join(
        [
            _optional_text(provider),
            _optional_text(provider_instance_id),
            _optional_text(tenant_id),
            _optional_text(external_agent_id),
            _optional_text(agent_type),
        ]
    )


def _key_path(stable_id: str) -> Path:
    key_dir = Path(os.getenv(KEY_DIR_ENV) or "~/.agentguard/agent_keys").expanduser()
    digest = hashlib.sha256(str(stable_id or "").encode("utf-8")).hexdigest()
    return key_dir / f"{digest}.pem"


def _generate_key_with_openssl(key_path: Path) -> AgentIdentityKey:
    tmp_path = key_path.with_suffix(".tmp")
    try:
        subprocess.run(
            ["openssl", "genpkey", "-algorithm", "Ed25519", "-out", str(tmp_path)],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
        )
        private_pem = tmp_path.read_bytes()
        os.replace(tmp_path, key_path)
        return AgentIdentityKey(
            private_pem=private_pem,
            public_jwk=_public_jwk_from_pem_with_openssl(key_path),
        )
    except FileNotFoundError as exc:
        raise RuntimeError(
            "cryptography is not installed and openssl is unavailable; "
            "cannot create an AgentGuard agent identity key"
        ) from exc
    except subprocess.CalledProcessError as exc:
        message = (exc.stderr or "").strip()
        raise RuntimeError(f"openssl failed to create AgentGuard agent key: {message}") from exc
    finally:
        try:
            tmp_path.unlink()
        except FileNotFoundError:
            pass


def _public_jwk_from_pem_with_openssl(key_path: Path) -> dict[str, str]:
    try:
        result = subprocess.run(
            ["openssl", "pkey", "-in", str(key_path), "-pubout", "-outform", "DER"],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except FileNotFoundError as exc:
        raise RuntimeError(
            "cryptography is not installed and openssl is unavailable; "
            "cannot read an AgentGuard agent identity key"
        ) from exc
    except subprocess.CalledProcessError as exc:
        message = (exc.stderr or b"").decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"openssl failed to read AgentGuard agent key: {message}") from exc
    public_bytes = _ed25519_public_key_from_spki_der(result.stdout)
    return {"kty": "OKP", "crv": "Ed25519", "x": _b64url(public_bytes)}


def _ed25519_public_key_from_spki_der(value: bytes) -> bytes:
    # Ed25519 SubjectPublicKeyInfo DER ends with BIT STRING marker 03 21 00
    # followed by the 32-byte raw public key.
    marker = b"\x03\x21\x00"
    index = value.rfind(marker)
    if index < 0 or len(value) < index + len(marker) + 32:
        raise ValueError("openssl returned an unexpected Ed25519 public key format")
    public_bytes = value[index + len(marker) : index + len(marker) + 32]
    if len(public_bytes) != 32:
        raise ValueError("openssl returned an invalid Ed25519 public key length")
    return public_bytes


def _b64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _optional_text(value: Any) -> str:
    return str(value).strip() if value is not None else ""

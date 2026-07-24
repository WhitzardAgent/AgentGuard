"""Shared internal helpers for Dify runtime adapters."""
from __future__ import annotations

import copy
import hashlib
import json
import logging
import os
import re
import threading
import time
from collections.abc import Generator, Iterable
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable

from agentguard.adapters.agent.normalization import (
    denormalize_llm_output_payload,
    denormalize_tool_result_payload,
)
from agentguard.schemas.context import RuntimeContext
from agentguard.schemas.decisions import DecisionType, GuardDecision
from agentguard.u_guard.agent_keys import load_or_create_agent_key
from agentguard.u_guard.dpop import DPoPKey
from agentguard.u_guard.agent_keys import agent_identity_key_id, build_agent_registration_payload
from agentguard.u_guard.remote_client import RemoteGuardClient
from agentguard.utils.errors import AdapterError
from agentguard.utils.json import safe_dumps, safe_loads

DIFY_RUNTIME_AUTH_KEY_DIR_ENV = "AGENTGUARD_DIFY_RUNTIME_AUTH_KEY_DIR"
_DEFAULT_DPOP_KEY_DIR = "/tmp/agentguard/dify-runtime-auth-keys"
_LOGGER = logging.getLogger(__name__)
_dify_flask_app: Any | None = None
_app_ready_callbacks: list[Callable[[Any], None]] = []
_app_registry_lock = threading.Lock()


@dataclass(frozen=True, slots=True)
class DifyAppInfo:
    app_id: str
    tenant_id: str | None = None
    name: str | None = None
    description: str | None = None
    account_email: str | None = None


@dataclass(frozen=True, slots=True)
class DifyRuntimeSpec:
    adapter_name: str
    runtime_name: str
    agent_type: str
    fallback_agent_id: Callable[[dict[str, Any]], str]
    session_id_from_metadata: Callable[[dict[str, Any]], str]
    external_session_id_from_metadata: Callable[[dict[str, Any]], str | None]
    internal_session_key_from_metadata: Callable[[dict[str, Any], str], str]
    external_agent_id_for_app: Callable[[str], str]


def optional_text(value: Any) -> str | None:
    if value is None:
        return None
    raw = getattr(value, "value", value)
    text = str(raw).strip()
    return text or None


def env_csv(name: str) -> set[str]:
    raw = os.getenv(name, "")
    return {item.strip() for item in raw.split(",") if item.strip()}


def env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def app_allowed(app_id: Any, *, env_csv_loader: Callable[[str], set[str]] = env_csv) -> bool:
    app_ids = env_csv_loader("AGENTGUARD_DIFY_APP_IDS")
    if not app_ids:
        return True
    return str(app_id or "").strip() in app_ids


def catalog_sync_process_allowed(argv: list[str] | tuple[str, ...] | None = None) -> bool:
    role = (os.getenv("AGENTGUARD_DIFY_CATALOG_SYNC_PROCESS") or "api").strip().lower()
    if role in {"all", "*"}:
        return True
    argv_text = " ".join(argv or []).lower()
    if role == "api":
        return "gunicorn" in argv_text and "app:socketio_app" in argv_text
    if role == "worker":
        return "celery" in argv_text
    return role in argv_text


def dify_provider_instance_id() -> str:
    return (
        os.getenv("AGENTGUARD_DIFY_INSTANCE_ID")
        or os.getenv("DIFY_DEPLOYMENT_ID")
        or os.getenv("DIFY_BASE_URL")
        or os.getenv("CONSOLE_API_URL")
        or ""
    ).strip()


def register_dify_agent(
    remote: Any,
    *,
    agent_id: str,
    agent_type: str,
    external_agent_id: str,
    tenant_id: str | None,
    account_email: str | None,
    name: str | None,
    description: str | None,
    metadata: dict[str, Any],
) -> dict[str, Any] | None:
    register = getattr(remote, "register_agent", None)
    if not callable(register):
        return None
    provider_instance = dify_provider_instance_id()
    key_id = agent_identity_key_id(
        provider="dify",
        provider_instance_id=provider_instance,
        tenant_id=tenant_id,
        external_agent_id=external_agent_id,
        agent_type=agent_type,
    )
    payload = build_agent_registration_payload(
        provider="dify",
        provider_instance_id=provider_instance,
        tenant_id=tenant_id,
        external_agent_id=external_agent_id,
        agent_type=agent_type,
        name=name,
        description=description,
        account_email=account_email,
        metadata=metadata,
    )
    registration = register(payload)
    if isinstance(registration, dict):
        registration = dict(registration)
        registration["agent_identity_key_id"] = key_id
    return registration


def dify_account_email_for_app(
    app: Any,
    *,
    optional_text_fn: Callable[[Any], str | None] = optional_text,
) -> str | None:
    for attr in ("dify_user_email", "account_email", "user_email", "email"):
        email = optional_text_fn(getattr(app, attr, None))
        if email and "@" in email:
            return email.lower()

    account_id = optional_text_fn(getattr(app, "updated_by", None)) or optional_text_fn(
        getattr(app, "created_by", None)
    )
    if not account_id:
        return None

    try:
        from extensions.ext_database import db  # type: ignore
        from sqlalchemy import select  # type: ignore
    except Exception:
        return None

    account_cls = None
    for module_name in ("models.account", "models.model"):
        try:
            module = __import__(module_name, fromlist=["Account"])
            account_cls = getattr(module, "Account", None)
        except Exception:
            account_cls = None
        if account_cls is not None:
            break
    if account_cls is None:
        return None

    try:
        stmt = select(account_cls).where(account_cls.id == account_id)
        session = db.session
        account = None
        if hasattr(session, "scalar"):
            account = session.scalar(stmt)
        elif hasattr(session, "execute"):
            result = session.execute(stmt)
            if hasattr(result, "scalar_one_or_none"):
                account = result.scalar_one_or_none()
            elif hasattr(result, "scalars"):
                scalars = result.scalars()
                account = scalars.first() if hasattr(scalars, "first") else None
        email = optional_text_fn(getattr(account, "email", None))
    except Exception:
        return None
    return email.lower() if email and "@" in email else None


def dify_app_info_for_runtime_registration(
    app_id: str,
    *,
    optional_text_fn: Callable[[Any], str | None] = optional_text,
    account_email_for_app: Callable[[Any], str | None] = dify_account_email_for_app,
) -> DifyAppInfo | None:
    try:
        from extensions.ext_database import db  # type: ignore
        from sqlalchemy import select  # type: ignore
    except Exception:
        return None

    app_cls = None
    for module_name in ("models.model", "models"):
        try:
            module = __import__(module_name, fromlist=["App"])
            app_cls = getattr(module, "App", None)
        except Exception:
            app_cls = None
        if app_cls is not None:
            break
    if app_cls is None:
        return None

    try:
        stmt = select(app_cls).where(app_cls.id == app_id)
        session = db.session
        app = None
        if hasattr(session, "scalar"):
            app = session.scalar(stmt)
        elif hasattr(session, "execute"):
            result = session.execute(stmt)
            if hasattr(result, "scalar_one_or_none"):
                app = result.scalar_one_or_none()
            elif hasattr(result, "scalars"):
                scalars = result.scalars()
                app = scalars.first() if hasattr(scalars, "first") else None
        if app is None:
            return None
        return DifyAppInfo(
            app_id=app_id,
            tenant_id=optional_text_fn(getattr(app, "tenant_id", None)),
            name=optional_text_fn(getattr(app, "name", None)),
            description=optional_text_fn(getattr(app, "description", None)),
            account_email=account_email_for_app(app),
        )
    except Exception:
        return None


def runtime_account_email_from_metadata(
    metadata: dict[str, Any],
    *,
    optional_text_fn: Callable[[Any], str | None] = optional_text,
    app_info_loader: Callable[[str], DifyAppInfo | None] | None = None,
) -> str | None:
    for key in ("dify_user_email", "external_account_email", "account_email", "user_email"):
        email = optional_text_fn(metadata.get(key))
        if email and "@" in email:
            return email.lower()
    app_id = optional_text_fn(metadata.get("app_id"))
    if not app_id or app_info_loader is None:
        return None
    app_info = app_info_loader(app_id)
    return optional_text_fn(app_info.account_email) if app_info is not None else None


def metadata_with_registered_agent(
    metadata: dict[str, Any],
    *,
    registration_loader: Callable[[dict[str, Any]], dict[str, Any] | None],
    external_agent_id_for_app: Callable[[str], str],
    runtime_account_email: Callable[[dict[str, Any]], str | None],
    optional_text_fn: Callable[[Any], str | None] = optional_text,
) -> dict[str, Any]:
    if optional_text_fn(metadata.get("agentguard_agent_id")):
        return metadata
    registration = registration_loader(metadata)
    if not registration:
        return metadata
    registered_agent = registration.get("agent") or {}
    canonical_agent_id = optional_text_fn(registered_agent.get("agent_id"))
    if not canonical_agent_id:
        return metadata

    enriched = dict(metadata)
    app_id = optional_text_fn(enriched.get("app_id"))
    enriched["agentguard_agent_id"] = canonical_agent_id
    if app_id:
        enriched["external_agent_id"] = external_agent_id_for_app(app_id)
    if registered_agent.get("agent_identity_code"):
        enriched["agent_identity_code"] = registered_agent.get("agent_identity_code")
    if registered_agent.get("public_key_thumbprint"):
        enriched["agent_public_key_thumbprint"] = registered_agent.get("public_key_thumbprint")
    if registration.get("agent_identity_key_id"):
        enriched["agent_identity_key_id"] = registration.get("agent_identity_key_id")
    user_agent = registration.get("user_agent") or {}
    if "bound" in user_agent:
        enriched["agentguard_user_bound"] = bool(user_agent.get("bound"))
    account_email = runtime_account_email(enriched)
    if account_email:
        enriched["external_provider"] = "dify"
        enriched["external_account_email"] = account_email
        enriched["dify_user_email"] = account_email
    return enriched


def runtime_auth_for_metadata(
    metadata: dict[str, Any],
    *,
    spec: DifyRuntimeSpec,
    agent_id: str,
    fallback_session_id: str,
    runtime_auth_manager: Any,
    runtime_account_email: Callable[[dict[str, Any]], str | None],
    logger: Any,
    log_label: str,
    optional_text_fn: Callable[[Any], str | None] = optional_text,
    env_float_fn: Callable[[str, float], float] = env_float,
) -> Any | None:
    metadata = dict(metadata)
    account_email = runtime_account_email(metadata)
    external_session_id = spec.external_session_id_from_metadata(metadata)
    server_url = os.getenv("AGENTGUARD_SERVER_URL") or None
    if not server_url or not account_email:
        return None
    internal_session_key = spec.internal_session_key_from_metadata(metadata, fallback_session_id)
    auth_metadata = {
        **metadata,
        "external_provider": "dify",
        "external_account_email": account_email,
        "dify_user_email": account_email,
    }
    if external_session_id:
        auth_metadata["external_session_id"] = external_session_id
    else:
        auth_metadata["agentguard_internal_session_key"] = internal_session_key
    try:
        runtime_auth = runtime_auth_manager.ensure(
            server_url=server_url,
            api_key=os.getenv("AGENTGUARD_API_KEY") or None,
            agent_id=agent_id,
            agent_identity_key_id=optional_text_fn(metadata.get("agent_identity_key_id")),
            external_session_id=external_session_id,
            cache_key=external_session_id or internal_session_key,
            account_email=account_email,
            external_user_id=optional_text_fn(metadata.get("user_id")),
            metadata=auth_metadata,
            timeout_s=env_float_fn("AGENTGUARD_DIFY_RUNTIME_AUTH_TIMEOUT_S", 5.0),
            retries=int(env_float_fn("AGENTGUARD_DIFY_RUNTIME_AUTH_RETRIES", 1.0)),
        )
    except Exception as exc:
        logger.warning(
            "AgentGuard %s runtime auth failed for app_id=%s external_session_id=%s: %s",
            log_label,
            metadata.get("app_id"),
            external_session_id,
            exc,
        )
        raise AdapterError(f"AgentGuard Dify runtime auth failed: {exc}") from exc
    if runtime_auth is None or not getattr(runtime_auth, "session_token", None):
        raise AdapterError("AgentGuard Dify runtime auth failed: server returned no session token")
    return runtime_auth


def make_dify_guard(
    metadata: dict[str, Any],
    *,
    spec: DifyRuntimeSpec,
    metadata_enricher: Callable[[dict[str, Any]], dict[str, Any]],
    runtime_auth_loader: Callable[[dict[str, Any], str, str], Any | None],
    plugin_config_loader: Callable[[], str | dict[str, Any] | None],
    optional_text_fn: Callable[[Any], str | None] = optional_text,
    task_id_key: str | None = None,
) -> Any:
    from agentguard.guard import AgentGuard

    metadata = metadata_enricher(metadata)
    session_id = spec.session_id_from_metadata(metadata)
    agent_id = optional_text_fn(metadata.get("agentguard_agent_id")) or spec.fallback_agent_id(metadata)
    runtime_auth = runtime_auth_loader(metadata, agent_id, session_id)
    if runtime_auth is not None:
        session_id = runtime_auth.session_id or session_id
        if runtime_auth.canonical_user_id:
            metadata["agentguard_user_id"] = runtime_auth.canonical_user_id
            metadata["user_id"] = runtime_auth.canonical_user_id
        metadata["agentguard_session_id"] = session_id
    guard = AgentGuard(
        session_id,
        user_id=runtime_auth.canonical_user_id if runtime_auth is not None else optional_text_fn(metadata.get("user_id")),
        agent_id=agent_id,
        policy=os.getenv("AGENTGUARD_POLICY") or None,
        server_url=os.getenv("AGENTGUARD_SERVER_URL") or None,
        api_key=os.getenv("AGENTGUARD_API_KEY") or None,
        environment=os.getenv("AGENTGUARD_ENVIRONMENT") or "dify",
        sandbox="noop",
        plugin_config=plugin_config_loader(),
        session_token=runtime_auth.session_token if runtime_auth is not None else None,
        dpop_proof_factory=runtime_auth.proof if runtime_auth is not None else None,
        use_dpop_auth=runtime_auth is not None,
        legacy_identity_headers=runtime_auth is None,
        auto_register_session=runtime_auth is None,
        auto_close_runtime_session=runtime_auth is None,
    )
    guard.context.metadata.update(metadata)
    if task_id_key and metadata.get(task_id_key):
        guard.context.task_id = str(metadata[task_id_key])
    return guard


def runtime_agent_registration(
    metadata: dict[str, Any],
    *,
    spec: DifyRuntimeSpec,
    app_info: DifyAppInfo,
    registration_metadata: dict[str, Any],
    remote_client_cls: Any,
    register_agent_fn: Callable[..., dict[str, Any] | None],
    registration_cache: dict[tuple[str, str, str, str, str], dict[str, Any]],
    registration_lock: threading.Lock,
    env_float_fn: Callable[[str, float], float] = env_float,
) -> dict[str, Any] | None:
    if not os.getenv("AGENTGUARD_SERVER_URL"):
        return None
    cache_key = (
        "dify",
        dify_provider_instance_id() or "",
        app_info.tenant_id or "",
        app_info.app_id,
        spec.agent_type,
    )
    with registration_lock:
        cached = registration_cache.get(cache_key)
    if cached is not None:
        return cached

    remote_kwargs = {
        "api_key": os.getenv("AGENTGUARD_API_KEY") or None,
        "timeout_s": env_float_fn("AGENTGUARD_DIFY_CATALOG_SYNC_TIMEOUT_S", 5.0),
        "retries": int(env_float_fn("AGENTGUARD_DIFY_CATALOG_SYNC_RETRIES", 1.0)),
    }
    if spec.agent_type == "workflow":
        remote_kwargs.update(
            {
                "session_id": f"dify-workflow-runtime-register:{app_info.app_id}",
                "agent_id": spec.external_agent_id_for_app(app_info.app_id),
                "session_key": registration_metadata.get("client_session_key"),
            }
        )
    remote = remote_client_cls(os.getenv("AGENTGUARD_SERVER_URL") or None, **remote_kwargs)
    if not getattr(remote, "enabled", False):
        return None

    try:
        registration = register_agent_fn(
            remote,
            agent_id=spec.external_agent_id_for_app(app_info.app_id),
            agent_type=spec.agent_type,
            external_agent_id=app_info.app_id,
            tenant_id=app_info.tenant_id,
            account_email=app_info.account_email,
            name=app_info.name,
            description=app_info.description,
            metadata=registration_metadata,
        )
    except Exception:
        return None
    if not registration:
        return None
    with registration_lock:
        registration_cache[cache_key] = registration
    return registration


def catalog_fingerprint(tools: list[dict[str, Any]], version: str | None = None) -> str:
    payload = {"version": version, "tools": tools}
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def catalog_fingerprint_unchanged(
    key: str,
    fingerprint: str,
    *,
    cache: dict[str, str],
    lock: threading.Lock,
) -> bool:
    with lock:
        return cache.get(key) == fingerprint


def remember_catalog_fingerprint(
    key: str,
    fingerprint: str,
    *,
    cache: dict[str, str],
    lock: threading.Lock,
) -> None:
    with lock:
        cache[key] = fingerprint


def clear_catalog_fingerprints(*, cache: dict[str, str], lock: threading.Lock) -> None:
    with lock:
        cache.clear()


@dataclass
class DifyRuntimeAuthState:
    agent_id: str
    cache_key: str
    external_session_id: str | None
    dpop_key: DPoPKey
    session_id: str | None = None
    session_token: str | None = None
    expires_at: int = 0
    canonical_user_id: str | None = None

    def proof(self, method: str, url: str, access_token: str | None = None) -> str:
        return self.dpop_key.proof(method, url, access_token)

    def token_valid(self, *, refresh_window_s: int = 60) -> bool:
        return bool(self.session_token and self.expires_at - int(time.time()) > refresh_window_s)


class DifyRuntimeAuthManager:
    def __init__(self) -> None:
        self._states: dict[tuple[str, str], DifyRuntimeAuthState] = {}
        self._lock = threading.Lock()

    def get(self, agent_id: str, cache_key: str) -> DifyRuntimeAuthState | None:
        with self._lock:
            return self._states.get((agent_id, cache_key))

    def ensure(
        self,
        *,
        server_url: str | None,
        api_key: str | None,
        agent_id: str,
        agent_identity_key_id: str | None,
        external_session_id: str | None,
        account_email: str | None,
        external_user_id: str | None,
        metadata: dict[str, Any],
        timeout_s: float,
        retries: int,
        cache_key: str | None = None,
    ) -> DifyRuntimeAuthState | None:
        if not server_url or not account_email:
            return None
        state = self._state(agent_id, cache_key or external_session_id, external_session_id=external_session_id)
        if state.token_valid():
            return state
        if state.session_token:
            try:
                self._refresh(state, server_url=server_url, api_key=api_key, timeout_s=timeout_s, retries=retries)
                if state.token_valid(refresh_window_s=0):
                    return state
            except Exception:
                state.session_token = None
                state.expires_at = 0
        self._create(
            state,
            server_url=server_url,
            api_key=api_key,
            agent_identity_key_id=agent_identity_key_id,
            account_email=account_email,
            external_user_id=external_user_id,
            metadata=metadata,
            timeout_s=timeout_s,
            retries=retries,
        )
        return state if state.session_token else None

    def _state(
        self,
        agent_id: str,
        cache_key: str | None,
        *,
        external_session_id: str | None,
    ) -> DifyRuntimeAuthState:
        resolved_cache_key = optional_text(cache_key) or f"agentguard-internal:{time.time_ns()}"
        resolved_external_session_id = optional_text(external_session_id)
        with self._lock:
            state = self._states.get((agent_id, resolved_cache_key))
            if state is None:
                state = DifyRuntimeAuthState(
                    agent_id=agent_id,
                    cache_key=resolved_cache_key,
                    external_session_id=resolved_external_session_id,
                    dpop_key=_load_or_create_dpop_key(agent_id=agent_id, cache_key=resolved_cache_key),
                )
                self._states[(agent_id, resolved_cache_key)] = state
            return state

    def _client(
        self,
        state: DifyRuntimeAuthState,
        *,
        server_url: str,
        api_key: str | None,
        timeout_s: float,
        retries: int,
    ) -> RemoteGuardClient:
        return RemoteGuardClient(
            server_url,
            api_key=api_key,
            session_token=state.session_token,
            dpop_proof_factory=state.proof,
            use_dpop_auth=True,
            legacy_identity_headers=False,
            timeout_s=timeout_s,
            retries=retries,
        )

    def _create(
        self,
        state: DifyRuntimeAuthState,
        *,
        server_url: str,
        api_key: str | None,
        agent_identity_key_id: str | None,
        account_email: str,
        external_user_id: str | None,
        metadata: dict[str, Any],
        timeout_s: float,
        retries: int,
    ) -> None:
        client = self._client(
            state,
            server_url=server_url,
            api_key=api_key,
            timeout_s=timeout_s,
            retries=retries,
        )
        body = {
            "provider": "dify",
            "agent_id": state.agent_id,
            "account_email": account_email,
            "external_user_id": external_user_id,
            "metadata": metadata,
        }
        if state.external_session_id:
            body["external_session_id"] = state.external_session_id
        result = client.create_runtime_session(
            body,
            extra_headers_factory=self._agent_identity_headers_factory(
                state,
                agent_identity_key_id=agent_identity_key_id,
            ),
        )
        self._update_state(state, result)

    def _agent_identity_headers_factory(
        self,
        state: DifyRuntimeAuthState,
        *,
        agent_identity_key_id: str | None,
    ) -> Callable[[str, str, dict[str, Any]], dict[str, str]] | None:
        key_id = optional_text(agent_identity_key_id)
        if not key_id:
            return None
        agent_key = load_or_create_agent_key(key_id)

        def _headers(method: str, url: str, body: dict[str, Any]) -> dict[str, str]:
            return {
                "X-AgentGuard-Agent-Proof": agent_key.sign_session_create_proof(
                    agent_id=state.agent_id,
                    method=method,
                    url=url,
                    body=body,
                    dpop_jkt=state.dpop_key.thumbprint,
                )
            }

        return _headers

    def _refresh(
        self,
        state: DifyRuntimeAuthState,
        *,
        server_url: str,
        api_key: str | None,
        timeout_s: float,
        retries: int,
    ) -> None:
        client = self._client(
            state,
            server_url=server_url,
            api_key=api_key,
            timeout_s=timeout_s,
            retries=retries,
        )
        self._update_state(state, client.refresh_runtime_session())

    def _update_state(self, state: DifyRuntimeAuthState, result: dict[str, Any]) -> None:
        state.session_id = optional_text(result.get("session_id"))
        state.session_token = optional_text(result.get("session_token"))
        state.canonical_user_id = optional_text(result.get("user_id"))
        try:
            state.expires_at = int(result.get("expires_at") or 0)
        except (TypeError, ValueError):
            state.expires_at = 0


manager = DifyRuntimeAuthManager()


def _load_or_create_dpop_key(*, agent_id: str, cache_key: str) -> DPoPKey:
    key_path = _dpop_key_path(agent_id=agent_id, cache_key=cache_key)
    try:
        if key_path.exists():
            return DPoPKey.from_private_pem(key_path.read_bytes())
        key_path.parent.mkdir(parents=True, exist_ok=True)
        key = DPoPKey()
        data = key.private_pem()
        try:
            fd = os.open(str(key_path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            return DPoPKey.from_private_pem(key_path.read_bytes())
        with os.fdopen(fd, "wb") as file:
            file.write(data)
        return key
    except Exception as exc:
        _LOGGER.warning("AgentGuard Dify DPoP key persistence failed at %s: %s", key_path, exc)
        return DPoPKey()


def _dpop_key_path(*, agent_id: str, cache_key: str) -> Path:
    root = Path(os.getenv(DIFY_RUNTIME_AUTH_KEY_DIR_ENV) or _DEFAULT_DPOP_KEY_DIR)
    digest = hashlib.sha256(f"{agent_id}\0{cache_key}".encode("utf-8")).hexdigest()
    return root / f"{digest}.pem"


def sync_tools_to_agentguard(
    tools: list[dict[str, Any]],
    *,
    spec: DifyRuntimeSpec,
    app_info: DifyAppInfo,
    metadata: dict[str, Any],
    session_id: str,
    session_key: str,
    fingerprint_key: str,
    fingerprint_version: str | None,
    result_fields: dict[str, Any],
    remote_client_cls: Any,
    register_agent_fn: Callable[..., dict[str, Any] | None],
    fingerprint_cache: dict[str, str],
    fingerprint_lock: threading.Lock,
    env_float_fn: Callable[[str, float], float] = env_float,
) -> dict[str, Any] | None:
    agent_id = spec.external_agent_id_for_app(app_info.app_id)
    fingerprint = catalog_fingerprint(tools, fingerprint_version)
    context = RuntimeContext(
        session_id=session_id,
        agent_id=agent_id,
        user_id=None,
        environment=os.getenv("AGENTGUARD_ENVIRONMENT") or "dify",
        metadata=metadata,
    )
    remote = remote_client_cls(
        os.getenv("AGENTGUARD_SERVER_URL") or None,
        api_key=os.getenv("AGENTGUARD_API_KEY") or None,
        session_id=session_id,
        agent_id=agent_id,
        session_key=session_key,
        timeout_s=env_float_fn("AGENTGUARD_DIFY_CATALOG_SYNC_TIMEOUT_S", 5.0),
        retries=int(env_float_fn("AGENTGUARD_DIFY_CATALOG_SYNC_RETRIES", 1.0)),
    )
    if not getattr(remote, "enabled", False):
        return None
    registration = register_agent_fn(
        remote,
        agent_id=agent_id,
        agent_type=spec.agent_type,
        external_agent_id=app_info.app_id,
        tenant_id=app_info.tenant_id,
        account_email=app_info.account_email,
        name=app_info.name,
        description=app_info.description,
        metadata=metadata,
    )
    if registration:
        registered_agent = registration.get("agent") or {}
        canonical_agent_id = optional_text(registered_agent.get("agent_id"))
        if canonical_agent_id:
            agent_id = canonical_agent_id
            context.agent_id = canonical_agent_id
            remote.agent_id = canonical_agent_id
            metadata["external_agent_id"] = spec.external_agent_id_for_app(app_info.app_id)
        metadata["agent_identity_code"] = registered_agent.get("agent_identity_code")
        metadata["agent_public_key_thumbprint"] = registered_agent.get("public_key_thumbprint")
        metadata["agentguard_user_bound"] = bool((registration.get("user_agent") or {}).get("bound"))
    if catalog_fingerprint_unchanged(
        fingerprint_key,
        fingerprint,
        cache=fingerprint_cache,
        lock=fingerprint_lock,
    ):
        return {
            **result_fields,
            "agent_id": agent_id,
            "tool_count": len(tools),
            "skipped": True,
            "reason": "unchanged",
        }
    result = remote.sync_tools(context, tools)
    remember_catalog_fingerprint(
        fingerprint_key,
        fingerprint,
        cache=fingerprint_cache,
        lock=fingerprint_lock,
    )
    return {
        **result_fields,
        "agent_id": agent_id,
        "tool_count": result.get("tool_count", len(tools)),
    }


def register_dify_flask_app(app: Any) -> bool:
    """Remember the Dify Flask app created by Dify itself."""
    if app is None or not callable(getattr(app, "app_context", None)):
        return False
    global _dify_flask_app
    with _app_registry_lock:
        _dify_flask_app = app
        callbacks = list(_app_ready_callbacks)
        _app_ready_callbacks.clear()
    for callback in callbacks:
        try:
            callback(app)
        except Exception:
            pass
    return True


def get_dify_flask_app() -> Any | None:
    if _dify_flask_app is not None:
        return _dify_flask_app
    try:
        from flask import current_app  # type: ignore

        app = current_app._get_current_object()
    except Exception:
        return None
    register_dify_flask_app(app)
    return app


def on_dify_flask_app_ready(callback: Callable[[Any], None]) -> bool:
    with _app_registry_lock:
        app = _dify_flask_app
        if app is None:
            _app_ready_callbacks.append(callback)
            return False
    try:
        callback(app)
    except Exception:
        pass
    return True


@dataclass(frozen=True)
class DifyLegacyLLMCall:
    prompt_messages: Any = None
    model_parameters: Any = None
    tools: Any = None
    stop: Any = None
    stream: Any = True
    callbacks: Any = None

    @classmethod
    def from_args_kwargs(
        cls,
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
    ) -> DifyLegacyLLMCall:
        names = ["prompt_messages", "model_parameters", "tools", "stop", "stream", "callbacks"]
        values = {name: kwargs.get(name) for name in names if name in kwargs}
        for index, value in enumerate(args):
            if index < len(names) and names[index] not in values:
                values[names[index]] = value
        values.setdefault("prompt_messages", [])
        values.setdefault("tools", None)
        values.setdefault("stream", True)
        return cls(**{name: values.get(name) for name in names})

    def get(self, key: str, default: Any = None) -> Any:
        return getattr(self, key, default)


GuardInput = Callable[[Any, DifyLegacyLLMCall, dict[str, Any] | None], GuardDecision]
GuardOutput = Callable[[Any, Any, DifyLegacyLLMCall, str | None, dict[str, Any] | None], GuardDecision]
BlockedValue = Callable[[GuardDecision], str | None]
Executor = Callable[[tuple[Any, ...], dict[str, Any]], Any]
ModifyOutput = Callable[[Any, Any], Any]
StreamResultBuilder = Callable[[str | None], Generator[Any, None, None]]


def run_dify_legacy_llm_call(
    *,
    model: Any,
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
    arg_names: tuple[str, ...],
    execute: Executor,
    guard_input: GuardInput,
    guard_output: GuardOutput,
    blocked_value: BlockedValue,
    normalizer: DifyLegacyLLMNormalizer,
    modify_output: ModifyOutput | None = None,
    stream_result_builder: StreamResultBuilder | None = None,
) -> Any:
    current_args = tuple(args)
    current_kwargs = dict(kwargs)
    call = DifyLegacyLLMCall.from_args_kwargs(current_args, current_kwargs)
    decision = guard_input(model, call, None)
    if decision.decision_type == DecisionType.MODIFY_LLM_INPUT:
        current_args, current_kwargs = replace_named_argument(
            current_args,
            current_kwargs,
            arg_names,
            "prompt_messages",
            decision_payload(decision),
        )
        call = DifyLegacyLLMCall.from_args_kwargs(current_args, current_kwargs)
    blocked = blocked_value(decision)
    if blocked is not None:
        raise AdapterError(blocked)
    try:
        result = execute(current_args, current_kwargs)
    except Exception as exc:
        guard_output(model, {"error": str(exc)}, call, str(exc), None)
        raise
    if is_generator_like(result):
        return _finalize_legacy_llm_stream(
            model=model,
            result=result,
            call=call,
            guard_output=guard_output,
            blocked_value=blocked_value,
            normalizer=normalizer,
            stream_result_builder=stream_result_builder,
        )
    decision = guard_output(model, result, call, None, None)
    if decision.decision_type == DecisionType.MODIFY_LLM_OUTPUT:
        output_modifier = modify_output or modified_llm_output_value
        result = output_modifier(decision_payload(decision), result)
    blocked = blocked_value(decision)
    if blocked is not None:
        raise AdapterError(blocked)
    return result


def _finalize_legacy_llm_stream(
    *,
    model: Any,
    result: Any,
    call: DifyLegacyLLMCall,
    guard_output: GuardOutput,
    blocked_value: BlockedValue,
    normalizer: DifyLegacyLLMNormalizer,
    stream_result_builder: StreamResultBuilder | None = None,
) -> Generator[Any, None, None]:
    chunks: list[Any] = []
    try:
        for chunk in result:
            chunks.append(chunk)
    except Exception as exc:
        guard_output(model, {"error": str(exc)}, call, str(exc), None)
        raise

    normalized_output = normalizer.stream_output_payload(chunks)
    decision = guard_output(model, normalized_output, call, None, None)
    stream_builder = stream_result_builder or _default_stream_result_builder

    if decision.decision_type == DecisionType.MODIFY_LLM_OUTPUT:
        payload = decision_payload(decision)
        text = _stream_result_text(
            payload,
            normalizer=normalizer,
            fallback=_stream_result_text(normalized_output, normalizer=normalizer),
        )
        return stream_builder(text)

    blocked = blocked_value(decision)
    if blocked is not None:
        return stream_builder(blocked)

    return _replay_legacy_llm_chunks(chunks)


def _replay_legacy_llm_chunks(chunks: list[Any]) -> Generator[Any, None, None]:
    for chunk in chunks:
        yield chunk


def _stream_result_text(
    value: Any,
    *,
    normalizer: DifyLegacyLLMNormalizer,
    fallback: str | None = None,
) -> str | None:
    if value is None:
        return fallback
    if isinstance(value, str):
        return value
    payload = normalizer.output_payload(value)
    if isinstance(payload, dict):
        for key in ("final_output", "output", "content", "text", "message"):
            text = normalizer.content_to_optional_text(payload.get(key))
            if text is not None:
                return text
    return fallback


def build_synthetic_llm_stream_chunk(text: str | None) -> Any:
    content = text or ""
    return SimpleNamespace(
        delta=SimpleNamespace(
            message=SimpleNamespace(content=content, tool_calls=[]),
            usage=None,
        )
    )


def _default_stream_result_builder(text: str | None) -> Generator[Any, None, None]:
    yield build_synthetic_llm_stream_chunk(text)


class DifyLegacyLLMNormalizer:
    def __init__(
        self,
        *,
        content_list_joiner: str = "\n",
        stream_text_joiner: str = "\n",
        preserve_dict_payload: bool = False,
        include_message_key: bool = False,
        include_text_data_attrs: bool = False,
        object_message_payload: bool = False,
        object_parts_payload: bool = False,
        include_stream_tool_calls: bool = False,
        value_attrs: tuple[str, ...] = ("role", "content", "name", "tool_name", "tool_call_id"),
    ) -> None:
        self.content_list_joiner = content_list_joiner
        self.stream_text_joiner = stream_text_joiner
        self.preserve_dict_payload = preserve_dict_payload
        self.include_message_key = include_message_key
        self.include_text_data_attrs = include_text_data_attrs
        self.object_message_payload = object_message_payload
        self.object_parts_payload = object_parts_payload
        self.include_stream_tool_calls = include_stream_tool_calls
        self.value_attrs = value_attrs

    def normalize_messages(self, messages: Any) -> list[dict[str, Any]]:
        if isinstance(messages, list):
            normalized: list[dict[str, Any]] = []
            for item in messages:
                if isinstance(item, dict):
                    normalized.append(
                        {
                            **item,
                            "role": str(item.get("role") or "user"),
                            "content": self.content_to_text(item.get("content")),
                        }
                    )
                else:
                    normalized.append(self.prompt_message_to_message(item))
            return normalized
        if messages is None:
            return []
        return [self.prompt_message_to_message(messages)]

    def prompt_message_to_message(self, message: Any) -> dict[str, Any]:
        role = self.message_role(message)
        content = get_attr_or_key(message, "content")
        data = self.normalize_value(message)
        if isinstance(data, dict):
            data.setdefault("role", role)
            data.setdefault("content", self.content_to_text(content))
            return data
        return {"role": role, "content": self.content_to_text(content)}

    def message_role(self, message: Any) -> str:
        name = type(message).__name__.lower()
        if "system" in name:
            return "system"
        if "assistant" in name:
            return "assistant"
        if "tool" in name:
            return "tool"
        return "user"

    def stream_output_payload(self, chunks: list[Any]) -> dict[str, Any]:
        text_parts: list[str] = []
        thought_parts: list[str] = []
        tool_calls: list[Any] = []
        for chunk in chunks:
            delta = get_attr_or_key(chunk, "delta")
            message = get_attr_or_key(delta, "message")
            content = get_attr_or_key(message, "content")
            if content is not None:
                text_parts.append(self.content_to_text(content))
            thought = self.extract_llm_thought(delta) or self.extract_llm_thought(message) or self.extract_llm_thought(chunk)
            if thought is not None:
                thought_parts.append(thought)
            calls = get_attr_or_key(message, "tool_calls")
            if self.include_stream_tool_calls and calls:
                tool_calls.extend(list(calls))
        output = self.stream_text_joiner.join(part for part in text_parts if part) or None
        payload = self.output_payload_from_text(
            output,
            thought="\n\n".join(thought_parts) or None,
        )
        if self.include_stream_tool_calls and tool_calls:
            payload["tool_calls"] = self.normalize_value(tool_calls)
        return payload

    def output_payload(self, output: Any) -> dict[str, Any]:
        if isinstance(output, dict):
            if "output" in output:
                text = self.content_to_optional_text(output.get("output"))
            elif "content" in output:
                text = self.content_to_optional_text(output.get("content"))
            elif "text" in output:
                text = self.content_to_optional_text(output.get("text"))
            elif self.include_message_key and "message" in output:
                text = self.content_to_optional_text(output.get("message"))
            elif output.get("tool_calls"):
                text = None
            else:
                text = self.content_to_text(output)
            payload = self.output_payload_from_text(
                text,
                thought=self.extract_llm_thought(output),
                final_output=self.content_to_optional_text(output.get("final_output"))
                if "final_output" in output
                else None,
            )
            if self.preserve_dict_payload:
                preserved = dict(output)
                preserved.update(payload)
                return preserved
            return payload

        if self.object_message_payload:
            message = get_attr_or_key(output, "message")
            if message is not None:
                content = get_attr_or_key(message, "content")
                tool_calls = get_attr_or_key(message, "tool_calls")
                payload = self.output_payload_from_text(
                    self.content_to_text(content),
                    thought=self.extract_llm_thought(output) or self.extract_llm_thought(message),
                )
                if tool_calls:
                    payload["tool_calls"] = self.normalize_value(tool_calls)
                return payload

        if self.object_parts_payload:
            parts = getattr(output, "parts", None)
            if isinstance(parts, list):
                text_parts: list[str] = []
                for part in parts:
                    content = getattr(part, "content", None)
                    if content is not None:
                        text_parts.append(self.content_to_text(content))
                text = "\n".join(part for part in text_parts if part) or None
                return self.output_payload_from_text(
                    text,
                    thought=self.extract_llm_thought({"parts": parts}),
                )

        return self.output_payload_from_text(
            self.content_to_text(output),
            thought=self.extract_llm_thought(output),
        )

    def output_payload_from_text(
        self,
        output: str | None,
        *,
        thought: str | None = None,
        final_output: str | None = None,
    ) -> dict[str, Any]:
        parsed = parse_tagged_llm_output(output) if output is not None else ParsedLLMOutput(None, None)
        payload = {
            "output": output,
            "final_output": final_output if final_output is not None else parsed.final_output,
        }
        resolved_thought = thought if thought is not None else parsed.thought
        if resolved_thought is not None:
            payload["thought"] = resolved_thought
        return payload

    def extract_llm_thought(self, value: Any) -> str | None:
        if isinstance(value, dict):
            direct = first_non_empty_text(
                self,
                value,
                "thought",
                "reasoning_content",
                "reasoningContent",
                "thinking",
                "reasoning",
            )
            if direct is not None:
                return direct
            for container_key in ("additional_kwargs", "response_metadata", "metadata", "extra"):
                nested = value.get(container_key)
                if isinstance(nested, dict):
                    nested_thought = self.extract_llm_thought(nested)
                    if nested_thought is not None:
                        return nested_thought
            for blocks_key in ("parts", "content", "output"):
                blocks = value.get(blocks_key)
                if isinstance(blocks, list):
                    block_thought = self.extract_llm_thought_from_blocks(blocks)
                    if block_thought is not None:
                        return block_thought
            return None

        direct = first_non_empty_attr(
            self,
            value,
            "thought",
            "reasoning_content",
            "reasoningContent",
            "thinking",
            "reasoning",
        )
        if direct is not None:
            return direct
        parts = getattr(value, "parts", None)
        if isinstance(parts, list):
            return self.extract_llm_thought_from_blocks(parts)
        return None

    def extract_llm_thought_from_blocks(self, blocks: list[Any]) -> str | None:
        thought_parts: list[str] = []
        for block in blocks:
            if isinstance(block, dict):
                block_type = str(block.get("type") or block.get("kind") or "").lower()
                if block_type in {"thinking", "thinkingblock", "reasoning", "reasoningblock"}:
                    text = first_non_empty_text(self, block, "content", "text", "thinking", "reasoning", "summary")
                    if text is not None:
                        thought_parts.append(text)
            else:
                block_type = str(getattr(block, "type", None) or getattr(block, "kind", None) or "").lower()
                if block_type in {"thinking", "thinkingblock", "reasoning", "reasoningblock"}:
                    text = first_non_empty_attr(self, block, "content", "text", "thinking", "reasoning", "summary")
                    if text is not None:
                        thought_parts.append(text)
        return "\n\n".join(thought_parts) or None

    def normalize_value(self, value: Any) -> Any:
        if value is None or isinstance(value, bool | int | float | str):
            return value
        if isinstance(value, bytes):
            return value.decode("utf-8", errors="replace")
        if isinstance(value, dict):
            return {str(key): self.normalize_value(item) for key, item in value.items()}
        if isinstance(value, list | tuple | set | frozenset):
            return [self.normalize_value(item) for item in value]
        for attr in ("model_dump", "to_dict", "dict"):
            dumper = getattr(value, attr, None)
            if callable(dumper):
                try:
                    return self.normalize_value(dumper())
                except Exception:
                    continue
        data: dict[str, Any] = {}
        for attr in self.value_attrs:
            item = getattr(value, attr, None)
            if item is not None:
                data[attr] = self.normalize_value(item)
        return data or str(value)

    def content_to_text(self, value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, str):
            return value
        if isinstance(value, bytes):
            return value.decode("utf-8", errors="replace")
        if isinstance(value, list | tuple):
            return self.content_list_joiner.join(self.content_to_text(item) for item in value)
        if isinstance(value, dict):
            return safe_dumps(value)
        if self.include_text_data_attrs:
            text = getattr(value, "text", None)
            if text is not None:
                return self.content_to_text(text)
            data = getattr(value, "data", None)
            if data is not None:
                return self.content_to_text(data)
        return str(value)

    def content_to_optional_text(self, value: Any) -> str | None:
        if value is None:
            return None
        text = self.content_to_text(value)
        return text if text else None


@dataclass(frozen=True)
class ParsedLLMOutput:
    thought: str | None
    final_output: str | None


_THOUGHT_TAG_RE = re.compile(
    r"<(?P<tag>think|thought|reason|reasoning)\b[^>]*>(?P<body>.*?)</(?P=tag)>",
    flags=re.IGNORECASE | re.DOTALL,
)
_FINAL_TAG_RE = re.compile(
    r"<(?P<tag>answer|final|final_output)\b[^>]*>(?P<body>.*?)</(?P=tag)>",
    flags=re.IGNORECASE | re.DOTALL,
)


def parse_tagged_llm_output(output: str) -> ParsedLLMOutput:
    thought_matches = list(_THOUGHT_TAG_RE.finditer(output))
    if not thought_matches:
        return ParsedLLMOutput(thought=None, final_output=output)

    thought_parts = [match.group("body").strip() for match in thought_matches]
    thought = "\n\n".join(part for part in thought_parts if part) or None
    remainder = _THOUGHT_TAG_RE.sub("", output).strip()

    final_matches = list(_FINAL_TAG_RE.finditer(remainder))
    if final_matches:
        final_parts = [match.group("body").strip() for match in final_matches]
        final_output = "\n\n".join(part for part in final_parts if part)
    else:
        final_output = remainder

    return ParsedLLMOutput(thought=thought, final_output=final_output)


def first_non_empty_text(
    normalizer: DifyLegacyLLMNormalizer,
    value: dict[str, Any],
    *keys: str,
) -> str | None:
    for key in keys:
        text = normalizer.content_to_optional_text(value.get(key))
        if text is not None:
            return text
    return None


def first_non_empty_attr(
    normalizer: DifyLegacyLLMNormalizer,
    value: Any,
    *keys: str,
) -> str | None:
    for key in keys:
        text = normalizer.content_to_optional_text(getattr(value, key, None))
        if text is not None:
            return text
    return None


def get_attr_or_key(value: Any, key: str, default: Any = None) -> Any:
    if value is None:
        return default
    if isinstance(value, dict):
        return value.get(key, default)
    return getattr(value, key, default)


def is_generator_like(value: Any) -> bool:
    return isinstance(value, Iterable) and not isinstance(value, str | bytes | dict | list | tuple)


def decision_payload(decision: GuardDecision) -> Any:
    payload = decision.processed_content
    if not isinstance(payload, str):
        return payload
    text = payload.strip()
    if not text or text[0] not in {"{", "["}:
        return payload
    return safe_loads(text, fallback=payload)


def replace_named_argument(
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
    names: tuple[str, ...],
    key: str,
    value: Any,
) -> tuple[tuple[Any, ...], dict[str, Any]]:
    current_args = list(args)
    current_kwargs = dict(kwargs)
    if key in current_kwargs:
        current_kwargs[key] = value
        return tuple(current_args), current_kwargs

    try:
        index = names.index(key)
    except ValueError:
        current_kwargs[key] = value
        return tuple(current_args), current_kwargs

    if index < len(current_args):
        current_args[index] = value
    else:
        current_kwargs[key] = value
    return tuple(current_args), current_kwargs


def modified_llm_output_value(payload: Any, result: Any) -> Any:
    return denormalize_llm_output_payload(payload=payload, output=result)


def modified_tool_result_value(payload: Any, result: Any, *, error: str | None = None) -> Any:
    return denormalize_tool_result_payload(
        payload=payload,
        result=result,
        error=error,
    ).result


def modified_message_llm_output_value(payload: Any, result: Any) -> Any:
    message = get_attr_or_key(result, "message")
    if message is not None and hasattr(message, "content"):
        updated_result = copy.copy(result)
        updated_message = copy.copy(message)
        updated_message.content = denormalize_llm_output_payload(
            payload=payload,
            output=getattr(message, "content", None),
        )
        updated_result.message = updated_message
        return updated_result
    return denormalize_llm_output_payload(payload=payload, output=result)


__all__ = [
    "DIFY_RUNTIME_AUTH_KEY_DIR_ENV",
    "DifyAppInfo",
    "DifyLegacyLLMCall",
    "DifyLegacyLLMNormalizer",
    "DifyRuntimeAuthManager",
    "DifyRuntimeAuthState",
    "DifyRuntimeSpec",
    "ParsedLLMOutput",
    "app_allowed",
    "catalog_fingerprint",
    "catalog_fingerprint_unchanged",
    "catalog_sync_process_allowed",
    "clear_catalog_fingerprints",
    "decision_payload",
    "dify_account_email_for_app",
    "dify_app_info_for_runtime_registration",
    "dify_provider_instance_id",
    "env_csv",
    "env_float",
    "get_attr_or_key",
    "get_dify_flask_app",
    "is_generator_like",
    "make_dify_guard",
    "manager",
    "metadata_with_registered_agent",
    "modified_llm_output_value",
    "modified_message_llm_output_value",
    "modified_tool_result_value",
    "on_dify_flask_app_ready",
    "optional_text",
    "parse_tagged_llm_output",
    "register_dify_agent",
    "register_dify_flask_app",
    "remember_catalog_fingerprint",
    "replace_named_argument",
    "run_dify_legacy_llm_call",
    "runtime_account_email_from_metadata",
    "runtime_agent_registration",
    "runtime_auth_for_metadata",
    "sync_tools_to_agentguard",
]

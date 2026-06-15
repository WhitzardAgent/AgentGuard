"""In-memory trace/decision storage."""
from __future__ import annotations

import threading
from typing import Any

from shared.schemas.context import RuntimeContext
from shared.utils.time import now_ts


def _session_storage_key(
    session_id: str | None,
    agent_id: str | None = None,
    user_id: str | None = None,
) -> str:
    return f"{session_id or 'unknown'}::{agent_id or 'unknown'}::{user_id or 'unknown'}"


class TraceStore:
    def __init__(self) -> None:
        self._traces: dict[str, list[dict[str, Any]]] = {}

    def append(
        self,
        session_id: str,
        record: dict[str, Any],
        *,
        agent_id: str | None = None,
        user_id: str | None = None,
    ) -> None:
        session_key = _session_storage_key(session_id, agent_id, user_id)
        self._traces.setdefault(session_key, []).append(record)

    def get(
        self,
        session_id: str,
        *,
        agent_id: str | None = None,
        user_id: str | None = None,
    ) -> list[dict[str, Any]]:
        session_key = self._resolve_key(session_id, agent_id=agent_id, user_id=user_id)
        if session_key is None:
            return []
        return list(self._traces.get(session_key, []))

    def sessions(self) -> list[str]:
        return list(self._traces.keys())

    def _resolve_key(
        self,
        session_id: str,
        *,
        agent_id: str | None = None,
        user_id: str | None = None,
    ) -> str | None:
        exact = _session_storage_key(session_id, agent_id, user_id)
        return exact if exact in self._traces else None


class SessionPool:
    """In-memory index of active client sessions seen by the backend."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._sessions: dict[str, dict[str, Any]] = {}

    @staticmethod
    def make_key(
        session_id: str | None,
        agent_id: str | None = None,
        user_id: str | None = None,
    ) -> str:
        return _session_storage_key(session_id, agent_id, user_id)

    @classmethod
    def key_for_context(cls, context: RuntimeContext) -> str:
        return cls.make_key(context.session_id, context.agent_id, context.user_id)

    def _resolve_session_key(
        self,
        session_id: str | None,
        *,
        agent_id: str | None = None,
        user_id: str | None = None,
    ) -> str | None:
        if not session_id:
            return None
        exact = self.make_key(session_id, agent_id, user_id)
        return exact if exact in self._sessions else None

    def upsert(
        self,
        context: RuntimeContext,
        *,
        client_ip: str | None = None,
        client_key: str | None = None,
        enforce_key: bool = False,
        event_dict: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        session_id = context.session_id or "unknown"
        session_key = self.key_for_context(context)
        event_metadata = dict((event_dict or {}).get("metadata") or {})
        principal = (event_dict or {}).get("principal") or event_metadata.get("principal")
        context_metadata = dict(context.metadata or {})
        now = now_ts()
        with self._lock:
            current = dict(self._sessions.get(session_key) or {})
            self._validate_key(current, client_key, enforce_key)
            metadata = dict(current.get("metadata") or {})
            metadata.update(context_metadata)
            if event_metadata:
                metadata["event_metadata"] = event_metadata
            record = {
                **current,
                "session_key": session_key,
                "session_id": session_id,
                "agent_id": context.agent_id or current.get("agent_id"),
                "user_id": context.user_id or current.get("user_id"),
                "task_id": context.task_id or current.get("task_id"),
                "policy": context.policy or current.get("policy"),
                "policy_version": context.policy_version or current.get("policy_version"),
                "environment": context.environment or current.get("environment"),
                "client_ip": client_ip or current.get("client_ip"),
                "client_key": client_key or current.get("client_key"),
                "client_config_url": (
                    context_metadata.get("client_config_url")
                    or current.get("client_config_url")
                ),
                "client_checker_list_url": (
                    context_metadata.get("client_checker_list_url")
                    or current.get("client_checker_list_url")
                ),
                "client_health_url": (
                    context_metadata.get("client_health_url")
                    or current.get("client_health_url")
                ),
                "client_checker_config": (
                    context_metadata.get("client_checker_config")
                    if "client_checker_config" in context_metadata
                    else current.get("client_checker_config")
                ),
                "remote_checker_config": (
                    context_metadata.get("remote_checker_config")
                    if "remote_checker_config" in context_metadata
                    else current.get("remote_checker_config")
                ),
                "principal": principal or current.get("principal"),
                "metadata": metadata,
                "last_seen": now,
            }
            self._sessions[session_key] = record
            return dict(record)

    def touch(
        self,
        session_id: str | None,
        *,
        agent_id: str | None = None,
        user_id: str | None = None,
        client_ip: str | None = None,
        client_key: str | None = None,
        enforce_key: bool = False,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        if not session_id:
            return None
        now = now_ts()
        with self._lock:
            session_key = self._resolve_session_key(
                session_id,
                agent_id=agent_id,
                user_id=user_id,
            )
            current = dict(self._sessions.get(session_key) or {}) if session_key else {}
            if not current:
                return None
            self._validate_key(current, client_key, enforce_key)
            merged_metadata = dict(current.get("metadata") or {})
            merged_metadata.update(metadata or {})
            current.update(
                {
                    "client_ip": client_ip or current.get("client_ip"),
                    "client_key": client_key or current.get("client_key"),
                    "metadata": merged_metadata,
                    "last_seen": now,
                }
            )
            self._sessions[session_key] = current
            return dict(current)

    @staticmethod
    def _validate_key(
        current: dict[str, Any],
        client_key: str | None,
        enforce_key: bool,
    ) -> None:
        if enforce_key and not client_key:
            raise PermissionError("missing client session key")
        existing = current.get("client_key")
        if existing and client_key and existing != client_key:
            raise PermissionError("invalid client session key")
        if enforce_key and existing and client_key != existing:
            raise PermissionError("invalid client session key")

    def get(
        self,
        session_id: str,
        *,
        agent_id: str | None = None,
        user_id: str | None = None,
    ) -> dict[str, Any] | None:
        with self._lock:
            session_key = self._resolve_session_key(
                session_id,
                agent_id=agent_id,
                user_id=user_id,
            )
            if session_key is None:
                return None
            record = self._sessions.get(session_key)
            return dict(record) if record else None

    def remove(
        self,
        session_id: str | None,
        *,
        agent_id: str | None = None,
        user_id: str | None = None,
        client_key: str | None = None,
        enforce_key: bool = False,
    ) -> bool:
        if not session_id:
            return False
        with self._lock:
            session_key = self._resolve_session_key(
                session_id,
                agent_id=agent_id,
                user_id=user_id,
            )
            current = dict(self._sessions.get(session_key) or {}) if session_key else {}
            if current:
                self._validate_key(current, client_key, enforce_key)
            elif enforce_key and not client_key:
                raise PermissionError("missing client session key")
            if session_key is None:
                return False
            return self._sessions.pop(session_key, None) is not None

    def list(self) -> list[dict[str, Any]]:
        with self._lock:
            return sorted(
                (dict(record) for record in self._sessions.values()),
                key=lambda item: (item.get("last_seen") or 0),
                reverse=True,
            )

    def find_by_principal(self, principal: dict[str, Any]) -> list[dict[str, Any]]:
        filters = {str(key): value for key, value in (principal or {}).items() if value is not None}
        if not filters:
            return []
        with self._lock:
            matches = [
                dict(record)
                for record in self._sessions.values()
                if _record_matches_principal(record, filters)
            ]
        return sorted(matches, key=lambda item: (item.get("last_seen") or 0), reverse=True)

    def set_client_checker_config(
        self,
        session_id: str | None,
        agent_id: str | None,
        user_id: str | None,
        checker_config: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        if not session_id:
            return None
        session_key = self.make_key(session_id, agent_id, user_id)
        now = now_ts()
        with self._lock:
            current = dict(self._sessions.get(session_key) or {})
            if not current:
                return None
            metadata = dict(current.get("metadata") or {})
            metadata["client_checker_config"] = checker_config
            current.update(
                {
                    "client_checker_config": checker_config,
                    "metadata": metadata,
                    "last_seen": now,
                }
            )
            self._sessions[session_key] = current
            return dict(current)

    def set_remote_checker_config(
        self,
        session_id: str | None,
        agent_id: str | None,
        user_id: str | None,
        checker_config: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        if not session_id:
            return None
        session_key = self.make_key(session_id, agent_id, user_id)
        now = now_ts()
        with self._lock:
            current = dict(self._sessions.get(session_key) or {})
            if not current:
                return None
            metadata = dict(current.get("metadata") or {})
            metadata["remote_checker_config"] = checker_config
            current.update(
                {
                    "remote_checker_config": checker_config,
                    "metadata": metadata,
                    "last_seen": now,
                }
            )
            self._sessions[session_key] = current
            return dict(current)


def _record_matches_principal(record: dict[str, Any], filters: dict[str, Any]) -> bool:
    principal = record.get("principal") if isinstance(record.get("principal"), dict) else {}
    for key, expected in filters.items():
        actual = record.get(key)
        if actual is None and isinstance(principal, dict):
            actual = principal.get(key)
        if actual != expected:
            return False
    return True


__all__ = ["TraceStore", "SessionPool"]

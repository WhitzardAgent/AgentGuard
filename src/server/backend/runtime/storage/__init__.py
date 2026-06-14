"""In-memory trace/decision storage."""
from __future__ import annotations

import threading
from typing import Any

from shared.schemas.context import RuntimeContext
from shared.utils.time import now_ts


class TraceStore:
    def __init__(self) -> None:
        self._traces: dict[str, list[dict[str, Any]]] = {}

    def append(self, session_id: str, record: dict[str, Any]) -> None:
        self._traces.setdefault(session_id, []).append(record)

    def get(self, session_id: str) -> list[dict[str, Any]]:
        return list(self._traces.get(session_id, []))

    def sessions(self) -> list[str]:
        return list(self._traces.keys())


class SessionPool:
    """In-memory index of active client sessions seen by the backend."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._sessions: dict[str, dict[str, Any]] = {}

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
        event_metadata = dict((event_dict or {}).get("metadata") or {})
        principal = (event_dict or {}).get("principal") or event_metadata.get("principal")
        context_metadata = dict(context.metadata or {})
        now = now_ts()
        with self._lock:
            current = dict(self._sessions.get(session_id) or {})
            self._validate_key(current, client_key, enforce_key)
            metadata = dict(current.get("metadata") or {})
            metadata.update(context_metadata)
            if event_metadata:
                metadata["event_metadata"] = event_metadata
            record = {
                **current,
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
            self._sessions[session_id] = record
            return dict(record)

    def touch(
        self,
        session_id: str | None,
        *,
        client_ip: str | None = None,
        client_key: str | None = None,
        enforce_key: bool = False,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        if not session_id:
            return None
        now = now_ts()
        with self._lock:
            current = dict(self._sessions.get(session_id) or {"session_id": session_id})
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
            self._sessions[session_id] = current
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

    def get(self, session_id: str) -> dict[str, Any] | None:
        with self._lock:
            record = self._sessions.get(session_id)
            return dict(record) if record else None

    def remove(
        self,
        session_id: str | None,
        *,
        client_key: str | None = None,
        enforce_key: bool = False,
    ) -> bool:
        if not session_id:
            return False
        with self._lock:
            current = dict(self._sessions.get(session_id) or {})
            if current:
                self._validate_key(current, client_key, enforce_key)
            elif enforce_key and not client_key:
                raise PermissionError("missing client session key")
            return self._sessions.pop(session_id, None) is not None

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
        checker_config: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        if not session_id:
            return None
        now = now_ts()
        with self._lock:
            current = dict(self._sessions.get(session_id) or {"session_id": session_id})
            metadata = dict(current.get("metadata") or {})
            metadata["client_checker_config"] = checker_config
            current.update(
                {
                    "client_checker_config": checker_config,
                    "metadata": metadata,
                    "last_seen": now,
                }
            )
            self._sessions[session_id] = current
            return dict(current)

    def set_remote_checker_config(
        self,
        session_id: str | None,
        checker_config: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        if not session_id:
            return None
        now = now_ts()
        with self._lock:
            current = dict(self._sessions.get(session_id) or {"session_id": session_id})
            metadata = dict(current.get("metadata") or {})
            metadata["remote_checker_config"] = checker_config
            current.update(
                {
                    "remote_checker_config": checker_config,
                    "metadata": metadata,
                    "last_seen": now,
                }
            )
            self._sessions[session_id] = current
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

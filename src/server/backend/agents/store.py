"""Persistent Agent registry and user-agent bindings."""
from __future__ import annotations

import hashlib
import json
import secrets
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from backend.database import MySQLDatabase, get_database


@dataclass(frozen=True)
class AgentRecord:
    agent_id: str
    agent_identity_code: str
    provider: str | None = None
    provider_instance_id: str | None = None
    tenant_id: str | None = None
    external_agent_id: str | None = None
    agent_type: str | None = None
    name: str | None = None
    description: str | None = None
    public_key_jwk: str | None = None
    public_key_thumbprint: str | None = None
    status: str = "active"
    metadata_json: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    last_seen_at: datetime | None = None


@dataclass(frozen=True)
class AgentRegistrationResult:
    agent: AgentRecord
    credential: "AgentCredentialRecord"
    user_id: int | None
    account_email: str | None
    user_binding_created: bool
    user_binding_updated: bool


@dataclass(frozen=True)
class AgentCredentialRecord:
    credential_id: str
    agent_id: str
    public_key_jwk: str
    public_key_thumbprint: str
    issuer: str = "agentguard-local"
    valid_from: datetime | None = None
    valid_to: datetime | None = None
    status: str = "active"
    revoked_at: datetime | None = None
    metadata_json: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    last_seen_at: datetime | None = None


@dataclass(frozen=True)
class AgentToolRecord:
    agent_id: str
    name: str
    description: str | None = None
    labels_json: str | None = None
    input_params_json: str | None = None
    capabilities_json: str | None = None
    required_args_json: str | None = None
    schema_json: str | None = None
    metadata_json: str | None = None
    raw_payload_json: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    last_seen_at: datetime | None = None

    def to_console_dict(self) -> dict[str, Any]:
        payload = {
            "owner_agent_id": self.agent_id,
            "name": self.name,
            "description": self.description or "",
            "labels": _json_dict(self.labels_json) or _default_tool_labels(),
            "input_params": _json_list(self.input_params_json),
            "capabilities": _json_list(self.capabilities_json),
            "required_args": _json_list(self.required_args_json),
            "schema": _json_dict(self.schema_json),
            "metadata": _json_dict(self.metadata_json),
        }
        raw_payload = _json_dict(self.raw_payload_json)
        if raw_payload:
            payload["raw_payload"] = raw_payload
        return payload


@dataclass(frozen=True)
class AgentDeletionResult:
    agent_id: str
    deleted: bool
    trace_event_count: int = 0
    runtime_token_count: int = 0
    runtime_session_count: int = 0
    external_session_mapping_count: int = 0
    user_agent_binding_count: int = 0
    credential_count: int = 0
    external_identity_count: int = 0
    tool_count: int = 0
    agent_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "deleted": self.deleted,
            "trace_event_count": self.trace_event_count,
            "runtime_token_count": self.runtime_token_count,
            "runtime_session_count": self.runtime_session_count,
            "external_session_mapping_count": self.external_session_mapping_count,
            "user_agent_binding_count": self.user_agent_binding_count,
            "credential_count": self.credential_count,
            "external_identity_count": self.external_identity_count,
            "tool_count": self.tool_count,
            "agent_count": self.agent_count,
        }


class AgentStore:
    def __init__(self, db: MySQLDatabase | None = None) -> None:
        self.db = db or get_database()

    def ensure_schema(self) -> None:
        for statement in _SCHEMA:
            self.db.execute(statement)
        _ensure_agent_tool_columns(self.db)
        _backfill_agent_credentials_from_agents(self.db)

    def register_agent(
        self,
        *,
        provider: str,
        external_agent_id: str,
        agent_type: str,
        public_key_jwk: dict[str, Any],
        account_email: str | None = None,
        provider_instance_id: str | None = None,
        tenant_id: str | None = None,
        name: str | None = None,
        description: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> AgentRegistrationResult:
        clean_provider = _normalize_provider(provider)
        clean_provider_instance_id = _normalize_optional_key(provider_instance_id)
        clean_tenant_id = _normalize_optional_key(tenant_id)
        clean_external_agent_id = _normalize_required(external_agent_id, "external_agent_id")
        clean_agent_type = _normalize_required(agent_type, "agent_type")
        clean_email = _normalize_email_or_none(account_email)
        clean_public_jwk = _validate_public_jwk(public_key_jwk)
        clean_metadata = dict(metadata or {})
        if clean_email:
            clean_metadata.setdefault("account_email", clean_email)
            clean_metadata.setdefault("external_account_email", clean_email)
        identity_hash = _external_identity_hash(
            clean_provider,
            clean_provider_instance_id,
            clean_tenant_id,
            clean_external_agent_id,
            clean_agent_type,
        )
        thumbprint = _jwk_thumbprint(clean_public_jwk)

        existing = self._find_by_external_identity(
            identity_hash=identity_hash,
        )
        metadata_json = _metadata_json(clean_metadata)
        if existing is None:
            clean_agent_id = _new_agent_id()
            agent_identity_code = _agent_identity_code(clean_agent_id)
            self.db.insert(
                """
                INSERT INTO agents (
                  agent_id, agent_identity_code, name, description,
                  public_key_jwk, public_key_thumbprint, status,
                  metadata_json, last_seen_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, 'active', %s, UTC_TIMESTAMP())
                """,
                (
                    clean_agent_id,
                    agent_identity_code,
                    _optional_text(name),
                    _optional_text(description),
                    json.dumps(clean_public_jwk, sort_keys=True, separators=(",", ":")),
                    thumbprint,
                    metadata_json,
                ),
            )
            self.db.insert(
                """
                INSERT INTO agent_external_identities (
                  agent_id, identity_hash, provider, provider_instance_id, tenant_id,
                  external_agent_id, agent_type, metadata_json, last_seen_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, UTC_TIMESTAMP())
                """,
                (
                    clean_agent_id,
                    identity_hash,
                    clean_provider,
                    clean_provider_instance_id,
                    clean_tenant_id,
                    clean_external_agent_id,
                    clean_agent_type,
                    metadata_json,
                ),
            )
            agent = self.get_agent(clean_agent_id)
        else:
            clean_agent_id = existing.agent_id
            self.db.execute(
                """
                UPDATE agents
                SET name = COALESCE(%s, name),
                    description = COALESCE(%s, description),
                    public_key_jwk = %s,
                    public_key_thumbprint = %s,
                    status = 'active',
                    metadata_json = COALESCE(%s, metadata_json),
                    last_seen_at = UTC_TIMESTAMP(),
                    updated_at = CURRENT_TIMESTAMP
                WHERE agent_id = %s
                """,
                (
                    _optional_text(name),
                    _optional_text(description),
                    json.dumps(clean_public_jwk, sort_keys=True, separators=(",", ":")),
                    thumbprint,
                    metadata_json,
                    clean_agent_id,
                ),
            )
            self.db.execute(
                """
                UPDATE agent_external_identities
                SET metadata_json = COALESCE(%s, metadata_json),
                    last_seen_at = UTC_TIMESTAMP(),
                    updated_at = CURRENT_TIMESTAMP
                WHERE identity_hash = %s
                """,
                (
                    metadata_json,
                    identity_hash,
                ),
            )
            agent = self.get_agent(clean_agent_id)
        if agent is None:
            raise RuntimeError("failed to register agent")
        credential = self._upsert_agent_credential(
            agent_id=agent.agent_id,
            public_key_jwk=clean_public_jwk,
            public_key_thumbprint=thumbprint,
            metadata=clean_metadata,
        )

        user_id = self._user_id_for_external_account(clean_provider, clean_email)
        created = False
        updated = False
        if user_id is not None:
            created, updated = self._upsert_user_agent(
                user_id=user_id,
                agent_id=agent.agent_id,
                provider=clean_provider,
                account_email=clean_email,
                metadata=clean_metadata,
            )
        return AgentRegistrationResult(
            agent=agent,
            credential=credential,
            user_id=user_id,
            account_email=clean_email,
            user_binding_created=created,
            user_binding_updated=updated,
        )

    def get_agent(self, agent_id: str) -> AgentRecord | None:
        row = self.db.fetchone(
            """
            SELECT a.agent_id, a.agent_identity_code, e.provider, e.provider_instance_id,
                   e.tenant_id, e.external_agent_id, e.agent_type, a.name, a.description,
                   a.public_key_jwk, a.public_key_thumbprint, a.status, a.metadata_json,
                   a.created_at, a.updated_at, a.last_seen_at
            FROM agents a
            LEFT JOIN agent_external_identities e ON e.agent_id = a.agent_id
            WHERE a.agent_id = %s
            ORDER BY e.updated_at DESC, e.created_at DESC
            LIMIT 1
            """,
            (agent_id,),
        )
        return _agent_from_row(row) if row else None

    def agent_ids_for_user(self, user_id: int) -> set[str]:
        rows = self.db.fetchall(
            """
            SELECT ua.agent_id
            FROM user_agents ua
            JOIN agents a ON a.agent_id = ua.agent_id
            WHERE ua.user_id = %s AND a.status = 'active'
            """,
            (int(user_id),),
        )
        return {str(row["agent_id"]) for row in rows}

    def bind_agent_to_user(
        self,
        *,
        user_id: int,
        agent_id: str,
        provider: str,
        account_email: str | None = None,
        source: str = "adapter_scan",
        metadata: dict[str, Any] | None = None,
    ) -> tuple[bool, bool]:
        agent = self.get_agent(agent_id)
        if agent is None or agent.status != "active":
            raise ValueError("agent is not registered or active")
        return self._upsert_user_agent(
            user_id=int(user_id),
            agent_id=agent_id,
            provider=_normalize_provider(provider),
            account_email=_normalize_email_or_none(account_email),
            source=_normalize_source(source),
            metadata=metadata,
        )

    def bind_existing_agents_for_external_account(
        self,
        *,
        user_id: int,
        provider: str,
        account_email: str,
        source: str = "external_account_bind",
    ) -> dict[str, Any]:
        clean_provider = _normalize_provider(provider)
        clean_email = _normalize_email_or_none(account_email)
        if not clean_email:
            return {
                "provider": clean_provider,
                "account_email": None,
                "matched_count": 0,
                "created_count": 0,
                "updated_count": 0,
                "agent_ids": [],
            }
        rows = self.db.fetchall(
            """
            SELECT e.agent_id, e.metadata_json
            FROM agent_external_identities e
            JOIN agents a ON a.agent_id = e.agent_id
            WHERE e.provider = %s AND a.status = 'active'
            """,
            (clean_provider,),
        )
        agent_ids: list[str] = []
        created_count = 0
        updated_count = 0
        for row in rows:
            metadata = _json_dict(row.get("metadata_json"))
            metadata_email = _normalize_email_or_none(
                metadata.get("external_account_email")
                or metadata.get("account_email")
                or metadata.get(f"{clean_provider}_user_email")
                or metadata.get("n8n_user_email")
                or metadata.get("dify_user_email")
            )
            if metadata_email != clean_email:
                continue
            agent_id = str(row["agent_id"])
            created, updated = self._upsert_user_agent(
                user_id=int(user_id),
                agent_id=agent_id,
                provider=clean_provider,
                account_email=clean_email,
                source=_normalize_source(source),
                metadata=metadata,
            )
            agent_ids.append(agent_id)
            created_count += int(created)
            updated_count += int(updated)
        return {
            "provider": clean_provider,
            "account_email": clean_email,
            "matched_count": len(agent_ids),
            "created_count": created_count,
            "updated_count": updated_count,
            "agent_ids": agent_ids,
        }

    def get_active_credential(
        self,
        *,
        agent_id: str,
        public_key_thumbprint: str,
    ) -> AgentCredentialRecord | None:
        row = self.db.fetchone(
            """
            SELECT credential_id, agent_id, public_key_jwk, public_key_thumbprint,
                   issuer, valid_from, valid_to, status, revoked_at, metadata_json,
                   created_at, updated_at, last_seen_at
            FROM agent_credentials
            WHERE agent_id = %s
              AND public_key_thumbprint = %s
              AND status = 'active'
              AND (valid_to IS NULL OR valid_to > UTC_TIMESTAMP())
            LIMIT 1
            """,
            (agent_id, public_key_thumbprint),
        )
        return _agent_credential_from_row(row) if row else None

    def list_agents(self, agent_ids: set[str] | None = None) -> list[AgentRecord]:
        if agent_ids is not None:
            normalized_agent_ids = sorted(str(item).strip() for item in agent_ids if str(item).strip())
            if not normalized_agent_ids:
                return []
            records = [self.get_agent(agent_id) for agent_id in normalized_agent_ids]
            return [record for record in records if record is not None and record.status == "active"]
        rows = self.db.fetchall(
            """
            SELECT a.agent_id, a.agent_identity_code, e.provider, e.provider_instance_id,
                   e.tenant_id, e.external_agent_id, e.agent_type, a.name, a.description,
                   a.public_key_jwk, a.public_key_thumbprint, a.status, a.metadata_json,
                   a.created_at, a.updated_at, a.last_seen_at
            FROM agents a
            LEFT JOIN agent_external_identities e ON e.agent_id = a.agent_id
            WHERE a.status = 'active'
            ORDER BY a.updated_at DESC, a.created_at DESC
            """,
        )
        return [_agent_from_row(row) for row in rows]

    def delete_agent(self, agent_id: str) -> AgentDeletionResult:
        clean_agent_id = _normalize_required(agent_id, "agent_id")
        if self.get_agent(clean_agent_id) is None:
            return AgentDeletionResult(agent_id=clean_agent_id, deleted=False)
        if hasattr(self.db, "connect"):
            return self._delete_agent_transactional(clean_agent_id)
        return _delete_agent_with_execute(self.db.execute, clean_agent_id)

    def sync_provider_agents(
        self,
        *,
        provider: str,
        agent_type: str,
        external_agent_ids: list[str],
        provider_instance_id: str | None = None,
        tenant_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        clean_provider = _normalize_provider(provider)
        clean_provider_instance_id = _normalize_optional_key(provider_instance_id)
        clean_agent_type = _normalize_required(agent_type, "agent_type")
        clean_tenant_id = _normalize_optional_key(tenant_id)
        seen_external_ids = {
            _normalize_required(item, "external_agent_id")
            for item in external_agent_ids
            if _optional_text(item)
        }
        where = [
            "e.provider = %s",
            "e.provider_instance_id = %s",
            "e.agent_type = %s",
        ]
        params: list[Any] = [clean_provider, clean_provider_instance_id, clean_agent_type]
        if clean_tenant_id:
            where.append("e.tenant_id = %s")
            params.append(clean_tenant_id)
        rows = self.db.fetchall(
            f"""
            SELECT e.agent_id, e.external_agent_id, a.status
            FROM agent_external_identities e
            JOIN agents a ON a.agent_id = e.agent_id
            WHERE {' AND '.join(where)}
            """,
            tuple(params),
        )
        stale_agent_ids = [
            str(row["agent_id"])
            for row in rows
            if str(row.get("status") or "active") == "active"
            and str(row["external_agent_id"]) not in seen_external_ids
        ]
        if stale_agent_ids:
            placeholders = ", ".join(["%s"] * len(stale_agent_ids))
            self.db.execute(
                f"""
                UPDATE agents
                SET status = 'deleted',
                    metadata_json = COALESCE(%s, metadata_json),
                    updated_at = CURRENT_TIMESTAMP
                WHERE agent_id IN ({placeholders})
                """,
                (_metadata_json(metadata), *stale_agent_ids),
            )
        return {
            "provider": clean_provider,
            "provider_instance_id": clean_provider_instance_id,
            "tenant_id": clean_tenant_id,
            "agent_type": clean_agent_type,
            "seen_external_agent_count": len(seen_external_ids),
            "deactivated_count": len(stale_agent_ids),
            "deactivated_agent_ids": stale_agent_ids,
        }

    def upsert_agent_tool(self, agent_id: str, tool: dict[str, Any]) -> AgentToolRecord | None:
        record = _agent_tool_from_payload(agent_id, tool)
        if record is None:
            return None
        self.db.execute(
            """
            INSERT INTO agent_tools (
              agent_id, name, description, labels_json, input_params_json,
              capabilities_json, required_args_json, schema_json, metadata_json,
              raw_payload_json, last_seen_at
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, UTC_TIMESTAMP())
            ON DUPLICATE KEY UPDATE
              description = VALUES(description),
              labels_json = VALUES(labels_json),
              input_params_json = VALUES(input_params_json),
              capabilities_json = VALUES(capabilities_json),
              required_args_json = VALUES(required_args_json),
              schema_json = VALUES(schema_json),
              metadata_json = VALUES(metadata_json),
              raw_payload_json = VALUES(raw_payload_json),
              last_seen_at = UTC_TIMESTAMP(),
              updated_at = CURRENT_TIMESTAMP
            """,
            _agent_tool_params(record),
        )
        return record

    def sync_agent_tools(self, agent_id: str, tools: list[dict[str, Any]]) -> list[AgentToolRecord]:
        synced: list[AgentToolRecord] = []
        seen_names: set[str] = set()
        for tool in tools:
            if not isinstance(tool, dict):
                continue
            record = self.upsert_agent_tool(agent_id, tool)
            if record is None:
                continue
            synced.append(record)
            seen_names.add(record.name)
        if seen_names:
            placeholders = ", ".join(["%s"] * len(seen_names))
            self.db.execute(
                f"""
                DELETE FROM agent_tools
                WHERE agent_id = %s AND name NOT IN ({placeholders})
                """,
                (agent_id, *sorted(seen_names)),
            )
        else:
            self.db.execute("DELETE FROM agent_tools WHERE agent_id = %s", (agent_id,))
        return synced

    def list_agent_tools(
        self,
        *,
        agent_id: str | None = None,
        agent_ids: set[str] | None = None,
    ) -> list[AgentToolRecord]:
        if agent_id:
            rows = self.db.fetchall(
                _AGENT_TOOL_SELECT + " WHERE agent_id = %s ORDER BY name",
                (agent_id,),
            )
        elif agent_ids is not None:
            normalized_agent_ids = sorted(str(item).strip() for item in agent_ids if str(item).strip())
            if not normalized_agent_ids:
                return []
            placeholders = ", ".join(["%s"] * len(normalized_agent_ids))
            rows = self.db.fetchall(
                _AGENT_TOOL_SELECT + f" WHERE agent_id IN ({placeholders}) ORDER BY agent_id, name",
                tuple(normalized_agent_ids),
            )
        else:
            rows = self.db.fetchall(_AGENT_TOOL_SELECT + " ORDER BY agent_id, name")
        return [_agent_tool_from_row(row) for row in rows]

    def update_agent_tool_labels(
        self,
        agent_id: str,
        tool_name: str,
        labels: dict[str, Any],
    ) -> AgentToolRecord | None:
        current = self.list_agent_tools(agent_id=agent_id)
        record = next((item for item in current if item.name == tool_name), None)
        if record is None:
            return None
        merged = record.to_console_dict()
        current_labels = dict(merged.get("labels") or {})
        for key in ("boundary", "sensitivity", "integrity"):
            if labels.get(key):
                current_labels[key] = str(labels[key])
        if "tags" in labels and isinstance(labels["tags"], list):
            current_labels["tags"] = [str(tag) for tag in labels["tags"] if str(tag).strip()]
        merged["labels"] = current_labels
        return self.upsert_agent_tool(agent_id, merged)

    def _find_by_external_identity(
        self,
        *,
        identity_hash: str,
    ) -> AgentRecord | None:
        row = self.db.fetchone(
            """
            SELECT a.agent_id, a.agent_identity_code, e.provider, e.provider_instance_id,
                   e.tenant_id, e.external_agent_id, e.agent_type, a.name, a.description,
                   a.public_key_jwk, a.public_key_thumbprint, a.status, a.metadata_json,
                   a.created_at, a.updated_at, a.last_seen_at
            FROM agent_external_identities e
            JOIN agents a ON a.agent_id = e.agent_id
            WHERE e.identity_hash = %s
            """,
            (identity_hash,),
        )
        return _agent_from_row(row) if row else None

    def _user_id_for_external_account(self, provider: str, account_email: str | None) -> int | None:
        if not account_email:
            return None
        row = self.db.fetchone(
            """
            SELECT user_id
            FROM user_external_accounts
            WHERE provider = %s AND account_email = %s
            """,
            (provider, account_email),
        )
        return int(row["user_id"]) if row else None

    def _upsert_user_agent(
        self,
        *,
        user_id: int,
        agent_id: str,
        provider: str,
        account_email: str | None,
        metadata: dict[str, Any] | None,
        source: str = "adapter_scan",
    ) -> tuple[bool, bool]:
        existing = self.db.fetchone(
            """
            SELECT id
            FROM user_agents
            WHERE user_id = %s AND agent_id = %s
            """,
            (int(user_id), agent_id),
        )
        metadata_json = _metadata_json(metadata)
        if existing is None:
            self.db.insert(
                """
                INSERT INTO user_agents (
                  user_id, agent_id, provider, account_email, source,
                  metadata_json, last_seen_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, UTC_TIMESTAMP())
                """,
                (int(user_id), agent_id, provider, account_email, source, metadata_json),
            )
            return True, False
        self.db.execute(
            """
            UPDATE user_agents
            SET provider = %s,
                account_email = COALESCE(%s, account_email),
                source = %s,
                metadata_json = COALESCE(%s, metadata_json),
                last_seen_at = UTC_TIMESTAMP(),
                updated_at = CURRENT_TIMESTAMP
            WHERE id = %s
            """,
            (provider, account_email, source, metadata_json, int(existing["id"])),
        )
        return False, True

    def _upsert_agent_credential(
        self,
        *,
        agent_id: str,
        public_key_jwk: dict[str, Any],
        public_key_thumbprint: str,
        metadata: dict[str, Any] | None,
    ) -> AgentCredentialRecord:
        credential = self.get_active_credential(
            agent_id=agent_id,
            public_key_thumbprint=public_key_thumbprint,
        )
        public_key_json = json.dumps(public_key_jwk, sort_keys=True, separators=(",", ":"))
        metadata_json = _metadata_json(metadata)
        if credential is not None:
            self.db.execute(
                """
                UPDATE agent_credentials
                SET public_key_jwk = %s,
                    metadata_json = COALESCE(%s, metadata_json),
                    last_seen_at = UTC_TIMESTAMP(),
                    updated_at = CURRENT_TIMESTAMP
                WHERE credential_id = %s
                """,
                (public_key_json, metadata_json, credential.credential_id),
            )
            refreshed = self.get_active_credential(
                agent_id=agent_id,
                public_key_thumbprint=public_key_thumbprint,
            )
            if refreshed is not None:
                return refreshed

        self.db.execute(
            """
            UPDATE agent_credentials
            SET status = 'rotated',
                revoked_at = COALESCE(revoked_at, UTC_TIMESTAMP()),
                updated_at = CURRENT_TIMESTAMP
            WHERE agent_id = %s AND status = 'active' AND public_key_thumbprint <> %s
            """,
            (agent_id, public_key_thumbprint),
        )
        credential_id = _new_agent_credential_id()
        self.db.insert(
            """
            INSERT INTO agent_credentials (
              credential_id, agent_id, public_key_jwk, public_key_thumbprint,
              issuer, valid_from, valid_to, status, metadata_json, last_seen_at
            )
            VALUES (%s, %s, %s, %s, 'agentguard-local', UTC_TIMESTAMP(), NULL, 'active', %s, UTC_TIMESTAMP())
            ON DUPLICATE KEY UPDATE
              public_key_jwk = VALUES(public_key_jwk),
              status = 'active',
              revoked_at = NULL,
              metadata_json = COALESCE(VALUES(metadata_json), metadata_json),
              last_seen_at = UTC_TIMESTAMP(),
              updated_at = CURRENT_TIMESTAMP
            """,
            (
                credential_id,
                agent_id,
                public_key_json,
                public_key_thumbprint,
                metadata_json,
            ),
        )
        credential = self.get_active_credential(
            agent_id=agent_id,
            public_key_thumbprint=public_key_thumbprint,
        )
        if credential is None:
            raise RuntimeError("failed to register agent credential")
        return credential

    def _delete_agent_transactional(self, agent_id: str) -> AgentDeletionResult:
        conn = self.db.connect()
        try:
            conn.begin()
            with conn.cursor() as cursor:
                result = _delete_agent_with_execute(cursor.execute, agent_id)
            conn.commit()
            return result
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()


def ensure_agent_schema() -> None:
    AgentStore().ensure_schema()


def agent_delete_allowed_from_console(record: AgentRecord) -> bool:
    metadata = _json_dict(record.metadata_json)
    provider = str(
        record.provider
        or metadata.get("external_provider")
        or metadata.get("provider")
        or ""
    ).strip().lower()
    return provider == "langchain"


def _delete_agent_with_execute(execute: Any, agent_id: str) -> AgentDeletionResult:
    trace_event_count = int(
        execute(
            """
            DELETE FROM runtime_trace_events
            WHERE agent_id = %s
               OR raw_agent_id = %s
               OR runtime_session_id IN (
                    SELECT session_id FROM runtime_sessions WHERE agent_id = %s
               )
               OR session_id IN (
                    SELECT session_id FROM runtime_sessions WHERE agent_id = %s
               )
            """,
            (agent_id, agent_id, agent_id, agent_id),
        )
    )
    runtime_token_count = int(
        execute(
            """
            DELETE FROM runtime_tokens
            WHERE session_id IN (
                SELECT session_id FROM runtime_sessions WHERE agent_id = %s
            )
            """,
            (agent_id,),
        )
    )
    runtime_session_count = int(
        execute("DELETE FROM runtime_sessions WHERE agent_id = %s", (agent_id,))
    )
    external_session_mapping_count = int(
        execute("DELETE FROM external_runtime_sessions WHERE agent_id = %s", (agent_id,))
    )
    tool_count = int(execute("DELETE FROM agent_tools WHERE agent_id = %s", (agent_id,)))
    user_agent_binding_count = int(
        execute("DELETE FROM user_agents WHERE agent_id = %s", (agent_id,))
    )
    credential_count = int(
        execute("DELETE FROM agent_credentials WHERE agent_id = %s", (agent_id,))
    )
    external_identity_count = int(
        execute("DELETE FROM agent_external_identities WHERE agent_id = %s", (agent_id,))
    )
    agent_count = int(execute("DELETE FROM agents WHERE agent_id = %s", (agent_id,)))
    return AgentDeletionResult(
        agent_id=agent_id,
        deleted=agent_count > 0,
        trace_event_count=trace_event_count,
        runtime_token_count=runtime_token_count,
        runtime_session_count=runtime_session_count,
        external_session_mapping_count=external_session_mapping_count,
        user_agent_binding_count=user_agent_binding_count,
        credential_count=credential_count,
        external_identity_count=external_identity_count,
        tool_count=tool_count,
        agent_count=agent_count,
    )


_SCHEMA = [
    """
    CREATE TABLE IF NOT EXISTS agents (
      agent_id VARCHAR(255) PRIMARY KEY,
      agent_identity_code VARCHAR(255) NOT NULL UNIQUE,
      name VARCHAR(255) NULL,
      description TEXT NULL,
      public_key_jwk JSON NOT NULL,
      public_key_thumbprint VARCHAR(255) NOT NULL,
      status VARCHAR(32) NOT NULL DEFAULT 'active',
      metadata_json JSON NULL,
      created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
      updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
      last_seen_at TIMESTAMP NULL,
      INDEX idx_agents_status (status)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS agent_external_identities (
      id INT AUTO_INCREMENT PRIMARY KEY,
      agent_id VARCHAR(255) NOT NULL,
      identity_hash CHAR(64) NOT NULL,
      provider VARCHAR(64) NOT NULL,
      provider_instance_id VARCHAR(255) NOT NULL DEFAULT '',
      tenant_id VARCHAR(255) NOT NULL DEFAULT '',
      external_agent_id VARCHAR(255) NOT NULL,
      agent_type VARCHAR(64) NOT NULL,
      metadata_json JSON NULL,
      created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
      updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
      last_seen_at TIMESTAMP NULL,
      UNIQUE KEY uniq_agent_external_identity_hash (identity_hash),
      INDEX idx_agent_external_identities_agent_id (agent_id),
      INDEX idx_agent_external_identities_provider (provider, agent_type),
      CONSTRAINT fk_agent_external_identities_agent
        FOREIGN KEY (agent_id) REFERENCES agents(agent_id)
        ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS user_agents (
      id INT AUTO_INCREMENT PRIMARY KEY,
      user_id INT NOT NULL,
      agent_id VARCHAR(255) NOT NULL,
      provider VARCHAR(64) NOT NULL,
      account_email VARCHAR(255) NULL,
      role VARCHAR(64) NOT NULL DEFAULT 'user',
      source VARCHAR(64) NOT NULL DEFAULT 'adapter_scan',
      metadata_json JSON NULL,
      created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
      updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
      last_seen_at TIMESTAMP NULL,
      UNIQUE KEY uniq_user_agents_user_agent (user_id, agent_id),
      INDEX idx_user_agents_user_id (user_id),
      INDEX idx_user_agents_agent_id (agent_id),
      CONSTRAINT fk_user_agents_user
        FOREIGN KEY (user_id) REFERENCES users(id)
        ON DELETE CASCADE,
      CONSTRAINT fk_user_agents_agent
        FOREIGN KEY (agent_id) REFERENCES agents(agent_id)
        ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS agent_credentials (
      credential_id VARCHAR(255) PRIMARY KEY,
      agent_id VARCHAR(255) NOT NULL,
      public_key_jwk JSON NOT NULL,
      public_key_thumbprint VARCHAR(255) NOT NULL,
      issuer VARCHAR(255) NOT NULL DEFAULT 'agentguard-local',
      valid_from TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
      valid_to TIMESTAMP NULL,
      status VARCHAR(32) NOT NULL DEFAULT 'active',
      revoked_at TIMESTAMP NULL,
      metadata_json JSON NULL,
      created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
      updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
      last_seen_at TIMESTAMP NULL,
      UNIQUE KEY uniq_agent_credentials_agent_thumbprint (agent_id, public_key_thumbprint),
      INDEX idx_agent_credentials_agent_id (agent_id),
      INDEX idx_agent_credentials_status (status),
      INDEX idx_agent_credentials_valid_to (valid_to),
      CONSTRAINT fk_agent_credentials_agent
        FOREIGN KEY (agent_id) REFERENCES agents(agent_id)
        ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS agent_tools (
      id INT AUTO_INCREMENT PRIMARY KEY,
      agent_id VARCHAR(255) NOT NULL,
      name VARCHAR(255) NOT NULL,
      description TEXT NULL,
      labels_json JSON NULL,
      input_params_json JSON NULL,
      capabilities_json JSON NULL,
      required_args_json JSON NULL,
      schema_json JSON NULL,
      metadata_json JSON NULL,
      raw_payload_json JSON NULL,
      created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
      updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
      last_seen_at TIMESTAMP NULL,
      UNIQUE KEY uniq_agent_tools_agent_name (agent_id, name),
      INDEX idx_agent_tools_agent_id (agent_id),
      CONSTRAINT fk_agent_tools_agent
        FOREIGN KEY (agent_id) REFERENCES agents(agent_id)
        ON DELETE CASCADE
    )
    """,
]

_AGENT_TOOL_SELECT = """
SELECT agent_id, name, description, labels_json, input_params_json,
       capabilities_json, required_args_json, schema_json, metadata_json, raw_payload_json,
       created_at, updated_at, last_seen_at
FROM agent_tools
"""


def _ensure_agent_tool_columns(db: MySQLDatabase) -> None:
    try:
        row = db.fetchone("SHOW COLUMNS FROM agent_tools LIKE %s", ("raw_payload_json",))
    except Exception:
        return
    if row is None:
        db.execute("ALTER TABLE agent_tools ADD COLUMN raw_payload_json JSON NULL AFTER metadata_json")


def _backfill_agent_credentials_from_agents(db: MySQLDatabase) -> None:
    try:
        db.execute(
            """
            INSERT IGNORE INTO agent_credentials (
              credential_id, agent_id, public_key_jwk, public_key_thumbprint,
              issuer, valid_from, valid_to, status, metadata_json, last_seen_at
            )
            SELECT CONCAT('agcred_', SHA2(CONCAT(agent_id, ':', public_key_thumbprint), 256)),
                   agent_id, public_key_jwk, public_key_thumbprint,
                   'agentguard-local-backfill',
                   COALESCE(created_at, UTC_TIMESTAMP()),
                   NULL,
                   'active',
                   metadata_json,
                   last_seen_at
            FROM agents
            WHERE status = 'active'
              AND public_key_jwk IS NOT NULL
              AND public_key_thumbprint IS NOT NULL
            """
        )
    except Exception:
        return


def _agent_from_row(row: dict[str, Any]) -> AgentRecord:
    return AgentRecord(
        agent_id=str(row["agent_id"]),
        agent_identity_code=str(row["agent_identity_code"]),
        provider=_optional_text(row.get("provider")),
        provider_instance_id=_optional_text(row.get("provider_instance_id")),
        tenant_id=_optional_text(row.get("tenant_id")),
        external_agent_id=_optional_text(row.get("external_agent_id")),
        agent_type=_optional_text(row.get("agent_type")),
        name=_optional_text(row.get("name")),
        description=_optional_text(row.get("description")),
        public_key_jwk=_optional_text(row.get("public_key_jwk")),
        public_key_thumbprint=_optional_text(row.get("public_key_thumbprint")),
        status=str(row["status"]),
        metadata_json=_optional_text(row.get("metadata_json")),
        created_at=_coerce_datetime(row.get("created_at")) if row.get("created_at") else None,
        updated_at=_coerce_datetime(row.get("updated_at")) if row.get("updated_at") else None,
        last_seen_at=_coerce_datetime(row.get("last_seen_at")) if row.get("last_seen_at") else None,
    )


def _agent_credential_from_row(row: dict[str, Any]) -> AgentCredentialRecord:
    return AgentCredentialRecord(
        credential_id=str(row["credential_id"]),
        agent_id=str(row["agent_id"]),
        public_key_jwk=_optional_text(row.get("public_key_jwk")) or "{}",
        public_key_thumbprint=str(row["public_key_thumbprint"]),
        issuer=str(row.get("issuer") or "agentguard-local"),
        valid_from=_coerce_datetime(row.get("valid_from")) if row.get("valid_from") else None,
        valid_to=_coerce_datetime(row.get("valid_to")) if row.get("valid_to") else None,
        status=str(row.get("status") or "active"),
        revoked_at=_coerce_datetime(row.get("revoked_at")) if row.get("revoked_at") else None,
        metadata_json=_optional_text(row.get("metadata_json")),
        created_at=_coerce_datetime(row.get("created_at")) if row.get("created_at") else None,
        updated_at=_coerce_datetime(row.get("updated_at")) if row.get("updated_at") else None,
        last_seen_at=_coerce_datetime(row.get("last_seen_at")) if row.get("last_seen_at") else None,
    )


def _agent_tool_from_payload(agent_id: str, tool: dict[str, Any]) -> AgentToolRecord | None:
    clean_agent_id = _normalize_required(agent_id, "agent_id")
    name = _optional_text(tool.get("name"))
    if not name:
        return None
    labels = dict(tool.get("labels") or {})
    normalized_labels = {
        "boundary": str(labels.get("boundary") or "internal"),
        "sensitivity": str(labels.get("sensitivity") or "low"),
        "integrity": str(labels.get("integrity") or "trusted"),
        "tags": [str(tag) for tag in (labels.get("tags") or []) if str(tag).strip()],
    }
    input_params = _string_list(tool.get("input_params"))
    required_args = _string_list(tool.get("required_args") or input_params)
    capabilities = _string_list(tool.get("capabilities") or normalized_labels.get("tags"))
    schema = tool.get("schema") if isinstance(tool.get("schema"), dict) else {}
    metadata = tool.get("metadata") if isinstance(tool.get("metadata"), dict) else {}
    return AgentToolRecord(
        agent_id=clean_agent_id,
        name=name,
        description=_optional_text(tool.get("description")),
        labels_json=_json_dump(normalized_labels),
        input_params_json=_json_dump(input_params),
        capabilities_json=_json_dump(capabilities),
        required_args_json=_json_dump(required_args),
        schema_json=_json_dump(schema),
        metadata_json=_json_dump(metadata),
        raw_payload_json=_json_dump(tool),
    )


def _agent_tool_params(record: AgentToolRecord) -> tuple[Any, ...]:
    return (
        record.agent_id,
        record.name,
        record.description,
        record.labels_json,
        record.input_params_json,
        record.capabilities_json,
        record.required_args_json,
        record.schema_json,
        record.metadata_json,
        record.raw_payload_json,
    )


def _agent_tool_from_row(row: dict[str, Any]) -> AgentToolRecord:
    return AgentToolRecord(
        agent_id=str(row["agent_id"]),
        name=str(row["name"]),
        description=_optional_text(row.get("description")),
        labels_json=_optional_text(row.get("labels_json")),
        input_params_json=_optional_text(row.get("input_params_json")),
        capabilities_json=_optional_text(row.get("capabilities_json")),
        required_args_json=_optional_text(row.get("required_args_json")),
        schema_json=_optional_text(row.get("schema_json")),
        metadata_json=_optional_text(row.get("metadata_json")),
        raw_payload_json=_optional_text(row.get("raw_payload_json")),
        created_at=_coerce_datetime(row.get("created_at")) if row.get("created_at") else None,
        updated_at=_coerce_datetime(row.get("updated_at")) if row.get("updated_at") else None,
        last_seen_at=_coerce_datetime(row.get("last_seen_at")) if row.get("last_seen_at") else None,
    )


def _validate_public_jwk(value: dict[str, Any]) -> dict[str, str]:
    if not isinstance(value, dict):
        raise ValueError("public_key_jwk must be an object")
    kty = _optional_text(value.get("kty"))
    if kty == "OKP":
        crv = _optional_text(value.get("crv"))
        x = _optional_text(value.get("x"))
        if not crv or not x:
            raise ValueError("OKP public_key_jwk requires crv and x")
        return {"kty": kty, "crv": crv, "x": x}
    if kty == "EC":
        crv = _optional_text(value.get("crv"))
        x = _optional_text(value.get("x"))
        y = _optional_text(value.get("y"))
        if not crv or not x or not y:
            raise ValueError("EC public_key_jwk requires crv, x, and y")
        return {"kty": kty, "crv": crv, "x": x, "y": y}
    raise ValueError("public_key_jwk must use kty OKP or EC")


def _jwk_thumbprint(jwk: dict[str, str]) -> str:
    canonical = json.dumps(jwk, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return _b64url(hashlib.sha256(canonical).digest())


def _agent_identity_code(agent_id: str) -> str:
    return f"agic_{hashlib.sha256(agent_id.encode('utf-8')).hexdigest()[:32]}"


def _external_identity_hash(
    provider: str,
    provider_instance_id: str,
    tenant_id: str,
    external_agent_id: str,
    agent_type: str,
) -> str:
    raw = "\x1f".join([provider, provider_instance_id, tenant_id, external_agent_id, agent_type])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _new_agent_id() -> str:
    return f"ag_{secrets.token_urlsafe(18)}"


def _new_agent_credential_id() -> str:
    return f"agcred_{secrets.token_urlsafe(18)}"


def _metadata_json(value: dict[str, Any] | None) -> str | None:
    if not value:
        return None
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _json_dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _json_value(value: str | None) -> Any:
    if not value:
        return None
    try:
        return json.loads(value)
    except Exception:
        return None


def _json_dict(value: str | None) -> dict[str, Any]:
    parsed = _json_value(value)
    return parsed if isinstance(parsed, dict) else {}


def _json_list(value: str | None) -> list[str]:
    parsed = _json_value(value)
    return [str(item) for item in parsed] if isinstance(parsed, list) else []


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if str(item).strip()]


def _default_tool_labels() -> dict[str, Any]:
    return {
        "boundary": "internal",
        "sensitivity": "low",
        "integrity": "trusted",
        "tags": [],
    }


def _normalize_provider(value: str) -> str:
    normalized = _normalize_required(value, "provider").lower()
    if len(normalized) > 64:
        raise ValueError("provider must be at most 64 characters")
    return normalized


def _normalize_required(value: Any, field: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{field} is required")
    if len(text) > 255:
        raise ValueError(f"{field} must be at most 255 characters")
    return text


def _normalize_optional_key(value: Any) -> str:
    text = _optional_text(value) or ""
    if len(text) > 255:
        raise ValueError("external identity fields must be at most 255 characters")
    return text


def _normalize_email_or_none(value: Any) -> str | None:
    text = _optional_text(value)
    if not text:
        return None
    text = text.lower()
    if "@" not in text:
        raise ValueError("account_email must be an email address")
    if len(text) > 255:
        raise ValueError("account_email must be at most 255 characters")
    return text


def _normalize_source(value: Any) -> str:
    text = _optional_text(value) or "adapter_scan"
    if len(text) > 64:
        raise ValueError("source must be at most 64 characters")
    return text


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _coerce_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    return datetime.fromisoformat(str(value)).replace(tzinfo=timezone.utc)


def _b64url(value: bytes) -> str:
    import base64

    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")

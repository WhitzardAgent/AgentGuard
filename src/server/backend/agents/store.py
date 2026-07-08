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
    user_id: int | None
    account_email: str | None
    user_binding_created: bool
    user_binding_updated: bool


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


class AgentStore:
    def __init__(self, db: MySQLDatabase | None = None) -> None:
        self.db = db or get_database()

    def ensure_schema(self) -> None:
        for statement in _SCHEMA:
            self.db.execute(statement)
        _ensure_agent_tool_columns(self.db)

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
        metadata_json = _metadata_json(metadata)
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

        user_id = self._user_id_for_external_account(clean_provider, clean_email)
        created = False
        updated = False
        if user_id is not None:
            created, updated = self._upsert_user_agent(
                user_id=user_id,
                agent_id=agent.agent_id,
                provider=clean_provider,
                account_email=clean_email,
                metadata=metadata,
            )
        return AgentRegistrationResult(
            agent=agent,
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
            SELECT agent_id
            FROM user_agents
            WHERE user_id = %s
            """,
            (int(user_id),),
        )
        return {str(row["agent_id"]) for row in rows}

    def list_agents(self, agent_ids: set[str] | None = None) -> list[AgentRecord]:
        if agent_ids is not None:
            normalized_agent_ids = sorted(str(item).strip() for item in agent_ids if str(item).strip())
            if not normalized_agent_ids:
                return []
            records = [self.get_agent(agent_id) for agent_id in normalized_agent_ids]
            return [record for record in records if record is not None]
        rows = self.db.fetchall(
            """
            SELECT a.agent_id, a.agent_identity_code, e.provider, e.provider_instance_id,
                   e.tenant_id, e.external_agent_id, e.agent_type, a.name, a.description,
                   a.public_key_jwk, a.public_key_thumbprint, a.status, a.metadata_json,
                   a.created_at, a.updated_at, a.last_seen_at
            FROM agents a
            LEFT JOIN agent_external_identities e ON e.agent_id = a.agent_id
            ORDER BY a.updated_at DESC, a.created_at DESC
            """,
        )
        return [_agent_from_row(row) for row in rows]

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
                VALUES (%s, %s, %s, %s, 'adapter_scan', %s, UTC_TIMESTAMP())
                """,
                (int(user_id), agent_id, provider, account_email, metadata_json),
            )
            return True, False
        self.db.execute(
            """
            UPDATE user_agents
            SET provider = %s,
                account_email = COALESCE(%s, account_email),
                metadata_json = COALESCE(%s, metadata_json),
                last_seen_at = UTC_TIMESTAMP(),
                updated_at = CURRENT_TIMESTAMP
            WHERE id = %s
            """,
            (provider, account_email, metadata_json, int(existing["id"])),
        )
        return False, True


def ensure_agent_schema() -> None:
    AgentStore().ensure_schema()


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

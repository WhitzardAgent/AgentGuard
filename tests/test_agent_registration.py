from __future__ import annotations

import json
from typing import Any

from backend.agents.store import AgentRecord, AgentStore, agent_delete_allowed_from_console


class FakeDB:
    def __init__(self) -> None:
        self.agents: dict[str, dict[str, Any]] = {}
        self.agent_external_identities: list[dict[str, Any]] = []
        self.user_external_accounts: list[dict[str, Any]] = []
        self.user_agents: list[dict[str, Any]] = []
        self.agent_credentials: list[dict[str, Any]] = []
        self.agent_tools: list[dict[str, Any]] = []
        self.agent_client_plugins: list[dict[str, Any]] = []
        self.runtime_sessions: list[dict[str, Any]] = []
        self.runtime_tokens: list[dict[str, Any]] = []
        self.external_runtime_sessions: list[dict[str, Any]] = []
        self.runtime_trace_events: list[dict[str, Any]] = []
        self.agent_audit_runs: list[dict[str, Any]] = []
        self.agent_audit_session_results: list[dict[str, Any]] = []
        self.agent_audit_findings: list[dict[str, Any]] = []
        self.next_id = 1

    def execute(self, sql: str, params: tuple[Any, ...] | None = None) -> int:
        if "CREATE TABLE" in sql:
            return 0
        if "DELETE FROM agent_audit_runs" in sql:
            agent_id = str(params[0])
            run_ids = {
                str(row["run_id"])
                for row in self.agent_audit_runs
                if str(row.get("agent_id") or "") == agent_id
            }
            before = len(self.agent_audit_runs)
            self.agent_audit_runs = [
                row for row in self.agent_audit_runs if str(row.get("agent_id") or "") != agent_id
            ]
            self.agent_audit_session_results = [
                row
                for row in self.agent_audit_session_results
                if str(row.get("run_id") or "") not in run_ids
            ]
            self.agent_audit_findings = [
                row
                for row in self.agent_audit_findings
                if str(row.get("run_id") or "") not in run_ids
            ]
            return before - len(self.agent_audit_runs)
        if "DELETE FROM runtime_trace_events" in sql:
            agent_id = str(params[0])
            session_ids = {
                str(row["session_id"])
                for row in self.runtime_sessions
                if str(row.get("agent_id") or "") == agent_id
            }
            before = len(self.runtime_trace_events)
            self.runtime_trace_events = [
                row
                for row in self.runtime_trace_events
                if str(row.get("agent_id") or "") != agent_id
                and str(row.get("raw_agent_id") or "") != agent_id
                and str(row.get("runtime_session_id") or "") not in session_ids
                and str(row.get("session_id") or "") not in session_ids
            ]
            return before - len(self.runtime_trace_events)
        if "DELETE FROM runtime_tokens" in sql:
            agent_id = str(params[0])
            session_ids = {
                str(row["session_id"])
                for row in self.runtime_sessions
                if str(row.get("agent_id") or "") == agent_id
            }
            before = len(self.runtime_tokens)
            self.runtime_tokens = [
                row for row in self.runtime_tokens if str(row.get("session_id") or "") not in session_ids
            ]
            return before - len(self.runtime_tokens)
        if "DELETE FROM runtime_sessions" in sql:
            agent_id = str(params[0])
            before = len(self.runtime_sessions)
            self.runtime_sessions = [
                row for row in self.runtime_sessions if str(row.get("agent_id") or "") != agent_id
            ]
            return before - len(self.runtime_sessions)
        if "DELETE FROM external_runtime_sessions" in sql:
            agent_id = str(params[0])
            before = len(self.external_runtime_sessions)
            self.external_runtime_sessions = [
                row for row in self.external_runtime_sessions if str(row.get("agent_id") or "") != agent_id
            ]
            return before - len(self.external_runtime_sessions)
        if "DELETE FROM user_agents" in sql:
            before = len(self.user_agents)
            if "WHERE user_id = %s AND provider = %s AND account_email = %s" in sql:
                user_id, provider, account_email = params
                self.user_agents = [
                    row
                    for row in self.user_agents
                    if not (
                        row["user_id"] == int(user_id)
                        and row["provider"] == provider
                        and row["account_email"] == account_email
                    )
                ]
                return before - len(self.user_agents)
            if "WHERE user_id = %s AND provider = %s" in sql:
                user_id, provider = params
                self.user_agents = [
                    row
                    for row in self.user_agents
                    if not (
                        row["user_id"] == int(user_id)
                        and row["provider"] == provider
                    )
                ]
                return before - len(self.user_agents)
            if "WHERE user_id = %s AND agent_id = %s AND provider = %s" in sql:
                user_id, agent_id, provider = params
                self.user_agents = [
                    row
                    for row in self.user_agents
                    if not (
                        row["user_id"] == int(user_id)
                        and row["agent_id"] == str(agent_id)
                        and row["provider"] == provider
                    )
                ]
                return before - len(self.user_agents)
            if "WHERE user_id = %s AND agent_id = %s" in sql:
                user_id, agent_id = params
                self.user_agents = [
                    row
                    for row in self.user_agents
                    if not (
                        row["user_id"] == int(user_id)
                        and row["agent_id"] == str(agent_id)
                    )
                ]
                return before - len(self.user_agents)
            agent_id = str(params[0])
            self.user_agents = [row for row in self.user_agents if row["agent_id"] != agent_id]
            return before - len(self.user_agents)
        if "DELETE FROM agent_credentials" in sql:
            agent_id = str(params[0])
            before = len(self.agent_credentials)
            self.agent_credentials = [row for row in self.agent_credentials if row["agent_id"] != agent_id]
            return before - len(self.agent_credentials)
        if "DELETE FROM agent_external_identities" in sql:
            agent_id = str(params[0])
            before = len(self.agent_external_identities)
            self.agent_external_identities = [
                row for row in self.agent_external_identities if row["agent_id"] != agent_id
            ]
            return before - len(self.agent_external_identities)
        if "DELETE FROM agents" in sql:
            agent_id = str(params[0])
            if agent_id in self.agents:
                del self.agents[agent_id]
                return 1
            return 0
        if "SET status = 'deleted'" in sql:
            for agent_id in params[1:]:
                if str(agent_id) in self.agents:
                    self.agents[str(agent_id)]["status"] = "deleted"
            return len(params[1:])
        if "UPDATE agent_credentials" in sql and "status = 'rotated'" in sql:
            agent_id, thumbprint = params
            for row in self.agent_credentials:
                if (
                    row["agent_id"] == agent_id
                    and row["status"] == "active"
                    and row["public_key_thumbprint"] != thumbprint
                ):
                    row["status"] = "rotated"
            return 1
        if "UPDATE agent_credentials" in sql:
            public_key_jwk, metadata_json, credential_id = params
            for row in self.agent_credentials:
                if row["credential_id"] == credential_id:
                    row["public_key_jwk"] = public_key_jwk
                    row["metadata_json"] = metadata_json or row.get("metadata_json")
                    return 1
            return 0
        if "INSERT INTO agent_tools" in sql:
            row = {
                "agent_id": params[0],
                "name": params[1],
                "description": params[2],
                "labels_json": params[3],
                "input_params_json": params[4],
                "capabilities_json": params[5],
                "required_args_json": params[6],
                "schema_json": params[7],
                "metadata_json": params[8],
                "raw_payload_json": params[9],
                "created_at": None,
                "updated_at": None,
                "last_seen_at": None,
            }
            for index, existing in enumerate(self.agent_tools):
                if existing["agent_id"] == row["agent_id"] and existing["name"] == row["name"]:
                    self.agent_tools[index] = {**existing, **row}
                    return 1
            self.agent_tools.append(row)
            return 1
        if "DELETE FROM agent_tools" in sql and "name NOT IN" in sql:
            agent_id = params[0]
            keep = set(params[1:])
            self.agent_tools = [
                row
                for row in self.agent_tools
                if row["agent_id"] != agent_id or row["name"] in keep
            ]
            return 1
        if "DELETE FROM agent_tools" in sql:
            (agent_id,) = params
            self.agent_tools = [row for row in self.agent_tools if row["agent_id"] != agent_id]
            return 1
        if "INSERT INTO agent_client_plugins" in sql:
            row = {
                "agent_id": params[0],
                "name": params[1],
                "description": params[2],
                "event_types_json": params[3],
                "phases_json": params[4],
                "raw_payload_json": params[5],
                "created_at": None,
                "updated_at": None,
                "last_seen_at": None,
            }
            for index, existing in enumerate(self.agent_client_plugins):
                if existing["agent_id"] == row["agent_id"] and existing["name"] == row["name"]:
                    self.agent_client_plugins[index] = {**existing, **row}
                    return 1
            self.agent_client_plugins.append(row)
            return 1
        if "DELETE FROM agent_client_plugins" in sql and "name NOT IN" in sql:
            agent_id = params[0]
            keep = set(params[1:])
            self.agent_client_plugins = [
                row
                for row in self.agent_client_plugins
                if row["agent_id"] != agent_id or row["name"] in keep
            ]
            return 1
        if "DELETE FROM agent_client_plugins" in sql:
            (agent_id,) = params
            self.agent_client_plugins = [row for row in self.agent_client_plugins if row["agent_id"] != agent_id]
            return 1
        if "UPDATE agents" in sql:
            if "SET location_tag = %s" in sql:
                location_tag, agent_id = params
                row = self.agents.get(str(agent_id))
                if row is None:
                    return 0
                row["location_tag"] = location_tag
                return 1
            agent_id = str(params[-1])
            row = self.agents[agent_id]
            row["name"] = params[0] or row.get("name")
            row["description"] = params[1] or row.get("description")
            row["public_key_jwk"] = params[2]
            row["public_key_thumbprint"] = params[3]
            row["metadata_json"] = params[4] or row.get("metadata_json")
            row["status"] = "active"
            return 1
        if "UPDATE agent_external_identities" in sql:
            metadata_json, identity_hash = params
            for row in self.agent_external_identities:
                if row["identity_hash"] == identity_hash:
                    row["metadata_json"] = metadata_json or row.get("metadata_json")
                    return 1
            return 0
        if "UPDATE user_agents" in sql:
            binding_id = int(params[-1])
            for row in self.user_agents:
                if row["id"] == binding_id:
                    row["provider"] = params[0]
                    row["account_email"] = params[1] or row.get("account_email")
                    row["source"] = params[2]
                    row["metadata_json"] = params[3] or row.get("metadata_json")
                    return 1
        return 0

    def insert(self, sql: str, params: tuple[Any, ...] | None = None) -> int:
        if "INSERT INTO agents" in sql:
            row = {
                "agent_id": params[0],
                "agent_identity_code": params[1],
                "location_tag": params[2],
                "name": params[3],
                "description": params[4],
                "public_key_jwk": params[5],
                "public_key_thumbprint": params[6],
                "status": "active",
                "metadata_json": params[7],
                "created_at": None,
                "updated_at": None,
                "last_seen_at": None,
            }
            self.agents[str(params[0])] = row
            return self.next_id
        if "INSERT INTO agent_external_identities" in sql:
            row = {
                "id": self.next_id,
                "agent_id": params[0],
                "identity_hash": params[1],
                "provider": params[2],
                "provider_instance_id": params[3],
                "tenant_id": params[4],
                "external_agent_id": params[5],
                "agent_type": params[6],
                "metadata_json": params[7],
            }
            self.next_id += 1
            self.agent_external_identities.append(row)
            return row["id"]
        if "INSERT INTO user_agents" in sql:
            row = {
                "id": self.next_id,
                "user_id": params[0],
                "agent_id": params[1],
                "provider": params[2],
                "account_email": params[3],
                "source": params[4],
                "metadata_json": params[5],
            }
            self.next_id += 1
            self.user_agents.append(row)
            return row["id"]
        if "INSERT INTO agent_credentials" in sql:
            row = {
                "credential_id": params[0],
                "agent_id": params[1],
                "public_key_jwk": params[2],
                "public_key_thumbprint": params[3],
                "issuer": "agentguard-local",
                "valid_from": None,
                "valid_to": None,
                "status": "active",
                "revoked_at": None,
                "metadata_json": params[4],
                "created_at": None,
                "updated_at": None,
                "last_seen_at": None,
            }
            for index, existing in enumerate(self.agent_credentials):
                if (
                    existing["agent_id"] == row["agent_id"]
                    and existing["public_key_thumbprint"] == row["public_key_thumbprint"]
                ):
                    self.agent_credentials[index] = {**existing, **row, "credential_id": existing["credential_id"]}
                    return self.next_id
            self.agent_credentials.append(row)
            return self.next_id
        raise AssertionError(sql)

    def fetchone(self, sql: str, params: tuple[Any, ...] | None = None):
        if "FROM agent_credentials" in sql:
            agent_id, thumbprint = params
            for row in self.agent_credentials:
                if (
                    row["agent_id"] == agent_id
                    and row["public_key_thumbprint"] == thumbprint
                    and row["status"] == "active"
                ):
                    return row
            return None
        if "FROM agents a" in sql and "WHERE a.agent_id" in sql:
            agent = self.agents.get(str(params[0]))
            if agent is None:
                return None
            identity = next(
                (
                    item
                    for item in self.agent_external_identities
                    if item["agent_id"] == agent["agent_id"]
                ),
                {},
            )
            return {**agent, **identity}
        if "FROM agent_external_identities" in sql:
            (identity_hash,) = params
            for identity in self.agent_external_identities:
                if identity["identity_hash"] == identity_hash:
                    return {**self.agents[identity["agent_id"]], **identity}
            return None
        if "FROM user_external_accounts" in sql:
            provider, account_email = params
            for row in self.user_external_accounts:
                if row["provider"] == provider and row["account_email"] == account_email:
                    return row
            return None
        if "FROM user_agents" in sql:
            user_id, agent_id = params
            for row in self.user_agents:
                if row["user_id"] == user_id and row["agent_id"] == agent_id:
                    return row
            return None
        return None

    def fetchall(self, sql: str, params: tuple[Any, ...] | None = None):
        if "FROM agent_external_identities e" in sql and "WHERE e.provider = %s AND a.status = 'active'" in sql:
            (provider,) = params
            return [
                identity
                for identity in self.agent_external_identities
                if identity["provider"] == provider
                and self.agents[identity["agent_id"]]["status"] == "active"
            ]
        if "FROM agent_external_identities e" in sql:
            provider, provider_instance_id, agent_type = params[:3]
            tenant_id = params[3] if len(params) > 3 else None
            rows = []
            for identity in self.agent_external_identities:
                if identity["provider"] != provider:
                    continue
                if identity["provider_instance_id"] != provider_instance_id:
                    continue
                if identity["agent_type"] != agent_type:
                    continue
                if tenant_id is not None and identity["tenant_id"] != tenant_id:
                    continue
                rows.append({**identity, "status": self.agents[identity["agent_id"]]["status"]})
            return rows
        if "FROM agent_tools" in sql:
            if "WHERE agent_id = %s" in sql:
                return [row for row in self.agent_tools if row["agent_id"] == params[0]]
            if "WHERE agent_id IN" in sql:
                allowed = set(params)
                return [row for row in self.agent_tools if row["agent_id"] in allowed]
            return list(self.agent_tools)
        if "FROM agent_client_plugins" in sql:
            if "WHERE agent_id = %s" in sql:
                return [row for row in self.agent_client_plugins if row["agent_id"] == params[0]]
            if "WHERE agent_id IN" in sql:
                allowed = set(params)
                return [row for row in self.agent_client_plugins if row["agent_id"] in allowed]
            return list(self.agent_client_plugins)
        if "FROM user_agents" in sql:
            if "SELECT ua.user_id, ua.agent_id, ua.provider, ua.account_email, ua.source" in sql:
                user_id = int(params[0])
                provider = params[1] if len(params) > 1 else None
                rows = [row for row in self.user_agents if row["user_id"] == user_id]
                if provider is not None:
                    rows = [row for row in rows if row["provider"] == provider]
                result = []
                for row in rows:
                    agent = self.agents.get(row["agent_id"])
                    if not agent or agent["status"] != "active":
                        continue
                    if row.get("account_email") is not None and not any(
                        account["user_id"] == row["user_id"]
                        and account["provider"] == row["provider"]
                        and account["account_email"] == row["account_email"]
                        for account in self.user_external_accounts
                    ):
                        continue
                    identity = next(
                        (
                            item
                            for item in self.agent_external_identities
                            if item["agent_id"] == row["agent_id"] and item["provider"] == row["provider"]
                        ),
                        {},
                    )
                    result.append(
                        {
                            **row,
                            "agent_name": agent.get("name"),
                            "agent_status": agent.get("status"),
                            "provider_instance_id": identity.get("provider_instance_id"),
                            "tenant_id": identity.get("tenant_id"),
                            "external_agent_id": identity.get("external_agent_id"),
                            "agent_type": identity.get("agent_type"),
                        }
                    )
                result.sort(
                    key=lambda item: (
                        item.get("updated_at") or "",
                        item.get("created_at") or "",
                        item["agent_id"],
                    ),
                    reverse=True,
                )
                return result
            if "WHERE user_id = %s AND provider = %s AND account_email = %s" in sql:
                user_id, provider, account_email = params
                return [
                    row
                    for row in self.user_agents
                    if row["user_id"] == int(user_id)
                    and row["provider"] == provider
                    and row["account_email"] == account_email
                ]
            if "WHERE user_id = %s AND provider = %s" in sql:
                user_id, provider = params
                return [
                    row
                    for row in self.user_agents
                    if row["user_id"] == int(user_id)
                    and row["provider"] == provider
                ]
            if "WHERE ua.agent_id = %s" in sql:
                agent_id = str(params[0])
                rows = [row for row in self.user_agents if row["agent_id"] == agent_id]
            else:
                user_id = int(params[0])
                rows = [row for row in self.user_agents if row["user_id"] == user_id]
            if "JOIN agents" in sql:
                rows = [row for row in rows if self.agents[row["agent_id"]]["status"] == "active"]
            if "LEFT JOIN user_external_accounts" in sql:
                rows = [
                    row
                    for row in rows
                    if row.get("account_email") is None
                    or any(
                        account["user_id"] == row["user_id"]
                        and account["provider"] == row["provider"]
                        and account["account_email"] == row["account_email"]
                        for account in self.user_external_accounts
                    )
                ]
            return rows
        return []


PUBLIC_JWK = {"kty": "OKP", "crv": "Ed25519", "x": "test-public-key"}


def test_register_agent_creates_user_agent_binding_for_bound_dify_email():
    db = FakeDB()
    db.user_external_accounts.append(
        {"user_id": 7, "provider": "dify", "account_email": "alice@example.com"}
    )
    store = AgentStore(db)

    result = store.register_agent(
        provider="dify",
        provider_instance_id="local-dify",
        tenant_id="tenant-1",
        external_agent_id="app-1",
        agent_type="workflow",
        account_email="Alice@Example.COM",
        public_key_jwk=PUBLIC_JWK,
        metadata={"app_id": "app-1"},
    )

    assert result.user_id == 7
    assert result.agent.agent_id.startswith("ag_")
    assert not result.agent.agent_id.startswith("ag_dify_")
    assert result.user_binding_created is True
    assert db.agent_external_identities[0]["external_agent_id"] == "app-1"
    assert db.agent_external_identities[0]["agent_id"] == result.agent.agent_id
    assert result.credential.agent_id == result.agent.agent_id
    assert result.credential.status == "active"
    assert db.agent_credentials[0]["public_key_thumbprint"] == result.agent.public_key_thumbprint
    assert db.user_agents[0]["agent_id"] == result.agent.agent_id
    assert store.agent_ids_for_user(7) == {result.agent.agent_id}
    assert store.list_agents({result.agent.agent_id})[0].external_agent_id == "app-1"
    assert result.agent.location_tag is None


def test_register_agent_without_bound_email_does_not_create_user_binding():
    db = FakeDB()
    store = AgentStore(db)

    result = store.register_agent(
        provider="dify",
        external_agent_id="app-2",
        agent_type="agent_chat",
        account_email="bob@example.com",
        public_key_jwk=PUBLIC_JWK,
    )

    assert result.user_id is None
    assert result.user_binding_created is False
    assert db.user_agents == []
    metadata = json.loads(db.agent_external_identities[0]["metadata_json"])
    assert metadata["external_account_email"] == "bob@example.com"


def test_update_agent_location_tag_persists_and_validates():
    db = FakeDB()
    store = AgentStore(db)
    registered = store.register_agent(
        provider="dify",
        external_agent_id="app-9",
        agent_type="workflow",
        public_key_jwk=PUBLIC_JWK,
    ).agent

    updated = store.update_agent_location_tag(registered.agent_id, "domestic")

    assert updated is not None
    assert updated.location_tag == "domestic"
    assert store.get_agent(registered.agent_id).location_tag == "domestic"

    try:
        store.update_agent_location_tag(registered.agent_id, "moon")
    except ValueError as exc:
        assert "location_tag" in str(exc)
    else:
        raise AssertionError("expected ValueError for invalid location_tag")


def test_bind_existing_agents_for_external_account_backfills_registered_agent():
    db = FakeDB()
    store = AgentStore(db)
    agent = store.register_agent(
        provider="n8n",
        external_agent_id="workflow-1",
        agent_type="workflow",
        account_email="owner@example.com",
        public_key_jwk=PUBLIC_JWK,
        metadata={"external_account_email": "owner@example.com"},
    ).agent
    assert db.user_agents == []

    result = store.bind_existing_agents_for_external_account(
        user_id=7,
        provider="n8n",
        account_email="Owner@Example.com",
    )

    assert result["matched_count"] == 1
    assert result["created_count"] == 1
    assert result["agent_ids"] == [agent.agent_id]
    assert db.user_agents[0]["user_id"] == 7
    assert db.user_agents[0]["provider"] == "n8n"
    assert db.user_agents[0]["account_email"] == "owner@example.com"


def test_agent_ids_for_user_ignores_stale_external_account_binding():
    db = FakeDB()
    store = AgentStore(db)
    agent = store.register_agent(
        provider="n8n",
        external_agent_id="workflow-1",
        agent_type="workflow",
        account_email="owner@example.com",
        public_key_jwk=PUBLIC_JWK,
    ).agent
    db.user_agents.append(
        {
            "id": db.next_id,
            "user_id": 7,
            "agent_id": agent.agent_id,
            "provider": "n8n",
            "account_email": "owner@example.com",
            "source": "adapter_scan",
            "metadata_json": None,
        }
    )
    db.next_id += 1

    assert store.agent_ids_for_user(7) == set()
    assert store.user_ids_for_agent(agent.agent_id) == set()

    db.user_external_accounts.append(
        {"user_id": 7, "provider": "n8n", "account_email": "owner@example.com"}
    )

    assert store.agent_ids_for_user(7) == {agent.agent_id}
    assert store.user_ids_for_agent(agent.agent_id) == {7}


def test_unbind_agents_for_external_account_removes_matching_bindings():
    db = FakeDB()
    store = AgentStore(db)
    db.user_agents.extend(
        [
            {
                "id": 1,
                "user_id": 7,
                "agent_id": "ag_1",
                "provider": "n8n",
                "account_email": "owner@example.com",
                "source": "adapter_scan",
                "metadata_json": None,
            },
            {
                "id": 2,
                "user_id": 7,
                "agent_id": "ag_2",
                "provider": "n8n",
                "account_email": "other@example.com",
                "source": "adapter_scan",
                "metadata_json": None,
            },
            {
                "id": 3,
                "user_id": 7,
                "agent_id": "ag_3",
                "provider": "openclaw",
                "account_email": None,
                "source": "user_ticket",
                "metadata_json": None,
            },
        ]
    )

    result = store.unbind_agents_for_external_account(
        user_id=7,
        provider="n8n",
        account_email="Owner@Example.com",
    )

    assert result == {
        "provider": "n8n",
        "account_email": "owner@example.com",
        "unbound_count": 1,
        "agent_ids": ["ag_1"],
    }
    assert [row["agent_id"] for row in db.user_agents] == ["ag_2", "ag_3"]


def test_list_user_agent_bindings_includes_openclaw_agent_details():
    db = FakeDB()
    store = AgentStore(db)
    registered = store.register_agent(
        provider="openclaw",
        external_agent_id="agentguard-emailcase2",
        agent_type="agent",
        public_key_jwk=PUBLIC_JWK,
        name="agentguard-emailcase2",
    ).agent
    db.user_agents.append(
        {
            "id": db.next_id,
            "user_id": 7,
            "agent_id": registered.agent_id,
            "provider": "openclaw",
            "account_email": None,
            "source": "user_ticket_bootstrap",
            "metadata_json": '{"ticket_prefix":"agt_test"}',
            "created_at": "2026-07-15 09:15:05",
            "updated_at": "2026-07-15 10:50:38",
        }
    )
    db.next_id += 1

    bindings = store.list_user_agent_bindings(7, provider="openclaw")

    assert len(bindings) == 1
    assert bindings[0].user_id == 7
    assert bindings[0].agent_id == registered.agent_id
    assert bindings[0].provider == "openclaw"
    assert bindings[0].agent_name == "agentguard-emailcase2"
    assert bindings[0].external_agent_id == "agentguard-emailcase2"
    assert bindings[0].agent_type == "agent"
    assert bindings[0].provider_instance_id is None
    assert bindings[0].account_email is None
    assert bindings[0].source == "user_ticket_bootstrap"


def test_unbind_user_agent_removes_matching_openclaw_binding():
    db = FakeDB()
    store = AgentStore(db)
    db.user_agents.extend(
        [
            {
                "id": 1,
                "user_id": 7,
                "agent_id": "ag_openclaw_1",
                "provider": "openclaw",
                "account_email": None,
                "source": "user_ticket_bootstrap",
                "metadata_json": None,
            },
            {
                "id": 2,
                "user_id": 7,
                "agent_id": "ag_n8n_1",
                "provider": "n8n",
                "account_email": "owner@example.com",
                "source": "adapter_scan",
                "metadata_json": None,
            },
        ]
    )

    changed = store.unbind_user_agent(
        user_id=7,
        agent_id="ag_openclaw_1",
        provider="openclaw",
    )

    assert changed is True
    assert [row["agent_id"] for row in db.user_agents] == ["ag_n8n_1"]


def test_unbind_user_provider_removes_all_openclaw_bindings():
    db = FakeDB()
    store = AgentStore(db)
    db.user_agents.extend(
        [
            {
                "id": 1,
                "user_id": 7,
                "agent_id": "ag_openclaw_1",
                "provider": "openclaw",
                "account_email": None,
                "source": "user_ticket_bootstrap",
                "metadata_json": None,
            },
            {
                "id": 2,
                "user_id": 7,
                "agent_id": "ag_openclaw_2",
                "provider": "openclaw",
                "account_email": None,
                "source": "user_ticket_bootstrap",
                "metadata_json": None,
            },
            {
                "id": 3,
                "user_id": 7,
                "agent_id": "ag_n8n_1",
                "provider": "n8n",
                "account_email": "owner@example.com",
                "source": "adapter_scan",
                "metadata_json": None,
            },
        ]
    )

    result = store.unbind_user_provider(
        user_id=7,
        provider="openclaw",
    )

    assert result == {
        "provider": "openclaw",
        "unbound_count": 2,
        "agent_ids": ["ag_openclaw_1", "ag_openclaw_2"],
    }
    assert [row["agent_id"] for row in db.user_agents] == ["ag_n8n_1"]


def test_sync_agent_tools_persists_and_replaces_catalog():
    db = FakeDB()
    store = AgentStore(db)
    agent = store.register_agent(
        provider="dify",
        external_agent_id="app-1",
        agent_type="workflow",
        public_key_jwk=PUBLIC_JWK,
    ).agent

    first = store.sync_agent_tools(
        agent.agent_id,
        [
            {
                "name": "weekday",
                "description": "Weekday",
                "input_params": ["year", "month", "day"],
                "capabilities": ["dify_tool"],
                "metadata": {"node_id": "node-1"},
                "dify_extra": {"provider_config_id": "provider-1"},
            },
            {"name": "old.disabled", "input_params": []},
        ],
    )
    assert [tool.name for tool in first] == ["weekday", "old.disabled"]

    second = store.sync_agent_tools(
        agent.agent_id,
        [
            {
                "name": "weekday",
                "input_params": ["year"],
                "dify_extra": {"provider_config_id": "provider-1"},
            }
        ],
    )
    assert [tool.name for tool in second] == ["weekday"]

    tools = store.list_agent_tools(agent_id=agent.agent_id)
    assert [tool.name for tool in tools] == ["weekday"]
    tool = tools[0].to_console_dict()
    assert tool["input_params"] == ["year"]
    assert tool["raw_payload"]["dify_extra"] == {"provider_config_id": "provider-1"}

    updated = store.update_agent_tool_labels(
        agent.agent_id,
        "weekday",
        {"boundary": "external", "tags": ["calendar"]},
    )
    assert updated is not None
    assert updated.to_console_dict()["labels"]["boundary"] == "external"
    assert updated.to_console_dict()["labels"]["tags"] == ["calendar"]


def test_sync_agent_tools_preserves_explicit_empty_required_args():
    db = FakeDB()
    store = AgentStore(db)
    agent = store.register_agent(
        provider="dify",
        external_agent_id="app-1",
        agent_type="workflow",
        public_key_jwk=PUBLIC_JWK,
    ).agent

    store.sync_agent_tools(
        agent.agent_id,
        [
            {
                "name": "queryEnterpriseInfo",
                "input_params": ["company_name", "credit_code"],
                "required_args": [],
                "schema": {
                    "type": "object",
                    "properties": {
                        "company_name": {},
                        "credit_code": {},
                    },
                    "required": [],
                },
            }
        ],
    )

    tool = store.list_agent_tools(agent_id=agent.agent_id)[0].to_console_dict()
    assert tool["input_params"] == ["company_name", "credit_code"]
    assert tool["required_args"] == []
    assert tool["schema"]["required"] == []


def test_register_agent_persists_client_plugins():
    db = FakeDB()
    store = AgentStore(db)

    agent = store.register_agent(
        provider="dify",
        external_agent_id="app-1",
        agent_type="workflow",
        public_key_jwk=PUBLIC_JWK,
        client_plugins=[
            {
                "name": "client_prompt_guard",
                "description": "Prompt guard",
                "event_types": ["llm_input"],
                "phases": ["llm_before"],
            }
        ],
    ).agent

    plugins = store.list_agent_client_plugins(agent_id=agent.agent_id)
    assert [item.name for item in plugins] == ["client_prompt_guard"]
    assert plugins[0].to_console_dict()["event_types"] == ["llm_input"]


def test_sync_agent_client_plugins_persists_and_replaces_catalog():
    db = FakeDB()
    store = AgentStore(db)
    agent = store.register_agent(
        provider="dify",
        external_agent_id="app-1",
        agent_type="workflow",
        public_key_jwk=PUBLIC_JWK,
    ).agent

    first = store.sync_agent_client_plugins(
        agent.agent_id,
        [
            {
                "name": "client_prompt_guard",
                "description": "Prompt guard",
                "event_types": ["llm_input"],
                "phases": ["llm_before"],
            },
            {
                "name": "tool_result",
                "description": "Result guard",
                "event_types": ["tool_result"],
                "phases": ["tool_after"],
            },
        ],
    )
    assert [item.name for item in first] == ["client_prompt_guard", "tool_result"]

    second = store.sync_agent_client_plugins(
        agent.agent_id,
        [
            {
                "name": "client_prompt_guard",
                "event_types": ["llm_input"],
                "phases": ["llm_before"],
            }
        ],
    )
    assert [item.name for item in second] == ["client_prompt_guard"]

    plugins = store.list_agent_client_plugins(agent_id=agent.agent_id)
    assert [item.name for item in plugins] == ["client_prompt_guard"]
    assert plugins[0].to_console_dict()["phases"] == ["llm_before"]


def test_provider_agent_sync_deactivates_missing_dify_agents():
    db = FakeDB()
    db.user_external_accounts.append(
        {"user_id": 7, "provider": "dify", "account_email": "alice@example.com"}
    )
    store = AgentStore(db)
    first = store.register_agent(
        provider="dify",
        provider_instance_id="local-dify",
        external_agent_id="app-1",
        agent_type="workflow",
        account_email="alice@example.com",
        public_key_jwk=PUBLIC_JWK,
    ).agent
    stale = store.register_agent(
        provider="dify",
        provider_instance_id="local-dify",
        external_agent_id="app-2",
        agent_type="workflow",
        account_email="alice@example.com",
        public_key_jwk=PUBLIC_JWK,
    ).agent

    result = store.sync_provider_agents(
        provider="dify",
        provider_instance_id="local-dify",
        agent_type="workflow",
        external_agent_ids=["app-1"],
        client_plugins=[
            {
                "name": "client_prompt_guard",
                "event_types": ["llm_input"],
                "phases": ["llm_before"],
            }
        ],
    )

    assert result["deactivated_agent_ids"] == [stale.agent_id]
    assert result["synced_agent_ids"] == [first.agent_id]
    assert db.agents[stale.agent_id]["status"] == "deleted"
    assert store.agent_ids_for_user(7) == {first.agent_id}
    assert store.list_agents({first.agent_id, stale.agent_id}) == [first]
    assert [item.name for item in store.list_agent_client_plugins(agent_id=first.agent_id)] == ["client_prompt_guard"]


def test_delete_agent_removes_registry_runtime_sessions_and_traces():
    db = FakeDB()
    db.user_external_accounts.append(
        {"user_id": 7, "provider": "dify", "account_email": "alice@example.com"}
    )
    store = AgentStore(db)
    agent = store.register_agent(
        provider="dify",
        external_agent_id="app-1",
        agent_type="workflow",
        account_email="alice@example.com",
        public_key_jwk=PUBLIC_JWK,
    ).agent
    other = store.register_agent(
        provider="dify",
        external_agent_id="app-2",
        agent_type="workflow",
        public_key_jwk=PUBLIC_JWK,
    ).agent
    store.sync_agent_tools(agent.agent_id, [{"name": "weekday"}])
    store.sync_agent_client_plugins(agent.agent_id, [{"name": "client_prompt_guard", "event_types": ["llm_input"]}])
    db.runtime_sessions.extend(
        [
            {"session_id": "session-1", "agent_id": agent.agent_id},
            {"session_id": "session-2", "agent_id": other.agent_id},
        ]
    )
    db.runtime_tokens.extend(
        [
            {"token_jti": "token-1", "session_id": "session-1"},
            {"token_jti": "token-2", "session_id": "session-2"},
        ]
    )
    db.external_runtime_sessions.extend(
        [
            {"id": 1, "agent_id": agent.agent_id},
            {"id": 2, "agent_id": other.agent_id},
        ]
    )
    db.runtime_trace_events.extend(
        [
            {"id": 1, "agent_id": agent.agent_id, "session_id": "unlinked"},
            {"id": 2, "raw_agent_id": agent.agent_id, "session_id": "legacy"},
            {"id": 3, "runtime_session_id": "session-1", "session_id": "session-1"},
            {"id": 4, "agent_id": other.agent_id, "session_id": "session-2"},
        ]
    )
    db.agent_audit_runs.extend(
        [
            {"run_id": "audit-1", "agent_id": agent.agent_id},
            {"run_id": "audit-2", "agent_id": other.agent_id},
        ]
    )
    db.agent_audit_session_results.extend(
        [
            {"id": 1, "run_id": "audit-1", "session_id": "session-1"},
            {"id": 2, "run_id": "audit-2", "session_id": "session-2"},
        ]
    )
    db.agent_audit_findings.extend(
        [
            {"id": 1, "run_id": "audit-1", "finding_id": "finding-1"},
            {"id": 2, "run_id": "audit-2", "finding_id": "finding-2"},
        ]
    )

    result = store.delete_agent(agent.agent_id)

    assert result.deleted is True
    assert result.audit_run_count == 1
    assert result.trace_event_count == 3
    assert result.runtime_token_count == 1
    assert result.runtime_session_count == 1
    assert result.external_session_mapping_count == 1
    assert result.user_agent_binding_count == 1
    assert result.credential_count == 1
    assert result.external_identity_count == 1
    assert result.tool_count == 1
    assert result.agent_count == 1
    assert agent.agent_id not in db.agents
    assert db.agent_client_plugins == []
    assert db.runtime_sessions == [{"session_id": "session-2", "agent_id": other.agent_id}]
    assert db.runtime_tokens == [{"token_jti": "token-2", "session_id": "session-2"}]
    assert db.external_runtime_sessions == [{"id": 2, "agent_id": other.agent_id}]
    assert db.runtime_trace_events == [{"id": 4, "agent_id": other.agent_id, "session_id": "session-2"}]
    assert db.agent_audit_runs == [{"run_id": "audit-2", "agent_id": other.agent_id}]
    assert db.agent_audit_session_results == [{"id": 2, "run_id": "audit-2", "session_id": "session-2"}]
    assert db.agent_audit_findings == [{"id": 2, "run_id": "audit-2", "finding_id": "finding-2"}]


def test_console_agent_delete_policy_allows_langchain_and_dify_agents():
    assert agent_delete_allowed_from_console(
        AgentRecord(
            agent_id="ag-langchain",
            agent_identity_code="agic-langchain",
            provider="langchain",
        )
    )
    assert agent_delete_allowed_from_console(
        AgentRecord(
            agent_id="ag-dify",
            agent_identity_code="agic-dify",
            provider="dify",
        )
    )
    assert not agent_delete_allowed_from_console(
        AgentRecord(
            agent_id="ag-openclaw",
            agent_identity_code="agic-openclaw",
            provider="openclaw",
        )
    )
    assert agent_delete_allowed_from_console(
        AgentRecord(
            agent_id="ag-metadata",
            agent_identity_code="agic-metadata",
            metadata_json='{"external_provider": "langchain"}',
        )
    )

from __future__ import annotations

from typing import Any

from backend.agents.store import AgentStore


class FakeDB:
    def __init__(self) -> None:
        self.agents: dict[str, dict[str, Any]] = {}
        self.agent_external_identities: list[dict[str, Any]] = []
        self.user_external_accounts: list[dict[str, Any]] = []
        self.user_agents: list[dict[str, Any]] = []
        self.agent_credentials: list[dict[str, Any]] = []
        self.agent_tools: list[dict[str, Any]] = []
        self.next_id = 1

    def execute(self, sql: str, params: tuple[Any, ...] | None = None) -> int:
        if "CREATE TABLE" in sql:
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
        if "UPDATE agents" in sql:
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
                    row["metadata_json"] = params[2] or row.get("metadata_json")
                    return 1
        return 0

    def insert(self, sql: str, params: tuple[Any, ...] | None = None) -> int:
        if "INSERT INTO agents" in sql:
            row = {
                "agent_id": params[0],
                "agent_identity_code": params[1],
                "name": params[2],
                "description": params[3],
                "public_key_jwk": params[4],
                "public_key_thumbprint": params[5],
                "status": "active",
                "metadata_json": params[6],
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
                "metadata_json": params[4],
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
        if "FROM user_agents" in sql:
            user_id = int(params[0])
            rows = [row for row in self.user_agents if row["user_id"] == user_id]
            if "JOIN agents" in sql:
                rows = [row for row in rows if self.agents[row["agent_id"]]["status"] == "active"]
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
    )

    assert result["deactivated_agent_ids"] == [stale.agent_id]
    assert db.agents[stale.agent_id]["status"] == "deleted"
    assert store.agent_ids_for_user(7) == {first.agent_id}
    assert store.list_agents({first.agent_id, stale.agent_id}) == [first]

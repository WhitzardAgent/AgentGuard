from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from backend.agents.store import AgentRecord
from backend.api.app import create_app
from backend.database.config import parse_mysql_url
from backend.user.passwords import hash_password, verify_password
from backend.user.store import (
    DuplicateEmail,
    DuplicateExternalAccount,
    DuplicateUsername,
    EmailVerificationIssue,
    ExternalAccountMapping,
    InvalidCredentials,
    InvalidEmailVerification,
    SessionIssue,
    TicketIssue,
    User,
)


class FakeUserStore:
    def __init__(self) -> None:
        self.users: dict[str, tuple[User, str]] = {}
        self.sessions: dict[str, User] = {}
        self.tickets: list[dict] = []
        self.external_accounts: list[ExternalAccountMapping] = []
        self.email_codes: dict[str, str] = {}

    def create_user(
        self,
        username: str,
        password: str,
        *,
        email: str | None = None,
        verification_code: str | None = None,
        require_verified_email: bool = True,
    ) -> User:
        if username in self.users:
            raise DuplicateUsername(f"username already exists: {username}")
        clean_email = email.strip().lower() if email else None
        if require_verified_email:
            if not clean_email:
                raise ValueError("email is required")
            if self.email_codes.get(clean_email) != verification_code:
                raise InvalidEmailVerification("invalid or expired email verification code")
        if clean_email and any(record[0].email == clean_email for record in self.users.values()):
            raise DuplicateEmail(f"email already exists: {clean_email}")
        user = User(
            id=len(self.users) + 1,
            username=username,
            email=clean_email,
            email_verified_at=datetime.now(timezone.utc) if clean_email else None,
        )
        self.users[username] = (user, password)
        if clean_email:
            self.email_codes.pop(clean_email, None)
        return user

    def issue_email_verification_code(self, email: str) -> EmailVerificationIssue:
        clean_email = email.strip().lower()
        if any(record[0].email == clean_email for record in self.users.values()):
            raise DuplicateEmail(f"email already exists: {clean_email}")
        code = "123456"
        self.email_codes[clean_email] = code
        return EmailVerificationIssue(
            email=clean_email,
            code=code,
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=10),
            cooldown_seconds=60,
        )

    def authenticate(self, username: str, password: str) -> User:
        record = self.users.get(username)
        if not record or record[1] != password:
            raise InvalidCredentials("invalid username or password")
        return record[0]

    def change_password(
        self,
        user: User,
        *,
        current_password: str,
        new_password: str,
    ) -> None:
        if len(new_password) < 8:
            raise ValueError("password must be at least 8 characters")
        for username, record in list(self.users.items()):
            record_user, password = record
            if record_user.id != user.id:
                continue
            if password != current_password:
                raise InvalidCredentials("invalid username or password")
            self.users[username] = (record_user, new_password)
            return
        raise InvalidCredentials("invalid username or password")

    def create_web_session(self, user: User) -> SessionIssue:
        token = f"session-{user.id}"
        self.sessions[token] = user
        return SessionIssue(
            token=token,
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
            user=user,
        )

    def user_for_session(self, token: str | None) -> User | None:
        return self.sessions.get(token or "")

    def revoke_session(self, token: str | None) -> None:
        if token:
            self.sessions.pop(token, None)

    def create_ticket(self, user: User) -> TicketIssue:
        ticket = f"agt-user-{user.id}"
        expires_at = datetime.now(timezone.utc) + timedelta(minutes=5)
        self.tickets.insert(
            0,
            {
                "id": len(self.tickets) + 1,
                "ticket_prefix": ticket[:16],
                "expires_at": expires_at,
                "created_at": datetime.now(timezone.utc),
                "last_used_at": None,
                "expired": False,
            },
        )
        return TicketIssue(ticket=ticket, expires_at=expires_at, prefix=ticket[:16])

    def list_tickets(self, user: User, *, limit: int = 20) -> list[dict]:
        return list(self.tickets[:limit])

    def bind_external_account(
        self,
        user: User,
        *,
        provider: str,
        account_email: str,
        display_name: str | None = None,
        metadata_json: str | None = None,
    ) -> ExternalAccountMapping:
        provider = provider.strip().lower()
        account_email = account_email.strip().lower()
        for account in self.external_accounts:
            if account.provider == provider and account.account_email == account_email:
                if account.user_id != user.id:
                    raise DuplicateExternalAccount("external account already bound")
                updated = ExternalAccountMapping(
                    id=account.id,
                    user_id=account.user_id,
                    provider=account.provider,
                    account_email=account.account_email,
                    display_name=display_name,
                    metadata_json=metadata_json,
                    created_at=account.created_at,
                    updated_at=datetime.now(timezone.utc),
                )
                self.external_accounts[self.external_accounts.index(account)] = updated
                return updated
        mapping = ExternalAccountMapping(
            id=len(self.external_accounts) + 1,
            user_id=user.id,
            provider=provider,
            account_email=account_email,
            display_name=display_name,
            metadata_json=metadata_json,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        self.external_accounts.append(mapping)
        return mapping

    def list_external_accounts(
        self,
        user: User,
        *,
        provider: str | None = None,
    ) -> list[ExternalAccountMapping]:
        return [
            account
            for account in self.external_accounts
            if account.user_id == user.id
            and (provider is None or account.provider == provider.strip().lower())
        ]

    def delete_external_account(self, user: User, mapping_id: int) -> bool:
        for account in list(self.external_accounts):
            if account.id == mapping_id and account.user_id == user.id:
                self.external_accounts.remove(account)
                return True
        return False


class FakeEmailSender:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []

    def send_verification_code(self, *, to_email: str, code: str) -> None:
        self.sent.append((to_email, code))


def patch_user_store_and_email(monkeypatch, store: FakeUserStore) -> FakeEmailSender:
    sender = FakeEmailSender()
    monkeypatch.setattr("backend.user.router.get_user_store", lambda: store)
    monkeypatch.setattr("backend.user.router.get_email_sender", lambda: sender)
    return sender


def register_user(
    client: TestClient,
    *,
    username: str = "alice",
    email: str = "alice@example.com",
    password: str = "correct horse",
    code: str = "123456",
):
    client.post("/v1/user/register/email-code", json={"email": email})
    return client.post(
        "/v1/user/register",
        json={
            "username": username,
            "email": email,
            "password": password,
            "verification_code": code,
        },
    )


def test_user_register_login_me_ticket_and_logout(monkeypatch):
    store = FakeUserStore()
    sender = patch_user_store_and_email(monkeypatch, store)
    client = TestClient(create_app())

    code_sent = client.post("/v1/user/register/email-code", json={"email": "Alice@Example.com"})
    assert code_sent.status_code == 200
    assert sender.sent == [("alice@example.com", "123456")]

    wrong_code = client.post(
        "/v1/user/register",
        json={
            "username": "alice",
            "email": "Alice@Example.com",
            "password": "correct horse",
            "verification_code": "000000",
        },
    )
    assert wrong_code.status_code == 400

    registered = client.post(
        "/v1/user/register",
        json={
            "username": "alice",
            "email": "Alice@Example.com",
            "password": "correct horse",
            "verification_code": "123456",
        },
    )
    assert registered.status_code == 200
    assert registered.json()["user"] == {
        "id": 1,
        "username": "alice",
        "email": "alice@example.com",
        "email_verified": True,
        "is_admin": False,
    }

    duplicate = client.post(
        "/v1/user/register",
        json={
            "username": "alice",
            "email": "alice@example.com",
            "password": "correct horse",
            "verification_code": "123456",
        },
    )
    assert duplicate.status_code in {400, 409}

    bad_login = client.post(
        "/v1/user/login",
        json={"username": "alice", "password": "wrong horse"},
    )
    assert bad_login.status_code == 401

    login = client.post(
        "/v1/user/login",
        json={"username": "alice", "password": "correct horse"},
    )
    assert login.status_code == 200
    assert "agentguard_user_session" in login.headers.get("set-cookie", "")

    me = client.get("/v1/user/me")
    assert me.status_code == 200
    assert me.json()["user"]["username"] == "alice"
    assert me.json()["user"]["is_admin"] is False

    ticket = client.post("/v1/user/tickets")
    assert ticket.status_code == 200
    assert ticket.json()["ticket"].startswith("agt-")

    tickets = client.get("/v1/user/tickets")
    assert tickets.status_code == 200
    assert tickets.json()["tickets"][0]["ticket_prefix"] == "agt-user-1"

    bound = client.post(
        "/v1/user/dify/bind",
        json={"email": "Alice@Example.com", "display_name": "Alice Dify"},
    )
    assert bound.status_code == 200
    assert bound.json()["external_account"]["provider"] == "dify"
    assert bound.json()["external_account"]["account_email"] == "alice@example.com"
    assert bound.json()["external_account"]["user_id"] == 1

    accounts = client.get("/v1/user/external-accounts?provider=dify")
    assert accounts.status_code == 200
    assert accounts.json()["external_accounts"][0]["display_name"] == "Alice Dify"

    deleted = client.delete("/v1/user/external-accounts/1")
    assert deleted.status_code == 200
    assert client.get("/v1/user/external-accounts?provider=dify").json()["external_accounts"] == []

    logout = client.post("/v1/user/logout")
    assert logout.status_code == 200
    assert client.get("/v1/user/me").status_code == 401


def test_logged_in_user_can_change_password(monkeypatch):
    store = FakeUserStore()
    patch_user_store_and_email(monkeypatch, store)
    client = TestClient(create_app())

    unauthenticated = client.post(
        "/v1/user/password",
        json={"current_password": "correct horse", "new_password": "better horse"},
    )
    assert unauthenticated.status_code == 401

    register_user(client)
    login = client.post(
        "/v1/user/login",
        json={"username": "alice", "password": "correct horse"},
    )
    assert login.status_code == 200

    too_short = client.post(
        "/v1/user/password",
        json={"current_password": "correct horse", "new_password": "short"},
    )
    assert too_short.status_code == 422

    wrong_current = client.post(
        "/v1/user/password",
        json={"current_password": "wrong horse", "new_password": "better horse"},
    )
    assert wrong_current.status_code == 401
    assert store.authenticate("alice", "correct horse").username == "alice"

    changed = client.post(
        "/v1/user/password",
        json={"current_password": "correct horse", "new_password": "better horse"},
    )
    assert changed.status_code == 200
    assert changed.json() == {"status": "ok"}
    assert client.get("/v1/user/me").status_code == 200

    logout = client.post("/v1/user/logout")
    assert logout.status_code == 200
    old_login = client.post(
        "/v1/user/login",
        json={"username": "alice", "password": "correct horse"},
    )
    assert old_login.status_code == 401
    new_login = client.post(
        "/v1/user/login",
        json={"username": "alice", "password": "better horse"},
    )
    assert new_login.status_code == 200


def test_dify_email_can_only_bind_one_agentguard_user(monkeypatch):
    store = FakeUserStore()
    patch_user_store_and_email(monkeypatch, store)
    client = TestClient(create_app())

    register_user(client, username="alice", email="alice@example.com")
    client.post("/v1/user/login", json={"username": "alice", "password": "correct horse"})
    first = client.post("/v1/user/dify/bind", json={"email": "shared@example.com"})
    assert first.status_code == 200
    client.post("/v1/user/logout")

    register_user(client, username="bob", email="bob@example.com")
    client.post("/v1/user/login", json={"username": "bob", "password": "correct horse"})
    duplicate = client.post("/v1/user/dify/bind", json={"email": "shared@example.com"})

    assert duplicate.status_code == 409


def test_console_visibility_requires_logged_in_user(monkeypatch):
    store = FakeUserStore()
    user = store.create_user(
        "alice",
        "correct horse",
        email="alice@example.com",
        verification_code="",
        require_verified_email=False,
    )
    session = store.create_web_session(user)
    store.bind_external_account(
        user,
        provider="dify",
        account_email="Alice@Example.com",
    )
    monkeypatch.setattr("backend.api.console_router.get_user_store", lambda: store)

    from backend.api.console_router import _visible_external_accounts

    assert _visible_external_accounts(None) == set()
    assert _visible_external_accounts("missing-session") == set()
    assert _visible_external_accounts(session.token) == {("dify", "alice@example.com")}


def test_admin_user_payload_and_visibility_are_unrestricted(monkeypatch):
    store = FakeUserStore()
    admin = User(id=1, username="AgentGuardAdmin", profile_json='{"role":"admin"}')
    store.users[admin.username] = (admin, "correct horse")
    session = store.create_web_session(admin)
    patch_user_store_and_email(monkeypatch, store)
    monkeypatch.setattr("backend.api.console_router.get_user_store", lambda: store)

    class FakeAgentStore:
        def list_agents(self, agent_ids=None):
            assert agent_ids is None
            return [
                AgentRecord(
                    agent_id="ag_1",
                    agent_identity_code="code-1",
                    status="active",
                    name="Agent One",
                ),
                AgentRecord(
                    agent_id="ag_2",
                    agent_identity_code="code-2",
                    status="active",
                    name="Agent Two",
                ),
            ]

        def agent_ids_for_user(self, user_id: int):
            raise AssertionError("admin visibility should not query user agent ids")

    monkeypatch.setattr("backend.api.console_router.AgentStore", FakeAgentStore)
    client = TestClient(create_app())

    me = client.get("/v1/user/me", cookies={"agentguard_user_session": session.token})
    assert me.status_code == 200
    assert me.json()["user"] == {
        "id": 1,
        "username": "AgentGuardAdmin",
        "email": None,
        "email_verified": False,
        "is_admin": True,
    }

    from backend.api.console_router import _visible_scope

    visible = _visible_scope(session.token)
    assert visible["is_admin"] is True
    assert visible["agent_ids"] is None
    assert visible["external_accounts"] is None

    agents = client.get(
        "/v1/backend/agents",
        cookies={"agentguard_user_session": session.token},
    )
    assert agents.status_code == 200
    assert [item["agent_id"] for item in agents.json()] == ["ag_1", "ag_2"]


def test_password_hashes_are_not_plaintext():
    encoded = hash_password("correct horse")

    assert encoded != "correct horse"
    assert encoded.startswith("pbkdf2_sha256$")
    assert verify_password("correct horse", encoded)
    assert not verify_password("wrong horse", encoded)


def test_parse_mysql_url():
    config = parse_mysql_url(
        "mysql+pymysql://agentguard:pa%24%24@db.example:3307/agentguard?charset=utf8mb4"
    )

    assert config.host == "db.example"
    assert config.port == 3307
    assert config.user == "agentguard"
    assert config.password == "pa$$"
    assert config.database == "agentguard"
    assert config.charset == "utf8mb4"

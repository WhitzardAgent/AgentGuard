from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from backend.api.app import create_app
from backend.database.config import parse_mysql_url
from backend.user.passwords import hash_password, verify_password
from backend.user.store import (
    DuplicateExternalAccount,
    DuplicateUsername,
    ExternalAccountMapping,
    InvalidCredentials,
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

    def create_user(self, username: str, password: str) -> User:
        if username in self.users:
            raise DuplicateUsername(f"username already exists: {username}")
        user = User(id=len(self.users) + 1, username=username)
        self.users[username] = (user, password)
        return user

    def authenticate(self, username: str, password: str) -> User:
        record = self.users.get(username)
        if not record or record[1] != password:
            raise InvalidCredentials("invalid username or password")
        return record[0]

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


def test_user_register_login_me_ticket_and_logout(monkeypatch):
    store = FakeUserStore()
    monkeypatch.setattr("backend.user.router.get_user_store", lambda: store)
    client = TestClient(create_app())

    registered = client.post(
        "/v1/user/register",
        json={"username": "alice", "password": "correct horse"},
    )
    assert registered.status_code == 200
    assert registered.json()["user"] == {"id": 1, "username": "alice"}

    duplicate = client.post(
        "/v1/user/register",
        json={"username": "alice", "password": "correct horse"},
    )
    assert duplicate.status_code == 409

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


def test_dify_email_can_only_bind_one_agentguard_user(monkeypatch):
    store = FakeUserStore()
    monkeypatch.setattr("backend.user.router.get_user_store", lambda: store)
    client = TestClient(create_app())

    client.post("/v1/user/register", json={"username": "alice", "password": "correct horse"})
    client.post("/v1/user/login", json={"username": "alice", "password": "correct horse"})
    first = client.post("/v1/user/dify/bind", json={"email": "shared@example.com"})
    assert first.status_code == 200
    client.post("/v1/user/logout")

    client.post("/v1/user/register", json={"username": "bob", "password": "correct horse"})
    client.post("/v1/user/login", json={"username": "bob", "password": "correct horse"})
    duplicate = client.post("/v1/user/dify/bind", json={"email": "shared@example.com"})

    assert duplicate.status_code == 409


def test_console_visibility_requires_logged_in_user(monkeypatch):
    store = FakeUserStore()
    user = store.create_user("alice", "correct horse")
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

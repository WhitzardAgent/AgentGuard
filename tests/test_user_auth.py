from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from backend.api.app import create_app
from backend.database.config import parse_mysql_url
from backend.user.passwords import hash_password, verify_password
from backend.user.store import (
    DuplicateUsername,
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

    logout = client.post("/v1/user/logout")
    assert logout.status_code == 200
    assert client.get("/v1/user/me").status_code == 401


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

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi.testclient import TestClient

from backend.api.app import create_app
from backend.user.org_store import (
    GroupRecord,
    InvitationIssue,
    InvitationRecord,
    MemberRecord,
    OrganizationAccessDenied,
    OrganizationRecord,
)
from backend.user.store import User


class FakeUserStore:
    def __init__(self, users: list[User]) -> None:
        self.users = {user.id: user for user in users}
        self.sessions = {f"session-{user.id}": user for user in users}

    def user_for_session(self, token: str | None) -> User | None:
        return self.sessions.get(token or "")

    def set_user(self, user: User) -> None:
        self.users[user.id] = user
        self.sessions[f"session-{user.id}"] = user


class FakeOrgStore:
    def __init__(self, user_store: FakeUserStore) -> None:
        self.user_store = user_store
        self.organizations: dict[int, OrganizationRecord] = {}
        self.groups: dict[int, GroupRecord] = {}
        self.organization_members: dict[int, dict[int, str]] = {}
        self.group_members: dict[int, dict[int, str]] = {}
        self.invitations: dict[str, InvitationRecord] = {}
        self.next_org_id = 1
        self.next_group_id = 1
        self.next_invitation_id = 1

    def update_user_profile(
        self,
        user: User,
        *,
        username: str | None = None,
        email: str | None = None,
        display_name: str | None = None,
    ) -> User:
        profile = _profile(user.profile_json)
        profile["display_name"] = display_name
        updated = User(
            id=user.id,
            username=username or user.username,
            email=email or user.email,
            email_verified_at=user.email_verified_at,
            profile_json=json.dumps(profile),
        )
        self.user_store.set_user(updated)
        return updated

    def create_organization(
        self,
        user: User,
        *,
        name: str,
        display_name: str | None = None,
        description: str | None = None,
    ) -> OrganizationRecord:
        org_id = self.next_org_id
        self.next_org_id += 1
        record = OrganizationRecord(
            id=org_id,
            name=name,
            display_name=display_name or name,
            description=description,
            admin_user_id=user.id,
            created_by_user_id=user.id,
            current_user_role="admin",
            member_count=1,
        )
        self.organizations[org_id] = record
        self.organization_members[org_id] = {user.id: "admin"}
        return record

    def list_organizations(self, user: User) -> list[OrganizationRecord]:
        return [
            self._org_for_user(user, org_id)
            for org_id, members in self.organization_members.items()
            if user.id in members
        ]

    def get_organization(self, user: User, organization_id: int) -> OrganizationRecord | None:
        if organization_id not in self.organizations:
            return None
        if user.id not in self.organization_members.get(organization_id, {}):
            raise OrganizationAccessDenied("organization not visible")
        return self._org_for_user(user, organization_id)

    def create_group(
        self,
        user: User,
        *,
        organization_id: int,
        name: str,
        display_name: str | None = None,
        description: str | None = None,
    ) -> GroupRecord:
        if user.id not in self.organization_members.get(organization_id, {}):
            raise OrganizationAccessDenied("organization membership required")
        group_id = self.next_group_id
        self.next_group_id += 1
        record = GroupRecord(
            id=group_id,
            organization_id=organization_id,
            name=name,
            display_name=display_name or name,
            description=description,
            admin_user_id=user.id,
            created_by_user_id=user.id,
            current_user_role="admin",
            member_count=1,
        )
        self.groups[group_id] = record
        self.group_members[group_id] = {user.id: "admin"}
        return record

    def list_groups(
        self,
        user: User,
        *,
        organization_id: int | None = None,
    ) -> list[GroupRecord]:
        return [
            self._group_for_user(user, group_id)
            for group_id, group in self.groups.items()
            if (organization_id is None or group.organization_id == organization_id)
            and user.id in self.organization_members.get(group.organization_id, {})
        ]

    def get_group(self, user: User, group_id: int) -> GroupRecord | None:
        group = self.groups.get(group_id)
        if group is None:
            return None
        if user.id not in self.organization_members.get(group.organization_id, {}):
            raise OrganizationAccessDenied("group not visible")
        return self._group_for_user(user, group_id)

    def list_organization_members(self, user: User, organization_id: int) -> list[MemberRecord]:
        self._require_org_member(user, organization_id)
        return [
            self._member(user_id, role)
            for user_id, role in self.organization_members.get(organization_id, {}).items()
        ]

    def list_group_members(self, user: User, group_id: int) -> list[MemberRecord]:
        group = self.groups[group_id]
        self._require_org_member(user, group.organization_id)
        return [
            self._member(user_id, role)
            for user_id, role in self.group_members.get(group_id, {}).items()
        ]

    def invite_to_organization(
        self,
        user: User,
        *,
        organization_id: int,
        email: str | None,
    ) -> InvitationIssue:
        self._require_org_admin(user, organization_id)
        return self._issue_invitation(user, organization_id=organization_id, group_id=None, email=email)

    def invite_to_group(
        self,
        user: User,
        *,
        group_id: int,
        email: str | None,
    ) -> InvitationIssue:
        group = self.groups[group_id]
        if self.group_members.get(group_id, {}).get(user.id) != "admin":
            raise OrganizationAccessDenied("group administrator access required")
        return self._issue_invitation(
            user,
            organization_id=group.organization_id,
            group_id=group_id,
            email=email,
        )

    def list_invitations(
        self,
        user: User,
        *,
        organization_id: int | None = None,
        group_id: int | None = None,
    ) -> list[InvitationRecord]:
        records = list(self.invitations.values())
        return [
            record
            for record in records
            if self.organization_members.get(record.organization_id, {}).get(user.id) == "admin"
            and (organization_id is None or record.organization_id == organization_id)
            and (group_id is None or record.group_id == group_id)
        ]

    def accept_invitation(self, user: User, *, token: str) -> InvitationRecord:
        record = self.invitations[token]
        self.organization_members.setdefault(record.organization_id, {})[user.id] = "member"
        if record.group_id is not None:
            self.group_members.setdefault(record.group_id, {})[user.id] = "member"
        accepted = InvitationRecord(
            id=record.id,
            token_prefix=record.token_prefix,
            email=record.email,
            organization_id=record.organization_id,
            organization_name=record.organization_name,
            group_id=record.group_id,
            group_name=record.group_name,
            invited_by_user_id=record.invited_by_user_id,
            status="accepted",
            expires_at=record.expires_at,
            accepted_at=datetime.now(timezone.utc),
            created_at=record.created_at,
        )
        self.invitations[token] = accepted
        return accepted

    def _issue_invitation(
        self,
        user: User,
        *,
        organization_id: int,
        group_id: int | None,
        email: str | None,
    ) -> InvitationIssue:
        token = f"agiv-test-{self.next_invitation_id}"
        invitation_id = self.next_invitation_id
        self.next_invitation_id += 1
        expires_at = datetime.now(timezone.utc) + timedelta(days=7)
        org = self.organizations[organization_id]
        group = self.groups.get(group_id or 0)
        record = InvitationRecord(
            id=invitation_id,
            token_prefix=token[:16],
            email=email.strip().lower() if email else None,
            organization_id=organization_id,
            organization_name=org.name,
            group_id=group_id,
            group_name=group.name if group else None,
            invited_by_user_id=user.id,
            status="pending",
            expires_at=expires_at,
            created_at=datetime.now(timezone.utc),
        )
        self.invitations[token] = record
        return InvitationIssue(
            id=invitation_id,
            token=token,
            token_prefix=token[:16],
            email=record.email,
            organization_id=organization_id,
            group_id=group_id,
            status="pending",
            expires_at=expires_at,
            created_at=record.created_at,
        )

    def _require_org_member(self, user: User, organization_id: int) -> None:
        if user.id not in self.organization_members.get(organization_id, {}):
            raise OrganizationAccessDenied("organization membership required")

    def _require_org_admin(self, user: User, organization_id: int) -> None:
        if self.organization_members.get(organization_id, {}).get(user.id) != "admin":
            raise OrganizationAccessDenied("organization administrator access required")

    def _org_for_user(self, user: User, organization_id: int) -> OrganizationRecord:
        record = self.organizations[organization_id]
        return OrganizationRecord(
            **{
                **record.__dict__,
                "current_user_role": self.organization_members[organization_id].get(user.id),
                "member_count": len(self.organization_members.get(organization_id, {})),
            }
        )

    def _group_for_user(self, user: User, group_id: int) -> GroupRecord:
        record = self.groups[group_id]
        return GroupRecord(
            **{
                **record.__dict__,
                "current_user_role": self.group_members.get(group_id, {}).get(user.id),
                "member_count": len(self.group_members.get(group_id, {})),
            }
        )

    def _member(self, user_id: int, role: str) -> MemberRecord:
        user = self.user_store.users[user_id]
        return MemberRecord(
            user_id=user.id,
            username=user.username,
            email=user.email,
            role=role,
            joined_at=datetime.now(timezone.utc),
        )


def test_organization_group_admins_invitations_and_profile(monkeypatch):
    alice = User(
        id=1,
        username="alice",
        email="alice@example.com",
        email_verified_at=datetime.now(timezone.utc),
    )
    bob = User(
        id=2,
        username="bob",
        email="bob@example.com",
        email_verified_at=datetime.now(timezone.utc),
    )
    user_store = FakeUserStore([alice, bob])
    org_store = FakeOrgStore(user_store)
    monkeypatch.setattr("backend.user.router.get_user_store", lambda: user_store)
    monkeypatch.setattr("backend.user.router.get_org_store", lambda: org_store)
    client = TestClient(create_app())

    profile = client.patch(
        "/v1/user/me",
        json={
            "username": "alice-renamed",
            "email": "alice-new@example.com",
            "display_name": "Alice Admin",
        },
        cookies={"agentguard_user_session": "session-1"},
    )
    assert profile.status_code == 200
    assert profile.json()["user"]["username"] == "alice-renamed"
    assert profile.json()["user"]["email"] == "alice-new@example.com"
    assert profile.json()["user"]["display_name"] == "Alice Admin"

    org = client.post(
        "/v1/user/organizations",
        json={"name": "engineering", "display_name": "Engineering"},
        cookies={"agentguard_user_session": "session-1"},
    )
    assert org.status_code == 200
    assert org.json()["organization"]["admin_user_id"] == alice.id
    assert org.json()["organization"]["current_user_role"] == "admin"

    group = client.post(
        "/v1/user/organizations/1/groups",
        json={"name": "platform", "display_name": "Platform"},
        cookies={"agentguard_user_session": "session-1"},
    )
    assert group.status_code == 200
    assert group.json()["group"]["admin_user_id"] == alice.id
    assert group.json()["group"]["current_user_role"] == "admin"

    forbidden_org_invite = client.post(
        "/v1/user/organizations/1/invitations",
        json={"email": "carol@example.com"},
        cookies={"agentguard_user_session": "session-2"},
    )
    assert forbidden_org_invite.status_code == 403

    forbidden_group_invite = client.post(
        "/v1/user/groups/1/invitations",
        json={"email": "carol@example.com"},
        cookies={"agentguard_user_session": "session-2"},
    )
    assert forbidden_group_invite.status_code == 403

    org_invite = client.post(
        "/v1/user/organizations/1/invitations",
        json={"email": "bob@example.com"},
        cookies={"agentguard_user_session": "session-1"},
    )
    assert org_invite.status_code == 200
    org_token = org_invite.json()["invitation"]["token"]

    accepted_org = client.post(
        "/v1/user/invitations/accept",
        json={"token": org_token},
        cookies={"agentguard_user_session": "session-2"},
    )
    assert accepted_org.status_code == 200
    assert accepted_org.json()["invitation"]["status"] == "accepted"

    org_members = client.get(
        "/v1/user/organizations/1/members",
        cookies={"agentguard_user_session": "session-1"},
    )
    assert org_members.status_code == 200
    assert {item["username"]: item["role"] for item in org_members.json()["members"]} == {
        "alice": "admin",
        "bob": "member",
    }

    group_invite = client.post(
        "/v1/user/groups/1/invitations",
        json={"email": "bob@example.com"},
        cookies={"agentguard_user_session": "session-1"},
    )
    assert group_invite.status_code == 200
    group_token = group_invite.json()["invitation"]["token"]

    accepted_group = client.post(
        "/v1/user/invitations/accept",
        json={"token": group_token},
        cookies={"agentguard_user_session": "session-2"},
    )
    assert accepted_group.status_code == 200

    group_members = client.get(
        "/v1/user/groups/1/members",
        cookies={"agentguard_user_session": "session-2"},
    )
    assert group_members.status_code == 200
    assert {item["username"]: item["role"] for item in group_members.json()["members"]} == {
        "alice": "admin",
        "bob": "member",
    }


def _profile(value: Any) -> dict[str, Any]:
    if not value:
        return {}
    parsed = json.loads(str(value))
    return parsed if isinstance(parsed, dict) else {}

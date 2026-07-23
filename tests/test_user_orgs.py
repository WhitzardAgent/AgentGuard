from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi.testclient import TestClient

from backend.api.app import create_app
from backend.user.org_store import (
    AdminRecord,
    GroupRecord,
    InvitationIssue,
    InvitationRecord,
    MemberRemovalNotAllowed,
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
        self.group_users: dict[int, dict[int, str]] = {}
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
        organization_name: str,
        organization_description: str | None = None,
    ) -> OrganizationRecord:
        org_id = self.next_org_id
        self.next_org_id += 1
        self.organization_members[org_id] = {}
        self._upsert_org_member(org_id, user.id, "admin")
        self._upsert_org_member(org_id, self._builtin_admin_user().id, "admin")
        record = OrganizationRecord(
            organization_id=org_id,
            organization_name=organization_name,
            organization_description=organization_description,
        )
        self.organizations[org_id] = record
        return self._org_for_user(user, org_id)

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

    def update_organization(
        self,
        user: User,
        organization_id: int,
        *,
        organization_name: str | None = None,
        organization_description: str | None = None,
    ) -> OrganizationRecord:
        self._require_org_admin(user, organization_id)
        organization = self.organizations[organization_id]
        updated = OrganizationRecord(
            organization_id=organization.organization_id,
            organization_name=organization_name or organization.organization_name,
            organization_description=organization_description,
        )
        self.organizations[organization_id] = updated
        return self._org_for_user(user, organization_id)

    def delete_organization(self, user: User, organization_id: int) -> bool:
        self._require_org_admin(user, organization_id)
        org_groups = [
            group_id
            for group_id, group in self.groups.items()
            if group.organization_id == organization_id
        ]
        for group_id in org_groups:
            self.groups.pop(group_id, None)
            self.group_users.pop(group_id, None)
        self.invitations = {
            token: record
            for token, record in self.invitations.items()
            if record.organization_id != organization_id
        }
        self.organization_members.pop(organization_id, None)
        deleted = self.organizations.pop(organization_id, None) is not None
        return deleted

    def create_group(
        self,
        user: User,
        *,
        organization_id: int,
        group_name: str,
        group_description: str | None = None,
    ) -> GroupRecord:
        if user.id not in self.organization_members.get(organization_id, {}):
            raise OrganizationAccessDenied("organization membership required")
        self._upsert_org_member(organization_id, self._builtin_admin_user().id, "admin")
        group_id = self.next_group_id
        self.next_group_id += 1
        record = GroupRecord(
            group_id=group_id,
            organization_id=organization_id,
            group_name=group_name,
            group_description=group_description,
        )
        self.groups[group_id] = record
        self.group_users[group_id] = {}
        self._upsert_group_user(group_id, user.id, "admin")
        self._upsert_group_user(group_id, self._builtin_admin_user().id, "admin")
        return self._group_for_user(user, group_id)

    def list_groups(
        self,
        user: User,
        *,
        organization_id: int | None = None,
    ) -> list[GroupRecord]:
        return [
            self._group_for_user(user, group_id)
            for group_id, group in self.groups.items()
            if (
                (organization_id is None and user.id in self.group_users.get(group_id, {}))
                or (
                    organization_id is not None
                    and group.organization_id == organization_id
                    and user.id in self.organization_members.get(group.organization_id, {})
                )
            )
        ]

    def get_group(self, user: User, group_id: int) -> GroupRecord | None:
        group = self.groups.get(group_id)
        if group is None:
            return None
        if user.id not in self.organization_members.get(group.organization_id, {}):
            raise OrganizationAccessDenied("group not visible")
        return self._group_for_user(user, group_id)

    def update_group(
        self,
        user: User,
        group_id: int,
        *,
        group_name: str | None = None,
        group_description: str | None = None,
    ) -> GroupRecord:
        group = self._require_group_admin(user, group_id)
        updated = GroupRecord(
            group_id=group.group_id,
            organization_id=group.organization_id,
            group_name=group_name or group.group_name,
            group_description=group_description,
        )
        self.groups[group_id] = updated
        return self._group_for_user(user, group_id)

    def delete_group(self, user: User, group_id: int) -> bool:
        self._require_group_admin(user, group_id)
        self.groups.pop(group_id, None)
        self.group_users.pop(group_id, None)
        return True

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
            for user_id, role in self.group_users.get(group_id, {}).items()
        ]

    def remove_organization_member(self, user: User, organization_id: int, member_user_id: int) -> bool:
        self._require_org_admin(user, organization_id)
        organization = self.organizations.get(organization_id)
        if organization is None:
            return False
        member_role = self.organization_members.get(organization_id, {}).get(member_user_id)
        if member_role is None:
            return False
        if member_role == "admin":
            raise MemberRemovalNotAllowed("cannot remove organization administrator")
        if any(
            group.organization_id == organization_id
            and self.group_users.get(group.group_id, {}).get(member_user_id) == "admin"
            for group in self.groups.values()
        ):
            raise MemberRemovalNotAllowed("cannot remove organization member who administers a group")
        for group_id, group in self.groups.items():
            if group.organization_id == organization_id:
                self.group_users.get(group_id, {}).pop(member_user_id, None)
        del self.organization_members[organization_id][member_user_id]
        return True

    def remove_group_member(self, user: User, group_id: int, member_user_id: int) -> bool:
        self._require_group_admin(user, group_id)
        if self.group_users.get(group_id, {}).get(member_user_id) == "admin":
            raise MemberRemovalNotAllowed("cannot remove group administrator")
        members = self.group_users.get(group_id, {})
        if member_user_id not in members:
            return False
        members.pop(member_user_id, None)
        return True

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
        group = self._require_group_admin(user, group_id)
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
            self.group_users.setdefault(record.group_id, {})[user.id] = "member"
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
        expires_at = datetime.now(timezone.utc) + timedelta(hours=24)
        org = self.organizations[organization_id]
        group = self.groups.get(group_id or 0)
        record = InvitationRecord(
            id=invitation_id,
            token_prefix=token[:16],
            email=email.strip().lower() if email else None,
            organization_id=organization_id,
            organization_name=org.organization_name,
            group_id=group_id,
            group_name=group.group_name if group else None,
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

    def _require_group_admin(self, user: User, group_id: int) -> GroupRecord:
        group = self.groups[group_id]
        if self.organization_members.get(group.organization_id, {}).get(user.id) == "admin":
            return group
        if self.group_users.get(group_id, {}).get(user.id) != "admin":
            raise OrganizationAccessDenied("group administrator access required")
        return group

    def _org_for_user(self, user: User, organization_id: int) -> OrganizationRecord:
        record = self.organizations[organization_id]
        return OrganizationRecord(
            **{
                **record.__dict__,
                "admins": self._organization_admins(organization_id),
                "current_user_role": self.organization_members[organization_id].get(user.id),
                "member_count": len(self.organization_members.get(organization_id, {})),
            }
        )

    def _group_for_user(self, user: User, group_id: int) -> GroupRecord:
        record = self.groups[group_id]
        return GroupRecord(
            **{
                **record.__dict__,
                "admins": self._group_admins(group_id),
                "current_user_role": self.group_users.get(group_id, {}).get(user.id),
                "member_count": len(self.group_users.get(group_id, {})),
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

    def _organization_admins(self, organization_id: int) -> tuple[AdminRecord, ...]:
        admin_ids = [
            user_id for user_id, role in self.organization_members.get(organization_id, {}).items() if role == "admin"
        ]
        return tuple(
            AdminRecord(
                user_id=admin.id,
                username=admin.username,
                email=admin.email,
            )
            for admin in sorted((self.user_store.users[user_id] for user_id in admin_ids), key=lambda item: item.username)
        )

    def _group_admins(self, group_id: int) -> tuple[AdminRecord, ...]:
        admin_ids = [
            user_id for user_id, role in self.group_users.get(group_id, {}).items() if role == "admin"
        ]
        return tuple(
            AdminRecord(
                user_id=admin.id,
                username=admin.username,
                email=admin.email,
            )
            for admin in sorted((self.user_store.users[user_id] for user_id in admin_ids), key=lambda item: item.username)
        )

    def _upsert_org_member(self, organization_id: int, user_id: int, role: str) -> None:
        self.organization_members.setdefault(organization_id, {})[user_id] = role

    def _upsert_group_user(self, group_id: int, user_id: int, role: str) -> None:
        self.group_users.setdefault(group_id, {})[user_id] = role

    def _builtin_admin_user(self) -> User:
        for user in self.user_store.users.values():
            if user.username == "AgentGuardAdmin":
                return user
        raise AssertionError("AgentGuardAdmin test user is missing")


def test_organization_group_admins_invitations_and_profile(monkeypatch):
    agentguard_admin = User(
        id=99,
        username="AgentGuardAdmin",
        email="admin@example.com",
        email_verified_at=datetime.now(timezone.utc),
        profile_json='{"role":"admin"}',
    )
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
    user_store = FakeUserStore([agentguard_admin, alice, bob])
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
        json={"organization_name": "engineering", "organization_description": "Engineering org"},
        cookies={"agentguard_user_session": "session-1"},
    )
    assert org.status_code == 200
    assert org.json()["organization"]["organization_name"] == "engineering"
    assert org.json()["organization"]["current_user_role"] == "admin"
    assert org.json()["organization"]["admins"] == [
        {"user_id": 99, "username": "AgentGuardAdmin", "email": "admin@example.com"},
        {"user_id": 1, "username": "alice-renamed", "email": "alice-new@example.com"},
    ]
    assert "organization_admin_id" not in org.json()["organization"]

    updated_org = client.patch(
        "/v1/user/organizations/1",
        json={"organization_name": "engineering-core", "organization_description": "Core engineering org"},
        cookies={"agentguard_user_session": "session-1"},
    )
    assert updated_org.status_code == 200
    assert updated_org.json()["organization"]["organization_name"] == "engineering-core"
    assert updated_org.json()["organization"]["organization_description"] == "Core engineering org"

    group = client.post(
        "/v1/user/organizations/1/groups",
        json={"group_name": "platform", "group_description": "Platform team"},
        cookies={"agentguard_user_session": "session-1"},
    )
    assert group.status_code == 200
    assert group.json()["group"]["group_name"] == "platform"
    assert group.json()["group"]["current_user_role"] == "admin"
    assert group.json()["group"]["admins"] == [
        {"user_id": 99, "username": "AgentGuardAdmin", "email": "admin@example.com"},
        {"user_id": 1, "username": "alice-renamed", "email": "alice-new@example.com"},
    ]
    assert "group_admin_id" not in group.json()["group"]

    updated_group = client.patch(
        "/v1/user/groups/1",
        json={"group_name": "platform-core", "group_description": "Core platform team"},
        cookies={"agentguard_user_session": "session-1"},
    )
    assert updated_group.status_code == 200
    assert updated_group.json()["group"]["group_name"] == "platform-core"
    assert updated_group.json()["group"]["group_description"] == "Core platform team"

    organization_invite = client.post(
        "/v1/user/organizations/1/invitations",
        json={"email": "carol@example.com"},
        cookies={"agentguard_user_session": "session-2"},
    )
    assert organization_invite.status_code == 404

    forbidden_group_invite = client.post(
        "/v1/user/groups/1/invitations",
        json={"email": "carol@example.com"},
        cookies={"agentguard_user_session": "session-2"},
    )
    assert forbidden_group_invite.status_code == 403

    group_invite = client.post(
        "/v1/user/groups/1/invitations",
        json={"email": "bob@example.com"},
        cookies={"agentguard_user_session": "session-1"},
    )
    assert group_invite.status_code == 200
    group_invitation = group_invite.json()["invitation"]
    group_token = group_invitation["token"]
    group_expires_at = datetime.fromisoformat(group_invitation["expires_at"].replace("Z", "+00:00"))
    assert timedelta(hours=23, minutes=59) <= group_expires_at - datetime.now(timezone.utc) <= timedelta(hours=24, minutes=1)

    accepted_group = client.post(
        "/v1/user/invitations/accept",
        json={"token": group_token},
        cookies={"agentguard_user_session": "session-2"},
    )
    assert accepted_group.status_code == 200

    org_members = client.get(
        "/v1/user/organizations/1/members",
        cookies={"agentguard_user_session": "session-1"},
    )
    assert org_members.status_code == 200
    assert {item["username"]: item["role"] for item in org_members.json()["members"]} == {
        "AgentGuardAdmin": "admin",
        "alice-renamed": "admin",
        "bob": "member",
    }

    group_members = client.get(
        "/v1/user/groups/1/members",
        cookies={"agentguard_user_session": "session-2"},
    )
    assert group_members.status_code == 200
    assert {item["username"]: item["role"] for item in group_members.json()["members"]} == {
        "AgentGuardAdmin": "admin",
        "alice-renamed": "admin",
        "bob": "member",
    }

    deleted_group_member = client.delete(
        "/v1/user/groups/1/members/2",
        cookies={"agentguard_user_session": "session-1"},
    )
    assert deleted_group_member.status_code == 200
    assert deleted_group_member.json()["removed"] is True

    remaining_group_members = client.get(
        "/v1/user/groups/1/members",
        cookies={"agentguard_user_session": "session-1"},
    )
    assert remaining_group_members.status_code == 200
    assert {item["username"]: item["role"] for item in remaining_group_members.json()["members"]} == {
        "AgentGuardAdmin": "admin",
        "alice-renamed": "admin",
    }

    forbidden_admin_group_removal = client.delete(
        "/v1/user/groups/1/members/1",
        cookies={"agentguard_user_session": "session-1"},
    )
    assert forbidden_admin_group_removal.status_code == 409

    deleted_org_member = client.delete(
        "/v1/user/organizations/1/members/2",
        cookies={"agentguard_user_session": "session-1"},
    )
    assert deleted_org_member.status_code == 200
    assert deleted_org_member.json()["removed"] is True

    remaining_org_members = client.get(
        "/v1/user/organizations/1/members",
        cookies={"agentguard_user_session": "session-1"},
    )
    assert remaining_org_members.status_code == 200
    assert {item["username"]: item["role"] for item in remaining_org_members.json()["members"]} == {
        "AgentGuardAdmin": "admin",
        "alice-renamed": "admin",
    }

    removed_member_org_visibility = client.get(
        "/v1/user/organizations/1/members",
        cookies={"agentguard_user_session": "session-2"},
    )
    assert removed_member_org_visibility.status_code == 403

    forbidden_group_delete = client.delete(
        "/v1/user/groups/1",
        cookies={"agentguard_user_session": "session-2"},
    )
    assert forbidden_group_delete.status_code == 403

    deleted_group = client.delete(
        "/v1/user/groups/1",
        cookies={"agentguard_user_session": "session-1"},
    )
    assert deleted_group.status_code == 200
    assert deleted_group.json()["deleted"] is True

    cascade_group = client.post(
        "/v1/user/organizations/1/groups",
        json={"group_name": "security", "group_description": "Security team"},
        cookies={"agentguard_user_session": "session-1"},
    )
    assert cascade_group.status_code == 200

    forbidden_org_delete = client.delete(
        "/v1/user/organizations/1",
        cookies={"agentguard_user_session": "session-2"},
    )
    assert forbidden_org_delete.status_code == 403

    deleted_org = client.delete(
        "/v1/user/organizations/1",
        cookies={"agentguard_user_session": "session-1"},
    )
    assert deleted_org.status_code == 200
    assert deleted_org.json()["deleted"] is True
    assert org_store.organizations == {}
    assert org_store.groups == {}


def _profile(value: Any) -> dict[str, Any]:
    if not value:
        return {}
    parsed = json.loads(str(value))
    return parsed if isinstance(parsed, dict) else {}

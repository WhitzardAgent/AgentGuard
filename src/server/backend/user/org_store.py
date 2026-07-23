"""Organization, group, membership, and invitation persistence."""
from __future__ import annotations

import hashlib
import json
import os
import secrets
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from backend.database import MySQLDatabase, get_database
from backend.user.permissions import is_admin_user
from backend.user.store import User

INVITATION_TTL_ENV = "AGENTGUARD_USER_INVITATION_TTL_SECONDS"
DEFAULT_INVITATION_TTL_SECONDS = 86_400
BUILTIN_ADMIN_USERNAME = "AgentGuardAdmin"
ROLE_ADMIN = "admin"
ROLE_MEMBER = "member"
INVITATION_PENDING = "pending"
INVITATION_ACCEPTED = "accepted"
INVITATION_REVOKED = "revoked"


@dataclass(frozen=True)
class AdminRecord:
    user_id: int
    username: str
    email: str | None


@dataclass(frozen=True)
class OrganizationRecord:
    organization_id: int
    organization_name: str
    organization_description: str | None
    admins: tuple[AdminRecord, ...] = ()
    current_user_role: str | None = None
    member_count: int | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    @property
    def id(self) -> int:
        return self.organization_id

    @property
    def name(self) -> str:
        return self.organization_name

    @property
    def display_name(self) -> str:
        return self.organization_name

    @property
    def description(self) -> str | None:
        return self.organization_description


@dataclass(frozen=True)
class GroupRecord:
    group_id: int
    organization_id: int
    group_name: str
    group_description: str | None
    admins: tuple[AdminRecord, ...] = ()
    current_user_role: str | None = None
    member_count: int | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    @property
    def id(self) -> int:
        return self.group_id

    @property
    def name(self) -> str:
        return self.group_name

    @property
    def display_name(self) -> str:
        return self.group_name

    @property
    def description(self) -> str | None:
        return self.group_description


@dataclass(frozen=True)
class MemberRecord:
    user_id: int
    username: str
    email: str | None
    role: str
    joined_at: datetime | None = None


@dataclass(frozen=True)
class InvitationIssue:
    id: int
    token: str
    token_prefix: str
    email: str | None
    organization_id: int
    group_id: int | None
    status: str
    expires_at: datetime
    created_at: datetime | None = None


@dataclass(frozen=True)
class InvitationRecord:
    id: int
    token_prefix: str
    email: str | None
    organization_id: int
    organization_name: str | None
    group_id: int | None
    group_name: str | None
    invited_by_user_id: int
    status: str
    expires_at: datetime
    accepted_at: datetime | None = None
    created_at: datetime | None = None


class OrganizationAccessDenied(PermissionError):
    pass


class OrganizationNotFound(ValueError):
    pass


class GroupNotFound(ValueError):
    pass


class InvalidInvitation(ValueError):
    pass


class MemberRemovalNotAllowed(ValueError):
    pass


class OrgStore:
    def __init__(self, db: MySQLDatabase | None = None) -> None:
        self.db = db or get_database()

    def ensure_schema(self) -> None:
        for statement in _SCHEMA:
            self.db.execute(statement)

    def update_user_profile(
        self,
        user: User,
        *,
        username: str | None = None,
        email: str | None = None,
        display_name: str | None = None,
    ) -> User:
        clean_username = _normalize_username(username) if username is not None else user.username
        clean_email = _normalize_email(email) if email is not None else user.email
        profile = _profile_object(user.profile_json)
        if display_name is not None:
            profile["display_name"] = _optional_text(display_name)
        profile_json = json.dumps(profile, sort_keys=True, separators=(",", ":"))
        self.db.execute(
            """
            UPDATE users
            SET username = %s,
                email = %s,
                email_verified_at = CASE
                  WHEN %s <=> email THEN email_verified_at
                  ELSE NULL
                END,
                profile_json = %s,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = %s
            """,
            (clean_username, clean_email, clean_email, profile_json, user.id),
        )
        row = self.db.fetchone(
            "SELECT id, username, email, email_verified_at, profile_json FROM users WHERE id = %s",
            (user.id,),
        )
        if row is None:
            raise ValueError("user not found")
        return User(
            id=int(row["id"]),
            username=str(row["username"]),
            email=_optional_text(row.get("email")),
            email_verified_at=_coerce_datetime(row["email_verified_at"]) if row.get("email_verified_at") else None,
            profile_json=row.get("profile_json"),
        )

    def create_organization(
        self,
        user: User,
        *,
        organization_name: str,
        organization_description: str | None = None,
    ) -> OrganizationRecord:
        clean_name = _normalize_name(organization_name, "organization name")
        builtin_admin = self._require_builtin_admin_user()
        org_id = self.db.insert(
            """
            INSERT INTO organizations (
              organization_name, organization_description
            )
            VALUES (%s, %s)
            """,
            (
                clean_name,
                _optional_text(organization_description),
            ),
        )
        self._upsert_org_member(org_id, user.id, ROLE_ADMIN)
        self._upsert_org_member(org_id, builtin_admin.id, ROLE_ADMIN)
        record = self.get_organization(user, org_id, global_admin=is_admin_user(user))
        if record is None:
            raise RuntimeError("failed to create organization")
        return record

    def list_organizations(
        self,
        user: User,
        *,
        global_admin: bool | None = None,
    ) -> list[OrganizationRecord]:
        if global_admin is None:
            global_admin = is_admin_user(user)
        params: tuple[Any, ...]
        if global_admin:
            where = ""
            params = ()
        else:
            where = "WHERE m.user_id = %s"
            params = (user.id,)
        rows = self.db.fetchall(
            f"""
            SELECT o.organization_id, o.organization_name, o.organization_description,
                   o.created_at, o.updated_at,
                   m.role AS current_user_role,
                   (
                     SELECT COUNT(*) FROM organization_members om
                     WHERE om.organization_id = o.organization_id
                   ) AS member_count
            FROM organizations o
            LEFT JOIN organization_members m
              ON m.organization_id = o.organization_id AND m.user_id = %s
            {where}
            ORDER BY o.updated_at DESC, o.created_at DESC, o.organization_name ASC
            """,
            (user.id, *params),
        )
        admin_map = self._organization_admins_by_id(int(row["organization_id"]) for row in rows)
        return [_organization_from_row(row, admin_map.get(int(row["organization_id"]), ())) for row in rows]

    def get_organization(
        self,
        user: User,
        organization_id: int,
        *,
        global_admin: bool | None = None,
    ) -> OrganizationRecord | None:
        if global_admin is None:
            global_admin = is_admin_user(user)
        row = self.db.fetchone(
            """
            SELECT o.organization_id, o.organization_name, o.organization_description,
                   o.created_at, o.updated_at,
                   m.role AS current_user_role,
                   (
                     SELECT COUNT(*) FROM organization_members om
                     WHERE om.organization_id = o.organization_id
                   ) AS member_count
            FROM organizations o
            LEFT JOIN organization_members m
              ON m.organization_id = o.organization_id AND m.user_id = %s
            WHERE o.organization_id = %s
            """,
            (user.id, int(organization_id)),
        )
        if row is None:
            return None
        if not global_admin and not row.get("current_user_role"):
            raise OrganizationAccessDenied("organization not visible")
        admins = self._organization_admins_by_id((int(organization_id),)).get(int(organization_id), ())
        return _organization_from_row(row, admins)

    def update_organization(
        self,
        user: User,
        organization_id: int,
        *,
        organization_name: str | None = None,
        organization_description: str | None = None,
        global_admin: bool | None = None,
    ) -> OrganizationRecord:
        self._require_org_admin(user, organization_id, global_admin=global_admin)
        self.db.execute(
            """
            UPDATE organizations
            SET organization_name = COALESCE(%s, organization_name),
                organization_description = %s,
                updated_at = CURRENT_TIMESTAMP
            WHERE organization_id = %s
            """,
            (_optional_text(organization_name), _optional_text(organization_description), int(organization_id)),
        )
        record = self.get_organization(user, organization_id, global_admin=True)
        if record is None:
            raise OrganizationNotFound("organization not found")
        return record

    def delete_organization(
        self,
        user: User,
        organization_id: int,
        *,
        global_admin: bool | None = None,
    ) -> bool:
        self._require_org_admin(user, organization_id, global_admin=global_admin)
        org_id = int(organization_id)
        self.db.execute(
            "DELETE FROM user_invitations WHERE organization_id = %s",
            (org_id,),
        )
        self.db.execute(
            """
            DELETE FROM group_users
            WHERE group_id IN (
              SELECT group_id FROM user_groups WHERE organization_id = %s
            )
            """,
            (org_id,),
        )
        self.db.execute(
            "DELETE FROM user_groups WHERE organization_id = %s",
            (org_id,),
        )
        self.db.execute(
            "DELETE FROM organization_members WHERE organization_id = %s",
            (org_id,),
        )
        changed = self.db.execute(
            "DELETE FROM organizations WHERE organization_id = %s",
            (org_id,),
        )
        return changed > 0

    def create_group(
        self,
        user: User,
        *,
        organization_id: int,
        group_name: str,
        group_description: str | None = None,
        global_admin: bool | None = None,
    ) -> GroupRecord:
        self._require_org_member(user, organization_id, global_admin=global_admin)
        clean_name = _normalize_name(group_name, "group name")
        builtin_admin = self._require_builtin_admin_user()
        self._upsert_org_member(int(organization_id), builtin_admin.id, ROLE_ADMIN)
        group_id = self.db.insert(
            """
            INSERT INTO user_groups (
              organization_id, group_name, group_description
            )
            VALUES (%s, %s, %s)
            """,
            (
                int(organization_id),
                clean_name,
                _optional_text(group_description),
            ),
        )
        self._upsert_group_user(group_id, user.id, ROLE_ADMIN)
        self._upsert_group_user(group_id, builtin_admin.id, ROLE_ADMIN)
        record = self.get_group(user, group_id, global_admin=True)
        if record is None:
            raise RuntimeError("failed to create group")
        return record

    def list_groups(
        self,
        user: User,
        *,
        organization_id: int | None = None,
        global_admin: bool | None = None,
    ) -> list[GroupRecord]:
        if global_admin is None:
            global_admin = is_admin_user(user)
        params: list[Any] = [user.id]
        where = []
        if organization_id is not None:
            where.append("g.organization_id = %s")
            params.append(int(organization_id))
        if not global_admin:
            if organization_id is None:
                where.append("gu.user_id IS NOT NULL")
            else:
                where.append(
                    """
                    EXISTS (
                      SELECT 1 FROM organization_members om
                      WHERE om.organization_id = g.organization_id AND om.user_id = %s
                    )
                    """
                )
                params.append(user.id)
        where_clause = f"WHERE {' AND '.join(where)}" if where else ""
        rows = self.db.fetchall(
            f"""
            SELECT g.group_id, g.organization_id, g.group_name, g.group_description,
                   g.created_at, g.updated_at,
                   gu.role AS current_user_role,
                   (
                     SELECT COUNT(*) FROM group_users gu_count
                     WHERE gu_count.group_id = g.group_id
                   ) AS member_count
            FROM user_groups g
            LEFT JOIN group_users gu
              ON gu.group_id = g.group_id AND gu.user_id = %s
            {where_clause}
            ORDER BY g.updated_at DESC, g.created_at DESC, g.group_name ASC
            """,
            tuple(params),
        )
        admin_map = self._group_admins_by_id(int(row["group_id"]) for row in rows)
        return [_group_from_row(row, admin_map.get(int(row["group_id"]), ())) for row in rows]

    def get_group(
        self,
        user: User,
        group_id: int,
        *,
        global_admin: bool | None = None,
    ) -> GroupRecord | None:
        if global_admin is None:
            global_admin = is_admin_user(user)
        row = self.db.fetchone(
            """
            SELECT g.group_id, g.organization_id, g.group_name, g.group_description,
                   g.created_at, g.updated_at,
                   gu.role AS current_user_role,
                   (
                     SELECT COUNT(*) FROM group_users gu_count
                     WHERE gu_count.group_id = g.group_id
                   ) AS member_count
            FROM user_groups g
            LEFT JOIN group_users gu
              ON gu.group_id = g.group_id AND gu.user_id = %s
            WHERE g.group_id = %s
            """,
            (user.id, int(group_id)),
        )
        if row is None:
            return None
        if not global_admin and not self._is_org_member(user.id, int(row["organization_id"])):
            raise OrganizationAccessDenied("group not visible")
        admins = self._group_admins_by_id((int(group_id),)).get(int(group_id), ())
        return _group_from_row(row, admins)

    def update_group(
        self,
        user: User,
        group_id: int,
        *,
        group_name: str | None = None,
        group_description: str | None = None,
        global_admin: bool | None = None,
    ) -> GroupRecord:
        self._require_group_admin(user, group_id, global_admin=global_admin)
        self.db.execute(
            """
            UPDATE user_groups
            SET group_name = COALESCE(%s, group_name),
                group_description = %s,
                updated_at = CURRENT_TIMESTAMP
            WHERE group_id = %s
            """,
            (_optional_text(group_name), _optional_text(group_description), int(group_id)),
        )
        record = self.get_group(user, group_id, global_admin=True)
        if record is None:
            raise GroupNotFound("group not found")
        return record

    def delete_group(
        self,
        user: User,
        group_id: int,
        *,
        global_admin: bool | None = None,
    ) -> bool:
        self._require_group_admin(user, group_id, global_admin=global_admin)
        changed = self.db.execute(
            "DELETE FROM user_groups WHERE group_id = %s",
            (int(group_id),),
        )
        return changed > 0

    def list_organization_members(
        self,
        user: User,
        organization_id: int,
        *,
        global_admin: bool | None = None,
    ) -> list[MemberRecord]:
        self._require_org_member(user, organization_id, global_admin=global_admin)
        rows = self.db.fetchall(
            """
            SELECT u.id AS user_id, u.username, u.email, m.role, m.created_at AS joined_at
            FROM organization_members m
            JOIN users u ON u.id = m.user_id
            WHERE m.organization_id = %s
            ORDER BY m.role ASC, u.username ASC
            """,
            (int(organization_id),),
        )
        return [_member_from_row(row) for row in rows]

    def list_group_members(
        self,
        user: User,
        group_id: int,
        *,
        global_admin: bool | None = None,
    ) -> list[MemberRecord]:
        group = self.get_group(user, group_id, global_admin=global_admin)
        if group is None:
            raise GroupNotFound("group not found")
        rows = self.db.fetchall(
            """
            SELECT u.id AS user_id, u.username, u.email, m.role, m.created_at AS joined_at
            FROM group_users m
            JOIN users u ON u.id = m.user_id
            WHERE m.group_id = %s
            ORDER BY role ASC, u.username ASC
            """,
            (int(group_id),),
        )
        return [_member_from_row(row) for row in rows]

    def remove_organization_member(
        self,
        user: User,
        organization_id: int,
        member_user_id: int,
        *,
        global_admin: bool | None = None,
    ) -> bool:
        self._require_org_admin(user, organization_id, global_admin=global_admin)
        org = self.get_organization(user, organization_id, global_admin=True)
        if org is None:
            raise OrganizationNotFound("organization not found")
        member_row = self.db.fetchone(
            """
            SELECT role FROM organization_members
            WHERE organization_id = %s AND user_id = %s
            """,
            (int(organization_id), int(member_user_id)),
        )
        if member_row is None:
            return False
        if str(member_row.get("role") or "").lower() == ROLE_ADMIN:
            raise MemberRemovalNotAllowed("cannot remove organization administrator")
        admin_group = self.db.fetchone(
            """
            SELECT gu.group_id
            FROM group_users gu
            JOIN user_groups g ON g.group_id = gu.group_id
            WHERE g.organization_id = %s
              AND gu.user_id = %s
              AND gu.role = %s
            LIMIT 1
            """,
            (int(organization_id), int(member_user_id), ROLE_ADMIN),
        )
        if admin_group is not None:
            raise MemberRemovalNotAllowed("cannot remove organization member who administers a group")
        self.db.execute(
            """
            DELETE FROM group_users
            WHERE user_id = %s
              AND group_id IN (
                SELECT group_id FROM user_groups WHERE organization_id = %s
              )
            """,
            (int(member_user_id), int(organization_id)),
        )
        changed = self.db.execute(
            """
            DELETE FROM organization_members
            WHERE organization_id = %s AND user_id = %s
            """,
            (int(organization_id), int(member_user_id)),
        )
        return changed > 0

    def remove_group_member(
        self,
        user: User,
        group_id: int,
        member_user_id: int,
        *,
        global_admin: bool | None = None,
    ) -> bool:
        self._require_group_admin(user, group_id, global_admin=global_admin)
        member_row = self.db.fetchone(
            """
            SELECT role FROM group_users
            WHERE group_id = %s AND user_id = %s
            """,
            (int(group_id), int(member_user_id)),
        )
        if member_row is None:
            return False
        if str(member_row.get("role") or "").lower() == ROLE_ADMIN:
            raise MemberRemovalNotAllowed("cannot remove group administrator")
        changed = self.db.execute(
            """
            DELETE FROM group_users
            WHERE group_id = %s AND user_id = %s
            """,
            (int(group_id), int(member_user_id)),
        )
        return changed > 0

    def invite_to_organization(
        self,
        user: User,
        *,
        organization_id: int,
        email: str | None,
        global_admin: bool | None = None,
    ) -> InvitationIssue:
        self._require_org_admin(user, organization_id, global_admin=global_admin)
        return self._create_invitation(
            user,
            organization_id=int(organization_id),
            group_id=None,
            email=email,
        )

    def invite_to_group(
        self,
        user: User,
        *,
        group_id: int,
        email: str | None,
        global_admin: bool | None = None,
    ) -> InvitationIssue:
        group = self._require_group_admin(user, group_id, global_admin=global_admin)
        return self._create_invitation(
            user,
            organization_id=group.organization_id,
            group_id=int(group_id),
            email=email,
        )

    def list_invitations(
        self,
        user: User,
        *,
        organization_id: int | None = None,
        group_id: int | None = None,
        global_admin: bool | None = None,
    ) -> list[InvitationRecord]:
        if global_admin is None:
            global_admin = is_admin_user(user)
        where: list[str] = []
        params: list[Any] = []
        if organization_id is not None:
            self._require_org_admin(user, organization_id, global_admin=global_admin)
            where.append("i.organization_id = %s")
            params.append(int(organization_id))
        elif not global_admin:
            where.append(
                """
                EXISTS (
                  SELECT 1 FROM organization_members om
                  WHERE om.organization_id = i.organization_id
                    AND om.user_id = %s
                    AND om.role = 'admin'
                )
                """
            )
            params.append(user.id)
        if group_id is not None:
            self._require_group_admin(user, group_id, global_admin=global_admin)
            where.append("i.group_id = %s")
            params.append(int(group_id))
        where_clause = f"WHERE {' AND '.join(where)}" if where else ""
        rows = self.db.fetchall(
            f"""
            SELECT i.id, i.token_prefix, i.email, i.organization_id, o.organization_name AS organization_name,
                   i.group_id, g.group_name AS group_name, i.invited_by_user_id, i.status,
                   i.expires_at, i.accepted_at, i.created_at
            FROM user_invitations i
            JOIN organizations o ON o.organization_id = i.organization_id
            LEFT JOIN user_groups g ON g.group_id = i.group_id
            {where_clause}
            ORDER BY i.created_at DESC, i.id DESC
            """,
            tuple(params),
        )
        return [_invitation_record_from_row(row) for row in rows]

    def accept_invitation(self, user: User, *, token: str) -> InvitationRecord:
        token_hash = _hash_token(token)
        row = self.db.fetchone(
            """
            SELECT i.id, i.token_prefix, i.email, i.organization_id, o.organization_name AS organization_name,
                   i.group_id, g.group_name AS group_name, i.invited_by_user_id, i.status,
                   i.expires_at, i.accepted_at, i.created_at
            FROM user_invitations i
            JOIN organizations o ON o.organization_id = i.organization_id
            LEFT JOIN user_groups g ON g.group_id = i.group_id
            WHERE i.token_hash = %s
            """,
            (token_hash,),
        )
        if row is None:
            raise InvalidInvitation("invalid invitation")
        invitation = _invitation_record_from_row(row)
        if invitation.status != INVITATION_PENDING:
            raise InvalidInvitation("invitation is not pending")
        if invitation.expires_at <= datetime.now(timezone.utc):
            raise InvalidInvitation("invitation has expired")
        if invitation.email and (user.email or "").strip().lower() != invitation.email:
            raise OrganizationAccessDenied("invitation email does not match current user")
        self._upsert_org_member(invitation.organization_id, user.id, ROLE_MEMBER)
        if invitation.group_id is not None:
            self._upsert_group_user(invitation.group_id, user.id, ROLE_MEMBER)
        self.db.execute(
            """
            UPDATE user_invitations
            SET status = %s,
                accepted_at = UTC_TIMESTAMP(),
                accepted_by_user_id = %s
            WHERE id = %s AND status = %s
            """,
            (INVITATION_ACCEPTED, user.id, invitation.id, INVITATION_PENDING),
        )
        updated = self.db.fetchone(
            """
            SELECT i.id, i.token_prefix, i.email, i.organization_id, o.organization_name AS organization_name,
                   i.group_id, g.group_name AS group_name, i.invited_by_user_id, i.status,
                   i.expires_at, i.accepted_at, i.created_at
            FROM user_invitations i
            JOIN organizations o ON o.organization_id = i.organization_id
            LEFT JOIN user_groups g ON g.group_id = i.group_id
            WHERE i.id = %s
            """,
            (invitation.id,),
        )
        return _invitation_record_from_row(updated)

    def _create_invitation(
        self,
        user: User,
        *,
        organization_id: int,
        group_id: int | None,
        email: str | None,
    ) -> InvitationIssue:
        token = _new_token()
        token_prefix = token[:16]
        ttl = _int_env(INVITATION_TTL_ENV, DEFAULT_INVITATION_TTL_SECONDS)
        invite_id = self.db.insert(
            """
            INSERT INTO user_invitations (
              token_hash, token_prefix, email, organization_id, group_id,
              invited_by_user_id, status, expires_at
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, DATE_ADD(UTC_TIMESTAMP(), INTERVAL %s SECOND))
            """,
            (
                _hash_token(token),
                token_prefix,
                _normalize_email_or_none(email),
                int(organization_id),
                int(group_id) if group_id is not None else None,
                user.id,
                INVITATION_PENDING,
                ttl,
            ),
        )
        row = self.db.fetchone(
            "SELECT expires_at, created_at FROM user_invitations WHERE id = %s",
            (invite_id,),
        )
        return InvitationIssue(
            id=invite_id,
            token=token,
            token_prefix=token_prefix,
            email=_normalize_email_or_none(email),
            organization_id=int(organization_id),
            group_id=int(group_id) if group_id is not None else None,
            status=INVITATION_PENDING,
            expires_at=_datetime_from_row(row, "expires_at"),
            created_at=_coerce_datetime(row["created_at"]) if row and row.get("created_at") else None,
        )

    def _require_org_member(
        self,
        user: User,
        organization_id: int,
        *,
        global_admin: bool | None,
    ) -> None:
        if global_admin is None:
            global_admin = is_admin_user(user)
        if global_admin:
            if not self._organization_exists(organization_id):
                raise OrganizationNotFound("organization not found")
            return
        if not self._is_org_member(user.id, organization_id):
            raise OrganizationAccessDenied("organization membership required")

    def _require_org_admin(
        self,
        user: User,
        organization_id: int,
        *,
        global_admin: bool | None,
    ) -> None:
        if global_admin is None:
            global_admin = is_admin_user(user)
        if global_admin:
            if not self._organization_exists(organization_id):
                raise OrganizationNotFound("organization not found")
            return
        row = self.db.fetchone(
            """
            SELECT role FROM organization_members
            WHERE organization_id = %s AND user_id = %s
            """,
            (int(organization_id), user.id),
        )
        if row is None:
            raise OrganizationAccessDenied("organization membership required")
        if str(row.get("role") or "").lower() != ROLE_ADMIN:
            raise OrganizationAccessDenied("organization administrator access required")

    def _require_group_admin(
        self,
        user: User,
        group_id: int,
        *,
        global_admin: bool | None,
    ) -> GroupRecord:
        group = self.get_group(user, group_id, global_admin=True)
        if group is None:
            raise GroupNotFound("group not found")
        if global_admin is None:
            global_admin = is_admin_user(user)
        if global_admin:
            return group
        if self._is_org_admin(user.id, group.organization_id):
            return group
        if not self._is_group_admin(user.id, group_id):
            raise OrganizationAccessDenied("group administrator access required")
        return group

    def _organization_exists(self, organization_id: int) -> bool:
        row = self.db.fetchone(
            "SELECT organization_id FROM organizations WHERE organization_id = %s",
            (int(organization_id),),
        )
        return row is not None

    def _is_org_member(self, user_id: int, organization_id: int) -> bool:
        row = self.db.fetchone(
            """
            SELECT id FROM organization_members
            WHERE organization_id = %s AND user_id = %s
            """,
            (int(organization_id), int(user_id)),
        )
        return row is not None

    def _is_org_admin(self, user_id: int, organization_id: int) -> bool:
        row = self.db.fetchone(
            """
            SELECT role FROM organization_members
            WHERE organization_id = %s AND user_id = %s
            """,
            (int(organization_id), int(user_id)),
        )
        return str(row.get("role") or "").lower() == ROLE_ADMIN if row else False

    def _is_group_admin(self, user_id: int, group_id: int) -> bool:
        row = self.db.fetchone(
            """
            SELECT role FROM group_users
            WHERE group_id = %s AND user_id = %s
            """,
            (int(group_id), int(user_id)),
        )
        return str(row.get("role") or "").lower() == ROLE_ADMIN if row else False

    def _upsert_org_member(self, organization_id: int, user_id: int, role: str) -> None:
        existing = self.db.fetchone(
            """
            SELECT id, role FROM organization_members
            WHERE organization_id = %s AND user_id = %s
            """,
            (int(organization_id), int(user_id)),
        )
        clean_role = _normalize_role(role)
        if existing is None:
            self.db.insert(
                """
                INSERT INTO organization_members (organization_id, user_id, role)
                VALUES (%s, %s, %s)
                """,
                (int(organization_id), int(user_id), clean_role),
            )
            return
        if clean_role == ROLE_ADMIN and str(existing.get("role") or "").lower() != ROLE_ADMIN:
            self.db.execute(
                "UPDATE organization_members SET role = %s WHERE id = %s",
                (ROLE_ADMIN, int(existing["id"])),
            )

    def _upsert_group_user(self, group_id: int, user_id: int, role: str) -> None:
        existing = self.db.fetchone(
            """
            SELECT role FROM group_users
            WHERE group_id = %s AND user_id = %s
            """,
            (int(group_id), int(user_id)),
        )
        clean_role = _normalize_role(role)
        if existing is None:
            self.db.insert(
                """
                INSERT INTO group_users (group_id, user_id, role)
                VALUES (%s, %s, %s)
                """,
                (int(group_id), int(user_id), clean_role),
            )
            return
        if clean_role == ROLE_ADMIN and str(existing.get("role") or "").lower() != ROLE_ADMIN:
            self.db.execute(
                """
                UPDATE group_users
                SET role = %s
                WHERE group_id = %s AND user_id = %s
                """,
                (ROLE_ADMIN, int(group_id), int(user_id)),
            )

    def _require_builtin_admin_user(self) -> User:
        row = self.db.fetchone(
            """
            SELECT id, username, email, email_verified_at, profile_json
            FROM users
            WHERE username = %s
            """,
            (BUILTIN_ADMIN_USERNAME,),
        )
        if row is None:
            raise ValueError(f"{BUILTIN_ADMIN_USERNAME} user is missing")
        user = User(
            id=int(row["id"]),
            username=str(row["username"]),
            email=_optional_text(row.get("email")),
            email_verified_at=_coerce_datetime(row["email_verified_at"]) if row.get("email_verified_at") else None,
            profile_json=row.get("profile_json"),
        )
        if not is_admin_user(user):
            raise ValueError(f"{BUILTIN_ADMIN_USERNAME} must be a global admin user")
        return user

    def _organization_admins_by_id(
        self,
        organization_ids: Any,
    ) -> dict[int, tuple[AdminRecord, ...]]:
        ids = sorted({int(item) for item in organization_ids})
        if not ids:
            return {}
        placeholders = ", ".join(["%s"] * len(ids))
        rows = self.db.fetchall(
            f"""
            SELECT m.organization_id, u.id AS user_id, u.username, u.email
            FROM organization_members m
            JOIN users u ON u.id = m.user_id
            WHERE m.organization_id IN ({placeholders})
              AND m.role = %s
            ORDER BY u.username ASC
            """,
            (*ids, ROLE_ADMIN),
        )
        admins: dict[int, list[AdminRecord]] = {org_id: [] for org_id in ids}
        for row in rows:
            admins[int(row["organization_id"])].append(_admin_from_row(row))
        return {org_id: tuple(items) for org_id, items in admins.items()}

    def _group_admins_by_id(
        self,
        group_ids: Any,
    ) -> dict[int, tuple[AdminRecord, ...]]:
        ids = sorted({int(item) for item in group_ids})
        if not ids:
            return {}
        placeholders = ", ".join(["%s"] * len(ids))
        rows = self.db.fetchall(
            f"""
            SELECT gu.group_id, u.id AS user_id, u.username, u.email
            FROM group_users gu
            JOIN users u ON u.id = gu.user_id
            WHERE gu.group_id IN ({placeholders})
              AND gu.role = %s
            ORDER BY u.username ASC
            """,
            (*ids, ROLE_ADMIN),
        )
        admins: dict[int, list[AdminRecord]] = {group_id: [] for group_id in ids}
        for row in rows:
            admins[int(row["group_id"])].append(_admin_from_row(row))
        return {group_id: tuple(items) for group_id, items in admins.items()}


def ensure_org_schema() -> None:
    OrgStore().ensure_schema()


_SCHEMA = [
    """
    CREATE TABLE IF NOT EXISTS organizations (
      organization_id INT AUTO_INCREMENT PRIMARY KEY,
      organization_name VARCHAR(255) NOT NULL UNIQUE,
      organization_description TEXT NULL,
      created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
      updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS organization_members (
      id INT AUTO_INCREMENT PRIMARY KEY,
      organization_id INT NOT NULL,
      user_id INT NOT NULL,
      role VARCHAR(32) NOT NULL DEFAULT 'member',
      created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
      UNIQUE KEY uniq_organization_membership (organization_id, user_id),
      INDEX idx_organization_members_user_id (user_id),
      CONSTRAINT fk_organization_members_org
        FOREIGN KEY (organization_id) REFERENCES organizations(organization_id)
        ON DELETE CASCADE,
      CONSTRAINT fk_organization_members_user
        FOREIGN KEY (user_id) REFERENCES users(id)
        ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS user_groups (
      group_id INT AUTO_INCREMENT PRIMARY KEY,
      organization_id INT NOT NULL,
      group_name VARCHAR(255) NOT NULL,
      group_description TEXT NULL,
      created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
      updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
      UNIQUE KEY uniq_user_groups_org_name (organization_id, group_name),
      INDEX idx_user_groups_organization_id (organization_id),
      CONSTRAINT fk_user_groups_organization
        FOREIGN KEY (organization_id) REFERENCES organizations(organization_id)
        ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS group_users (
      group_id INT NOT NULL,
      user_id INT NOT NULL,
      role VARCHAR(32) NOT NULL DEFAULT 'member',
      created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
      PRIMARY KEY (group_id, user_id),
      INDEX idx_group_users_user_id (user_id),
      CONSTRAINT fk_group_users_group
        FOREIGN KEY (group_id) REFERENCES user_groups(group_id)
        ON DELETE CASCADE,
      CONSTRAINT fk_group_users_user
        FOREIGN KEY (user_id) REFERENCES users(id)
        ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS user_invitations (
      id INT AUTO_INCREMENT PRIMARY KEY,
      token_hash CHAR(64) NOT NULL UNIQUE,
      token_prefix VARCHAR(16) NOT NULL,
      email VARCHAR(255) NULL,
      organization_id INT NOT NULL,
      group_id INT NULL,
      invited_by_user_id INT NOT NULL,
      accepted_by_user_id INT NULL,
      status VARCHAR(32) NOT NULL DEFAULT 'pending',
      expires_at TIMESTAMP NOT NULL,
      accepted_at TIMESTAMP NULL,
      created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
      INDEX idx_user_invitations_org (organization_id),
      INDEX idx_user_invitations_group (group_id),
      INDEX idx_user_invitations_status (status),
      INDEX idx_user_invitations_email (email),
      CONSTRAINT fk_user_invitations_org
        FOREIGN KEY (organization_id) REFERENCES organizations(organization_id)
        ON DELETE CASCADE,
      CONSTRAINT fk_user_invitations_group
        FOREIGN KEY (group_id) REFERENCES user_groups(group_id)
        ON DELETE CASCADE,
      CONSTRAINT fk_user_invitations_invited_by
        FOREIGN KEY (invited_by_user_id) REFERENCES users(id)
        ON DELETE CASCADE,
      CONSTRAINT fk_user_invitations_accepted_by
        FOREIGN KEY (accepted_by_user_id) REFERENCES users(id)
        ON DELETE SET NULL
    )
    """,
]


def _organization_from_row(
    row: dict[str, Any],
    admins: tuple[AdminRecord, ...],
) -> OrganizationRecord:
    return OrganizationRecord(
        organization_id=int(row["organization_id"]),
        organization_name=str(row["organization_name"]),
        organization_description=_optional_text(row.get("organization_description")),
        admins=admins,
        current_user_role=_optional_text(row.get("current_user_role")),
        member_count=int(row["member_count"]) if row.get("member_count") is not None else None,
        created_at=_coerce_datetime(row["created_at"]) if row.get("created_at") else None,
        updated_at=_coerce_datetime(row["updated_at"]) if row.get("updated_at") else None,
    )


def _group_from_row(
    row: dict[str, Any],
    admins: tuple[AdminRecord, ...],
) -> GroupRecord:
    return GroupRecord(
        group_id=int(row["group_id"]),
        organization_id=int(row["organization_id"]),
        group_name=str(row["group_name"]),
        group_description=_optional_text(row.get("group_description")),
        admins=admins,
        current_user_role=_optional_text(row.get("current_user_role")),
        member_count=int(row["member_count"]) if row.get("member_count") is not None else None,
        created_at=_coerce_datetime(row["created_at"]) if row.get("created_at") else None,
        updated_at=_coerce_datetime(row["updated_at"]) if row.get("updated_at") else None,
    )


def _admin_from_row(row: dict[str, Any]) -> AdminRecord:
    return AdminRecord(
        user_id=int(row["user_id"]),
        username=str(row["username"]),
        email=_optional_text(row.get("email")),
    )


def _member_from_row(row: dict[str, Any]) -> MemberRecord:
    return MemberRecord(
        user_id=int(row["user_id"]),
        username=str(row["username"]),
        email=_optional_text(row.get("email")),
        role=str(row.get("role") or ROLE_MEMBER),
        joined_at=_coerce_datetime(row["joined_at"]) if row.get("joined_at") else None,
    )


def _invitation_record_from_row(row: dict[str, Any] | None) -> InvitationRecord:
    if row is None:
        raise InvalidInvitation("invitation not found")
    return InvitationRecord(
        id=int(row["id"]),
        token_prefix=str(row["token_prefix"]),
        email=_optional_text(row.get("email")),
        organization_id=int(row["organization_id"]),
        organization_name=_optional_text(row.get("organization_name")),
        group_id=int(row["group_id"]) if row.get("group_id") is not None else None,
        group_name=_optional_text(row.get("group_name")),
        invited_by_user_id=int(row["invited_by_user_id"]),
        status=str(row.get("status") or INVITATION_PENDING),
        expires_at=_coerce_datetime(row["expires_at"]),
        accepted_at=_coerce_datetime(row["accepted_at"]) if row.get("accepted_at") else None,
        created_at=_coerce_datetime(row["created_at"]) if row.get("created_at") else None,
    )


def _profile_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if not value:
        return {}
    try:
        parsed = json.loads(str(value))
    except Exception:
        return {}
    return dict(parsed) if isinstance(parsed, dict) else {}


def _normalize_name(value: str, label: str) -> str:
    normalized = str(value or "").strip()
    if len(normalized) < 2:
        raise ValueError(f"{label} must be at least 2 characters")
    if len(normalized) > 255:
        raise ValueError(f"{label} must be at most 255 characters")
    return normalized


def _normalize_username(value: str) -> str:
    return _normalize_name(value, "username")


def _normalize_email(value: str) -> str:
    text = _normalize_email_or_none(value)
    if text is None:
        raise ValueError("email is required")
    return text


def _display_name(value: str | None, fallback: str) -> str:
    return _optional_text(value) or fallback


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _normalize_email_or_none(value: str | None) -> str | None:
    text = _optional_text(value)
    if text is None:
        return None
    email = text.lower()
    if "@" not in email or email.startswith("@") or email.endswith("@"):
        raise ValueError("email must be a valid email address")
    if len(email) > 255:
        raise ValueError("email must be at most 255 characters")
    return email


def _normalize_role(value: str) -> str:
    role = str(value or "").strip().lower()
    return ROLE_ADMIN if role == ROLE_ADMIN else ROLE_MEMBER


def _hash_token(token: str) -> str:
    return hashlib.sha256(str(token or "").encode("utf-8")).hexdigest()


def _new_token() -> str:
    return f"agiv_{secrets.token_urlsafe(32)}"


def _int_env(name: str, default: int) -> int:
    try:
        value = int(os.environ.get(name, "").strip() or default)
    except ValueError:
        return default
    return max(1, value)


def _datetime_from_row(row: dict[str, Any] | None, key: str) -> datetime:
    if not row or row.get(key) is None:
        return datetime.now(timezone.utc)
    return _coerce_datetime(row[key])


def _coerce_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    return datetime.fromisoformat(str(value)).replace(tzinfo=timezone.utc)

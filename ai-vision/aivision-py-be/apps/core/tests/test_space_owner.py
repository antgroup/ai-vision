from importlib import import_module
from types import SimpleNamespace
from django.apps import apps
from django.db import connection
from django.test import TestCase
from rest_framework.test import APIClient

from apps.core.models import Space, SpaceMember, SpaceInvitation, User
from apps.core.permissions.space import SpaceAuthorizationError
from apps.core.services.invitation_service import (
    create_invitations,
    accept_invitation,
    resend_invitation,
    revoke_invitation,
    InvitationServiceError,
)
from apps.core.tests.helpers import create_local_user


class SpaceOwnerTests(TestCase):
    def setUp(self):
        self.owner = create_local_user("owner")
        self.admin = create_local_user("admin")
        self.member = create_local_user("member")
        self.space = Space.objects.create(
            name="Team", type=2, creator=self.owner, owner=self.owner
        )
        for user, role in [(self.owner, 1), (self.admin, 1), (self.member, 3)]:
            SpaceMember.objects.create(space=self.space, user=user, role=role)
        self.client = APIClient()
        self.base = f"/api/v1/spaces/{self.space.uuid}"

    def role(self, actor, target, role):
        self.client.force_authenticate(actor)
        return self.client.patch(
            f"{self.base}/members/{target.uuid}", {"role": role}, format="json"
        )

    def transfer(self, actor, target):
        self.client.force_authenticate(actor)
        return self.client.post(
            f"{self.base}/owner/transfer",
            {"target_user_uuid": target.uuid},
            format="json",
        )

    def invite(self, actor, role=1, email="new@example.test"):
        return create_invitations(
            space=self.space, invited_by=actor, emails=[email], role=role
        )[0]

    def test_only_owner_can_grant_admin(self):
        self.assertEqual(self.role(self.admin, self.member, 1).status_code, 403)
        self.assertEqual(self.role(self.owner, self.member, 1).status_code, 200)

    def test_admin_cannot_change_or_remove_peer(self):
        peer = create_local_user("peer")
        SpaceMember.objects.create(space=self.space, user=peer, role=1)
        self.assertEqual(self.role(self.admin, peer, 3).status_code, 403)
        self.assertEqual(
            self.client.delete(f"{self.base}/members/{peer.uuid}").status_code, 403
        )
        self.assertEqual(SpaceMember.objects.get(space=self.space, user=peer).role, 1)

    def test_owner_can_demote_and_remove_other_admin(self):
        self.assertEqual(self.role(self.owner, self.admin, 2).status_code, 200)
        self.assertEqual(
            self.client.delete(f"{self.base}/members/{self.admin.uuid}").status_code,
            200,
        )

    def test_admin_can_manage_lower_roles(self):
        self.assertEqual(self.role(self.admin, self.member, 2).status_code, 200)
        self.assertEqual(self.role(self.admin, self.member, 3).status_code, 200)
        self.assertEqual(
            self.client.delete(f"{self.base}/members/{self.member.uuid}").status_code,
            200,
        )

    def test_owner_cannot_be_changed_removed_or_leave(self):
        for actor in [self.owner, self.admin]:
            self.assertEqual(self.role(actor, self.owner, 3).status_code, 403)
            self.assertEqual(
                self.client.delete(
                    f"{self.base}/members/{self.owner.uuid}"
                ).status_code,
                403,
            )
        self.client.force_authenticate(self.owner)
        from apps.core.views.space import SpaceMemberExitView
        from rest_framework.test import APIRequestFactory, force_authenticate

        req = APIRequestFactory().post("/exit")
        force_authenticate(req, self.owner)
        response = SpaceMemberExitView.as_view()(req, space_id=self.space.uuid)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(
            response.data["data"]["error_key"], "SPACE_OWNER_TRANSFER_REQUIRED"
        )

    def test_transfer_changes_owner_without_changing_creator(self):
        self.assertEqual(self.transfer(self.owner, self.member).status_code, 200)
        self.space.refresh_from_db()
        self.assertEqual(self.space.owner_id, self.member.pk)
        self.assertEqual(self.space.creator_id, self.owner.pk)
        for user in [self.owner, self.member]:
            self.assertEqual(
                SpaceMember.objects.get(space=self.space, user=user).role, 1
            )
        self.assertEqual(self.transfer(self.owner, self.admin).status_code, 403)
        self.assertEqual(self.role(self.owner, self.admin, 3).status_code, 403)

    def test_transfer_rejects_invalid_targets_and_nonowners(self):
        self.assertEqual(self.transfer(self.admin, self.member).status_code, 403)
        self.assertEqual(self.transfer(self.owner, self.owner).status_code, 422)
        outsider = create_local_user("outsider")
        self.assertEqual(self.transfer(self.owner, outsider).status_code, 404)
        self.member.status = 0
        self.member.save()
        self.assertEqual(self.transfer(self.owner, self.member).status_code, 404)

    def test_owner_flags_and_new_space(self):
        self.client.force_authenticate(self.owner)
        data = self.client.get(self.base).data["data"]
        self.assertTrue(data["is_owner"])
        self.assertEqual(data["owner_uuid"], self.owner.uuid)
        rows = self.client.get(self.base + "/members").data["data"]["items"]
        self.assertEqual(sum(row["is_owner"] for row in rows), 1)
        response = self.client.post("/api/v1/spaces", {"name": "New"}, format="json")
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["data"]["owner_uuid"], self.owner.uuid)

    def test_missing_owner_fails_closed(self):
        self.space.owner = None
        self.space.save()
        response = self.role(self.admin, self.member, 2)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.data["data"]["error_key"], "SPACE_OWNER_UNRESOLVED")

    def test_invitation_roles(self):
        with self.assertRaises(SpaceAuthorizationError):
            self.invite(self.admin)
        self.invite(self.owner)
        self.invite(self.admin, 2, "dev@example.test")
        self.invite(self.admin, 3, "reader@example.test")

    def test_invitation_cannot_overwrite_protected_member(self):
        with self.assertRaises(SpaceAuthorizationError):
            self.invite(self.admin, 3, self.owner.normalized_email)
        self.assertEqual(SpaceInvitation.objects.count(), 0)

    def test_batch_invitation_rolls_back(self):
        with self.assertRaises(SpaceAuthorizationError):
            create_invitations(
                space=self.space,
                invited_by=self.admin,
                emails=["first@example.test", self.owner.normalized_email],
                role=3,
            )
        self.assertFalse(
            User.objects.filter(normalized_email="first@example.test").exists()
        )
        self.assertEqual(SpaceInvitation.objects.count(), 0)

    def test_admin_invitation_actions_are_owner_only(self):
        invitation, _ = self.invite(self.owner)
        with self.assertRaises(SpaceAuthorizationError):
            resend_invitation(invitation, self.admin)
        with self.assertRaises(SpaceAuthorizationError):
            revoke_invitation(invitation, self.admin)
        resend_invitation(invitation, self.owner)
        revoke_invitation(invitation, self.owner)

    def test_old_owner_invitation_loses_authority_after_transfer(self):
        invitation, token = self.invite(self.owner)
        self.transfer(self.owner, self.admin)
        with self.assertRaises(InvitationServiceError) as error:
            accept_invitation(
                raw_token=token, name="New", password="Correct-Horse-123!"
            )
        self.assertEqual(error.exception.error_key, "INVITATION_AUTHORITY_LOST")
        with self.assertRaises(InvitationServiceError):
            resend_invitation(invitation, self.admin)
        revoke_invitation(invitation, self.admin)

    def test_demoted_inviter_cannot_authorize_acceptance(self):
        _, token = self.invite(self.admin, 2)
        self.role(self.owner, self.admin, 3)
        with self.assertRaises(InvitationServiceError) as error:
            accept_invitation(raw_token=token)
        self.assertEqual(error.exception.error_key, "INVITATION_AUTHORITY_LOST")

    def test_existing_membership_not_overwritten_by_old_invite(self):
        target = create_local_user("invited")
        _, token = self.invite(self.owner, 3, target.normalized_email)
        SpaceMember.objects.create(space=self.space, user=target, role=1)
        with self.assertRaises(InvitationServiceError) as error:
            accept_invitation(raw_token=token, authenticated_user=target)
        self.assertEqual(error.exception.error_key, "ALREADY_MEMBER")
        self.assertEqual(SpaceMember.objects.get(space=self.space, user=target).role, 1)

    def test_migration_only_backfills_valid_creator_admin(self):
        self.space.owner = None
        self.space.save()
        other = Space.objects.create(name="Unresolved", type=2, creator=self.member)
        SpaceMember.objects.create(space=other, user=self.member, role=3)
        backfill = import_module(
            "apps.core.migrations.0026_space_owner"
        ).backfill_owners
        backfill(apps, SimpleNamespace(connection=connection))
        backfill(apps, SimpleNamespace(connection=connection))
        self.space.refresh_from_db()
        other.refresh_from_db()
        self.assertEqual(self.space.owner_id, self.owner.pk)
        self.assertIsNone(other.owner_id)

    def test_explicit_owner_assignment_requires_existing_admin(self):
        from django.core.management import call_command, CommandError
        from io import StringIO

        self.space.owner = None
        self.space.save()
        out = StringIO()
        call_command("assign_space_owner", stdout=out)
        self.assertIn(self.space.uuid, out.getvalue())
        with self.assertRaises(CommandError):
            call_command(
                "assign_space_owner",
                space=self.space.uuid,
                user=self.member.uuid,
                stdout=out,
            )
        call_command(
            "assign_space_owner",
            space=self.space.uuid,
            user=self.admin.uuid,
            stdout=out,
        )
        call_command(
            "assign_space_owner",
            space=self.space.uuid,
            user=self.admin.uuid,
            stdout=out,
        )
        self.space.refresh_from_db()
        self.assertEqual(self.space.owner_id, self.admin.pk)
        with self.assertRaises(CommandError):
            call_command(
                "assign_space_owner",
                space=self.space.uuid,
                user=self.owner.uuid,
                stdout=out,
            )

    def test_lower_role_invite_cannot_remove_or_demote_promoted_pending_admin(self):
        invitation, token = self.invite(self.admin, 3)
        pending = User.objects.get(normalized_email=invitation.normalized_email)
        SpaceMember.objects.filter(space=self.space, user=pending).update(role=1)
        with self.assertRaises(SpaceAuthorizationError):
            revoke_invitation(invitation, self.admin)
        with self.assertRaises(InvitationServiceError):
            resend_invitation(invitation, self.admin)
        with self.assertRaises(InvitationServiceError):
            accept_invitation(
                raw_token=token, name="Pending", password="Correct-Horse-123!"
            )
        invitation.refresh_from_db()
        self.assertEqual(invitation.status, SpaceInvitation.STATUS_PENDING)
        self.assertEqual(
            SpaceMember.objects.get(space=self.space, user=pending).role, 1
        )

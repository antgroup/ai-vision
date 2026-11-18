from urllib.parse import parse_qs, urlparse

from django.core.cache import cache
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.core.models import Space, SpaceInvitation, SpaceMember, User
from apps.core.tests.helpers import create_test_space_member


@override_settings(
    SITE_URL="https://aivision.example.test",
)
class SpaceInvitationAPITests(TestCase):
    def setUp(self):
        cache.clear()
        self.admin_client = APIClient(enforce_csrf_checks=True)
        self.admin_client.get("/api/v1/auth/csrf")
        response = self.admin_client.post(
            "/api/v1/auth/register",
            {
                "name": "Admin",
                "email": "admin@example.com",
                "password": "Correct-Horse-123!",
                "password_confirm": "Correct-Horse-123!",
            },
            format="json",
            HTTP_X_CSRFTOKEN=self.admin_client.cookies["csrftoken"].value,
        )
        self.assertEqual(response.status_code, 201, response.data)
        self.admin = User.objects.get(normalized_email="admin@example.com")
        self.space = Space.objects.create(
            name="Business",
            type=2,
            scope=1,
            creator=self.admin,
            owner=self.admin,
            status=1,
        )
        create_test_space_member(space=self.space, user=self.admin, role=1)

    def create_invitation(self):
        response = self.admin_client.post(
            f"/api/v1/spaces/{self.space.uuid}/invitations",
            {"emails": [" New.User@Example.com "], "role": 2},
            format="json",
            HTTP_X_CSRFTOKEN=self.admin_client.cookies["csrftoken"].value,
        )
        self.assertEqual(response.status_code, 201, response.data)
        url = response.data["data"]["items"][0]["invite_url"]
        return parse_qs(urlparse(url).query)["token"][0]

    def test_new_account_accept_is_atomic_and_token_is_one_time(self):
        raw_token = self.create_invitation()
        invitation = SpaceInvitation.objects.get()
        self.assertNotEqual(invitation.token_hash, raw_token)
        pending_user = User.objects.get(normalized_email="new.user@example.com")
        self.assertEqual(pending_user.status, User.STATUS_PENDING)
        self.assertFalse(pending_user.has_usable_password())
        self.assertFalse(pending_user.product_guide_pending)
        self.assertTrue(
            SpaceMember.objects.filter(
                space=self.space, user=pending_user, role=2
            ).exists()
        )

        members = self.admin_client.get(f"/api/v1/spaces/{self.space.uuid}/members")
        pending_member = next(
            item
            for item in members.data["data"]["items"]
            if item["email"] == "new.user@example.com"
        )
        self.assertEqual(pending_member["account_status"], "pending")

        client = APIClient(enforce_csrf_checks=True)
        client.get("/api/v1/auth/csrf")
        checked = client.post(
            "/api/v1/invitations/check",
            {"token": raw_token},
            format="json",
            HTTP_X_CSRFTOKEN=client.cookies["csrftoken"].value,
        )
        self.assertEqual(checked.status_code, 200, checked.data)
        self.assertFalse(checked.data["data"]["account_exists"])

        accepted = client.post(
            "/api/v1/invitations/accept",
            {
                "token": raw_token,
                "name": "New User",
                "password": "Correct-Horse-456!",
                "password_confirm": "Correct-Horse-456!",
            },
            format="json",
            HTTP_X_CSRFTOKEN=client.cookies["csrftoken"].value,
            HTTP_ACCEPT_LANGUAGE="ja-JP",
        )
        self.assertEqual(accepted.status_code, 200, accepted.data)
        user = User.objects.get(normalized_email="new.user@example.com")
        self.assertEqual(user.status, User.STATUS_NORMAL)
        self.assertTrue(user.has_usable_password())
        self.assertTrue(
            SpaceMember.objects.filter(space=self.space, user=user, role=2).exists()
        )
        self.assertEqual(
            Space.objects.get(creator=user, type=1).name, "New Userの個人スペース"
        )
        self.assertIn("aivision_access", accepted.cookies)
        self.assertIs(
            client.get("/api/v1/user/me").data["data"]["product_guide_pending"], True
        )
        invitation.refresh_from_db()
        self.assertEqual(invitation.status, SpaceInvitation.STATUS_ACCEPTED)
        replay = client.post(
            "/api/v1/invitations/accept",
            {"token": raw_token},
            format="json",
            HTTP_X_CSRFTOKEN=client.cookies["csrftoken"].value,
        )
        self.assertEqual(replay.status_code, 400)

    def test_revoke_removes_pending_membership_and_orphan_account(self):
        self.create_invitation()
        invitation = SpaceInvitation.objects.get()
        pending_user = User.objects.get(normalized_email="new.user@example.com")

        response = self.admin_client.post(
            f"/api/v1/spaces/{self.space.uuid}/invitations/"
            f"{invitation.invitation_uuid}/revoke",
            {},
            format="json",
            HTTP_X_CSRFTOKEN=self.admin_client.cookies["csrftoken"].value,
        )

        self.assertEqual(response.status_code, 200, response.data)
        invitation.refresh_from_db()
        self.assertEqual(invitation.status, SpaceInvitation.STATUS_REVOKED)
        self.assertFalse(
            SpaceMember.objects.filter(space=self.space, user=pending_user).exists()
        )
        self.assertFalse(User.objects.filter(pk=pending_user.pk).exists())

    def test_existing_account_joins_only_after_authenticated_accept(self):
        existing = User.objects.create(
            name="Existing",
            email="existing@example.com",
            normalized_email="existing@example.com",
            status=User.STATUS_NORMAL,
        )
        existing.set_password("Correct-Horse-789!")
        existing.save(update_fields=["password"])
        personal = Space.objects.create(
            name="My existing Space", creator=existing, type=1, scope=1, status=1
        )
        response = self.admin_client.post(
            f"/api/v1/spaces/{self.space.uuid}/invitations",
            {"emails": [existing.email], "role": 3},
            format="json",
            HTTP_X_CSRFTOKEN=self.admin_client.cookies["csrftoken"].value,
        )
        raw_token = parse_qs(
            urlparse(response.data["data"]["items"][0]["invite_url"]).query
        )["token"][0]
        self.assertFalse(
            SpaceMember.objects.filter(space=self.space, user=existing).exists()
        )

        client = APIClient(enforce_csrf_checks=True)
        client.get("/api/v1/auth/csrf")
        login = client.post(
            "/api/v1/auth/login",
            {"email": existing.email, "password": "Correct-Horse-789!"},
            format="json",
            HTTP_X_CSRFTOKEN=client.cookies["csrftoken"].value,
        )
        self.assertEqual(login.status_code, 200, login.data)
        self.assertIs(
            client.get("/api/v1/user/me").data["data"]["product_guide_pending"], False
        )
        accepted = client.post(
            "/api/v1/invitations/accept",
            {"token": raw_token},
            format="json",
            HTTP_X_CSRFTOKEN=client.cookies["csrftoken"].value,
            HTTP_ACCEPT_LANGUAGE="zh-CN",
        )
        self.assertEqual(accepted.status_code, 200, accepted.data)
        personal.refresh_from_db()
        self.assertEqual(personal.name, "My existing Space")
        self.assertIs(
            client.get("/api/v1/user/me").data["data"]["product_guide_pending"], False
        )
        self.assertTrue(
            SpaceMember.objects.filter(
                space=self.space, user=existing, role=3, gmt_deleted__isnull=True
            ).exists()
        )

    def test_personal_space_cannot_create_invitation(self):
        personal = Space.objects.get(creator=self.admin, type=1)
        response = self.admin_client.post(
            f"/api/v1/spaces/{personal.uuid}/invitations",
            {"emails": ["user@example.com"], "role": 3},
            format="json",
            HTTP_X_CSRFTOKEN=self.admin_client.cookies["csrftoken"].value,
        )
        self.assertEqual(response.status_code, 403)

    def test_non_admin_cannot_manage_invitations(self):
        developer = User.objects.create(
            name="Dev",
            email="dev@example.com",
            normalized_email="dev@example.com",
            status=1,
        )
        developer.set_password("Correct-Horse-789!")
        developer.save(update_fields=["password"])
        create_test_space_member(space=self.space, user=developer, role=2)
        client = APIClient(enforce_csrf_checks=True)
        client.get("/api/v1/auth/csrf")
        login = client.post(
            "/api/v1/auth/login",
            {"email": "dev@example.com", "password": "Correct-Horse-789!"},
            format="json",
            HTTP_X_CSRFTOKEN=client.cookies["csrftoken"].value,
        )
        self.assertEqual(login.status_code, 200, login.data)
        denied = client.get(f"/api/v1/spaces/{self.space.uuid}/invitations")
        self.assertEqual(denied.status_code, 403)

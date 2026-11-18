from django.test import TestCase
from rest_framework.test import APIClient

from apps.core.tests.helpers import create_test_space_member

from apps.core.models import ObservationSubjectBinding, Space, SpaceMember, User


class SpacePermissionAPITests(TestCase):
    def setUp(self):
        self.admin = self._user("admin@example.com", "Admin")
        self.member = self._user("member@example.com", "Member")
        self.outsider = self._user("outsider@example.com", "Outsider")
        self.space = Space.objects.create(
            name="Business A",
            type=2,
            scope=1,
            creator=self.admin,
            owner=self.admin,
            status=1,
        )
        create_test_space_member(space=self.space, user=self.admin, role=1)
        create_test_space_member(space=self.space, user=self.member, role=3)
        self.client = APIClient()

    @staticmethod
    def _user(email, name):
        return User.objects.create(
            name=name,
            email=email,
            normalized_email=email,
            status=1,
        )

    def authenticate(self, user):
        self.client.force_authenticate(user=user)

    def test_list_and_detail_return_real_role_without_work_id(self):
        self.authenticate(self.member)
        listed = self.client.get("/api/v1/spaces")
        self.assertEqual(listed.status_code, 200, listed.data)
        item = listed.data["data"]["items"][0]
        self.assertEqual(item["role"], 3)
        self.assertNotIn("work_id", str(item))

        detail = self.client.get(f"/api/v1/spaces/{self.space.uuid}")
        self.assertEqual(detail.status_code, 200, detail.data)
        self.assertEqual(detail.data["data"]["role"], 3)

    def test_non_member_gets_non_disclosing_not_found(self):
        self.authenticate(self.outsider)
        response = self.client.get(f"/api/v1/spaces/{self.space.uuid}")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.data["data"]["error_key"], "SPACE_NOT_FOUND")
        self.assertNotIn(self.space.name, str(response.data))

    def test_create_always_creates_business_space_and_subject_binding(self):
        self.authenticate(self.member)
        response = self.client.post(
            "/api/v1/spaces", {"name": "Business B"}, format="json"
        )
        self.assertEqual(response.status_code, 201, response.data)
        created = Space.objects.get(uuid=response.data["data"]["space_id"])
        self.assertEqual(created.type, 2)
        self.assertTrue(
            SpaceMember.objects.filter(
                space=created, user=self.member, role=1, gmt_deleted__isnull=True
            ).exists()
        )
        self.assertTrue(
            ObservationSubjectBinding.objects.filter(
                space=created,
                user=self.member,
                subject_id=self.member.uuid,
                source_system="collector",
            ).exists()
        )
        rejected = self.client.post(
            "/api/v1/spaces", {"name": "Personal", "type": 1}, format="json"
        )
        self.assertEqual(rejected.status_code, 422)

    def test_member_cannot_manage_members_and_admin_uses_user_uuid(self):
        self.authenticate(self.member)
        denied = self.client.patch(
            f"/api/v1/spaces/{self.space.uuid}/members/{self.member.uuid}",
            {"role": 2},
            format="json",
        )
        self.assertEqual(denied.status_code, 403)

        self.authenticate(self.admin)
        updated = self.client.patch(
            f"/api/v1/spaces/{self.space.uuid}/members/{self.member.uuid}",
            {"role": 2},
            format="json",
        )
        self.assertEqual(updated.status_code, 200, updated.data)
        self.assertEqual(updated.data["data"]["user_uuid"], self.member.uuid)
        self.assertNotIn("work_id", str(updated.data))

    def test_self_role_change_and_cross_space_target_are_protected(self):
        self.authenticate(self.admin)
        second_admin = self._user("admin2@example.com", "Admin 2")
        create_test_space_member(space=self.space, user=second_admin, role=1)
        self_role_change = self.client.patch(
            f"/api/v1/spaces/{self.space.uuid}/members/{self.admin.uuid}",
            {"role": 3},
            format="json",
        )
        self.assertEqual(self_role_change.status_code, 403)
        self.assertEqual(
            self_role_change.data["data"]["error_key"],
            "SPACE_OWNER_PROTECTED",
        )
        self.assertEqual(
            SpaceMember.objects.get(space=self.space, user=self.admin).role, 1
        )

        other_space = Space.objects.create(
            name="Business B", type=2, scope=1, creator=self.outsider, status=1
        )
        create_test_space_member(space=other_space, user=self.outsider, role=1)
        missing = self.client.delete(
            f"/api/v1/spaces/{self.space.uuid}/members/{self.outsider.uuid}"
        )
        self.assertEqual(missing.status_code, 404)
        self.assertEqual(missing.data["data"]["error_key"], "RESOURCE_NOT_IN_SPACE")

"""SpaceKey changes must take effect on the next authenticated request."""

from unittest.mock import patch

from django.utils import timezone
from rest_framework.test import APIClient

from apps.core.models import Space
from apps.core.services.key_cache import _KeyCache
from apps.core.tests.helpers import (
    SpaceKeyTestCase,
    create_local_user,
    create_test_space_member,
)


class SpaceKeyLifecycleTests(SpaceKeyTestCase):
    def setUp(self):
        super().setUp()
        self.owner = create_local_user("key-lifecycle-owner")
        self.space = Space.objects.create(
            name="Key lifecycle", type=2, scope=1, creator=self.owner, status=1
        )
        create_test_space_member(space=self.space, user=self.owner, role=1)
        self.management = APIClient()
        self.management.force_authenticate(user=self.owner)
        self.query = APIClient()
        self.keys_url = f"/api/v1/spaces/{self.space.uuid}/space-keys"
        self.agents_url = f"/openapi/v1/spaces/{self.space.uuid}/agents"
        # Independent warm caches represent requests handled by separate workers.
        self.workers = [_KeyCache(), _KeyCache()]

    def create_key(self):
        response = self.management.post(
            self.keys_url, {"name": "CLI query"}, format="json"
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["code"], 0)
        return response.json()["data"]

    def assert_auth_status(self, raw_key, expected):
        for worker in self.workers:
            with self.subTest(worker=worker), patch(
                "apps.core.services.key_cache.key_cache", worker
            ):
                response = self.query.get(
                    self.agents_url, HTTP_AUTHORIZATION=f"Bearer {raw_key}"
                )
                self.assertEqual(response.status_code, expected, response.content)

    def test_new_key_works_after_a_cached_miss(self):
        self.assert_auth_status("sk-aiv-not-a-real-key", 401)
        key = self.create_key()
        self.assert_auth_status(key["key"], 200)

    def test_revocation_rejects_previously_accepted_key_in_all_workers(self):
        key = self.create_key()
        self.assert_auth_status(key["key"], 200)
        response = self.management.delete(f"{self.keys_url}/{key['id']}")
        self.assertEqual(response.json()["code"], 0)
        self.assert_auth_status(key["key"], 401)

    def test_disabled_and_reenabled_key_takes_effect_in_all_workers(self):
        key = self.create_key()
        self.assert_auth_status(key["key"], 200)
        for enabled, expected in ((False, 401), (True, 200)):
            response = self.management.put(
                f"{self.keys_url}/{key['id']}", {"enabled": enabled}, format="json"
            )
            self.assertEqual(response.json()["code"], 0)
            self.assert_auth_status(key["key"], expected)

    def test_space_state_changes_reject_previously_accepted_key(self):
        key = self.create_key()
        self.assert_auth_status(key["key"], 200)
        Space.objects.filter(pk=self.space.pk).update(status=0)
        self.assert_auth_status(key["key"], 401)
        Space.objects.filter(pk=self.space.pk).update(status=1)
        self.assert_auth_status(key["key"], 200)
        Space.objects.filter(pk=self.space.pk).update(gmt_deleted=timezone.now())
        self.assert_auth_status(key["key"], 401)

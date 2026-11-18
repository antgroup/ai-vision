from concurrent.futures import ThreadPoolExecutor, TimeoutError
from secrets import token_hex
from threading import Event
from unittest.mock import patch

from django.db import connections
from django.test import TransactionTestCase, override_settings, skipUnlessDBFeature
from rest_framework.test import APIClient

from apps.core.models import AgentTypeConfig, Space, SpaceKey
from apps.core.permissions.space import require_space_permission
from apps.core.services.space_key_policy import MAX_KEYS_PER_SPACE
from apps.core.tests.helpers import (
    create_local_user,
    create_test_agent,
    create_test_space_member,
)


class SpaceKeyQuotaTests(TransactionTestCase):
    @skipUnlessDBFeature("has_select_for_update")
    @override_settings(CREDENTIAL_ENCRYPTION_KEY=token_hex(32))
    def test_regular_create_and_project_reveal_share_last_slot(self):
        user = create_local_user("space-key-quota")
        space = Space.objects.create(name="Key quota", type=2, creator=user)
        create_test_space_member(space=space, user=user, role=1)
        config = AgentTypeConfig.objects.create(
            agent_name="Langfuse quota",
            agent_product="langfuse",
            category="CUSTOM",
            setup_mode="LANGFUSE",
            status=1,
        )
        agent = create_test_agent(
            space=space,
            agent_type=config,
            agent_product=config.agent_product,
            name="Quota project",
            status=2,
        )
        # Disabled keys still count toward the quota.
        for _ in range(MAX_KEYS_PER_SPACE - 1):
            _, prefix, key_hash = SpaceKey.generate_key()
            SpaceKey.objects.create(
                space=space, key_prefix=prefix, key_hash=key_hash, enabled=False
            )

        ordinary_ready = Event()
        resume_ordinary = Event()
        project_authorized = Event()
        save_key = SpaceKey.save

        def before_save(key, *args, **kwargs):
            if key.onboarding_agent_id is None:
                ordinary_ready.set()
                if not resume_ordinary.wait(10):
                    raise AssertionError("Ordinary creation was not released")
            return save_key(key, *args, **kwargs)

        def authorize_project(**kwargs):
            context = require_space_permission(**kwargs)
            project_authorized.set()
            return context

        base = f"/api/v1/spaces/{space.uuid}"

        def post(path, payload):
            # Each request runs in its own database connection and transaction.
            try:
                client = APIClient()
                client.force_authenticate(user=user)
                response = client.post(path, payload, format="json")
                return response.status_code, response.data["code"]
            finally:
                connections.close_all()

        with (
            patch.object(SpaceKey, "save", autospec=True, side_effect=before_save),
            patch(
                "apps.core.views.agent.require_space_permission",
                side_effect=authorize_project,
            ),
            ThreadPoolExecutor(max_workers=2) as workers,
        ):
            ordinary = workers.submit(
                post, f"{base}/space-keys", {"name": "Ordinary key"}
            )
            try:
                self.assertTrue(ordinary_ready.wait(5))
                project = workers.submit(
                    post, f"{base}/agents/{agent.uuid}/onboarding-credential", {}
                )
                self.assertTrue(project_authorized.wait(5))
                # Let the second worker reach its quota check before the first
                # inserts. With a shared Space lock it waits for that insert.
                try:
                    project.result(timeout=1)
                except TimeoutError:
                    pass
            finally:
                resume_ordinary.set()
            self.assertEqual(ordinary.result(timeout=5), (200, 0))
            self.assertEqual(project.result(timeout=5), (409, 3033))

        self.assertEqual(
            SpaceKey.objects.filter(space=space).count(), MAX_KEYS_PER_SPACE
        )

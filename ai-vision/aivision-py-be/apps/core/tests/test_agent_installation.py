from datetime import timedelta

from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from apps.core.models import (
    AgentInstallation,
    AgentTypeConfig,
    Space,
    SpaceKey,
    SpaceMember,
)
from apps.core.services.ingest_token import verify_ingest_token
from apps.core.tests.helpers import (
    SpaceKeyTestCase,
    create_local_user,
    create_test_agent,
    create_test_space_member,
)


@override_settings(
    AIVISION_INGEST_SECRET="test-browser-installation-signing-secret",
    AIVISION_OTLP_INGEST_ENDPOINT="http://gateway.test/v1/traces",
)
class BrowserAgentInstallationTests(SpaceKeyTestCase):
    def setUp(self):
        super().setUp()
        self.user = create_local_user("browser-install-user")
        self.space = Space.objects.create(
            name="Installation", type=2, creator=self.user
        )
        self.member = create_test_space_member(space=self.space, user=self.user, role=2)
        self.kind = AgentTypeConfig.objects.create(
            agent_name="CODEX",
            agent_product="Codex",
            setup_mode="CLI",
            category="CUSTOM",
            config_guide={"schema": "v2", "plugin_key": "codex", "sections": []},
        )
        self.agent = create_test_agent(
            space=self.space, agent_type=self.kind, agent_product="Codex", status=2
        )
        self.browser = APIClient()
        self.browser.force_authenticate(user=self.user)
        self.path = f"/api/v1/spaces/{self.space.uuid}/agents/{self.agent.uuid}"

    def issue(self):
        response = self.browser.post(
            self.path + "/installation-tokens", {}, format="json"
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response["Cache-Control"], "no-store")
        return response.data["data"]["installation_token"]

    def exchange(self, ticket, **overrides):
        return APIClient().post(
            "/openapi/v1/agent-installations/exchange",
            {"space_id": self.space.uuid, "agent_uuid": self.agent.uuid, **overrides},
            format="json",
            HTTP_AUTHORIZATION=f"Bearer {ticket}",
        )

    def machine(self):
        ticket = self.issue()
        response = self.exchange(ticket)
        self.assertEqual(response.status_code, 200, response.data)
        data = response.data["data"]
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {data['agent_credential']}")
        return client, ticket, data

    def mint(self, client, agent_uuid=None):
        return client.post(
            "/openapi/v1/ingest-tokens",
            {
                "agent_uuid": agent_uuid or self.agent.uuid,
            },
            format="json",
        )

    def test_installation_token_response_pins_canonical_base_url(self):
        # The install card fills {baseUrl} from this field instead of guessing
        # window.location.origin, so the command's --base-url equals the origin
        # catalog.json builds artifact URLs from and the installer's same-origin
        # download check passes regardless of which port serves the UI.
        with self.settings(AIVISION_PUBLIC_BASE_URL="http://127.0.0.1:8001/"):
            pinned = self.browser.post(
                self.path + "/installation-tokens", {}, format="json"
            ).data["data"]
        self.assertEqual(pinned["base_url"], "http://127.0.0.1:8001")
        # Unset (single-origin): fall back to the request origin, still matching
        # what catalog.json resolves for the same request.
        with self.settings(AIVISION_PUBLIC_BASE_URL=""):
            derived = self.browser.post(
                self.path + "/installation-tokens", {}, format="json"
            ).data["data"]
        self.assertEqual(derived["base_url"], "http://testserver")

    def test_personal_and_team_install_without_creating_space_keys(self):
        for kind in (1, 2):
            with self.subTest(space_type=kind):
                self.space.type = kind
                self.space.save()
                self.agent.status = 2
                self.agent.save()
                machine, ticket, data = self.machine()
                self.assertFalse(SpaceKey.objects.exists())
                claims = verify_ingest_token(data["token"])
                self.assertEqual(claims["sp"], self.space.uuid)
                self.assertEqual(claims["aid"], self.agent.uuid)
                self.assertEqual(self.mint(machine).status_code, 200)
                self.agent.refresh_from_db()
                self.assertEqual(self.agent.status, 2)
                grant = AgentInstallation.objects.get(
                    token_hash=AgentInstallation.digest(ticket)
                )
                self.assertEqual(
                    grant.credential_hash,
                    AgentInstallation.digest(data["agent_credential"]),
                )
                self.assertNotIn(ticket, str(grant.__dict__))
                self.assertNotIn(data["agent_credential"], str(grant.__dict__))
                response = machine.post(
                    f"/openapi/v1/agents/{self.agent.uuid}/installation-complete"
                )
                self.assertEqual(response.status_code, 200, response.data)
                self.agent.refresh_from_db()
                self.assertEqual(self.agent.status, 1)

    def test_observer_and_outsider_cannot_issue_installation_commands(self):
        self.member.role = 3
        self.member.save()
        self.assertEqual(
            self.browser.post(self.path + "/installation-tokens").status_code, 403
        )
        outsider = create_local_user("install-outsider")
        self.browser.force_authenticate(user=outsider)
        self.assertEqual(
            self.browser.post(self.path + "/installation-tokens").status_code, 404
        )
        self.assertFalse(AgentInstallation.objects.exists())

    def test_grant_is_one_use_and_cannot_be_redeemed_in_another_space_or_agent(self):
        ticket = self.issue()
        for change in ({"space_id": "other"}, {"agent_uuid": "other"}):
            self.assertEqual(self.exchange(ticket, **change).status_code, 403)
        response = self.exchange(ticket)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(self.exchange(ticket).status_code, 401)

    def test_expired_grant_is_rejected_but_installed_credential_remains_valid(self):
        ticket = self.issue()
        AgentInstallation.objects.update(
            expires_at=timezone.now() - timedelta(seconds=1)
        )
        self.assertEqual(self.exchange(ticket).status_code, 401)
        machine, _, _ = self.machine()
        AgentInstallation.objects.update(expires_at=timezone.now() - timedelta(days=30))
        self.assertEqual(self.mint(machine).status_code, 200)

    def test_new_command_invalidates_unused_command_and_keeps_installed_machine(self):
        machine, _, _ = self.machine()
        unused = self.issue()
        current = self.issue()
        self.assertEqual(self.exchange(unused).status_code, 401)
        self.assertEqual(self.exchange(current).status_code, 200)
        self.assertEqual(self.mint(machine).status_code, 200)

    def test_agent_credential_cannot_query_data_or_authorize_another_agent(self):
        machine, _, _ = self.machine()
        other = create_test_agent(
            space=self.space, agent_type=self.kind, agent_product="Other"
        )
        self.assertEqual(self.mint(machine, other.uuid).status_code, 404)
        self.assertEqual(
            machine.post(
                f"/openapi/v1/agents/{other.uuid}/installation-complete",
                {},
                format="json",
            ).status_code,
            404,
        )
        self.assertEqual(
            machine.get(f"/openapi/v1/spaces/{self.space.uuid}/agents").status_code, 401
        )

    def test_removal_revokes_grants_and_credentials_even_after_readding_agent(self):
        machine, _, _ = self.machine()
        unused = self.issue()
        response = self.browser.post(self.path + "/disable")
        self.assertEqual(response.data["code"], 0, response.data)
        self.assertEqual(self.mint(machine).status_code, 401)
        self.assertEqual(self.exchange(unused).status_code, 401)
        restored = self.browser.post(
            f"/api/v1/spaces/{self.space.uuid}/agents/create",
            {"agent_product": "Codex", "name": "Browser Agent"},
            format="json",
        )
        self.assertEqual(restored.data["code"], 0, restored.data)
        self.assertEqual(self.mint(machine).status_code, 401)
        fresh, _, _ = self.machine()
        self.assertEqual(self.mint(fresh).status_code, 200)

    def test_issuer_permission_changes_prevent_exchange_and_renewal(self):
        machine, _, _ = self.machine()
        unused = self.issue()
        SpaceMember.objects.filter(pk=self.member.pk).update(role=3)
        self.assertEqual(self.exchange(unused).status_code, 401)
        self.assertEqual(self.mint(machine).status_code, 401)

    def test_unconfigured_signing_does_not_consume_grant(self):
        ticket = self.issue()
        with override_settings(AIVISION_INGEST_SECRET=""):
            self.assertEqual(self.exchange(ticket).status_code, 500)
        grant = AgentInstallation.objects.get(
            token_hash=AgentInstallation.digest(ticket)
        )
        self.assertIsNone(grant.exchanged_at)
        self.assertIsNone(grant.credential_hash)
        self.assertEqual(self.exchange(ticket).status_code, 200)

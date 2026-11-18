import base64
import json
from secrets import token_hex, token_urlsafe
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.test import TestCase, override_settings
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import (
    ExportTraceServiceRequest,
)
from rest_framework.test import APIClient

from apps.core.models import (
    Agent,
    AgentTypeConfig,
    DataSource,
    Space,
    SpaceKey,
    SpaceObservationProject,
)
from apps.core.services.agent_routing import resolve_space_agent_for_query
from apps.core.services.credential_service import encrypt_secret
from apps.core.services.ingest_token import verify_ingest_token
from apps.core.trace_query.service import TraceQueryService
from apps.core.tests.helpers import (
    SpaceKeyTestCase,
    create_local_user,
    create_test_agent,
    create_test_space_member,
)


@override_settings(CREDENTIAL_ENCRYPTION_KEY=token_hex(32))
class LangfuseOnboardingCredentialsTests(TestCase):
    def setUp(self):
        super().setUp()
        self.user = create_local_user("langfuse-user", name="Langfuse Tester")
        self.space = Space.objects.create(
            name="Langfuse Demo",
            type=2,
            scope=1,
            creator=self.user,
            status=1,
        )
        create_test_space_member(space=self.space, user=self.user, role=1)
        AgentTypeConfig.objects.create(
            agent_name="langfuse",
            agent_product="langfuse",
            runtime_type="langfuse-sdk",
            category="CUSTOM",
            setup_mode="LANGFUSE",
            display_scope="ALL",
            status=1,
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)
        self.create_url = f"/api/v1/spaces/{self.space.uuid}/agents/create"
        self.settings_url = f"/api/v1/spaces/{self.space.uuid}/settings"

    def test_creating_langfuse_agent_does_not_generate_local_credentials(self):
        response = self.client.post(
            self.create_url,
            {"agent_product": "langfuse", "name": "Langfuse 应用"},
            format="json",
        )

        self.assertEqual(response.data["code"], 0, response.data)
        self.assertFalse(SpaceObservationProject.objects.exists())

    def test_personal_space_langfuse_agent_does_not_generate_local_credentials(self):
        personal_space = Space.objects.create(
            name="Personal Langfuse",
            type=1,
            scope=1,
            creator=self.user,
            status=1,
        )
        create_test_space_member(space=personal_space, user=self.user, role=1)

        response = self.client.post(
            f"/api/v1/spaces/{personal_space.uuid}/agents/create",
            {"agent_product": "langfuse", "name": "个人 Langfuse 应用"},
            format="json",
        )

        self.assertEqual(response.data["code"], 0, response.data)
        self.assertFalse(
            SpaceObservationProject.objects.filter(space=personal_space).exists()
        )

    def test_settings_do_not_expose_fixed_langfuse_placeholder_credentials(self):
        settings_response = self.client.get(self.settings_url)
        self.assertNotIn("langfuse_compatibility", settings_response.data["data"])

    def create_project(self, product="langfuse"):
        response = self.client.post(
            self.create_url,
            {"agent_product": product, "name": "SDK project"},
            format="json",
        )
        self.assertEqual(response.data["code"], 0, response.data)
        return response.data["data"]["agent_uuid"]

    def credential_url(self, project_uuid, space=None):
        return (
            f"/api/v1/spaces/{(space or self.space).uuid}/agents/"
            f"{project_uuid}/onboarding-credential"
        )

    def test_setup_credential_requires_private_encryption_material(self):
        for django_key, dedicated in (
            ("aivision-local-django-secret-change-me", ""),
            (
                "your-secret-key-change-in-production",
                "your-shared-credential-encryption-key",
            ),
            ("django-insecure-change-this-in-production", ""),
            ("aivision-container-change-me", ""),
            ("short", "  YOUR-SHARED-CREDENTIAL-ENCRYPTION-KEY  "),
            ("a" * 64, "short"),
        ):
            with (
                self.subTest(django_key=django_key),
                override_settings(
                    SECRET_KEY=django_key, CREDENTIAL_ENCRYPTION_KEY=dedicated
                ),
            ):
                project = self.create_project()
                response = self.client.post(self.credential_url(project))
                self.assertEqual(response.status_code, 503)
                self.assertIsNone(response.data["data"])
                self.assertFalse(
                    SpaceKey.objects.filter(onboarding_agent__uuid=project).exists()
                )

    @override_settings(
        SECRET_KEY="aivision-local-django-secret-change-me",
        CREDENTIAL_ENCRYPTION_KEY="",
    )
    def test_ordinary_space_key_does_not_require_private_encryption_material(self):
        ordinary = self.client.post(
            f"/api/v1/spaces/{self.space.uuid}/space-keys",
            {"name": "Ordinary key"},
            format="json",
        )
        self.assertEqual(ordinary.data["code"], 0)
        self.assertIsNone(SpaceKey.objects.get(space=self.space).onboarding_ciphertext)

    def test_setup_reveals_existing_key_encrypted_with_private_django_secret(self):
        project = self.create_project()
        url = self.credential_url(project)
        django_key = token_urlsafe(16)
        with override_settings(
            SECRET_KEY=django_key,
            CREDENTIAL_ENCRYPTION_KEY="your-shared-credential-encryption-key",
        ):
            first = self.client.post(url)
            self.assertEqual(first.status_code, 200)

        with override_settings(
            SECRET_KEY=django_key, CREDENTIAL_ENCRYPTION_KEY=token_hex(32)
        ):
            reopened = self.client.post(url)
        self.assertEqual(reopened.status_code, 200)
        self.assertTrue(first.data["data"]["key"] == reopened.data["data"]["key"])
        self.assertEqual(SpaceKey.objects.filter(onboarding_agent__uuid=project).count(), 1)

    def test_setup_does_not_reveal_ciphertext_encrypted_with_public_default(self):
        project = self.create_project()
        url = self.credential_url(project)
        django_default = "aivision-local-django-secret-change-me"
        raw_key, prefix, key_hash = SpaceKey.generate_key()
        with override_settings(SECRET_KEY=django_default, CREDENTIAL_ENCRYPTION_KEY=""):
            ciphertext = encrypt_secret(raw_key)
        stored = SpaceKey.objects.create(
            space=self.space,
            onboarding_agent=Agent.objects.get(uuid=project),
            onboarding_ciphertext=ciphertext,
            key_prefix=prefix,
            key_hash=key_hash,
            creator=self.user,
        )
        with override_settings(SECRET_KEY=django_default):
            response = self.client.post(url)
        self.assertEqual(response.status_code, 409)
        self.assertIsNone(response.data["data"])
        stored.refresh_from_db()
        self.assertEqual(stored.onboarding_ciphertext, ciphertext)

    def test_setup_credential_reuses_key_without_exposing_it_in_the_key_list(self):
        AgentTypeConfig.objects.create(
            agent_name="NVIDIA NeMo Relay",
            agent_product="nvidia-nemo-relay",
            category="CUSTOM",
            setup_mode="OTHER",
            status=1,
        )
        _, prefix, key_hash = SpaceKey.generate_key()
        legacy = SpaceKey.objects.create(
            space=self.space, key_prefix=prefix, key_hash=key_hash, creator=self.user
        )
        issued = []
        for product in ("langfuse", "nvidia-nemo-relay"):
            with self.subTest(product=product):
                project = self.create_project(product)
                url = self.credential_url(project)
                self.assertFalse(
                    SpaceKey.objects.filter(onboarding_agent__uuid=project).exists()
                )
                first = self.client.post(url)
                self.assertEqual(first.status_code, 200)
                self.assertEqual(first["Cache-Control"], "private, no-store")
                raw_key = first.data["data"]["key"]
                issued.append(raw_key)
                self.assertEqual(self.client.post(url).data["data"]["key"], raw_key)
                stored = SpaceKey.objects.get(onboarding_agent__uuid=project)
                self.assertNotIn(raw_key, stored.onboarding_ciphertext)
                self.assertNotEqual(stored.key_hash, legacy.key_hash)

        self.assertEqual(len(set(issued)), 2)
        legacy.refresh_from_db()
        self.assertTrue(legacy.enabled)
        self.assertIsNone(legacy.gmt_deleted)
        response = self.client.get(f"/api/v1/spaces/{self.space.uuid}/space-keys")
        self.assertEqual(response.status_code, 200)
        for item in response.data["data"]["items"]:
            for field in ("key", "key_hash", "onboarding_ciphertext"):
                self.assertNotIn(field, item)

    def test_setup_credential_requires_project_space_and_reveal_permissions(self):
        project = self.create_project()
        url = self.credential_url(project)
        users = [create_local_user(f"setup-role-{i}") for i in range(3)]
        for user, role in zip(users, (2, 2, 3), strict=True):
            create_test_space_member(space=self.space, user=user, role=role)

        self.client.force_authenticate(user=users[2])
        self.assertEqual(self.client.post(url).status_code, 403)
        self.assertFalse(SpaceKey.objects.filter(space=self.space).exists())

        self.client.force_authenticate(user=users[0])
        created = self.client.post(url)
        self.assertEqual(created.status_code, 200)
        self.client.force_authenticate(user=users[1])
        denied = self.client.post(url)
        self.assertEqual(denied.status_code, 403)
        self.assertIsNone(denied.data["data"])

        self.client.force_authenticate(user=self.user)
        self.assertEqual(self.client.post(url).data["data"], created.data["data"])
        other_space = Space.objects.create(
            name="Other Space", type=2, scope=1, creator=self.user, status=1
        )
        create_test_space_member(space=other_space, user=self.user, role=1)
        self.assertEqual(
            self.client.post(self.credential_url(project, other_space)).status_code,
            404,
        )

        Agent.objects.filter(uuid=project).update(status=0)
        self.assertEqual(self.client.post(url).status_code, 404)

    def test_setup_credential_does_not_silently_replace_disabled_or_unreadable_keys(self):
        project = self.create_project()
        url = self.credential_url(project)
        created = self.client.post(url)
        self.assertEqual(created.status_code, 200)
        stored = SpaceKey.objects.get(onboarding_agent__uuid=project)
        detail_url = f"/api/v1/spaces/{self.space.uuid}/space-keys/{stored.pk}"
        self.assertEqual(
            self.client.put(detail_url, {"enabled": False}, format="json").status_code,
            200,
        )
        self.assertEqual(self.client.post(url).status_code, 409)
        SpaceKey.objects.filter(pk=stored.pk).update(
            enabled=True, onboarding_ciphertext="invalid"
        )
        self.assertEqual(self.client.post(url).status_code, 409)
        self.assertEqual(
            SpaceKey.objects.filter(onboarding_agent__uuid=project).count(), 1
        )

        self.assertEqual(self.client.delete(detail_url).status_code, 200)
        replacement = self.client.post(url)
        self.assertEqual(replacement.status_code, 200)
        self.assertNotEqual(replacement.data["data"], created.data["data"])

    def test_langfuse_agent_requires_project_name(self):
        response = self.client.post(
            self.create_url, {"agent_product": "langfuse"}, format="json"
        )

        self.assertEqual(response.data["code"], 1002, response.data)
        self.assertFalse(Agent.objects.filter(space=self.space).exists())
        self.assertFalse(SpaceObservationProject.objects.exists())

    def test_langfuse_agent_supports_multiple_projects(self):
        first = self.client.post(
            self.create_url,
            {"agent_product": "langfuse", "name": "Billing Assistant"},
            format="json",
        )
        second = self.client.post(
            self.create_url,
            {"agent_product": "langfuse", "name": "Support Assistant"},
            format="json",
        )
        duplicate_name = self.client.post(
            self.create_url,
            {"agent_product": "langfuse", "name": "Billing Assistant"},
            format="json",
        )

        self.assertEqual(first.data["code"], 0, first.data)
        self.assertEqual(second.data["code"], 0, second.data)
        self.assertEqual(duplicate_name.data["code"], 0, duplicate_name.data)
        self.assertEqual(Agent.objects.filter(space=self.space).count(), 3)
        self.assertEqual(
            set(
                Agent.objects.filter(space=self.space).values_list(
                    "custom_name", flat=True
                )
            ),
            {"Billing Assistant", "Support Assistant"},
        )

    def test_mark_connected_promotes_pending_langfuse_application(self):
        created = self.client.post(
            self.create_url,
            {
                "agent_product": "langfuse",
                "name": "Support Assistant",
                "metadata": {"framework": "LangChain"},
            },
            format="json",
        )
        agent_uuid = created.data["data"]["agent_uuid"]

        response = self.client.post(
            f"/api/v1/spaces/{self.space.uuid}/agents/{agent_uuid}/mark-connected"
        )

        self.assertEqual(response.data["code"], 0, response.data)
        self.assertEqual(response.data["data"]["status"], 1)
        self.assertEqual(
            Agent.objects.get(uuid=agent_uuid).metadata,
            {"framework": "LangChain"},
        )

    def test_mark_connected_updates_framework_for_connected_application(self):
        created = self.client.post(
            self.create_url,
            {"agent_product": "langfuse", "name": "Connected Assistant"},
            format="json",
        )
        agent_uuid = created.data["data"]["agent_uuid"]
        agent = Agent.objects.get(uuid=agent_uuid)
        agent.status = 1
        agent.save(update_fields=["status", "gmt_modified"])

        response = self.client.post(
            f"/api/v1/spaces/{self.space.uuid}/agents/{agent_uuid}/mark-connected",
            {"metadata": {"framework": "LlamaIndex"}},
            format="json",
        )

        self.assertEqual(response.data["code"], 0, response.data)
        self.assertEqual(response.data["data"]["status"], 1)
        agent.refresh_from_db()
        self.assertEqual(agent.metadata, {"framework": "LlamaIndex"})

    def test_mark_connected_rejects_non_langfuse_application(self):
        custom = AgentTypeConfig.objects.create(
            agent_name="MANUAL",
            agent_product="Manual",
            setup_mode="CLI",
            category="CUSTOM",
            status=1,
        )
        agent = create_test_agent(
            space=self.space,
            agent_type=custom,
            agent_product=custom.agent_product,
            name="Manual",
            custom_name="Manual",
            status=2,
        )

        response = self.client.post(
            f"/api/v1/spaces/{self.space.uuid}/agents/{agent.uuid}/mark-connected"
        )

        self.assertEqual(response.data["code"], 1012, response.data)
        agent.refresh_from_db()
        self.assertEqual(agent.status, 2)


@override_settings(
    AIVISION_INGEST_SECRET="langfuse-compatibility-signing-secret",
    AIVISION_OTLP_INGEST_ENDPOINT="http://gateway.test",
    CREDENTIAL_ENCRYPTION_KEY=token_hex(32),
)
class LangfuseOtelCompatibilityTests(SpaceKeyTestCase):
    def setUp(self):
        super().setUp()
        self.user = create_local_user("langfuse-ingest-user")
        self.space = Space.objects.create(
            name="Langfuse Ingest",
            type=2,
            scope=1,
            creator=self.user,
            status=1,
        )
        create_test_space_member(space=self.space, user=self.user, role=1)
        self.agent_type = AgentTypeConfig.objects.create(
            agent_name="langfuse-ingest",
            agent_product="langfuse",
            runtime_type="langfuse-sdk",
            category="CUSTOM",
            setup_mode="LANGFUSE",
            display_scope="ALL",
            status=1,
        )
        self.agent = create_test_agent(
            space=self.space,
            agent_type=self.agent_type,
            agent_product="langfuse",
            name="Langfuse 应用",
            custom_name="Langfuse 应用",
            status=2,
        )
        self.datasource = DataSource.objects.create(
            agent_type=self.agent_type,
            datasource_type="traces",
            source_system="signoz",
            enabled=1,
            is_default=1,
            config={"agent_product_filter": "langfuse"},
        )
        raw_key, key_prefix, key_hash = SpaceKey.generate_key()
        self.space_key = SpaceKey.objects.create(
            space=self.space,
            key_prefix=key_prefix,
            key_hash=key_hash,
            name="Langfuse SDK",
        )
        credentials = f"{self.agent.uuid}:{raw_key}".encode()
        self.authorization = "Basic " + base64.b64encode(credentials).decode()
        self.client = APIClient()
        self.url = "/api/public/otel/v1/traces"
        self.payload = self._otlp_payload()

    def _otlp_payload(self):
        request = ExportTraceServiceRequest()
        resource_spans = request.resource_spans.add()
        scope_spans = resource_spans.scope_spans.add()
        span = scope_spans.spans.add()
        span.trace_id = b"0123456789abcdef"
        span.span_id = b"01234567"
        span.name = "Chat"
        model = span.attributes.add()
        model.key = "langfuse.observation.model.name"
        model.value.string_value = "gpt-4o"
        usage = span.attributes.add()
        usage.key = "langfuse.observation.usage_details"
        usage.value.string_value = json.dumps({"input": 3, "output": 5, "total": 8})
        return request.SerializeToString()

    def test_forwards_official_langfuse_otlp_using_the_revealed_project_key(self):
        console = APIClient()
        console.force_authenticate(user=self.user)
        credential_url = (
            f"/api/v1/spaces/{self.space.uuid}/agents/"
            f"{self.agent.uuid}/onboarding-credential"
        )
        self.assertEqual(console.post(credential_url).status_code, 200)
        revealed = console.post(credential_url)
        self.assertEqual(revealed.status_code, 200)
        credentials = f"{self.agent.uuid}:{revealed.data['data']['key']}".encode()

        with patch("apps.core.views.langfuse_ingest.requests.post") as upstream_post:
            upstream_post.return_value = SimpleNamespace(
                status_code=202, content=b"", headers={}
            )

            response = self.client.post(
                self.url,
                data=self.payload,
                content_type="application/x-protobuf",
                HTTP_AUTHORIZATION="Basic " + base64.b64encode(credentials).decode(),
            )

        self.assertEqual(response.status_code, 202, response.content)
        upstream_post.assert_called_once()
        kwargs = upstream_post.call_args.kwargs
        self.assertEqual(kwargs["headers"]["Content-Type"], "application/x-protobuf")

        token = kwargs["headers"]["Authorization"].removeprefix("Bearer ")
        claims = verify_ingest_token(token, b"langfuse-compatibility-signing-secret")
        self.assertEqual(claims["aid"], self.agent.uuid)
        self.assertEqual(claims["ap"], "langfuse")
        self.assertEqual(claims["sp"], self.space.uuid)

        forwarded = ExportTraceServiceRequest()
        forwarded.ParseFromString(kwargs["data"])
        attributes = forwarded.resource_spans[0].scope_spans[0].spans[0].attributes
        values = {item.key: item.value for item in attributes}
        self.assertEqual(values["gen_ai.request.model"].string_value, "gpt-4o")
        self.assertEqual(values["gen_ai.usage.input_tokens"].int_value, 3)
        self.assertEqual(values["gen_ai.usage.output_tokens"].int_value, 5)
        self.assertEqual(values["gen_ai.usage.total_tokens"].int_value, 8)

    def test_rejects_invalid_basic_credentials(self):
        with patch("apps.core.views.langfuse_ingest.requests.post") as upstream_post:
            response = self.client.post(
                self.url,
                data=self.payload,
                content_type="application/x-protobuf",
                HTTP_AUTHORIZATION="Basic invalid",
            )

        self.assertEqual(response.status_code, 401)
        upstream_post.assert_not_called()

    def test_ingest_uses_trusted_datasource_filter_not_agent_routing_id(self):
        self.datasource.config = {"agent_product_filter": "ConfiguredLangfuseRows"}
        self.datasource.save(update_fields=["config"])
        with patch("apps.core.views.langfuse_ingest.requests.post") as post:
            post.return_value = SimpleNamespace(
                status_code=200, content=b"", headers={}
            )
            response = self.client.post(
                self.url,
                data=self.payload,
                content_type="application/x-protobuf",
                HTTP_AUTHORIZATION=self.authorization,
            )
        self.assertEqual(response.status_code, 200, response.content)
        forwarded = ExportTraceServiceRequest.FromString(post.call_args.kwargs["data"])
        values = {
            item.key: item.value.string_value
            for item in forwarded.resource_spans[0].resource.attributes
        }
        self.assertEqual(values["ant.agent.product"], "ConfiguredLangfuseRows")
        token = post.call_args.kwargs["headers"]["Authorization"].removeprefix(
            "Bearer "
        )
        self.assertEqual(
            verify_ingest_token(token, b"langfuse-compatibility-signing-secret")["ap"],
            "langfuse",
        )

    def test_missing_storage_filter_rejects_ingest_before_forwarding(self):
        for configuration in ({}, {"agent_product_filter": "   "}):
            self.datasource.config = configuration
            self.datasource.save(update_fields=["config"])
            with patch("apps.core.views.langfuse_ingest.requests.post") as post:
                post.return_value = SimpleNamespace(
                    status_code=200, content=b"", headers={}
                )
                response = self.client.post(
                    self.url,
                    data=self.payload,
                    content_type="application/x-protobuf",
                    HTTP_AUTHORIZATION=self.authorization,
                )
            self.assertEqual(response.status_code, 503, response.content)
            self.assertIn("agent_product_filter", response.json()["error"])
            post.assert_not_called()

    def test_trace_query_resolves_each_langfuse_project(self):
        second_agent = create_test_agent(
            space=self.space,
            agent_type=self.agent_type,
            agent_product="langfuse",
            name="Second Langfuse Project",
            custom_name="Second Langfuse Project",
            status=2,
        )

        resolved_first = resolve_space_agent_for_query(
            self.space,
            "langfuse",
            agent_uuid=self.agent.uuid,
            active_statuses=(1, 2),
        )
        resolved_second = resolve_space_agent_for_query(
            self.space,
            "langfuse",
            agent_uuid=second_agent.uuid,
            active_statuses=(1, 2),
        )

        self.assertEqual(resolved_first.pk, self.agent.pk)
        self.assertEqual(resolved_second.pk, second_agent.pk)


class LangfuseAutoPromoteTests(TestCase):
    """后台兜底：Pending Agent 有对应 agent_product 的 trace 数据时自动升级为已接入。

    修复场景：用户在 onboarding 页面创建 Agent（langfuse 应用、nvidia-nemo-relay
    等）后离开，trace 数据在之后才上报，status 停留在 Pending(2) 而不会自动
    升级。trace 列表查询发现真实数据时，应将匹配的 Pending Agent 提升为已接入(1)。
    """

    def setUp(self):
        super().setUp()
        self.user = create_local_user("langfuse-auto-promote")
        self.space = Space.objects.create(
            name="Langfuse Auto Promote",
            type=1,
            scope=1,
            creator=self.user,
            status=1,
        )
        create_test_space_member(space=self.space, user=self.user, role=1)
        self.agent_type = AgentTypeConfig.objects.create(
            agent_name="langfuse",
            agent_product="langfuse",
            runtime_type="langfuse-sdk",
            category="CUSTOM",
            setup_mode="LANGFUSE",
            display_scope="ALL",
            status=1,
        )
        self.agent = create_test_agent(
            space=self.space,
            agent_type=self.agent_type,
            agent_product="langfuse",
            name="langfuse_test_trace",
            custom_name="langfuse_test_trace",
            status=2,
        )

    def _service_with_provider(self, total_items: int) -> TraceQueryService:
        svc = TraceQueryService()
        provider = Mock()
        provider.list_traces.return_value = {
            "data": [{"id": "t1"}] if total_items > 0 else [],
            "meta": {"totalItems": total_items},
        }
        svc.register_provider("signoz", provider, source_systems=("signoz",))
        return svc

    def test_trace_list_with_data_promotes_pending_langfuse_agent(self):
        svc = self._service_with_provider(total_items=1)
        result = svc.list_traces(
            space=self.space,
            user=self.user,
            query_params={"agent_product": "langfuse", "page": 1, "limit": 1},
            source_system_override="signoz",
        )
        self.assertEqual(result["meta"]["totalItems"], 1)
        self.agent.refresh_from_db()
        self.assertEqual(self.agent.status, 1)

    def test_trace_list_without_data_keeps_pending_status(self):
        svc = self._service_with_provider(total_items=0)
        svc.list_traces(
            space=self.space,
            user=self.user,
            query_params={"agent_product": "langfuse", "page": 1, "limit": 1},
            source_system_override="signoz",
        )
        self.agent.refresh_from_db()
        self.assertEqual(self.agent.status, 2)

    def test_relay_pending_agent_is_promoted_by_own_trace_data(self):
        """nvidia-nemo-relay（setup_mode=OTHER）有 trace 时同样自动升级。"""
        relay_type = AgentTypeConfig.objects.create(
            agent_name="nvidia-nemo-relay",
            agent_product="nvidia-nemo-relay",
            runtime_type="signoz",
            category="CUSTOM",
            setup_mode="OTHER",
            display_scope="HIDDEN",
            status=1,
        )
        relay = create_test_agent(
            space=self.space,
            agent_type=relay_type,
            agent_product="nvidia-nemo-relay",
            name="relay_test_trace",
            status=2,
        )
        svc = self._service_with_provider(total_items=1)
        svc.list_traces(
            space=self.space,
            user=self.user,
            query_params={
                "agent_product": "nvidia-nemo-relay",
                "page": 1,
                "limit": 1,
            },
            source_system_override="signoz",
        )
        relay.refresh_from_db()
        self.assertEqual(relay.status, 1)
        # 查询未命中 langfuse，langfuse Pending Agent 不受影响
        self.agent.refresh_from_db()
        self.assertEqual(self.agent.status, 2)

    def test_other_product_pending_agent_is_not_promoted(self):
        """升级严格按 agent_product 隔离：查 codex 的数据只升级 codex 自身。"""
        other_type = AgentTypeConfig.objects.create(
            agent_name="codex",
            agent_product="codex",
            runtime_type="codex",
            category="BUILT_IN",
            setup_mode="AUTO",
            display_scope="ALL",
            status=1,
        )
        other = create_test_agent(
            space=self.space,
            agent_type=other_type,
            agent_product="codex",
            name="codex",
            status=2,
        )
        svc = self._service_with_provider(total_items=1)
        svc.list_traces(
            space=self.space,
            user=self.user,
            query_params={"agent_product": "codex", "page": 1, "limit": 1},
            source_system_override="signoz",
        )
        other.refresh_from_db()
        self.assertEqual(other.status, 1)
        # langfuse agent 不被别的产品的查询牵连
        self.agent.refresh_from_db()
        self.assertEqual(self.agent.status, 2)

    def test_promotion_triggers_personal_diagnosis_sync(self):
        with patch(
            "apps.core.trace_query.service.sync_personal_diagnosis_after_commit"
        ) as mock_sync:
            svc = self._service_with_provider(total_items=1)
            svc.list_traces(
                space=self.space,
                user=self.user,
                query_params={
                    "agent_product": "langfuse",
                    "page": 1,
                    "limit": 1,
                },
                source_system_override="signoz",
            )
            self.agent.refresh_from_db()
            self.assertEqual(self.agent.status, 1)
            mock_sync.assert_called_once()

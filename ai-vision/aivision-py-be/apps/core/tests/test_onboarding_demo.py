from datetime import timedelta
from unittest.mock import Mock, patch

from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import (
    ExportTraceServiceRequest,
    ExportTraceServiceResponse,
)
from opentelemetry.proto.trace.v1.trace_pb2 import Status
from rest_framework.test import APIClient

from apps.core.models import Agent, AgentTypeConfig, DataSource, Space, SpaceKey
from apps.core.services.ingest_token import verify_ingest_token
from apps.core.services.onboarding_demo import (
    OnboardingDemoError,
    load_demo_sample,
    rebase_demo_sample,
)
from apps.core.tests.helpers import (
    create_local_user,
    create_test_agent,
    create_test_space_member,
)
from apps.core.trace_query.types import TraceQueryServiceError


class DemoConsentTests(SimpleTestCase):
    def test_missing_consent_never_reaches_provisioning(self):
        client = APIClient()
        client.force_authenticate(user=Mock(is_authenticated=True))
        for body in (
            {},
            {"consent": False},
            {"consent": 1},
            {"consent": True, "trace": "external"},
        ):
            with self.subTest(body=body):
                response = client.post(
                    "/api/v1/spaces/test-space/onboarding/demo", body, format="json"
                )
                self.assertEqual(response.status_code, 400)

    def test_invalid_language_never_reaches_provisioning(self):
        client = APIClient()
        client.force_authenticate(user=Mock(is_authenticated=True))
        for language in ("fr-FR", "", None, [], {}):
            with self.subTest(language=language):
                response = client.post(
                    "/api/v1/spaces/test-space/onboarding/demo",
                    {"consent": True, "language": language},
                    format="json",
                )
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.data["message"], "DEMO_LANGUAGE_UNSUPPORTED")


class DemoSampleTests(SimpleTestCase):
    def test_memory_read_resolves_to_its_own_rebased_trace_and_is_checked(self):
        sample, digest = load_demo_sample()
        scoped_spans = [
            (scope.scope.name, span)
            for resource in sample.resource_spans
            for scope in resource.scope_spans
            for span in scope.spans
        ]

        def attributes(span):
            return {attr.key: attr.value.string_value for attr in span.attributes}

        receipt, readback = rebase_demo_sample(
            sample,
            "sample-agent",
            {"sample_digest": digest, "anchor_ns": "1800000000000000000"},
        )

        memory = receipt["scenarios"]["memory"]
        memory_spans = {
            span.span_id.hex(): (scope_name, span)
            for scope_name, span in scoped_spans
            if span.trace_id.hex() == memory["trace_id"]
        }
        scope_name, read = memory_spans[memory["span_id"]]
        self.assertEqual(scope_name, "powercontext.client")
        self.assertEqual(len(memory_spans), memory["span_count"])
        self.assertEqual(readback[memory["trace_id"]], len(memory_spans))
        self.assertNotEqual(
            receipt["scenarios"]["session"]["trace_id"], memory["trace_id"]
        )
        read_attrs = attributes(read)
        self.assertEqual(read_attrs["aivision.demo.scenario"], "memory")
        self.assertEqual(read_attrs["powercontext.operation.name"], "prepare_context")
        self.assertEqual(read_attrs["powercontext.operation.outcome"], "success")
        self.assertEqual(read.status.code, Status.STATUS_CODE_OK)
        self.assertNotEqual(
            receipt["scenarios"]["session"]["session_id"], read_attrs["session.id"]
        )
        memory_key = read_attrs["aivision.demo.memory.key"]
        self.assertTrue(memory_key)
        writes = [
            span
            for scope_name, span in scoped_spans
            if scope_name == "powercontext.client"
            and (attrs := attributes(span)).get("aivision.demo.memory.key") == memory_key
            and attrs.get("powercontext.operation.name") == "capture_content_source"
        ]
        self.assertEqual(len(writes), 1)
        write = writes[0]
        write_attrs = attributes(write)
        self.assertEqual(write.status.code, Status.STATUS_CODE_OK)
        self.assertEqual(write_attrs["powercontext.operation.outcome"], "success")
        self.assertTrue(write_attrs["session.id"])
        self.assertNotEqual(write_attrs["session.id"], read_attrs["session.id"])
        self.assertLess(write.end_time_unix_nano, read.start_time_unix_nano)

    def test_sample_without_a_healthy_comparison_trace_is_rejected(self):
        sample, digest = load_demo_sample()
        for resource in sample.resource_spans:
            for scope in resource.scope_spans:
                for span in scope.spans:
                    span.status.code = 2

        with self.assertRaises(OnboardingDemoError):
            rebase_demo_sample(
                sample,
                "sample-agent",
                {"sample_digest": digest, "anchor_ns": "1800000000000000000"},
            )


@override_settings(
    AIVISION_INGEST_SECRET="sample-test-ingest-secret",
    AIVISION_OTLP_UPSTREAM_ENDPOINT="http://collector.test",
)
class OnboardingDemoTests(TestCase):
    def setUp(self):
        self.user = create_local_user("demo-owner")
        self.space = Space.objects.create(name="Demo test", type=1, creator=self.user)
        create_test_space_member(space=self.space, user=self.user, role=1)
        self.agent_type = AgentTypeConfig.objects.create(
            agent_name="langfuse",
            agent_product="langfuse",
            runtime_type="langfuse-sdk",
            setup_mode="LANGFUSE",
            category="CUSTOM",
        )
        DataSource.objects.create(
            agent_type=self.agent_type,
            datasource_type="traces",
            source_system="signoz",
            enabled=1,
            is_default=1,
            config={"agent_product_filter": "langfuse"},
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)
        self.url = f"/api/v1/spaces/{self.space.uuid}/onboarding/demo"

    def _confirm(self, language=None):
        body = {"consent": True}
        if language is not None:
            body["language"] = language
        return self.client.post(self.url, body, format="json")

    def _builtin_response(self, method, path, *, json):
        self.assertEqual((method, path), ("POST", "diagnosers/sync-common-builtins"))
        self.assertEqual(json["space_uuid"], self.space.uuid)
        return {
            "diagnosers": [
                {
                    **json,
                    "uuid": "demo-tool-success",
                    "type": "builtin",
                    "mode": "rule",
                    "spec": {"executor": "tool_success"},
                }
            ]
        }

    def test_viewer_cannot_provision_demo(self):
        viewer = create_local_user("demo-viewer")
        create_test_space_member(space=self.space, user=viewer, role=3)
        self.client.force_authenticate(user=viewer)

        response = self._confirm()

        self.assertEqual(response.status_code, 403)
        self.assertFalse(Agent.objects.filter(space=self.space).exists())

    @patch("apps.core.views.langfuse_ingest.requests.post")
    @patch("apps.core.views.onboarding_demo.remote")
    def test_new_agent_name_uses_requested_language(self, remote, send):
        remote.side_effect = self._builtin_response
        send.return_value = Mock(status_code=200, content=b"")
        for language, expected_name in (
            (None, "Example Agent"),
            ("en-US", "Example Agent"),
            ("zh-CN", "示例 Agent"),
            ("ja-JP", "サンプル Agent"),
        ):
            with self.subTest(language=language):
                self.space = Space.objects.create(
                    name="Localized demo", type=1, creator=self.user
                )
                create_test_space_member(space=self.space, user=self.user, role=1)
                self.url = f"/api/v1/spaces/{self.space.uuid}/onboarding/demo"
                response = self._confirm(language)

                self.assertEqual(response.status_code, 200, response.data)
                self.assertEqual(
                    Agent.objects.get(space=self.space).name, expected_name
                )

    @patch("apps.core.views.langfuse_ingest.requests.post")
    @patch("apps.core.views.onboarding_demo.remote")
    def test_replay_preserves_existing_and_user_renamed_agent_names(self, remote, send):
        remote.side_effect = self._builtin_response
        send.return_value = Mock(status_code=200, content=b"")
        response = self._confirm("zh-CN")
        self.assertEqual(response.status_code, 200, response.data)
        receipt = response.data["data"]
        demo = Agent.objects.get(space=self.space)

        self.assertEqual(self._confirm("ja-JP").data["data"], receipt)
        demo.refresh_from_db()
        self.assertEqual(demo.name, "示例 Agent")

        demo.name = "My renamed Agent"
        demo.save(update_fields=["name"])
        self.assertEqual(self._confirm("en-US").data["data"], receipt)
        demo.refresh_from_db()
        self.assertEqual(demo.name, "My renamed Agent")

        later = timezone.now() + timedelta(days=7)
        with patch("django.utils.timezone.now", return_value=later):
            refreshed = self._confirm("ja-JP")
        self.assertEqual(refreshed.status_code, 200, refreshed.data)
        demo.refresh_from_db()
        self.assertEqual(demo.name, "My renamed Agent")
        self.assertEqual(Agent.objects.filter(space=self.space).count(), 1)

    @patch("apps.core.views.langfuse_ingest.requests.post")
    @patch("apps.core.views.onboarding_demo.remote")
    def test_failed_ingest_retries_same_batch_and_completed_demo_reuses_agent(
        self, remote, send
    ):
        business_agent = create_test_agent(
            space=self.space,
            agent_type=self.agent_type,
            agent_product="langfuse",
            name="My Agent",
        )
        remote.side_effect = self._builtin_response
        send.side_effect = [
            Mock(status_code=503),
            Mock(
                status_code=200,
                content=ExportTraceServiceResponse(
                    partial_success={"rejected_spans": 1}
                ).SerializeToString(),
            ),
            Mock(
                status_code=200,
                content=ExportTraceServiceResponse().SerializeToString(),
            ),
        ]

        self.assertEqual(self._confirm().status_code, 503)
        demo = Agent.objects.exclude(pk=business_agent.pk).get(space=self.space)
        self.assertEqual(demo.status, 2)
        self.assertEqual(self._confirm().status_code, 503)

        response = self._confirm()
        self.assertEqual(response.status_code, 200, response.data)
        receipt = response.data["data"]
        self.assertEqual(receipt["agent_uuid"], demo.uuid)
        self.assertEqual(receipt["agent_product"], "langfuse")
        self.assertEqual(receipt["diagnoser_uuid"], "demo-tool-success")
        self.assertEqual(self._confirm().data["data"], receipt)
        self.assertEqual(send.call_count, 3)
        self.assertEqual(Agent.objects.filter(space=self.space).count(), 2)
        self.assertFalse(SpaceKey.objects.exists())
        demo.refresh_from_db()
        self.assertEqual(demo.status, 1)

        first, partial, retry = (call.kwargs for call in send.call_args_list)
        self.assertEqual(first["data"], partial["data"])
        self.assertEqual(partial["data"], retry["data"])
        self.assertEqual(send.call_args.args, ("http://collector.test/v1/traces",))
        claims = verify_ingest_token(retry["headers"]["Authorization"])
        self.assertEqual(claims["aid"], demo.uuid)
        self.assertEqual(claims["sp"], self.space.uuid)
        self.assertEqual(claims["u"], demo.observation_subject_binding.subject_id)
        request = ExportTraceServiceRequest.FromString(retry["data"])
        spans = [
            span
            for resource in request.resource_spans
            for scope in resource.scope_spans
            for span in scope.spans
        ]
        selected = [
            span for span in spans if span.trace_id.hex() == receipt["trace_id"]
        ]
        self.assertEqual(len(selected), receipt["span_count"])
        roots = [span for span in spans if not span.parent_span_id]
        self.assertGreaterEqual(len(roots), 12)
        self.assertEqual(len({span.trace_id for span in roots}), len(roots))
        hour_ns = 60 * 60 * 1_000_000_000
        self.assertGreaterEqual(
            len({span.start_time_unix_nano // hour_ns for span in roots}), 6
        )
        normalized_models = set()
        for span in spans:
            attrs = {item.key: item.value for item in span.attributes}
            kind_attr = attrs.get("langfuse.observation.type")
            kind = kind_attr.string_value if kind_attr is not None else ""
            if kind not in {"generation", "embedding"}:
                continue
            normalized_models.add(attrs["gen_ai.request.model"].string_value)
            input_tokens = attrs["gen_ai.usage.input_tokens"].int_value
            output_tokens = attrs["gen_ai.usage.output_tokens"].int_value
            self.assertGreater(input_tokens, 0)
            self.assertEqual(
                attrs["gen_ai.usage.total_tokens"].int_value,
                input_tokens + output_tokens,
            )
            if kind == "embedding":
                self.assertEqual(output_tokens, 0)
        self.assertEqual(
            normalized_models,
            {"gpt-4o-mini", "gpt-4.1-mini", "text-embedding-3-small"},
        )
        scenarios = receipt["scenarios"]
        self.assertEqual(set(scenarios), {"session", "mcp", "skill", "decision", "memory"})
        by_id = {(span.trace_id.hex(), span.span_id.hex()): span for span in spans}
        for scenario in ("mcp", "skill", "decision", "memory"):
            target = scenarios[scenario]
            span = by_id[(target["trace_id"], target["span_id"])]
            attrs = {item.key: item.value for item in span.attributes}
            self.assertEqual(attrs["aivision.demo.scenario"].string_value, scenario)
        self.assertEqual(scenarios["mcp"]["trace_id"], receipt["trace_id"])
        session = scenarios["session"]
        turn_ids = {turn["trace_id"] for turn in session["turns"]}
        self.assertGreaterEqual(len(turn_ids), 3)
        self.assertEqual(
            {
                root.trace_id.hex()
                for root in roots
                if any(
                    attr.key == "gen_ai.conversation.id"
                    and attr.value.string_value == session["session_id"]
                    for attr in root.attributes
                )
            },
            turn_ids,
        )
        self.assertTrue(any(span.status.code == 2 for span in selected))
        self.assertIn("comparison_trace_id", receipt)
        comparison = [
            span
            for span in spans
            if span.trace_id.hex() == receipt["comparison_trace_id"]
        ]
        self.assertNotEqual(receipt["trace_id"], receipt["comparison_trace_id"])
        self.assertGreater(len(comparison), 0)
        self.assertEqual(len(comparison), receipt["comparison_span_count"])
        self.assertTrue(all(span.status.code != 2 for span in comparison))
        retry_root = next(span for span in selected if not span.parent_span_id)
        healthy_root = next(span for span in comparison if not span.parent_span_id)
        self.assertGreater(
            retry_root.end_time_unix_nano - retry_root.start_time_unix_nano,
            healthy_root.end_time_unix_nano - healthy_root.start_time_unix_nano,
        )

    @patch("apps.core.views.langfuse_ingest.requests.post")
    @patch("apps.core.views.onboarding_demo.remote")
    def test_old_sample_refreshes_without_creating_a_second_agent(self, remote, send):
        remote.side_effect = self._builtin_response
        send.return_value = Mock(status_code=200, content=b"")
        first = self._confirm().data["data"]

        later = timezone.now() + timedelta(hours=16, minutes=1)
        with patch("django.utils.timezone.now", return_value=later):
            refreshed = self._confirm().data["data"]
            self.assertEqual(self._confirm().data["data"], refreshed)

        self.assertEqual(send.call_count, 2)
        self.assertEqual(first["agent_uuid"], refreshed["agent_uuid"])
        self.assertEqual(first["diagnoser_uuid"], refreshed["diagnoser_uuid"])
        self.assertNotEqual(first["trace_id"], refreshed["trace_id"])
        self.assertIn("comparison_trace_id", first)
        self.assertNotEqual(
            first["comparison_trace_id"], refreshed["comparison_trace_id"]
        )
        self.assertNotEqual(
            first["scenarios"]["session"]["session_id"],
            refreshed["scenarios"]["session"]["session_id"],
        )
        self.assertEqual(Agent.objects.filter(space=self.space).count(), 1)

    @patch("apps.core.views.onboarding_demo.get_default_trace_query_service")
    @patch("apps.core.views.langfuse_ingest.requests.post")
    @patch("apps.core.views.onboarding_demo.remote")
    def test_accepted_batch_is_replayed_if_storage_remains_empty(
        self, remote, send, query
    ):
        remote.side_effect = self._builtin_response
        send.return_value = Mock(status_code=200, content=b"")
        receipt = self._confirm().data["data"]
        read = query.return_value.get_trace_detail
        read.return_value = {"spans": []}

        now = timezone.now()
        with patch(
            "django.utils.timezone.now", return_value=now + timedelta(minutes=1)
        ):
            self.assertEqual(self._confirm().data["data"], receipt)
        targets = {
            receipt["trace_id"]: receipt["span_count"],
            receipt["comparison_trace_id"]: receipt["comparison_span_count"],
        }
        for scenario in receipt["scenarios"].values():
            targets[scenario["trace_id"]] = scenario["span_count"]
        for turn in receipt["scenarios"]["session"]["turns"]:
            targets[turn["trace_id"]] = turn["span_count"]

        def complete(*, trace_id, **kwargs):
            return {"meta": {"complete": True, "expectedSpanCount": targets[trace_id]}}

        read.side_effect = complete
        with patch(
            "django.utils.timezone.now", return_value=now + timedelta(minutes=2)
        ):
            self.assertEqual(self._confirm().data["data"], receipt)

        self.assertEqual(read.call_count, 1 + len(targets))
        self.assertEqual(send.call_count, 2)
        self.assertEqual(
            send.call_args_list[0].kwargs["data"], send.call_args.kwargs["data"]
        )

    @patch("apps.core.views.onboarding_demo.get_default_trace_query_service")
    @patch("apps.core.views.langfuse_ingest.requests.post")
    @patch("apps.core.views.onboarding_demo.remote")
    def test_busy_settled_replay_preserves_demo_and_can_retry(
        self, remote, send, query
    ):
        remote.side_effect = self._builtin_response
        send.return_value = Mock(status_code=200, content=b"")
        receipt = self._confirm().data["data"]
        agent = Agent.objects.get(uuid=receipt["agent_uuid"])
        saved_metadata = agent.metadata
        agent.status = 0
        agent.save(update_fields=["status"])
        read = query.return_value.get_trace_detail
        read.side_effect = TraceQueryServiceError(
            "Private upstream capacity detail", 3037
        )
        later = timezone.now() + timedelta(minutes=1)

        with patch("django.utils.timezone.now", return_value=later):
            busy = self._confirm()

        self.assertEqual(busy.status_code, 429)
        self.assertEqual(
            busy.data, {"code": 3037, "message": "DEMO_BUSY", "data": None}
        )
        agent.refresh_from_db()
        self.assertEqual(agent.status, 0)
        self.assertEqual(agent.metadata, saved_metadata)
        self.assertEqual(Agent.objects.filter(space=self.space).count(), 1)
        self.assertEqual(send.call_count, 1)

        targets = {
            receipt["trace_id"]: receipt["span_count"],
            receipt["comparison_trace_id"]: receipt["comparison_span_count"],
            **{
                scene["trace_id"]: scene["span_count"]
                for scene in receipt["scenarios"].values()
            },
        }
        for turn in receipt["scenarios"]["session"]["turns"]:
            targets[turn["trace_id"]] = turn["span_count"]

        def complete(*, trace_id, **kwargs):
            return {"meta": {"complete": True, "expectedSpanCount": targets[trace_id]}}

        read.side_effect = complete
        with patch("django.utils.timezone.now", return_value=later):
            retried = self._confirm()

        self.assertEqual(retried.status_code, 200)
        self.assertEqual(retried.data["data"], receipt)
        agent.refresh_from_db()
        self.assertEqual(agent.status, 1)
        self.assertEqual(agent.metadata, saved_metadata)
        self.assertEqual(send.call_count, 1)

    @patch("apps.core.views.onboarding_demo.get_default_trace_query_service")
    @patch("apps.core.views.langfuse_ingest.requests.post")
    @patch("apps.core.views.onboarding_demo.remote")
    def test_other_settled_trace_errors_remain_unavailable(self, remote, send, query):
        remote.side_effect = self._builtin_response
        send.return_value = Mock(status_code=200, content=b"")
        receipt = self._confirm().data["data"]
        read = query.return_value.get_trace_detail
        later = timezone.now() + timedelta(minutes=1)

        for code in (3019, 3035, 3036):
            with self.subTest(code=code):
                read.side_effect = TraceQueryServiceError(
                    "Private upstream detail", code
                )
                with patch("django.utils.timezone.now", return_value=later):
                    response = self._confirm()

                self.assertEqual(response.status_code, 503)
                self.assertEqual(
                    response.data,
                    {"code": 5001, "message": "DEMO_UNAVAILABLE", "data": None},
                )

        self.assertEqual(send.call_count, 1)
        self.assertEqual(Agent.objects.filter(uuid=receipt["agent_uuid"]).count(), 1)

    @patch("apps.core.views.onboarding_demo.get_default_trace_query_service")
    @patch("apps.core.views.langfuse_ingest.requests.post")
    @patch("apps.core.views.onboarding_demo.remote")
    def test_accepted_batch_is_replayed_if_only_comparison_trace_is_missing(
        self, remote, send, query
    ):
        remote.side_effect = self._builtin_response
        send.return_value = Mock(status_code=200, content=b"")
        receipt = self._confirm().data["data"]
        read = query.return_value.get_trace_detail
        read.side_effect = [
            {"meta": {"complete": True, "expectedSpanCount": receipt["span_count"]}},
            {"spans": []},
        ]

        later = timezone.now() + timedelta(minutes=1)
        with patch("django.utils.timezone.now", return_value=later):
            self.assertEqual(self._confirm().data["data"], receipt)

        self.assertEqual(send.call_count, 2)
        self.assertEqual(
            read.call_args.kwargs["trace_id"], receipt["comparison_trace_id"]
        )
        self.assertEqual(
            send.call_args_list[0].kwargs["data"], send.call_args.kwargs["data"]
        )

    @patch("apps.core.views.onboarding_demo.get_default_trace_query_service")
    @patch("apps.core.views.langfuse_ingest.requests.post")
    @patch("apps.core.views.onboarding_demo.remote")
    def test_unreadable_memory_scene_retries_the_same_import(
        self, remote, send, query
    ):
        remote.side_effect = self._builtin_response
        send.return_value = Mock(status_code=200, content=b"")
        receipt = self._confirm().data["data"]
        targets = {
            receipt["trace_id"]: receipt["span_count"],
            receipt["comparison_trace_id"]: receipt["comparison_span_count"],
            **{
                scene["trace_id"]: scene["span_count"]
                for scene in receipt["scenarios"].values()
            },
        }
        for turn in receipt["scenarios"]["session"]["turns"]:
            targets[turn["trace_id"]] = turn["span_count"]
        memory_trace_id = receipt["scenarios"]["memory"]["trace_id"]

        def read(*, trace_id, **kwargs):
            if trace_id == memory_trace_id:
                return {"spans": []}
            return {"meta": {"complete": True, "expectedSpanCount": targets[trace_id]}}

        query_detail = query.return_value.get_trace_detail
        query_detail.side_effect = read
        later = timezone.now() + timedelta(minutes=1)
        with patch("django.utils.timezone.now", return_value=later):
            self.assertEqual(self._confirm().data["data"], receipt)
        self.assertEqual(send.call_count, 2)
        self.assertEqual(query_detail.call_args.kwargs["trace_id"], memory_trace_id)
        self.assertEqual(
            send.call_args_list[0].kwargs["data"], send.call_args.kwargs["data"]
        )

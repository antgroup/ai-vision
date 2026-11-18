import json
from urllib.parse import parse_qs, urlparse
from unittest.mock import patch

from django.test import TestCase, override_settings
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.core.models import AgentTypeConfig, DataSource, Space
from apps.core.services.onboarding_demo import DEMO_METADATA_KEY, demo_agent_uuid
from apps.core.tests.helpers import (
    create_local_user, create_test_agent, create_test_space_member,
)
from apps.core.trace_query.service import TraceQueryService
from apps.core.trace_query.signoz.provider import SignozTraceProvider
from apps.core.utils.share_signing import (
    build_share_payload, parse_and_verify_share, sign_share_payload,
)
from apps.core.views.share import (
    GenerateShareLinkView, ShareDataView, ShareObservationView, ShareTraceDetailView,
)


@override_settings(SHARE_LINK_SIGNING_KEY="agent-scoped-share-test")
class AgentScopedShareTests(TestCase):
    def setUp(self):
        self.endpoint, self.api_key = "https://share-storage.invalid", "fixture"
        self.user = create_local_user("agent-share-owner")
        self.space = Space.objects.create(name="Shared", type=2, creator=self.user)
        create_test_space_member(space=self.space, user=self.user, role=1)
        self.agent_type = AgentTypeConfig.objects.create(
            agent_name="share-framework", agent_product="share-framework", status=1,
        )
        self.a = create_test_agent(
            space=self.space, agent_type=self.agent_type,
            agent_product="share-framework", name="A",
        )
        self.b = create_test_agent(
            space=self.space, agent_type=self.agent_type,
            agent_product="share-framework", name="B",
        )
        self.details = [self.detail("trace-A", self.a), self.detail("trace-B", self.b)]
        self.service = TraceQueryService()
        self.service.register_provider(
            "signoz", SignozTraceProvider(), source_systems=("signoz",),
        )
        self.factory = APIRequestFactory()

    def detail(self, trace_id, agent):
        return {"traceId": trace_id, "spans": [{
            "trace_id": trace_id, "span_id": trace_id + "-root", "name": agent.name,
            "start_time_unix_nano": "1790985600000000000",
            "end_time_unix_nano": "1790985601000000000",
            "resource": {
                "aivision.space.id": self.space.uuid, "aivision.agent.id": agent.uuid,
            },
            "attributes": {
                "langfuse.observation.type": "GENERATION",
                "langfuse.session.id": "same-session",
                "gen_ai.usage.input_tokens": 10, "gen_ai.usage.output_tokens": 2,
                "langfuse.observation.output": json.dumps(agent.name + " private answer"),
            },
        }]}

    def get(self, path, params):
        # Fake only the external storage HTTP boundary; query and signing stay real.
        if path == "/api/v1/ai-vision/traces/page" or path.startswith("/api/v1/ai-vision/traces/read-model/"):
            # This fixture exposes legacy list/detail storage, so exercise the
            # real provider's explicit capability fallback for share reads.
            return {"supported": False, "reason": "Legacy share-test storage"}
        filters = json.loads(params.get("filters", "[]"))
        selector = next((f["value"] for f in filters
                         if f["field"] == "resource.attributes.aivision.agent.id"), None)
        selected = [d for d in self.details if selector is None or
                    d["spans"][0]["resource"]["aivision.agent.id"] == selector]
        if path == "/api/v1/ai-vision/traces":
            return {
                "data": [{"trace_id": d["traceId"], "span_count": 1} for d in selected],
                "meta": {"page": int(params.get("page", 1)),
                         "limit": int(params.get("limit", 200)),
                         "totalItems": len(selected), "totalPages": 1 if selected else 0},
            }
        identifier = path.rsplit("/", 1)[1]
        return next((d for d in selected if d["traceId"] == identifier), {})

    def generate(self, **kwargs):
        body = {
            "resource_type": "session", "resource_id": "same-session",
            "agent_product": "share-framework", "protocol": "signoz",
            "from": "1790985600000", "to": "1791072000000", **kwargs,
        }
        request = self.factory.post("/share-links/generate", body, format="json")
        force_authenticate(request, user=self.user)
        return GenerateShareLinkView.as_view()(request, space_id=self.space.uuid)

    def link(self, response):
        self.assertEqual(response.status_code, 200, response.data)
        return {key: values[0] for key, values in
                parse_qs(urlparse(response.data["data"]["url"]).query).items()}

    def read(self, query, view=ShareDataView, **kwargs):
        with (
            patch.object(SignozTraceProvider, "_context", return_value=(
                self, {"agent_product_filter": "share-framework"},
            )),
            patch("apps.core.views.share.get_default_trace_query_service",
                  return_value=self.service),
        ):
            return view.as_view()(self.factory.get("/share/data", query), **kwargs)

    def test_selected_agent_share_preserves_session_io_and_usage_scope(self):
        query = self.link(self.generate(agent_uuid=self.a.uuid))
        # An unsigned URL selector must never override the signed selection.
        response = self.read({**query, "agent_uuid": self.b.uuid})
        self.assertEqual(response.status_code, 200, response.data)
        session = response.data["data"]["session"]
        self.assertEqual(session["traceIds"], ["trace-A"])
        self.assertEqual([item["outputValue"] for item in session["io"]],
                         ["A private answer"])
        self.assertEqual(session["usage"]["totalTokens"], 12)

    def test_only_the_signed_native_demo_agent_marks_public_share_usage_simulated(self):
        marker = {DEMO_METADATA_KEY: {"sample_digest": "example-digest"}}
        demo = create_test_agent(
            uuid=demo_agent_uuid(self.space.uuid), space=self.space,
            agent_type=self.agent_type, agent_product="share-framework",
            name="Example", metadata=marker,
        )
        self.details.append(self.detail("trace-demo", demo))
        self.a.metadata = marker
        self.a.save(update_fields=["metadata"])

        for resource_type, resource_id in (
            ("trace", "trace-demo"),
            ("session", "same-session"),
        ):
            with self.subTest(resource_type=resource_type):
                query = self.link(self.generate(
                    agent_uuid=demo.uuid, resource_type=resource_type,
                    resource_id=resource_id,
                ))
                response = self.read({**query, "agent_uuid": self.a.uuid})
                self.assertEqual(response.status_code, 200, response.data)
                self.assertIs(response.data["data"]["simulated_usage"], True)

        query = self.link(self.generate(agent_uuid=self.a.uuid))
        response = self.read({
            **query, "agent_uuid": demo.uuid, "simulated_usage": "true",
        })
        self.assertEqual(response.status_code, 200, response.data)
        self.assertNotIn("simulated_usage", response.data["data"])

    def test_drilldown_cannot_read_other_agent_with_same_session_id(self):
        query = self.link(self.generate(agent_uuid=self.a.uuid))
        response = self.read(query, ShareTraceDetailView, trace_id="trace-B")
        self.assertEqual(response.data["code"], 3025)
        allowed = self.read(query, ShareTraceDetailView, trace_id="trace-A")
        self.assertEqual(allowed.status_code, 200, allowed.data)
        self.assertEqual(allowed.data["data"]["traceId"], "trace-A")

    def test_trace_share_observation_read_uses_signed_agent(self):
        query = self.link(self.generate(
            agent_uuid=self.a.uuid, resource_type="trace", resource_id="trace-A",
        ))
        allowed = self.read(query, ShareObservationView, observation_id="trace-A-root")
        self.assertEqual(allowed.status_code, 200, allowed.data)
        rejected = self.read(query, ShareObservationView, observation_id="trace-B-root")
        self.assertEqual(rejected.data["code"], 3025)

    def test_foreign_disabled_or_product_mismatched_agent_cannot_be_signed(self):
        foreign_space = Space.objects.create(name="Other", type=2, creator=self.user)
        foreign = create_test_agent(
            space=foreign_space, agent_type=self.agent_type,
            agent_product="share-framework", name="Foreign",
        )
        self.b.status = 0
        self.b.save(update_fields=["status"])
        for params in (
            {"agent_uuid": foreign.uuid}, {"agent_uuid": self.b.uuid},
            {"agent_uuid": self.a.uuid, "agent_product": "another-product"},
        ):
            with self.subTest(params=params):
                response = self.generate(**params)
                self.assertNotEqual(response.status_code, 200)
                self.assertNotIn("url", response.data.get("data") or {})

    def test_unselected_multi_agent_share_requires_selection(self):
        response = self.generate()
        self.assertNotEqual(response.status_code, 200)

    def test_default_protocol_cannot_bypass_native_agent_selection(self):
        DataSource.objects.create(
            agent_type=self.agent_type,
            datasource_type="traces", source_system="signoz", enabled=1,
            is_default=1, config={"agent_product_filter": "share-framework"},
        )
        response = self.generate(protocol="lf")
        self.assertEqual(response.data["code"], 3027)
        self.b.status = 0
        self.b.save(update_fields=["status"])
        query = self.link(self.generate(protocol="lf"))
        claims = parse_and_verify_share(query["payload"], query["sig"])
        self.assertEqual(claims.get("au"), self.a.uuid)
        self.assertEqual(claims["p"], "signoz")
        self.b.status = 1
        self.b.save(update_fields=["status"])
        response = self.read(query)
        self.assertEqual(response.data["data"]["session"]["traceIds"], ["trace-A"])

    def test_single_agent_share_cannot_expand_after_another_instance_is_enabled(self):
        self.b.status = 0
        self.b.save(update_fields=["status"])
        query = self.link(self.generate())
        self.b.status = 1
        self.b.save(update_fields=["status"])
        response = self.read(query)
        self.assertEqual(response.data["data"]["session"]["traceIds"], ["trace-A"])

    def test_legacy_signed_product_scope_remains_readable(self):
        query = self.link(self.generate(agent_uuid=self.a.uuid))
        claims = parse_and_verify_share(query["payload"], query["sig"])
        claims.pop("au", None)
        payload = build_share_payload(claims)
        response = self.read({"payload": payload, "sig": sign_share_payload(payload)})
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["data"]["session"]["traceIds"],
                         ["trace-A", "trace-B"])

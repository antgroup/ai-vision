"""Versioned SDK metadata contracts, through the real OTLP normalizer."""

import gzip
import json

from django.test import SimpleTestCase
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import (
    ExportTraceServiceRequest,
)

from apps.core.trace_query.semantics.observations import (
    project_observation,
    project_trace,
)
from apps.core.views.langfuse_ingest import normalize_langfuse_otlp_payload


PREFIX = "aivision.otlp."
PROVENANCE = (PREFIX + "scope.name", PREFIX + "scope.version", PREFIX + "sdk.language")


def add_attribute(attributes, key, value):
    item = attributes.add(key=key)
    if isinstance(value, bool):
        item.value.bool_value = value
    elif isinstance(value, int):
        item.value.int_value = value
    else:
        item.value.string_value = value


def values(attributes):
    return {
        item.key: getattr(item.value, item.value.WhichOneof("value"))
        for item in attributes
    }


def project_proto(span):
    return {
        "trace_id": span.trace_id.hex(),
        "span_id": span.span_id.hex(),
        "parent_span_id": span.parent_span_id.hex(),
        "name": span.name,
        "start_time_unix_nano": span.start_time_unix_nano,
        "end_time_unix_nano": span.end_time_unix_nano,
        "attributes": values(span.attributes),
    }


class LangfuseSdkEncodingTests(SimpleTestCase):
    def payload(
        self, language="python", version="5.0.2", scope="langfuse-sdk", metadata=None
    ):
        request = ExportTraceServiceRequest()
        resource = request.resource_spans.add()
        add_attribute(resource.resource.attributes, "telemetry.sdk.language", language)
        # OTel's version must never be interpreted as the Langfuse SDK version.
        add_attribute(resource.resource.attributes, "telemetry.sdk.version", "99.0.0")
        scoped = resource.scope_spans.add()
        scoped.scope.name = scope
        scoped.scope.version = version
        span = scoped.spans.add(
            trace_id=bytes.fromhex("ab" * 16),
            span_id=bytes.fromhex("cd" * 8),
            name="sdk-contract",
            start_time_unix_nano=1780000000000000001,
            end_time_unix_nano=1780000000000000009,
        )
        for prefix in ("langfuse.trace.metadata", "langfuse.observation.metadata"):
            for key, value in (metadata or {}).items():
                add_attribute(span.attributes, prefix + "." + key, value)
        return request

    def normalize(self, request, encoding="identity"):
        body = request.SerializeToString()
        if encoding == "gzip":
            body = gzip.compress(body)
        output = normalize_langfuse_otlp_payload(
            body, encoding, agent_product="langfuse"
        )
        if encoding == "gzip":
            output = gzip.decompress(output)
        return ExportTraceServiceRequest.FromString(output)

    def test_connection_metadata_marks_only_the_model_call_and_preserves_usage(self):
        for language, version, encode in (
            ("python", "4.16.0", str),
            ("python", "5.0.2", json.dumps),
            ("nodejs", "6.0.0", json.dumps),
        ):
            with self.subTest(language=language, version=version):
                request = self.payload(language, version)
                spans = request.resource_spans[0].scope_spans[0].spans
                root = spans[0]
                add_attribute(root.attributes, "langfuse.observation.type", "agent")
                mode_key = "langfuse.observation.metadata.aivisionBillingMode"
                provider_key = "langfuse.observation.metadata.aivisionBillingProvider"
                add_attribute(root.attributes, mode_key, encode("subscription"))
                for i in range(1, 4):
                    span = spans.add()
                    span.CopyFrom(root)
                    span.ClearField("attributes")
                    span.span_id = bytes([i]) * 8
                    span.parent_span_id = root.span_id
                    add_attribute(span.attributes, "langfuse.observation.type", "generation")
                    add_attribute(span.attributes, "langfuse.observation.model.name", "deepseek-v4-pro-0813")
                    add_attribute(
                        span.attributes, "langfuse.observation.usage_details",
                        '{"input":218,"output":5,"total":223}',
                    )
                    add_attribute(span.attributes, "langfuse.observation.cost_details", '{"total":0.125}')
                for span in spans[1:3]:
                    add_attribute(span.attributes, mode_key, encode("subscription"))
                    add_attribute(span.attributes, provider_key, encode("baidu"))
                add_attribute(spans[2].attributes, "aivision.billing.mode", "pay_as_you_go")
                add_attribute(spans[2].attributes, "gen_ai.provider.name", "direct-vendor")

                parsed = self.normalize(request, "gzip")
                rows = [project_proto(span) for span in parsed.resource_spans[0].scope_spans[0].spans]
                self.assertNotIn("aivision.billing.mode", rows[0]["attributes"])
                self.assertNotIn("aivision.billing.mode", rows[3]["attributes"])
                self.assertEqual(rows[1]["attributes"][mode_key], encode("subscription"))
                observation = project_observation(rows[1], project_id="p")
                cost = observation["usage"]["cost"]
                self.assertEqual(observation["provider"], "baidu")
                self.assertEqual(observation["usage"]["total"], 223)
                self.assertEqual(cost["reason"], "subscription_billing")
                self.assertIsNone(cost["total"])
                self.assertEqual(cost["reportedCost"]["total"], "0.125")
                explicit = project_observation(rows[2], project_id="p")
                self.assertEqual(explicit["provider"], "direct-vendor")
                self.assertEqual(explicit["usage"]["cost"]["total"], "0.125")

    def test_non_string_billing_metadata_does_not_reject_the_batch(self):
        nested = '[' * 1600 + '0' + ']' * 1600
        request = self.payload(metadata={"aivisionBillingMode": nested})
        spans = request.resource_spans[0].scope_spans[0].spans
        add_attribute(spans[0].attributes, "langfuse.observation.type", "generation")
        sibling = spans.add(span_id=bytes.fromhex("ef" * 8))
        add_attribute(sibling.attributes, "langfuse.observation.type", "generation")
        add_attribute(sibling.attributes, "langfuse.observation.metadata.aivisionBillingMode", '"subscription"')

        parsed = self.normalize(request)
        malformed, valid = parsed.resource_spans[0].scope_spans[0].spans
        malformed_attrs = values(malformed.attributes)
        self.assertNotIn("aivision.billing.mode", malformed_attrs)
        self.assertEqual(malformed_attrs["langfuse.observation.metadata.aivisionBillingMode"], nested)
        self.assertEqual(values(valid.attributes)["aivision.billing.mode"], "subscription")

    def test_new_sdk_metadata_decodes_once_with_language_specific_version_boundary(
        self,
    ):
        cases = {
            "numeric_string": "123",
            "boolean_string": "false",
            "null_string": "null",
            "quoted": '"quoted"',
            "json_string": '{"a":1}',
            "number": 123,
            "boolean": False,
            "array": [1, "2"],
            "object": {"a": "1"},
            "empty": "",
            "unicode": "你好",
            "null": None,
        }
        raw = {
            key: json.dumps(value, ensure_ascii=False) for key, value in cases.items()
        }
        for language, version in (
            ("python", "5.0.2"),
            ("javascript", "6.0.0"),
            ("nodejs", "6.0.0"),
        ):
            for encoding in ("identity", "gzip"):
                with self.subTest(
                    language=language, version=version, encoding=encoding
                ):
                    parsed = self.normalize(
                        self.payload(language, version, metadata=raw), encoding
                    )
                    row = project_proto(
                        parsed.resource_spans[0].scope_spans[0].spans[0]
                    )
                    observation = project_observation(row, project_id="p")
                    trace = project_trace(
                        {"traceId": row["trace_id"], "spans": [row]}, project_id="p"
                    )
                    for result in (observation, trace):
                        self.assertEqual(result["metadata"], cases)
                        for key, value in cases.items():
                            self.assertIs(type(result["metadata"][key]), type(value))
                    self.assertEqual(row["attributes"][PROVENANCE[0]], "langfuse-sdk")
                    self.assertEqual(row["attributes"][PROVENANCE[1]], version)
                    self.assertEqual(row["attributes"][PROVENANCE[2]], language)
                    for key, value in raw.items():
                        self.assertEqual(
                            observation["attributes"][
                                "langfuse.observation.metadata." + key
                            ],
                            value,
                        )

    def test_known_old_sdks_keep_ambiguous_metadata_strings(self):
        # Old SDKs encode 123 and "123" identically. Recovering the original type
        # is impossible, so retain their actual OTLP value instead of guessing.
        raw = {
            "number": "123",
            "boolean": "false",
            "null": "null",
            "array": "[1,2]",
            "object": '{"x":1}',
            "quoted": '"hello"',
        }
        for language, version in (
            ("python", "3.15.0"),
            ("python", "4.16.0"),
            ("javascript", "5.14.0"),
            ("nodejs", "5.14.0"),
        ):
            with self.subTest(language=language, version=version):
                parsed = self.normalize(self.payload(language, version, metadata=raw))
                row = project_proto(parsed.resource_spans[0].scope_spans[0].spans[0])
                self.assertEqual(
                    project_observation(row, project_id="p")["metadata"], raw
                )
                self.assertEqual(
                    project_trace(
                        {"traceId": row["trace_id"], "spans": [row]}, project_id="p"
                    )["metadata"],
                    raw,
                )

    def test_historical_or_unknown_provenance_preserves_existing_projection(self):
        for language, version, scope in (
            ("", "", ""),
            ("python", "", "langfuse-sdk"),
            ("python", "invalid", "langfuse-sdk"),
            ("python", "9" * 5000 + ".0.0", "langfuse-sdk"),
            ("python", "4.16.0", "other-library"),
            ("unknown", "4.16.0", "langfuse-sdk"),
        ):
            with self.subTest(language=language, version=version, scope=scope):
                parsed = self.normalize(
                    self.payload(language, version, scope, {"value": "123"})
                )
                row = project_proto(parsed.resource_spans[0].scope_spans[0].spans[0])
                self.assertEqual(
                    project_observation(row, project_id="p")["metadata"], {"value": 123}
                )

    def test_client_cannot_override_scope_provenance_or_duplicate_it(self):
        request = self.payload("javascript", "5.14.0", metadata={"value": "123"})
        resource = request.resource_spans[0]
        span = resource.scope_spans[0].spans[0]
        for attributes in (resource.resource.attributes, span.attributes):
            for key in PROVENANCE:
                add_attribute(attributes, key, "forged")
                add_attribute(attributes, key, "forged-again")
        parsed = self.normalize(request)
        normalized_resource = parsed.resource_spans[0]
        normalized_span = normalized_resource.scope_spans[0].spans[0]
        for key, expected in zip(PROVENANCE, ("langfuse-sdk", "5.14.0", "javascript")):
            self.assertEqual(
                [
                    a.value.string_value
                    for a in normalized_span.attributes
                    if a.key == key
                ],
                [expected],
            )
            self.assertNotIn(key, values(normalized_resource.resource.attributes))
        second = self.normalize(parsed)
        self.assertEqual(second, parsed)

    def test_mixed_scope_versions_in_one_batch_are_decoded_per_span(self):
        request = self.payload("javascript", "5.14.0", metadata={"value": "123"})
        new_scope = request.resource_spans[0].scope_spans.add()
        new_scope.CopyFrom(
            self.payload("javascript", "6.0.0", metadata={"value": '"123"'})
            .resource_spans[0]
            .scope_spans[0]
        )
        parsed = self.normalize(request)
        for scoped in parsed.resource_spans[0].scope_spans:
            self.assertEqual(
                project_observation(project_proto(scoped.spans[0]), project_id="p")[
                    "metadata"
                ],
                {"value": "123"},
            )

    def test_missing_envelope_provenance_cannot_be_forged_in_attributes(self):
        request = self.payload("", "", "", {"value": "123"})
        resource = request.resource_spans[0]
        span = resource.scope_spans[0].spans[0]
        for attributes in (resource.resource.attributes, span.attributes):
            for key, value in zip(PROVENANCE, ("langfuse-sdk", "4.16.0", "python")):
                add_attribute(attributes, key, value)
        parsed = self.normalize(request)
        row = project_proto(parsed.resource_spans[0].scope_spans[0].spans[0])
        self.assertFalse(set(PROVENANCE) & row["attributes"].keys())
        self.assertEqual(
            project_observation(row, project_id="p")["metadata"], {"value": 123}
        )

    def test_malformed_historical_language_preserves_unknown_producer_behavior(self):
        for language in (["python"], {"name": "python"}):
            with self.subTest(language=language):
                raw = self.payload(metadata={"value": "123"})
                row = project_proto(raw.resource_spans[0].scope_spans[0].spans[0])
                row["attributes"].update(
                    dict(zip(PROVENANCE, ("langfuse-sdk", "5.0.2", language)))
                )
                self.assertEqual(
                    project_observation(row, project_id="p")["metadata"], {"value": 123}
                )

    def test_metadata_rules_do_not_change_io_or_trusted_agent_resource(self):
        request = self.payload("python", "5.0.2", metadata={"value": '"123"'})
        resource = request.resource_spans[0]
        span = resource.scope_spans[0].spans[0]
        for key, value in (
            ("langfuse.observation.type", "generation"),
            ("langfuse.observation.input", '{"question":"你好"}'),
            ("langfuse.observation.output", '"123"'),
        ):
            add_attribute(span.attributes, key, value)
        add_attribute(resource.resource.attributes, "ant.agent.product", "forged")
        parsed = self.normalize(request)
        row = project_proto(parsed.resource_spans[0].scope_spans[0].spans[0])
        result = project_observation(row, project_id="p")
        self.assertEqual(result["input"], {"question": "你好"})
        self.assertEqual(result["output"], "123")
        self.assertEqual(result["attributes"]["langfuse.observation.output"], '"123"')
        self.assertEqual(
            values(parsed.resource_spans[0].resource.attributes)["ant.agent.product"],
            "langfuse",
        )

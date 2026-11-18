"""Quick input purpose on the real native provider; storage is the external boundary."""

from copy import deepcopy
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

from apps.core.tests.test_native_query import trace
from apps.core.trace_query.signoz.provider import SignozTraceProvider
from apps.core.trace_query.types import TraceListQuery, TraceQueryServiceError


def output(index, name="diagnosis_record"):
    detail = trace(index)
    span = detail["spans"][0]
    span.update(
        name=name, service_name="agentlens", resource={"service.name": "agentlens"}
    )
    span["attributes"].update(
        {"record.body": "{}", "job_uuid": "job", "task_uuid": "task"}
    )
    span["start_time_unix_nano"] = str(1790999323000000000 + index)
    span["end_time_unix_nano"] = str(1790999323100000000 + index)
    return detail


class DiagnosisInputPurposeTests(TestCase):
    def query(self, **params):
        return TraceListQuery(SimpleNamespace(uuid="space", type=2), None, params)

    @patch.object(SignozTraceProvider, "collect_traces")
    def test_latest_outputs_are_removed_before_page_total_and_facets(self, collect):
        details = [trace(i) for i in range(12)] + [
            output(100 + i, "task_stats" if i % 2 else "diagnosis_record")
            for i in range(15)
        ]
        original = deepcopy(details)
        collect.return_value = {
            "data": details,
            "meta": {"totalItems": 27, "complete": True},
        }
        provider = SignozTraceProvider()
        first = provider.query_traces(
            self.query(purpose="diagnosis_input", limit=10, page=1)
        )
        second = provider.query_traces(
            self.query(purpose="diagnosis_input", limit=10, page=2)
        )
        self.assertEqual(first["meta"]["totalItems"], 12)
        self.assertEqual(len(first["data"]), 10)
        self.assertEqual(len(second["data"]), 2)
        self.assertEqual(
            {r["trace_id"] for r in first["data"] + second["data"]},
            {f"trace-{i:04d}" for i in range(12)},
        )
        from apps.core.trace_query.native_query import NativeTraceWindow

        self.assertEqual(
            first["facets"],
            NativeTraceWindow(details[:12], project_id="space").facets({}),
        )
        # This test compares the full-window diagnosis and raw contracts; the
        # storage pagination boundary has separate request/response tests.
        with (patch.object(provider, "_query_trace_page", return_value=None),
              patch("apps.core.trace_query.signoz.snapshot_window.query_snapshot_window", return_value=None)):
            raw = provider.query_traces(self.query(limit=10, page=1))
        self.assertEqual(raw["meta"]["totalItems"], 27)
        self.assertTrue(
            all(r["name"] in {"diagnosis_record", "task_stats"} for r in raw["data"])
        )
        self.assertEqual(details, original)

    @patch.object(SignozTraceProvider, "collect_traces")
    def test_same_name_partial_conflicting_and_mixed_business_traces_remain(
        self, collect
    ):
        details = []
        for index, control in enumerate(
            ("same_name", "other_service", "partial", "conflict", "mixed", "nonroot")
        ):
            item = output(index)
            span = item["spans"][0]
            if control == "same_name":
                span["attributes"] = {}
                span.pop("resource")
                span.pop("service_name")
            elif control == "other_service":
                span["service_name"] = "business"
                span["resource"]["service.name"] = "business"
            elif control == "partial":
                span["attributes"].pop("task_uuid")
            elif control == "conflict":
                span["service_name"] = "business"
            elif control == "mixed":
                child = deepcopy(span)
                child.update(
                    span_id="real-child",
                    parent_span_id=span["span_id"],
                    name="real tool",
                )
                item["spans"].append(child)
            else:
                span.update(name="execute_tool", parent_span_id="external-parent")
            details.append(item)
        collect.return_value = {"data": details, "meta": {"complete": True}}
        result = SignozTraceProvider().query_traces(
            self.query(purpose="diagnosis_input")
        )
        self.assertEqual(result["meta"]["totalItems"], 6)
        self.assertEqual(
            next(r for r in result["data"] if r["trace_id"] == "trace-0004")[
                "span_count"
            ],
            2,
        )

    @patch.object(SignozTraceProvider, "collect_traces")
    def test_invalid_purpose_is_native_400_before_storage(self, collect):
        from apps.core.views.trace_unified import _handle_error

        for value in (None, "", "all", True, [], {}):
            with (
                self.subTest(value=value),
                self.assertRaises(TraceQueryServiceError) as caught,
            ):
                SignozTraceProvider().query_traces(self.query(purpose=value))
            response = _handle_error(caught.exception, "trace list")
            self.assertEqual(response.status_code, 400)
            self.assertEqual(response.data["code"], 3027)
        collect.assert_not_called()

    @patch.object(SignozTraceProvider, "collect_traces")
    def test_http_body_purpose_reaches_native_provider_and_invalid_value_rejects(
        self, collect
    ):
        from unittest.mock import Mock

        from rest_framework.test import APIRequestFactory, force_authenticate

        from apps.core.views import trace_unified

        provider = SignozTraceProvider()
        service = Mock()
        service.list_traces.side_effect = lambda **kwargs: provider.query_traces(
            TraceListQuery(kwargs["space"], kwargs["user"], kwargs["query_params"])
        )
        collect.return_value = {
            "data": [output(100), trace(1)],
            "meta": {"complete": True},
        }
        space = SimpleNamespace(uuid="space", type=2)
        view = trace_unified.UnifiedTraceListView.as_view(source_system="signoz")
        with (
            patch.object(
                trace_unified, "_get_space_or_error", return_value=(space, None)
            ),
            patch.object(trace_unified, "_check_access", return_value=None),
            patch.object(
                trace_unified, "get_default_trace_query_service", return_value=service
            ),
        ):
            for purpose, status in (("diagnosis_input", 200), (None, 400)):
                request = APIRequestFactory().post(
                    "/api/v1/spaces/space/signoz/traces/list",
                    {"purpose": purpose},
                    format="json",
                )
                force_authenticate(request, user=SimpleNamespace(is_authenticated=True))
                response = view(request, space_id="space")
                self.assertEqual(response.status_code, status)
                if status == 200:
                    self.assertEqual(response.data["data"]["meta"]["totalItems"], 1)
                else:
                    self.assertEqual(response.data["code"], 3027)

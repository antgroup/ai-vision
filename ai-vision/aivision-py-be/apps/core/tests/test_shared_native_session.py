from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.test import SimpleTestCase
from rest_framework.test import APIRequestFactory

from apps.core.trace_query.types import TraceQueryServiceError
from apps.core.views.share import ShareDataView


class SharedNativeSessionTests(SimpleTestCase):
    def setUp(self):
        self.ids = [f"trace-{index:03}" for index in range(207)]
        self.space = SimpleNamespace(id=7, uuid="authorized-space")
        self.claims = {
            "s": 7,
            "t": "session",
            "id": "session-1",
            "a": "langfuse",
            "p": "signoz",
            "uid": "owner",
            "sub": "collector-subject",
            "u": "Owner",
            "iat": 1,
            "exp": 9999999999,
            "qf": "1790985600000",
            "qt": "1791072000000",
        }
        self.service = Mock()
        self.service.get_session_detail.side_effect = lambda **kwargs: {
            "sessionId": "session-1",
            "traceCount": len(self.ids),
            "traceIds": list(self.ids),
        }
        self.service.list_traces.side_effect = self.page
        self.service.get_trace_io_batch.side_effect = lambda **kwargs: {
            "data": [
                {
                    "id": identifier,
                    "input": None,
                    "output": "answer",
                    "inputSource": None,
                    "outputSource": {
                        "traceId": identifier,
                        "spanId": "root",
                        "rule": "logicalRoot",
                    },
                    "outputState": "available",
                }
                for identifier in kwargs["trace_ids"]
            ]
        }
        self.usage = {
            "id": "session-1",
            "inputTokens": 730,
            "outputTokens": 62,
            "totalTokens": 792,
            "usage": {
                "input": 730,
                "output": 62,
                "total": 792,
                "cacheReadInput": 256,
                "cost": {"total": "0.0051", "source": "reported"},
            },
        }
        self.service.get_session_usage_batch.return_value = {
            "data": [self.usage],
            "meta": {"complete": True},
        }

    def page(self, **kwargs):
        params = kwargs["query_params"]
        limit, page = params["limit"], params.get("page", 1)
        if limit > 200:
            raise TraceQueryServiceError("limit between 1 and 200", 3027)
        total = len(self.ids)
        return {
            "data": [
                {"trace_id": value}
                for value in self.ids[(page - 1) * limit : page * limit]
            ],
            "meta": {
                "page": page,
                "limit": limit,
                "totalItems": total,
                "totalPages": (total + limit - 1) // limit,
                "complete": True,
            },
        }

    def get(self):
        with (
            patch(
                "apps.core.views.share.parse_and_verify_share", return_value=self.claims
            ),
            patch("apps.core.views.share.Space.objects.get", return_value=self.space),
            patch(
                "apps.core.views.share.get_default_trace_query_service",
                return_value=self.service,
            ),
            patch("apps.core.views.share.log_share_event"),
        ):
            return ShareDataView.as_view()(
                APIRequestFactory().get("/api/v1/share/data")
            )

    def test_more_than_200_traces_are_complete_and_io_provenance_is_preserved(self):
        response = self.get()
        self.assertEqual(response.status_code, 200)
        session = response.data["data"]["session"]
        self.assertEqual([item["trace_id"] for item in session["traces"]], self.ids)
        self.assertEqual(len(session["io"]), 207)
        self.assertEqual(session["io"][-1]["outputSource"]["traceId"], self.ids[-1])
        self.assertEqual(
            [
                call.kwargs["query_params"]["page"]
                for call in self.service.list_traces.call_args_list
            ],
            [1, 2],
        )
        self.assertTrue(
            all(
                len(call.kwargs["trace_ids"]) <= 100
                for call in self.service.get_trace_io_batch.call_args_list
            )
        )
        for call in self.service.list_traces.call_args_list:
            self.assertEqual(call.kwargs["space"], self.space)
            self.assertEqual(call.kwargs["query_params"]["from"], self.claims["qf"])
            self.assertEqual(call.kwargs["query_params"]["to"], self.claims["qt"])

    def test_share_contains_same_unified_usage_item_as_authenticated_session(self):
        response = self.get()
        self.assertEqual(response.data["data"]["session"].get("usage"), self.usage)
        self.assertEqual(
            self.service.get_session_usage_batch.call_args.kwargs["session_ids"],
            ["session-1"],
        )

    def test_trace_query_errors_are_not_swallowed_into_empty_success(self):
        self.service.list_traces.side_effect = TraceQueryServiceError(
            "Incomplete trace scan", 3035
        )
        response = self.get()
        self.assertNotEqual(response.status_code, 200)
        self.assertEqual(response.data["code"], 3035)

    def test_missing_or_repeated_trace_rows_are_rejected(self):
        first = self.page(query_params={"limit": 200, "page": 1})
        second = self.page(query_params={"limit": 200, "page": 2})
        for broken in (
            {**second, "data": second["data"][:-1]},
            {**second, "data": [first["data"][0], *second["data"][1:]]},
        ):
            self.service.list_traces.side_effect = [first, broken]
            response = self.get()
            self.assertNotEqual(response.status_code, 200)
            self.assertEqual(response.data["code"], 3035)

    def test_io_query_errors_are_not_swallowed(self):
        self.service.get_trace_io_batch.side_effect = TraceQueryServiceError(
            "Storage unavailable", 3013
        )
        response = self.get()
        self.assertNotEqual(response.status_code, 200)
        self.assertEqual(response.data["code"], 3013)

    def test_usage_for_another_session_is_not_disclosed(self):
        self.service.get_session_usage_batch.return_value = {
            "data": [{**self.usage, "id": "other-session"}]
        }
        response = self.get()
        self.assertNotEqual(response.status_code, 200)
        self.assertEqual(response.data["code"], 3035)

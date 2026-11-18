"""Public trace identity uses SDK trace updates without changing auth scope."""

from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase

from apps.core.tests.test_native_query import trace
from apps.core.trace_query.native_query import NativeTraceWindow
from apps.core.trace_query.signoz.pagination import page_params
from apps.core.trace_query.signoz.provider import SignozTraceProvider
from apps.core.trace_query.types import TraceListQuery
from apps.core.views.trace_handlers import merge_session_list_params, merge_trace_list_params


class NativeUserIdentityTests(SimpleTestCase):
    def test_trace_username_shortcut_does_not_gain_a_legacy_raw_predicate(self):
        params = merge_trace_list_params({"username": "customer-42"}, {})
        self.assertNotIn("filters", params)
        self.assertEqual(merge_session_list_params({"username": "collector-subject"}, {})["filters"], [{
            "field": "attributes.ant.username", "op": "=", "value": "collector-subject",
        }])

    def test_sdk_user_aliases_are_queryable_and_replace_collector_facets(self):
        for key in ("gen_ai.user.id", "langfuse.user.id", "user.id"):
            with self.subTest(key=key):
                detail = trace(1)
                detail["spans"][0]["resource"] = {"ant.username": "collector-subject"}
                detail["spans"][0]["attributes"][key] = "customer-42"
                window = NativeTraceWindow([detail], project_id="space")
                result = window.list({"username": "customer-42", "fields": ["trace_id", "userId"]})
                self.assertEqual(result["data"], [{"trace_id": "trace-0001", "userId": "customer-42"}])
                self.assertEqual(window.facets({})["username"], [{"value": "customer-42", "count": 1}])
                self.assertEqual(window.list({"username": "collector-subject"})["meta"]["totalItems"], 0)
                # Raw telemetry remains inspectable through its legacy field.
                self.assertEqual(window.list({"filters": [{
                    "field": "attributes.ant.username", "value": "collector-subject",
                }]})["meta"]["totalItems"], 1)

    def test_username_follows_latest_trace_user_update_instead_of_any_child(self):
        detail = trace(1)
        root = detail["spans"][0]
        root["attributes"]["user.id"] = "initial-user"
        child = deepcopy(root)
        child.update(span_id="child", parent_span_id=root["span_id"],
                     end_time_unix_nano="1790989323200000000")
        child["attributes"] = {"langfuse.user.id": "updated-user"}
        detail["spans"].append(child)
        window = NativeTraceWindow([detail], project_id="space")
        self.assertEqual(window.facets({})["username"], [{"value": "updated-user", "count": 1}])
        self.assertEqual(window.list({"username": "initial-user"})["meta"]["totalItems"], 0)
        self.assertEqual(window.list({"username": "updated-user"})["meta"]["totalItems"], 1)

    def test_explicit_sdk_user_clear_does_not_reveal_collector_identity(self):
        for cleared in (None, ""):
            with self.subTest(cleared=cleared):
                detail = trace(1)
                root = detail["spans"][0]
                root["resource"] = {"ant.username": "collector-subject"}
                root["attributes"]["gen_ai.user.id"] = "initial-user"
                child = deepcopy(root)
                child.update(span_id="child", parent_span_id=root["span_id"],
                             end_time_unix_nano="1790989323200000000")
                child["attributes"] = {"langfuse.user.id": cleared}
                detail["spans"].append(child)
                window = NativeTraceWindow([detail], project_id="space")
                self.assertEqual(window.facets({})["username"], [])
                self.assertEqual(window.list({"fields": ["userId"]})["data"], [{"userId": cleared}])

    def test_legacy_subject_still_matches_without_a_public_sdk_user(self):
        for legacy in ("resource", "attributes"):
            with self.subTest(legacy=legacy):
                detail = trace(1)
                detail["spans"][0].setdefault(legacy, {})["ant.username"] = "legacy-user"
                window = NativeTraceWindow([detail], project_id="space")
                self.assertEqual(window.list({"username": "legacy-user"})["meta"]["totalItems"], 1)
                self.assertEqual(window.facets({})["username"], [{"value": "legacy-user", "count": 1}])

    def test_root_legacy_user_precedes_child_resource_without_changing_raw_alias(self):
        base = {
            "trace_id": "trace-1", "start_time_unix_nano": "1790989323000000000",
            "end_time_unix_nano": "1790989323100000000", "status": {"code": "1"},
        }
        detail = {"traceId": "trace-1", "spans": [
            {**base, "span_id": "root", "name": "task", "attributes": {"ant.username": "root-user"}},
            {**base, "span_id": "child", "parent_span_id": "root", "attributes": {},
             "resource": {"ant.username": "child-user"}},
        ]}
        window = NativeTraceWindow([detail], project_id="space")
        self.assertEqual(window.list({"fields": ["userId", "attributes.ant.username"]})["data"], [{
            "userId": "root-user", "attributes.ant.username": "child-user",
        }])
        self.assertEqual(window.list({"username": "root-user"})["meta"]["totalItems"], 1)
        self.assertEqual(window.list({"username": "child-user"})["meta"]["totalItems"], 0)
        self.assertEqual(window.facets({})["username"], [{"value": "root-user", "count": 1}])
        self.assertEqual(window.list({"filters": [{
            "field": "attributes.ant.username", "value": "child-user",
        }]})["meta"]["totalItems"], 1)

    def test_public_identity_requests_use_complete_window_not_legacy_storage_predicate(self):
        space = SimpleNamespace(uuid="space", type=2)
        for params in ({"username": "customer-42"}, {"filters": [{"field": "userId", "value": "customer-42"}]}):
            with self.subTest(params=params):
                self.assertIsNone(page_params(TraceListQuery(space, None, params)))

    def test_standalone_facets_identify_canonical_identity_contract(self):
        detail = trace(1)
        detail["spans"][0]["attributes"]["gen_ai.user.id"] = "customer-42"
        provider = SignozTraceProvider()
        window = NativeTraceWindow([detail], project_id="space")
        query = TraceListQuery(SimpleNamespace(uuid="space", type=2), None, {})
        with patch.object(provider, "_window", return_value=(window, {"complete": True})):
            result = provider.list_facets(query)
        self.assertIs(result["meta"].get("userIdentityFacetsComplete"), True)
        self.assertEqual(result["meta"].get("userIdentitySemantics"), "trace-sdk-user-id-v1")

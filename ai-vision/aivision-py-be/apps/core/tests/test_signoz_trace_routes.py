from django.test import SimpleTestCase
from django.urls import resolve

from apps.core.views.share import PROTOCOL_TO_SOURCE_SYSTEM


class SigNozTraceRouteResolutionTests(SimpleTestCase):
    SIGNOZ_ROUTES = {
        "/api/v1/spaces/space-1/signoz/traces/list": "u_signoz_trace_list",
        "/api/v1/spaces/space-1/signoz/traces/facets": "u_signoz_trace_facets",
        "/api/v1/spaces/space-1/signoz/traces/io": "u_signoz_trace_io",
        "/api/v1/spaces/space-1/signoz/traces/usage": "u_signoz_trace_usage",
        "/api/v1/spaces/space-1/signoz/sessions/list": "u_signoz_session_list",
        "/api/v1/spaces/space-1/signoz/sessions/io": "u_signoz_session_io_batch",
        "/api/v1/spaces/space-1/signoz/sessions/usage": "u_signoz_session_usage",
        "/api/v1/spaces/space-1/signoz/trace/trace-1/detail": "u_signoz_trace_detail",
        "/api/v1/spaces/space-1/signoz/trace/trace-1/spans/list": "u_signoz_span_list",
        "/api/v1/spaces/space-1/signoz/trace/trace-1/span/span-1/detail": "u_signoz_span_detail",
        "/api/v1/spaces/space-1/signoz/session/session-1/detail": "u_signoz_session_detail",
        "/api/v1/spaces/space-1/signoz/session/session-1/io": "u_signoz_session_io",
    }

    def assert_routes_pin_provider(self, routes, source_system):
        for route, expected_url_name in routes.items():
            with self.subTest(route=route):
                match = resolve(route)
                self.assertEqual(match.url_name, expected_url_name)
                self.assertEqual(
                    match.func.view_initkwargs.get("source_system"), source_system
                )

    def test_signoz_routes_pin_the_signoz_provider(self):
        self.assert_routes_pin_provider(self.SIGNOZ_ROUTES, "signoz")

    def test_signoz_share_links_preserve_the_provider_override(self):
        self.assertEqual(PROTOCOL_TO_SOURCE_SYSTEM["signoz"], "signoz")

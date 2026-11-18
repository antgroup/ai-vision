from django.test import SimpleTestCase
from django.urls import Resolver404, resolve, reverse


class CoreUrlRoutingTests(SimpleTestCase):
    def test_representative_routes_keep_their_public_names(self):
        routes = {
            "/openapi/v1/spaces/space-1/agents/codex/traces/list": (
                "openapi_trace_list"
            ),
            "/api/v1/spaces/space-1/lftraces/sessions/session-1": ("lf_session_detail"),
            "/api/v1/spaces/space-1/diagnose/stats": "diagnose_stats",
            "/assets/app.js": "cdn_assets",
            "/dashboard": "frontend",
        }

        for route, expected_name in routes.items():
            with self.subTest(route=route):
                self.assertEqual(resolve(route).url_name, expected_name)

    def test_api_prefixes_do_not_fall_back_to_the_spa(self):
        for route in ("/api/not-found", "/openapi/not-found"):
            with self.subTest(route=route):
                with self.assertRaises(Resolver404):
                    resolve(route)

    def test_route_names_remain_global_without_namespaces(self):
        self.assertEqual(reverse("health_check"), "/api/v1/health")

    def test_removed_internal_routes_do_not_resolve(self):
        for route in ("/api/v2/team-insight/me", "/api/v2/register-agent"):
            with self.subTest(route=route):
                with self.assertRaises(Resolver404):
                    resolve(route)

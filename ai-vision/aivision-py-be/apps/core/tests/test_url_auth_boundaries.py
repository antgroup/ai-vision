from django.test import Client, SimpleTestCase
from django.urls import URLPattern, URLResolver, get_resolver

from apps.core.authentication import (
    AgentIngestAuthentication,
    LocalSessionAuthentication,
    SpaceKeyAuthentication,
)
from apps.core.views.langfuse_public import NativeLangfuseAuthentication
from apps.core.views.sandbox_installation import (
    SandboxBootstrapAuthentication,
    SandboxEnrollmentAuthentication,
    SandboxProgressAuthentication,
)


PUBLIC_READONLY_METADATA_ROUTES = frozenset({"api/v1/version", "api/v1/releases"})
SANDBOX_INSTALLATION_MACHINE_ROUTES = {
    "api/public/sandbox-installations/<str:installation_uuid>/bootstrap": (
        SandboxBootstrapAuthentication,
    ),
    "api/public/sandbox-installations/<str:installation_uuid>/enroll": (
        SandboxEnrollmentAuthentication,
    ),
    "api/public/sandbox-installations/<str:installation_uuid>/events": (
        SandboxProgressAuthentication,
    ),
}


def _routes(patterns, prefix=""):
    for item in patterns:
        route = prefix + str(item.pattern)
        if isinstance(item, URLResolver):
            yield from _routes(item.url_patterns, route)
        elif isinstance(item, URLPattern):
            view_class = getattr(item.callback, "view_class", None)
            auth_classes = tuple(getattr(view_class, "authentication_classes", ()))
            yield route, auth_classes


class URLAuthenticationBoundaryTests(SimpleTestCase):
    def test_every_url_has_exactly_one_authentication_boundary(self):
        routes = list(_routes(get_resolver().url_patterns))
        self.assertTrue(routes)

        for route, auth_classes in routes:
            public_metadata = route in PUBLIC_READONLY_METADATA_ROUTES
            if public_metadata:
                client = Client(enforce_csrf_checks=True)
                for method in ("HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"):
                    with self.subTest(route=route, method=method):
                        response = client.generic(method, f"/{route}")
                        self.assertEqual(response.status_code, 405)
                        self.assertEqual(response["Allow"], "GET")
            if route == "api/internal/agentlens/ingest-tokens":
                # This service route validates AGENTLENS_TOKEN in the view.
                self.assertEqual(auth_classes, ())
                continue
            if route == "api/public/otel/v1/traces":
                # This protobuf endpoint validates AgentUUID:SpaceKey before
                # parsing/forwarding the payload, without DRF body parsers.
                self.assertEqual(auth_classes, ())
                continue
            if route == "api/public/trace-attachments":
                # Attachment writes validate AgentUUID:SpaceKey in the view,
                # before accepting multipart content or issuing an upstream call.
                self.assertEqual(auth_classes, ())
                for method in ("post", "delete"):
                    response = getattr(Client(), method)(f"/{route}")
                    self.assertEqual(response.status_code, 401)
                continue
            if route in {
                "openapi/v1/agents/<uuid:agent_uuid>/installation-complete",
                "openapi/v1/ingest-tokens",
            }:
                self.assertEqual(auth_classes, (AgentIngestAuthentication,))
                continue
            boundaries = {
                "sandbox_installation_machine": route
                in SANDBOX_INSTALLATION_MACHINE_ROUTES,
                "space_key": route.startswith("openapi/v1/")
                and route != "openapi/v1/agent-installations/exchange",
                "native_external_read": route.startswith(
                    (
                        "api/public/projects",
                        "api/public/sessions",
                        "api/public/traces",
                        "api/public/observations",
                    )
                ),
                "anonymous": public_metadata
                or route.startswith(
                    (
                        "api/v1/health",
                        "api/v1/ready",
                        "openapi/v1/agent-installations/exchange",
                        "api/v1/auth/csrf",
                        "api/v1/auth/register",
                        "api/v1/auth/login",
                        "api/v1/auth/refresh",
                        "api/v1/share/",
                        "api/v1/trace-link/",
                        "api/v1/observation-link/",
                        "api/public/onboarding/",
                        "assets/",
                        "^(?!api/|openapi/",
                    )
                ),
                "local_session": route.startswith(("api/v1/", "api/v2/", "project/")),
            }
            if boundaries["anonymous"]:
                boundaries["local_session"] = False
            matched = [name for name, applies in boundaries.items() if applies]
            self.assertEqual(len(matched), 1, (route, matched))

            expected = {
                "sandbox_installation_machine": SANDBOX_INSTALLATION_MACHINE_ROUTES.get(
                    route
                ),
                "space_key": (SpaceKeyAuthentication,),
                "native_external_read": (NativeLangfuseAuthentication,),
                "anonymous": (),
                "local_session": (LocalSessionAuthentication,),
            }[matched[0]]
            self.assertEqual(auth_classes, expected, route)

    def test_removed_internal_and_external_routes_are_absent(self):
        route_names = {route for route, _ in _routes(get_resolver().url_patterns)}
        forbidden_fragments = (
            "openapi/platform/",
            "api/v1/iam/",
            "api/v1/setup",
            "api/v2/register-agent",
            "api/v2/team-insight/",
            "api/v2/external/business-spaces",
            "aiv-panel/",
            "admin/",
        )
        for fragment in forbidden_fragments:
            self.assertFalse(
                any(route.startswith(fragment) for route in route_names),
                fragment,
            )

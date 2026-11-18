"""Project-level URL assembly."""

from django.db import DatabaseError, connection
from django.http import JsonResponse
from django.urls import include, path, re_path

from aivision_backend.build_info import version_info
from aivision_backend.releases import release_info
from apps.core.views import CdnAssetProxyView, FrontendIndexView
from apps.core.views.langfuse_public import NativeTraceURLView


def health_check(request):
    """Return the legacy unwrapped service-health response."""

    return JsonResponse({"status": "ok", "message": "AI Vision Backend is running"})


def readiness_check(request):
    """Confirm the application database is reachable without exposing errors."""
    try:
        if connection.vendor == "mysql":
            # Use the configured driver and credentials, but keep probe socket
            # limits independent of potentially long business queries.
            params = connection.get_connection_params()
            params.update(connection_timeout=1, read_timeout=1, write_timeout=1)
            with connection.wrap_database_errors:
                probe = connection.Database.connect(**params)
                try:
                    cursor = probe.cursor()
                    try:
                        cursor.execute("SELECT 1")
                        ready = cursor.fetchone() == (1,)
                    finally:
                        cursor.close()
                finally:
                    probe.close()
        else:
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1")
                ready = cursor.fetchone() == (1,)
    except DatabaseError:
        return JsonResponse({"status": "unavailable"}, status=503)
    return JsonResponse({"status": "ok" if ready else "unavailable"}, status=200 if ready else 503)


urlpatterns = [
    path(
        "project/<str:project_id>/traces/<str:trace_id>",
        NativeTraceURLView.as_view(),
        name="native_trace_url",
    ),
    path("openapi/", include("apps.core.urls.openapi")),
    path("api/v1/health", health_check, name="health_check"),
    path("api/v1/ready", readiness_check, name="readiness_check"),
    path("api/v1/version", version_info, name="version_info"),
    path("api/v1/releases", release_info, name="release_info"),
    path("api/", include("apps.core.urls.api")),
    # Frontend static resources are proxied through the application origin.
    path("assets/<path:asset_path>", CdnAssetProxyView.as_view(), name="cdn_assets"),
    # All non-API/Admin/static paths fall back to the SPA index.
    re_path(
        r"^(?!api/|openapi/|aiv-panel/|static/|assets/).*$",
        FrontendIndexView.as_view(),
        name="frontend",
    ),
]

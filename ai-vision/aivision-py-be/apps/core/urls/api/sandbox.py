"""Optional sandbox integrations, authenticated and authorized by Space."""

from django.urls import path

from apps.core.views.sandbox_installation import (
    SandboxBootstrapCapabilityView,
    SandboxBootstrapClaimView,
    SandboxBootstrapEnrollView,
    SandboxInstallationDetailView,
    SandboxInstallationEnableView,
    SandboxInstallationEventsView,
    SandboxInstallationListView,
)
from apps.core.views.sandbox_proxy import (
    SandboxBindingView,
    SandboxConnectionDetailView,
    SandboxConnectionListView,
    SandboxConnectionRuntimeView,
    SandboxConnectionTestView,
    SandboxProvidersView,
)

_base = "v1/spaces/<str:space_id>/integrations/"
urlpatterns = [
    path(_base + "sandbox-bootstrap", SandboxBootstrapCapabilityView.as_view()),
    path(
        _base + "sandbox-installation/<str:installation_uuid>/enable",
        SandboxInstallationEnableView.as_view(),
    ),
    path(
        "public/sandbox-installations/<str:installation_uuid>/bootstrap",
        SandboxBootstrapClaimView.as_view(),
    ),
    path(
        "public/sandbox-installations/<str:installation_uuid>/enroll",
        SandboxBootstrapEnrollView.as_view(),
    ),
    path(_base + "sandbox-installations", SandboxInstallationListView.as_view()),
    path(
        _base + "sandbox-installation/<str:installation_uuid>",
        SandboxInstallationDetailView.as_view(),
    ),
    path(
        "public/sandbox-installations/<str:installation_uuid>/events",
        SandboxInstallationEventsView.as_view(),
    ),
    path(_base + "sandbox-providers", SandboxProvidersView.as_view()),
    path(_base + "sandbox-connections", SandboxConnectionListView.as_view()),
    path(
        _base + "sandbox-connection/<str:connection_uuid>",
        SandboxConnectionDetailView.as_view(),
    ),
    path(
        _base + "sandbox-connection/<str:connection_uuid>/test",
        SandboxConnectionTestView.as_view(),
    ),
    path(
        _base + "sandbox-connection/<str:connection_uuid>/runtime",
        SandboxConnectionRuntimeView.as_view(),
    ),
    path(_base + "sandbox-binding", SandboxBindingView.as_view()),
]

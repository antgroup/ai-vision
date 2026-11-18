"""Primary V1 business and compatibility API routes."""

from django.urls import path

from apps.core.views import (
    AgentDashboardPanelListView,
    AgentDownloadLatestView,
    AgentListView,
    AgentTypeListView,
    ApiKeyListView,
    CacheInfoView,
    CredentialRegenerateView,
    CredentialRevealView,
    CurrentUserView,
    DashboardMetricsView,
    DashboardSummaryView,
    DataQueryView,
    GenerateShareLinkView,
    PlatformNotifyView,
    ProjectOnboardingCredentialView,
    PublicAgentDownloadLatestView,
    RecentAnomaliesView,
    RecentSessionsView,
    ShareDataView,
    ShareObservationView,
    ShareTraceDetailView,
    SpaceAgentDisableView,
    SpaceAgentEnableView,
    SpaceAgentMarkConnectedView,
    SpaceAgentsCreateView,
    SpaceAgentsView,
    SpaceDashboardPanelListView,
    SpaceDetailView,
    SpaceListView,
    SpaceMemberExitView,
    SpaceMemberRoleUpdateView,
    SpaceMembersView,
    SpaceOperationLogListView,
    SpacePermissionsView,
    SpaceSettingsView,
    TokenDistributionView,
    TopAgentsView,
)
from apps.core.views.agent_installation import AgentInstallationTokenView
from apps.core.views.ingest_tokens import SpaceKeyIngestTokenView
from apps.core.views.invitations import (
    InvitationAcceptView,
    InvitationCheckView,
    SpaceInvitationListCreateView,
    SpaceInvitationResendView,
    SpaceInvitationRevokeView,
)
from apps.core.views.langfuse_ingest import LangfuseOtelTraceView
from apps.core.views.langfuse_public import (
    NativePublicObservationDetailView,
    NativePublicObservationListView,
    NativePublicProjectsView,
    NativePublicSessionDetailView,
    NativePublicSessionListView,
    NativePublicTraceDetailView,
    NativePublicTraceListView,
)
from apps.core.views.local_auth import (
    CsrfView,
    LoginView,
    LogoutView,
    MeView,
    RefreshView,
    RegisterView,
)
from apps.core.views.onboarding import (
    AgentBinaryView,
    AgentInstallScriptView,
    ConnectScriptView,
    InstallScriptView,
    OnboardingSupportView,
    PluginArtifactView,
    PluginLatestVersionView,
)
from apps.core.views.onboarding_demo import OnboardingDemoView
from apps.core.views.space import SpaceOwnerTransferView
from apps.core.views.space_keys import SpaceKeyDetailView, SpaceKeyListCreateView
from apps.core.views.trace_attachment import TraceAttachmentUploadView, TraceAttachmentContentView, TraceAttachmentLimitsView

urlpatterns = [
    path("public/trace-attachments", TraceAttachmentUploadView.as_view(), name="trace_attachment_upload"),
    path("v1/trace-attachments/limits", TraceAttachmentLimitsView.as_view(), name="trace_attachment_limits"),
    path("v1/trace-attachments/<str:attachment_id>/content", TraceAttachmentContentView.as_view(), name="trace_attachment_content"),
    path(
        "public/projects",
        NativePublicProjectsView.as_view(),
        name="native_public_projects",
    ),
    path(
        "public/otel/v1/traces",
        LangfuseOtelTraceView.as_view(),
        name="langfuse_otel_traces",
    ),
    path(
        "public/onboarding/catalog.json",
        PublicAgentDownloadLatestView.as_view(),
        name="onboarding_catalog",
    ),
    path(
        "public/onboarding/support/<str:filename>",
        OnboardingSupportView.as_view(),
        name="onboarding_support",
    ),
    path(
        "public/onboarding/install-<slug:agent>.py",
        AgentInstallScriptView.as_view(),
        name="onboarding_agent_install",
    ),
    path(
        "public/onboarding/plugins/<slug:agent>/latest.txt",
        PluginLatestVersionView.as_view(),
        name="onboarding_plugin_latest",
    ),
    path(
        "public/onboarding/plugins/<slug:agent>/<str:version>/<str:filename>",
        PluginArtifactView.as_view(),
        name="onboarding_plugin_artifact",
    ),
    path("v1/auth/csrf", CsrfView.as_view(), name="local_auth_csrf"),
    path("v1/auth/register", RegisterView.as_view(), name="local_register"),
    path("v1/auth/login", LoginView.as_view(), name="local_login"),
    path("v1/auth/refresh", RefreshView.as_view(), name="local_refresh"),
    path("v1/auth/logout", LogoutView.as_view(), name="local_logout"),
    path("v1/auth/me", MeView.as_view(), name="local_me"),
    path(
        "v1/spaces/<str:space_id>/invitations",
        SpaceInvitationListCreateView.as_view(),
        name="space_invitations",
    ),
    path(
        "v1/spaces/<str:space_id>/invitations/<str:invitation_uuid>/revoke",
        SpaceInvitationRevokeView.as_view(),
        name="space_invitation_revoke",
    ),
    path(
        "v1/spaces/<str:space_id>/invitations/<str:invitation_uuid>/resend",
        SpaceInvitationResendView.as_view(),
        name="space_invitation_resend",
    ),
    path(
        "v1/invitations/check", InvitationCheckView.as_view(), name="invitation_check"
    ),
    path(
        "v1/invitations/accept",
        InvitationAcceptView.as_view(),
        name="invitation_accept",
    ),
    path("v1/system/cache-info", CacheInfoView.as_view(), name="cache_info"),
    path("v1/user/me", CurrentUserView.as_view(), name="current_user"),
    path(
        "v1/spaces/<str:space_id>/owner/transfer",
        SpaceOwnerTransferView.as_view(),
        name="space_owner_transfer",
    ),
    path("v1/spaces", SpaceListView.as_view(), name="space_list"),
    path("v1/spaces/<str:space_id>", SpaceDetailView.as_view(), name="space_detail"),
    path(
        "v1/spaces/<str:space_id>/permissions",
        SpacePermissionsView.as_view(),
        name="space_permissions",
    ),
    path(
        "v1/spaces/<str:space_id>/members",
        SpaceMembersView.as_view(),
        name="space_members",
    ),
    path(
        "v1/spaces/<str:space_id>/leave",
        SpaceMemberExitView.as_view(),
        name="space_member_exit",
    ),
    path(
        "v1/spaces/<str:space_id>/members/<str:user_uuid>",
        SpaceMemberRoleUpdateView.as_view(),
        name="space_member_role_update",
    ),
    path("v1/agent-types", AgentTypeListView.as_view(), name="agent_type_list"),
    path("v1/agents", AgentListView.as_view(), name="agent_list"),
    path("v1/apikeys", ApiKeyListView.as_view(), name="apikey_list"),
    path(
        "v1/dashboard/summary", DashboardSummaryView.as_view(), name="dashboard_summary"
    ),
    path(
        "v1/spaces/<str:space_id>/dashboard/metrics",
        DashboardMetricsView.as_view(),
        name="dashboard_metrics",
    ),
    path(
        "v1/spaces/<str:space_id>/dashboard/top-agents",
        TopAgentsView.as_view(),
        name="top_agents",
    ),
    path(
        "v1/spaces/<str:space_id>/dashboard/recent-anomalies",
        RecentAnomaliesView.as_view(),
        name="recent_anomalies",
    ),
    path(
        "v1/spaces/<str:space_id>/dashboard/token-distribution",
        TokenDistributionView.as_view(),
        name="token_distribution",
    ),
    path(
        "v1/spaces/<str:space_id>/recent-sessions",
        RecentSessionsView.as_view(),
        name="recent_sessions",
    ),
    path(
        "v1/spaces/<str:space_id>/settings",
        SpaceSettingsView.as_view(),
        name="space_settings",
    ),
    path(
        "v1/spaces/<str:space_id>/space-keys",
        SpaceKeyListCreateView.as_view(),
        name="space_key_list_create",
    ),
    path(
        "v1/spaces/<str:space_id>/space-keys/<int:key_id>",
        SpaceKeyDetailView.as_view(),
        name="space_key_detail",
    ),
    path(
        "v1/spaces/<str:space_id>/space-keys/<int:key_id>/ingest-token",
        SpaceKeyIngestTokenView.as_view(),
        name="space_key_ingest_token",
    ),
    # Public onboarding artifact distribution (private one-liner install).
    # Public onboarding assets; these never carry account credentials.
    path(
        "public/onboarding/install.sh",
        InstallScriptView.as_view(),
        name="onboarding_install_script",
    ),
    path(
        "public/onboarding/scripts/connect",
        ConnectScriptView.as_view(),
        name="onboarding_connect_script",
    ),
    path(
        "public/onboarding/artifacts/<str:artifact>",
        AgentBinaryView.as_view(),
        name="onboarding_artifact",
    ),
    path(
        "v1/spaces/<str:space_id>/agents",
        SpaceAgentsView.as_view(),
        name="space_agents",
    ),
    path(
        "v1/spaces/<str:space_id>/onboarding/demo",
        OnboardingDemoView.as_view(),
        name="onboarding_demo",
    ),
    path(
        "v1/spaces/<str:space_id>/agents/create",
        SpaceAgentsCreateView.as_view(),
        name="space_agents_create",
    ),
    path(
        "v1/spaces/<str:space_id>/agents/<uuid:agent_uuid>/installation-tokens",
        AgentInstallationTokenView.as_view(),
        name="agent_installation_token",
    ),
    path(
        "v1/spaces/<str:space_id>/agents/<str:agent_uuid>/disable",
        SpaceAgentDisableView.as_view(),
        name="space_agent_disable",
    ),
    path(
        "v1/spaces/<str:space_id>/agents/<str:agent_uuid>/enable",
        SpaceAgentEnableView.as_view(),
        name="space_agent_enable",
    ),
    path(
        "v1/spaces/<str:space_id>/agents/<str:agent_uuid>/mark-connected",
        SpaceAgentMarkConnectedView.as_view(),
        name="space_agent_mark_connected",
    ),
    path(
        "v1/spaces/<str:space_id>/agents/<str:agent_uuid>/onboarding-credential",
        ProjectOnboardingCredentialView.as_view(),
        name="space_agent_onboarding_credential",
    ),
    path(
        "v1/spaces/<str:space_id>/share-links/generate",
        GenerateShareLinkView.as_view(),
        name="share_link_generate",
    ),
    path("v1/share/data", ShareDataView.as_view(), name="share_data"),
    path(
        "v1/share/observations/<str:observation_id>",
        ShareObservationView.as_view(),
        name="share_observation",
    ),
    path(
        "v1/share/trace/<str:trace_id>",
        ShareTraceDetailView.as_view(),
        name="share_trace_detail",
    ),
    path(
        "v1/spaces/<str:space_id>/credentials/<int:credential_id>/reveal",
        CredentialRevealView.as_view(),
        name="credential_reveal",
    ),
    path(
        "v1/spaces/<str:space_id>/credentials/<int:credential_id>/regenerate",
        CredentialRegenerateView.as_view(),
        name="credential_regenerate",
    ),
    path(
        "v1/spaces/<str:space_id>/operation-logs",
        SpaceOperationLogListView.as_view(),
        name="space_operation_logs",
    ),
    # External compatible API (no {code, message, data} wrapping).
    path(
        "public/sessions",
        NativePublicSessionListView.as_view(),
        name="public_session_list",
    ),
    path(
        "public/sessions/<str:session_id>",
        NativePublicSessionDetailView.as_view(),
        name="public_session_detail",
    ),
    path(
        "public/traces", NativePublicTraceListView.as_view(), name="public_trace_list"
    ),
    path(
        "public/traces/<str:trace_id>",
        NativePublicTraceDetailView.as_view(),
        name="public_trace_detail",
    ),
    path(
        "public/observations",
        NativePublicObservationListView.as_view(),
        name="public_observation_list",
    ),
    path(
        "public/observations/<str:observation_id>",
        NativePublicObservationDetailView.as_view(),
        name="public_observation_detail",
    ),
    path("v1/spaces/<str:space_id>/query", DataQueryView.as_view(), name="data_query"),
    path(
        "v1/agent-types/<str:agent_name>/panels",
        AgentDashboardPanelListView.as_view(),
        name="agent_dashboard_panels",
    ),
    path(
        "v1/spaces/<str:space_id>/dashboard/panels",
        SpaceDashboardPanelListView.as_view(),
        name="space_dashboard_panels",
    ),
    path("v1/platform/notify", PlatformNotifyView.as_view(), name="platform_notify"),
    path(
        "v1/download/latest",
        AgentDownloadLatestView.as_view(),
        name="agent_download_latest",
    ),
]

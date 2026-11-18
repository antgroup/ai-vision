"""
views 子包 - 按场景拆分的视图模块

所有公开符号在此重新导出，保持 `from apps.core.views import X` 的兼容性。
"""

# common
from .common import (
    SpaceObservationCredentialDecryptionError,
    StandardPagination,
    success_response,
    error_response,
    get_active_observation_endpoint,
    check_space_access,
    check_space_access_or_error,
    get_or_create_personal_space,
    build_auth_callback_url,
)

# user
from .user import (
    HealthView,
    CacheInfoView,
    CurrentUserView,
)

# space
from .space import (
    SpaceListView,
    SpaceDetailView,
    SpacePermissionsView,
    SpaceMembersView,
    SpaceMemberRoleUpdateView,
    SpaceMemberExitView,
    SpaceSettingsView,
)

# agent
from .agent import (
    AgentTypeListView,
    AgentListView,
    ApiKeyListView,
    ProjectOnboardingCredentialView,
    SpaceAgentsView,
    SpaceAgentsCreateView,
    SpaceAgentDisableView,
    SpaceAgentEnableView,
    SpaceAgentMarkConnectedView,
)

# dashboard
from .dashboard import (
    DashboardSummaryView,
    DashboardMetricsView,
    TopAgentsView,
    RecentAnomaliesView,
    TokenDistributionView,
    RecentSessionsView,
    get_time_range_filter,
)

# langfuse
from .langfuse import (
    get_langfuse_config,
    get_langfuse_auth_header,
    get_langfuse_debug_headers,
    decode_base64url,
    parse_trace_link_payload,
    validate_trace_link_claims,
    get_space_read_credential,
    verify_trace_link_signature,
    resolve_trace_link_request,
    build_trace_link_query_params,
    build_langfuse_user_trace_filter,
    get_space_observation_runtime_config,
    build_langfuse_trace_filter,
    resolve_space_agent_name,
    get_langfuse_traces_page_filtered,
    get_langfuse_traces_page,
    get_langfuse_trace_detail,
    extract_usage_details,
    sum_usage_details_by_keyword,
    get_trace_token_metrics,
    normalize_trace_usage_metrics,
    get_parent_observation,
    get_trace_token_metrics_from_observation,
    aggregate_token_metrics_from_observations,
    enrich_traces_with_detail_usage,
    get_trace_total_tokens,
    parse_langfuse_timestamp,
    build_session_summary_from_traces,
    get_user_session_summaries,
    get_user_session_traces,
    get_space_session_summaries,
    get_space_session_traces,
    TRACE_LINK_MAX_TTL_SECONDS,
)

# trace
from .trace import (
    SessionListView,
    SessionDetailView,
    TraceListView,
    TraceIOBatchView,
    TraceUsageBatchView,
    TraceDetailView,
    TraceLinkDetailView,
    TraceLinkObservationDetailView,
    ObservationLinkDetailView,
    ObservationDetailView,
)

# credential
from .credential import (
    CredentialRevealView,
    CredentialRegenerateView,
)

# operation log
from .operation_log import (
    SpaceOperationLogListView,
)

# observation (外部兼容 API)
from .observation import (
    PublicAPIView,
    PublicSessionDetailView,
    PublicSessionListView,
    PublicTraceListView,
    PublicTraceDetailView,
    PublicObservationListView,
    PublicObservationDetailView,
)

# query (查询适配器)
from .query import (
    DataQueryView,
    AgentDashboardPanelListView,
    SpaceDashboardPanelListView,
    find_datasource_for_item,
    parse_time_range,
    resolve_time_window,
)

# v2 query (Agent Dashboard V2 query)
from .v2_query import (
    AgentQueryConfigView,
    SpaceAgentQueryConfigView,
    DatasourceConfigView,
    SpaceDatasourceConfigView,
    AgentDashboardQueryView,
    AgentDashboardBatchQueryView,
    AgentDashboardSigNozQueryView,
    AgentDashboardSigNozBatchQueryView,
)

# v2 space (Agent Dashboard V2 space)
from .v2_space import (
    V2SpaceAgentsView,
)

# labs MCP
from .labs_mcp import (
    McpConfigDetailView,
    McpConfigListView,
    McpConfigToolsView,
)
from .labs_mcp import McpObservabilityView
from .labs_mcp_registry import McpRegistryDetailView, McpRegistrySearchView

# labs skill
from .labs_skill import (
    SkillConfigDetailView,
    SkillConfigListView,
)
from .labs_skill import SkillObservabilityView

# labs memory
from .labs_memory import MemoryObservationView

from .labs_skill_registry import (
    SkillRegistryFetchView,
    SkillRegistryResolveView,
    SkillRegistrySearchView,
)

# platform management OpenAPI

# platform
from .platform import (
    PlatformNotifyView,
)

# share
from .share import (
    GenerateShareLinkView,
    ShareDataView,
    ShareObservationView,
    ShareTraceDetailView,
)

# download
from .download import (
    AgentDownloadLatestView,
    PublicAgentDownloadLatestView,
)

# frontend (SPA + CDN asset proxy)
from .frontend import (
    FrontendIndexView,
    CdnAssetProxyView,
)

# agentlens proxy (诊断模块)
from .agentlens_proxy import (
    DiagnoseRecordListView,
    DiagnoseRecordDetailView,
    DiagnoseRecordFeedbackView,
    TraceProjectionView,
    DiagnosisStatsView,
    DiagnoserListView,
    DiagnoserCopyView,
    DiagnoserDetailView,
    ModelConnectionListView,
    ModelConnectionDiscoverView,
    ModelConnectionDetailView,
    ModelConnectionTestView,
    ModelConnectionValidateView,
    ModelConnectionReferencesView,
    DiagnoseDatasetDefaultSpecView,
    DiagnoseJobListView,
    DiagnoseJobDetailView,
    DiagnoseJobTriggerView,
    DiagnoseTaskListView,
    DiagnoseTaskDetailView,
    DiagnoseTaskCancelView,
    DiagnoseDatasetListView,
    DiagnoseDatasetDetailView,
    DiagnoseSystemHealthView,
    DiagnoseClusterInstancesView,
    DiagnoseClusterLeaderView,
    DiagnoserTraceSampleView,
    RuleTrialView,
    RuleInputPreviewView,
    LlmDiagnoserTrialView,
)

# 以下导入用于保持 @patch("apps.core.views.XXX") 的兼容性（测试文件依赖）
import requests as http_requests  # noqa: F401
from django.utils import timezone  # noqa: F401
from ..models import Space  # noqa: F401
from ..services.space_observation_provisioning import (  # noqa: F401
    provision_space_observation_project,
)
from ..services.trace_query_service import get_default_trace_query_service  # noqa: F401

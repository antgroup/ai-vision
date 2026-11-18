"""
models 子包 - 按功能模块拆分的数据模型

所有公开符号在此重新导出，保持 `from apps.core.models import X` 的兼容性。
"""

# user
from .user import (
    generate_uuid,
    User,
)

from .auth import AuthRefreshToken, AuthSession
from .agent_installation import AgentInstallation
from .sandbox_installation import SandboxInstallationSession

# space
from .space import (
    Space,
    SpaceMember,
    ApiKey,
)

# agent
from .agent import (
    AgentTypeConfig,
    Agent,
    DataSource,
    AgentDashboardPanel,
)

# platform
from .platform import (
    PlatformConfig,
    Session,
)

# observation
from .observation import (
    ObservationEndpointConfig,
    SpaceObservationProject,
    SpaceDatasourceBinding,
    SpaceObservationCredential,
)
from .observation_subject import ObservationSubjectBinding
from .invitation import SpaceInvitation
from .audit import AuthAuditLog

# operation log
from .operation_log import (
    SpaceOperationLog,
)

# space keys (external API auth)
from .space_keys import (
    SpaceKey,
)

# labs MCP
from .labs_mcp import McpServerConfig

# labs skill
from .labs_skill import SkillConfig

# platform control plane

from .diagnosis_quick_run import DiagnosisQuickRunRequest as DiagnosisQuickRunRequest

from .model_pricing import ModelPricingCatalogSnapshot, ModelPricingCatalogState, ModelPricingRule
from .model_exchange_rates import ModelExchangeRateSnapshot, ModelExchangeRateState

"""
admin 子包 - 按功能模块拆分的 Admin 配置

所有 Admin 类通过子模块的 @admin.register 自动注册。
此 __init__.py 负责导入所有子模块以触发注册，并重新导出公开符号。
"""

from .agent import (  # noqa: F401
    AgentAdmin,
    AgentDashboardPanelAdmin,
    AgentDashboardPanelAdminForm,
    AgentTypeConfigAdmin,
    AgentTypeConfigForm,
    DataSourceAdmin,
    DataSourceAdminForm,
)
from .observation import (  # noqa: F401
    ObservationEndpointConfigAdmin,
    SpaceDatasourceBindingAdmin,
    SpaceObservationCredentialAdmin,
    SpaceObservationCredentialInline,
    SpaceObservationProjectAdmin,
)
from .platform import PlatformConfigAdmin, SessionAdmin  # noqa: F401
from .space import (  # noqa: F401
    ApiKeyAdmin,
    SpaceAdmin,
    SpaceMemberAdmin,
    SpaceMemberInline,
)
from .user import (  # noqa: F401
    UserAdmin,
    get_active_observation_endpoints,
)

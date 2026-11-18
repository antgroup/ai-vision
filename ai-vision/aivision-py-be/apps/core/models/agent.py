from django.db import models

from .space import Space
from .user import generate_uuid


class AgentTypeConfigQuerySet(models.QuerySet):
    def runtime(self):
        return self.filter(gmt_deleted__isnull=True)

    def shared_runtime(self):
        return self.runtime()

    def active_shared_runtime(self):
        return self.shared_runtime().filter(status=1)


class AgentTypeConfigManager(models.Manager):
    """Expose runtime queries without relying on Python-function inspection."""

    def get_queryset(self):
        return AgentTypeConfigQuerySet(self.model, using=self._db, hints=self._hints)

    def runtime(self):
        return self.get_queryset().runtime()

    def shared_runtime(self):
        return self.get_queryset().shared_runtime()

    def active_shared_runtime(self):
        return self.get_queryset().active_shared_runtime()


class AgentTypeConfig(models.Model):
    """Agent runtime configuration.

    `agent_product` 只作为 agent 唯一 id 使用，
    由于历史原因字段名称无法修改。云原生 trace/event SQL 过滤语义由
    DataSource.config["agent_product_filter"] 承载，禁止把 agent_product
    用于 SQL 过滤或比较。
    """

    CATEGORY_CHOICES = [
        ("BUILT_IN", "内置 Agent"),
        ("CUSTOM", "第三方Agent"),
    ]
    USER_MAPPING_CHOICES = [
        ("DIRECT", "Direct"),
        ("LOOKUP", "Lookup"),
        ("TRANSFORM", "Transform"),
    ]
    STATUS_CHOICES = [
        (0, "Disabled"),
        (1, "Enabled"),
    ]
    SETUP_MODE_CHOICES = [
        ("AUTO", "自动发现"),
        ("CLI", "CLI接入"),
        ("APIKEY", "APIKey接入"),
        ("LANGFUSE", "Langfuse接入"),
        ("OTHER", "其他方式"),
    ]
    DISPLAY_SCOPE_CHOICES = [
        ("PERSONAL", "个人空间"),
        ("BUSINESS", "业务空间"),
        ("ALL", "全部空间"),
        ("HIDDEN", "隐藏"),
    ]
    agent_name = models.CharField(max_length=64, unique=True)
    agent_product = models.CharField(
        max_length=128,
        db_comment="仅作为 agent 唯一 id；查询过滤用 DataSource.config['agent_product_filter']",
    )
    runtime_type = models.CharField(max_length=64, blank=True, default="")
    display_name = models.CharField(max_length=128, null=True, blank=True)
    display_scope = models.CharField(
        max_length=16, default="PERSONAL", choices=DISPLAY_SCOPE_CHOICES
    )
    category = models.CharField(
        max_length=32, default="BUILT_IN", choices=CATEGORY_CHOICES
    )
    setup_mode = models.CharField(
        max_length=16, default="AUTO", choices=SETUP_MODE_CHOICES
    )
    config_guide = models.JSONField(null=True, blank=True)
    icon = models.CharField(max_length=256, null=True, blank=True)
    description = models.CharField(max_length=512, null=True, blank=True)
    cli_command = models.TextField(null=True, blank=True)
    sort_order = models.IntegerField(default=0)
    query_config = models.JSONField(null=True, blank=True)
    user_mapping_type = models.CharField(
        max_length=32, default="DIRECT", choices=USER_MAPPING_CHOICES
    )
    capabilities = models.JSONField(default=dict, blank=True)
    status = models.SmallIntegerField(default=1, choices=STATUS_CHOICES)
    gmt_create = models.DateTimeField(auto_now_add=True)
    gmt_modified = models.DateTimeField(auto_now=True)
    gmt_deleted = models.DateTimeField(null=True, blank=True)

    objects = AgentTypeConfigManager()

    class Meta:
        db_table = "aivision_agent_type_config"
        ordering = ["sort_order", "agent_product"]

    def __str__(self):
        return self.agent_product


class Agent(models.Model):
    STATUS_CHOICES = [
        (0, "Disabled"),
        (1, "Normal"),
        (2, "Pending"),
    ]

    uuid = models.CharField(
        max_length=36, unique=True, editable=False, default=generate_uuid
    )
    space = models.ForeignKey(
        Space, on_delete=models.CASCADE, related_name="agents", db_constraint=False
    )
    api_key_id = models.BigIntegerField(
        db_column="api_key_id",
        null=True,
        blank=True,
        db_comment="已废弃的API Key ID",
    )
    agent_type = models.ForeignKey(
        AgentTypeConfig,
        on_delete=models.SET_NULL,
        related_name="agents",
        db_constraint=False,
        null=True,
        blank=True,
    )
    agent_product = models.CharField(max_length=64)
    observation_subject_binding = models.ForeignKey(
        "ObservationSubjectBinding",
        on_delete=models.PROTECT,
        related_name="agents",
        db_constraint=False,
    )
    name = models.CharField(max_length=128)
    custom_name = models.CharField(max_length=128, null=True, blank=True)
    version = models.CharField(max_length=64, null=True, blank=True)
    instance_id = models.CharField(max_length=128, null=True, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    status = models.SmallIntegerField(default=1, choices=STATUS_CHOICES)
    last_active_at = models.DateTimeField(null=True, blank=True)
    gmt_create = models.DateTimeField(auto_now_add=True)
    gmt_modified = models.DateTimeField(auto_now=True)
    gmt_deleted = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "aivision_agent"
        ordering = ["-gmt_create"]

    def __str__(self):
        return self.custom_name or self.name


class DataSource(models.Model):
    """Cloud-native data source bound to an AgentTypeConfig.

    `config["agent_product_filter"]` stores the real cloud-native product name used
    inside trace/event SQL filters (e.g. `resource.attributes.ant.agent.product = 'X'`).
    It is intentionally separate from `AgentTypeConfig.agent_product`, which is an
    identity key only.
    """

    DATASOURCE_TYPE_CHOICES = [
        ("metrics", "Metrics"),
        ("traces", "Traces"),
        ("logs", "Logs"),
    ]
    SOURCE_SYSTEM_CHOICES = [
        ("langfuse", "Langfuse"),
        ("signoz", "SigNoz"),
    ]
    STATUS_CHOICES = [
        (0, "Disabled"),
        (1, "Enabled"),
    ]

    agent_type = models.ForeignKey(
        AgentTypeConfig,
        on_delete=models.CASCADE,
        related_name="datasources",
        db_constraint=False,
    )
    datasource_type = models.CharField(max_length=32, choices=DATASOURCE_TYPE_CHOICES)
    source_system = models.CharField(max_length=32, choices=SOURCE_SYSTEM_CHOICES)
    enabled = models.SmallIntegerField(default=0, choices=STATUS_CHOICES)
    is_default = models.SmallIntegerField(default=0)
    config = models.JSONField(null=True, blank=True)
    gmt_create = models.DateTimeField(auto_now_add=True)
    gmt_modified = models.DateTimeField(auto_now=True)
    gmt_deleted = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "aivision_datasource"
        ordering = ["agent_type", "datasource_type"]
        unique_together = [["agent_type", "datasource_type", "source_system"]]

    def __str__(self):
        return f"{self.agent_type.agent_name} - {self.datasource_type} ({self.source_system})"


class AgentDashboardPanel(models.Model):
    """
    描述某个 AgentType 在前端页面上展示哪些数据面板。
    通过 data_item_key 关联到 DataSource.config.queries 中的查询定义。
    """

    PAGE_CHOICES = [
        ("overview", "概览页"),
        ("detail", "详情页"),
    ]
    PANEL_TYPE_CHOICES = [
        ("stat_card", "统计卡片"),
        ("timeseries_chart", "时间序列图"),
        ("table", "数据表格"),
        ("pie_chart", "饼图"),
        ("bar_chart", "柱状图"),
    ]
    STATUS_CHOICES = [
        (0, "Disabled"),
        (1, "Enabled"),
    ]

    agent_type = models.ForeignKey(
        AgentTypeConfig,
        on_delete=models.CASCADE,
        related_name="dashboard_panels",
        db_constraint=False,
    )
    page = models.CharField(max_length=32, choices=PAGE_CHOICES)
    group_name = models.CharField(max_length=64)
    panel_type = models.CharField(max_length=32, choices=PANEL_TYPE_CHOICES)
    title = models.CharField(max_length=128)
    description = models.CharField(max_length=256, null=True, blank=True)
    data_item_key = models.CharField(max_length=64)
    datasource_type = models.CharField(max_length=32)
    sort_order = models.IntegerField(default=0)
    grid_width = models.IntegerField(default=6)
    panel_config = models.JSONField(null=True, blank=True)
    status = models.SmallIntegerField(default=1, choices=STATUS_CHOICES)
    gmt_create = models.DateTimeField(auto_now_add=True)
    gmt_modified = models.DateTimeField(auto_now=True)
    gmt_deleted = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "aivision_agent_dashboard_panel"
        ordering = ["page", "group_name", "sort_order"]
        verbose_name = "[废弃] Agent Dashboard Panel"
        verbose_name_plural = "[废弃] Agent Dashboard Panels"

    def __str__(self):
        return (
            f"{self.agent_type.agent_name} - {self.page}/{self.group_name}/{self.title}"
        )

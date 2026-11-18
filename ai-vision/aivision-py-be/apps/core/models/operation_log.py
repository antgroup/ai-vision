from django.db import models

from .space import Space
from .user import User


class SpaceOperationLog(models.Model):
    """空间操作审计日志（仅记录成功操作，写入后不可更新/删除）"""

    CATEGORY_MEMBER_CHANGE = "member_change"
    CATEGORY_SPACE_INFO_CHANGE = "space_info_change"
    CATEGORY_KEY_OPERATION = "key_operation"
    CATEGORY_TRACE_DESENSITIZE_VIEW = "trace_desensitize_view"
    CATEGORY_AGENT_ACCESS = "agent_access"

    CATEGORY_CHOICES = [
        (CATEGORY_MEMBER_CHANGE, "成员变更"),
        (CATEGORY_SPACE_INFO_CHANGE, "空间信息变更"),
        (CATEGORY_KEY_OPERATION, "Key 操作"),
        (CATEGORY_TRACE_DESENSITIZE_VIEW, "轨迹脱敏查看"),
        (CATEGORY_AGENT_ACCESS, "Agent 接入"),
    ]

    space = models.ForeignKey(
        Space,
        on_delete=models.CASCADE,
        related_name="operation_logs",
        db_constraint=False,
    )
    operator = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="operation_logs",
        db_constraint=False,
    )
    category = models.CharField(max_length=32, choices=CATEGORY_CHOICES)
    action = models.CharField(max_length=64)
    target_display = models.CharField(max_length=256, blank=True, default="")
    detail = models.CharField(max_length=512, blank=True, default="")
    extra = models.JSONField(null=True, blank=True)
    gmt_create = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "aivision_space_operation_log"
        ordering = ["-gmt_create"]

    def __str__(self):
        return f"{self.category}/{self.action} by {self.operator_id} in {self.space_id}"

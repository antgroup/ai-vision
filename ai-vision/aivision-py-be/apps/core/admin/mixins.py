from django.utils.safestring import mark_safe


class SoftDeleteAdminMixin:
    """在 admin 列表页为已软删除的记录显示醒目标记。"""

    def is_deleted(self, obj):
        if obj.gmt_deleted:
            return mark_safe(
                '<span style="color:#fff;background:#d32f2f;padding:2px 8px;'
                'border-radius:4px;font-weight:bold;font-size:11px;">'
                "已删除</span>"
            )
        return ""

    is_deleted.short_description = "软删除"

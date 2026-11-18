import json
import re
from urllib.parse import urlencode

from django import forms
from django.contrib import admin
from django.contrib.admin.helpers import ACTION_CHECKBOX_NAME
from django.core.exceptions import ValidationError
from django.db import connection, models, transaction
from django.urls import path, reverse
from django.utils import timezone
from django_json_widget.widgets import JSONEditorWidget

from ..models import (
    Agent,
    AgentDashboardPanel,
    AgentTypeConfig,
    DataSource,
)
from ..validators import validate_datasource_config, validate_panel_config
from .mixins import SoftDeleteAdminMixin

AGENT_ADMIN_BACKFILL_BATCH_SIZE = 500


def _table_has_columns(table_name, column_names):
    with connection.cursor() as cursor:
        existing = {
            column.name
            for column in connection.introspection.get_table_description(
                cursor, table_name
            )
        }
    return set(column_names).issubset(existing)


def _fetchall(sql, params=None):
    with connection.cursor() as cursor:
        cursor.execute(sql, params or [])
        columns = [column[0] for column in cursor.description]
        return [dict(zip(columns, row)) for row in cursor.fetchall()]


def _fetchone(sql, params=None):
    rows = _fetchall(sql, params)
    return rows[0] if rows else None


def _default_runtime_type(agent_product):
    explicit = {
        "AntCC": "claudecode",
        "AntClaudeCode": "claudecode",
        "ClaudeCode": "claudecode",
        "个人 Claude Code": "claudecode",
        "Codex": "codex",
        "AntCodex": "codex",
        "个人 Codex": "codex",
        "HomiClaw": "openclaw",
    }
    if agent_product in explicit:
        return explicit[agent_product]
    return re.sub(r"[^0-9a-z]+", "", (agent_product or "").lower()) or "unknown"


def _normalize_agent_name(agent_product):
    value = re.sub(r"[^0-9A-Za-z]+", "_", agent_product or "").strip("_").upper()
    return (value or "AGENT")[:64]


def _agent_name_exists(agent_name):
    row = _fetchone(
        "SELECT COUNT(*) AS cnt FROM aivision_agent_type_config WHERE agent_name = %s",
        [agent_name],
    )
    return row["cnt"] > 0


def _unique_agent_name(agent_product):
    base = _normalize_agent_name(agent_product)
    candidate = base
    suffix = 1
    while _agent_name_exists(candidate):
        suffix += 1
        suffix_text = f"_{suffix}"
        candidate = f"{base[: 64 - len(suffix_text)]}{suffix_text}"
    return candidate


class AgentTypeConfigForm(forms.ModelForm):
    class Meta:
        model = AgentTypeConfig
        fields = "__all__"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # 为所有 JSONField 使用 JSON 编辑器
        for field_name in ("config_guide", "query_config", "capabilities"):
            if field_name in self.fields:
                self.fields[field_name].widget = JSONEditorWidget()
        # 标记废弃字段
        if "agent_name" in self.fields:
            self.fields["agent_name"].label = "[废弃] agent_name"
        if "cli_command" in self.fields:
            self.fields["cli_command"].label = "[废弃] cli_command"


@admin.register(AgentTypeConfig)
class AgentTypeConfigAdmin(SoftDeleteAdminMixin, admin.ModelAdmin):
    form = AgentTypeConfigForm
    list_display = [
        "id",
        "deprecated_agent_name",
        "agent_product",
        "runtime_type",
        "display_name",
        "display_scope",
        "category",
        "setup_mode",
        "sort_order",
        "status",
        "is_deleted",
    ]
    list_editable = ["category", "setup_mode", "sort_order"]
    list_filter = [
        "category",
        "setup_mode",
        "display_scope",
        "runtime_type",
        "status",
    ]
    search_fields = ["agent_name", "agent_product", "runtime_type", "display_name"]

    @admin.display(description="[废弃] agent_name")
    def deprecated_agent_name(self, obj):
        return obj.agent_name

    ordering = ["sort_order", "agent_product"]
    actions = [
        "set_status_enabled",
        "set_status_disabled",
        "rename_agent_product",
        "clone_dashboard_panels",
    ]

    @admin.action(description="Set status to Enabled")
    def set_status_enabled(self, request, queryset):
        updated = queryset.update(status=1)
        from ..cache import _delete_keys_by_prefix

        for prefix in ("agent_types:", "agent_type:"):
            _delete_keys_by_prefix(prefix)
        self.message_user(request, f"{updated} agent type(s) enabled.")

    @admin.action(description="Set status to Disabled")
    def set_status_disabled(self, request, queryset):
        updated = queryset.update(status=0)
        from ..cache import _delete_keys_by_prefix

        for prefix in ("agent_types:", "agent_type:"):
            _delete_keys_by_prefix(prefix)
        self.message_user(request, f"{updated} agent type(s) disabled.")

    @admin.action(description="重命名 agent_product（同步更新 Agent 表）")
    def rename_agent_product(self, request, queryset):
        from django.http import HttpResponseRedirect
        from django.template.response import TemplateResponse

        new_name = request.POST.get("new_agent_product")
        if new_name:
            for atc in queryset:
                old_name = atc.agent_product
                Agent.objects.filter(agent_product=old_name).update(
                    agent_product=new_name,
                    name=new_name,
                    custom_name=new_name,
                )
                atc.agent_product = new_name
                atc.save(update_fields=["agent_product"])
            from ..cache import _delete_keys_by_prefix

            for prefix in ("agent_types:", "agent_type:"):
                _delete_keys_by_prefix(prefix)
            self.message_user(
                request,
                f"已将 {queryset.count()} 个 agent_product 重命名为 '{new_name}'，关联 Agent 记录已同步更新。",
            )
            return HttpResponseRedirect(request.get_full_path())

        return TemplateResponse(
            request,
            "admin/rename_agent_product.html",
            {
                "title": "重命名 agent_product",
                "queryset": queryset,
                "opts": self.model._meta,
                "action_checkbox_name": admin.helpers.ACTION_CHECKBOX_NAME,
            },
        )

    @admin.action(description="复制 Dashboard 面板（从其他 Agent Type 克隆）")
    def clone_dashboard_panels(self, request, queryset):
        from django.http import HttpResponseRedirect
        from django.template.response import TemplateResponse
        from django.utils import timezone

        source_id = request.POST.get("source_agent_type")
        if source_id:
            try:
                source = AgentTypeConfig.objects.get(pk=source_id)
            except AgentTypeConfig.DoesNotExist:
                self.message_user(request, "源 Agent Type 不存在", level="error")
                return HttpResponseRedirect(request.get_full_path())

            source_panels = AgentDashboardPanel.objects.filter(
                agent_type=source, status=1, gmt_deleted__isnull=True
            )
            if not source_panels.exists():
                self.message_user(
                    request,
                    f"源 Agent Type「{source.agent_product}」没有可用的面板",
                    level="warning",
                )
                return HttpResponseRedirect(request.get_full_path())

            total_cloned = 0
            target_names = []
            for target in queryset:
                if target.pk == source.pk:
                    continue
                for src_panel in source_panels:
                    definition = {
                        "page": src_panel.page,
                        "group_name": src_panel.group_name,
                        "panel_type": src_panel.panel_type,
                        "title": src_panel.title,
                        "description": src_panel.description,
                        "data_item_key": src_panel.data_item_key,
                        "datasource_type": src_panel.datasource_type,
                        "sort_order": src_panel.sort_order,
                        "grid_width": src_panel.grid_width,
                        "panel_config": src_panel.panel_config,
                    }
                    candidates = AgentDashboardPanel.objects.filter(
                        agent_type=target,
                        page=definition["page"],
                        data_item_key=definition["data_item_key"],
                    ).order_by("id")

                    existing = candidates.first()
                    if existing:
                        for field, value in definition.items():
                            setattr(existing, field, value)
                        existing.status = 1
                        existing.gmt_deleted = None
                        existing.save()
                    else:
                        existing = AgentDashboardPanel.objects.create(
                            agent_type=target, status=1, **definition
                        )

                    duplicates = candidates.exclude(id=existing.id)
                    if duplicates.exists():
                        duplicates.update(status=0, gmt_deleted=timezone.now())

                    total_cloned += 1
                target_names.append(target.agent_product)

            from ..cache import _delete_keys_by_prefix

            for prefix in ("panels:", "agent_type:"):
                _delete_keys_by_prefix(prefix)

            self.message_user(
                request,
                f"已从「{source.agent_product}」克隆 {source_panels.count()} 个面板到 {', '.join(target_names)}（共 {total_cloned} 条记录）",
            )
            return HttpResponseRedirect(request.get_full_path())

        target_ids = set(queryset.values_list("pk", flat=True))
        sources = (
            AgentTypeConfig.objects.filter(status=1, gmt_deleted__isnull=True)
            .exclude(pk__in=target_ids)
            .order_by("agent_product")
        )

        return TemplateResponse(
            request,
            "admin/clone_dashboard_panels.html",
            {
                "title": "复制 Dashboard 面板",
                "queryset": queryset,
                "sources": sources,
                "opts": self.model._meta,
                "action_checkbox_name": admin.helpers.ACTION_CHECKBOX_NAME,
            },
        )


class AgentAdminForm(forms.ModelForm):
    class Meta:
        model = Agent
        fields = "__all__"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        deprecated_fields = {
            "api_key_id": "[已废弃] api_key_id",
            "agent_product": "[未使用] agent_product",
            "name": "[未使用] name",
            "custom_name": "[未使用] custom_name",
            "version": "[未使用] version",
            "instance_id": "[未使用] instance_id",
        }
        for field_name, label in deprecated_fields.items():
            if field_name in self.fields:
                self.fields[field_name].label = label


@admin.register(Agent)
class AgentAdmin(SoftDeleteAdminMixin, admin.ModelAdmin):
    form = AgentAdminForm
    list_display = [
        "id",
        "deprecated_name",
        "deprecated_agent_product",
        "agent_type",
        "space",
        "status",
        "gmt_create",
        "is_deleted",
    ]
    list_filter = ["agent_product", "status", "gmt_create"]
    search_fields = [
        "name",
        "custom_name",
        "agent_product",
        "agent_type__agent_product",
        "agent_type__agent_name",
        "instance_id",
    ]
    autocomplete_fields = ["space", "agent_type"]
    readonly_fields = ["api_key_id"]

    @admin.display(description="[未使用] name")
    def deprecated_name(self, obj):
        return obj.name

    @admin.display(description="[未使用] agent_product")
    def deprecated_agent_product(self, obj):
        return obj.agent_product

    actions = [
        "create_missing_agent_type_configs",
        "create_all_missing_agent_type_configs",
        "backfill_agent_type_id",
        "backfill_all_agent_type_id",
    ]

    def _create_missing_agent_type_configs(self, request, agent_ids=None):
        params = []
        id_filter = ""
        if agent_ids is not None:
            if not agent_ids:
                self.message_user(request, "未选择 Agent。", level="warning")
                return
            placeholders = ", ".join(["%s"] * len(agent_ids))
            id_filter = f"AND a.id IN ({placeholders})"
            params = agent_ids

        missing_products = _fetchall(
            f"""
            SELECT
              a.agent_product,
              COUNT(*) AS agent_count,
              SUM(CASE WHEN s.type = 1 THEN 1 ELSE 0 END) AS personal_count,
              SUM(CASE WHEN s.type = 2 THEN 1 ELSE 0 END) AS business_count
            FROM aivision_agent a
            LEFT JOIN aivision_agent_type_config c
              ON c.agent_product = a.agent_product
             AND c.gmt_deleted IS NULL
            LEFT JOIN aivision_space s
              ON s.id = a.space_id
            WHERE a.gmt_deleted IS NULL
              {id_filter}
              AND c.id IS NULL
            GROUP BY a.agent_product
            ORDER BY agent_count DESC, a.agent_product
            """,
            params,
        )

        if not missing_products:
            self.message_user(request, "Agent 的 agent_product 都已有模板。")
            return

        created = 0
        for row in missing_products:
            agent_product = row["agent_product"]
            AgentTypeConfig.objects.create(
                agent_name=_unique_agent_name(agent_product),
                agent_product=agent_product,
                category="CUSTOM",
                setup_mode="AUTO",
                sort_order=0,
                user_mapping_type="DIRECT",
                capabilities={},
                status=1,
            )
            created += 1

        from ..cache import _delete_keys_by_prefix

        for prefix in ("agent_types:", "agent_type:"):
            _delete_keys_by_prefix(prefix)
        self.message_user(request, f"已创建 {created} 条缺失 AgentTypeConfig。")

    @admin.action(description="为所选 Agent 缺失的 agent_product 创建模板")
    def create_missing_agent_type_configs(self, request, queryset):
        agent_ids = list(queryset.values_list("id", flat=True))
        self._create_missing_agent_type_configs(request, agent_ids=agent_ids)

    @admin.action(description="为所有 Agent 缺失的 agent_product 创建模板")
    def create_all_missing_agent_type_configs(self, request, queryset):
        self._create_missing_agent_type_configs(request, agent_ids=None)

    def _backfill_agent_type_id(self, request, agent_ids=None):
        if not _table_has_columns("aivision_agent", {"agent_type_id"}):
            self.message_user(
                request,
                "请先在 ODC 给 aivision_agent 添加 agent_type_id 字段。",
                level="error",
            )
            return

        params = []
        id_filter = ""
        target_agent_ids = agent_ids
        if agent_ids is not None:
            if not agent_ids:
                self.message_user(request, "未选择 Agent。", level="warning")
                return
            placeholders = ", ".join(["%s"] * len(agent_ids))
            id_filter = f"AND a.id IN ({placeholders})"
            params = agent_ids

        missing = _fetchall(
            f"""
            SELECT DISTINCT a.agent_product
            FROM aivision_agent a
            LEFT JOIN aivision_agent_type_config c
              ON c.agent_product = a.agent_product
             AND c.gmt_deleted IS NULL
            WHERE a.gmt_deleted IS NULL
              {id_filter}
              AND c.id IS NULL
            ORDER BY a.agent_product
            """,
            params,
        )
        if missing:
            products = ", ".join(row["agent_product"] for row in missing[:20])
            if len(missing) > 20:
                products += f" 等 {len(missing)} 个"
            self.message_user(
                request,
                f"以下 agent_product 缺少 AgentTypeConfig，先创建模板后再回填: {products}",
                level="error",
            )
            return

        if target_agent_ids is None:
            target_agent_ids = [
                row["id"]
                for row in _fetchall(
                    """
                    SELECT a.id
                    FROM aivision_agent a
                    JOIN aivision_agent_type_config c
                      ON c.agent_product = a.agent_product
                     AND c.gmt_deleted IS NULL
                    WHERE a.gmt_deleted IS NULL
                      AND a.agent_type_id IS NULL
                    ORDER BY a.id
                    """
                )
            ]

        updated = 0
        batch_count = 0
        with connection.cursor() as cursor:
            for start in range(
                0, len(target_agent_ids), AGENT_ADMIN_BACKFILL_BATCH_SIZE
            ):
                batch_ids = target_agent_ids[
                    start : start + AGENT_ADMIN_BACKFILL_BATCH_SIZE
                ]
                if not batch_ids:
                    continue
                placeholders = ", ".join(["%s"] * len(batch_ids))
                cursor.execute(
                    f"""
                    UPDATE aivision_agent a
                    JOIN aivision_agent_type_config c
                      ON c.agent_product = a.agent_product
                     AND c.gmt_deleted IS NULL
                    SET a.agent_type_id = c.id,
                        a.gmt_modified = NOW()
                    WHERE a.id IN ({placeholders})
                      AND a.gmt_deleted IS NULL
                      AND a.agent_type_id IS NULL
                    """,
                    batch_ids,
                )
                updated += cursor.rowcount
                batch_count += 1

        self.message_user(
            request,
            f"已分 {batch_count} 批回填 {updated} 条 Agent.agent_type_id，每批最多 {AGENT_ADMIN_BACKFILL_BATCH_SIZE} 条。",
        )

    @admin.action(description="按 agent_product 回填所选 Agent 的 agent_type_id")
    def backfill_agent_type_id(self, request, queryset):
        agent_ids = list(queryset.values_list("id", flat=True))
        self._backfill_agent_type_id(request, agent_ids=agent_ids)

    @admin.action(description="按 agent_product 回填所有 Agent 的 agent_type_id")
    def backfill_all_agent_type_id(self, request, queryset):
        self._backfill_agent_type_id(request, agent_ids=None)


class DataSourceAdminForm(forms.ModelForm):
    """DataSource 表单，在 save 前校验 config JSON 结构"""

    class Meta:
        model = DataSource
        fields = "__all__"
        widgets = {
            "config": JSONEditorWidget,
        }

    def clean_config(self):
        config = self.cleaned_data.get("config")
        if config is None or config == "":
            return config

        datasource_type = self.cleaned_data.get("datasource_type", "")
        source_system = self.cleaned_data.get("source_system", "")

        try:
            validate_datasource_config(config, datasource_type, source_system)
        except ValidationError:
            raise
        except Exception as exc:
            raise ValidationError(f"config 校验异常: {exc}") from exc

        return config


@admin.register(DataSource)
class DataSourceAdmin(SoftDeleteAdminMixin, admin.ModelAdmin):
    form = DataSourceAdminForm
    list_display = [
        "id",
        "agent_type",
        "datasource_type",
        "source_system",
        "enabled",
        "is_default",
        "is_deleted",
    ]
    list_filter = ["datasource_type", "source_system", "enabled", "is_default"]
    search_fields = ["agent_type__agent_name", "agent_type__agent_product"]
    autocomplete_fields = ["agent_type"]
    actions = [
        "set_default_route",
        "export_as_json",
    ]

    @admin.action(description="导出选中的 DataSource 为 JSON")
    def export_as_json(self, request, queryset):
        from django.http import HttpResponse

        data = []
        for ds in queryset.select_related("agent_type"):
            data.append(
                {
                    "id": ds.id,
                    "agent_type": {
                        "id": ds.agent_type_id,
                        "agent_product": ds.agent_type.agent_product,
                        "agent_name": ds.agent_type.agent_name,
                    },
                    "datasource_type": ds.datasource_type,
                    "source_system": ds.source_system,
                    "enabled": ds.enabled,
                    "is_default": ds.is_default,
                    "config": ds.config,
                }
            )

        content = json.dumps(data, ensure_ascii=False, indent=2)
        response = HttpResponse(content, content_type="application/json; charset=utf-8")
        response["Content-Disposition"] = (
            'attachment; filename="datasource_export.json"'
        )
        return response

    @admin.action(description="将选中的 DataSource 设为默认路由")
    def set_default_route(self, request, queryset):
        if queryset.count() != 1:
            self.message_user(request, "请选择且只选择一条 DataSource。", level="error")
            return

        datasource = queryset.select_related("agent_type").first()
        if datasource.enabled != 1:
            self.message_user(
                request,
                "只能将 enabled=1 的 DataSource 设为默认路由。",
                level="error",
            )
            return

        with transaction.atomic():
            group = DataSource.objects.select_for_update().filter(
                agent_type=datasource.agent_type,
                datasource_type=datasource.datasource_type,
                gmt_deleted__isnull=True,
            )
            group.exclude(pk=datasource.pk).update(is_default=0)
            group.filter(pk=datasource.pk).update(is_default=1, enabled=1)

        self.message_user(
            request,
            (
                f"已将 {datasource.agent_type.agent_product} / "
                f"{datasource.datasource_type} / {datasource.source_system} 设为默认路由。"
            ),
        )


class AgentDashboardPanelAdminForm(forms.ModelForm):
    """AgentDashboardPanel 表单，在 save 前校验 panel_config JSON 结构"""

    class Meta:
        model = AgentDashboardPanel
        fields = "__all__"
        widgets = {
            "panel_config": JSONEditorWidget,
        }

    def clean_panel_config(self):
        panel_config = self.cleaned_data.get("panel_config")
        if panel_config is None or panel_config == "":
            return panel_config

        panel_type = self.cleaned_data.get("panel_type", "")

        try:
            validate_panel_config(panel_config, panel_type)
        except ValidationError:
            raise
        except Exception as exc:
            raise ValidationError(f"panel_config 校验异常: {exc}") from exc

        return panel_config


@admin.register(AgentDashboardPanel)
class AgentDashboardPanelAdmin(SoftDeleteAdminMixin, admin.ModelAdmin):
    form = AgentDashboardPanelAdminForm
    list_display = [
        "id",
        "agent_type",
        "page",
        "group_name",
        "panel_type",
        "title",
        "data_item_key",
        "sort_order",
        "grid_width",
        "status",
        "is_deleted",
    ]
    list_filter = ["page", "panel_type", "status"]
    search_fields = ["title", "group_name", "data_item_key"]
    autocomplete_fields = ["agent_type"]
    list_editable = ["sort_order", "grid_width", "status"]

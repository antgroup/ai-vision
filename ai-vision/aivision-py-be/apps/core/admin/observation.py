from django.contrib import admin
from django.db import models, transaction
from django_json_widget.widgets import JSONEditorWidget

from .mixins import SoftDeleteAdminMixin
from ..models import (
    Agent,
    DataSource,
    ObservationEndpointConfig,
    SpaceDatasourceBinding,
    SpaceObservationCredential,
    SpaceObservationProject,
)


@admin.register(ObservationEndpointConfig)
class ObservationEndpointConfigAdmin(SoftDeleteAdminMixin, admin.ModelAdmin):
    formfield_overrides = {
        models.JSONField: {"widget": JSONEditorWidget},
    }
    list_display = [
        "id",
        "name",
        "backend_type",
        "base_url",
        "tenant_name",
        "is_default",
        "status",
        "is_deleted",
    ]
    list_filter = ["backend_type", "is_default", "status"]
    search_fields = ["name", "base_url"]
    readonly_fields = ["gmt_create", "gmt_modified"]


class SpaceObservationCredentialInline(admin.TabularInline):
    model = SpaceObservationCredential
    extra = 0
    readonly_fields = ["public_key", "scope", "status", "last_used_at", "gmt_create"]
    fields = ["scope", "public_key", "name", "status", "last_used_at"]


@admin.register(SpaceObservationProject)
class SpaceObservationProjectAdmin(SoftDeleteAdminMixin, admin.ModelAdmin):
    list_display = [
        "id",
        "space",
        "endpoint_config",
        "upstream_project_id",
        "provision_status",
        "status",
        "gmt_create",
        "is_deleted",
    ]
    list_filter = ["provision_status", "status"]
    search_fields = ["space__name", "upstream_project_id", "upstream_project_name"]
    autocomplete_fields = ["space", "endpoint_config"]
    readonly_fields = ["gmt_create", "gmt_modified"]
    inlines = [SpaceObservationCredentialInline]


@admin.register(SpaceDatasourceBinding)
class SpaceDatasourceBindingAdmin(SoftDeleteAdminMixin, admin.ModelAdmin):
    formfield_overrides = {
        models.JSONField: {"widget": JSONEditorWidget},
    }
    list_display = [
        "id",
        "space_observation_project",
        "datasource_type",
        "source_system",
        "enabled",
        "is_default",
        "gmt_create",
        "is_deleted",
    ]
    list_filter = ["datasource_type", "source_system", "enabled", "is_default"]
    search_fields = [
        "space_observation_project__space__name",
        "space_observation_project__upstream_project_name",
    ]
    autocomplete_fields = ["space_observation_project"]
    readonly_fields = ["gmt_create", "gmt_modified"]
    actions = ["set_default_route", "migrate_to_agent_type_datasource"]

    @admin.action(description="将选中的空间数据源绑定设为默认路由")
    def set_default_route(self, request, queryset):
        if queryset.count() != 1:
            self.message_user(
                request,
                "请选择且只选择一条 SpaceDatasourceBinding。",
                level="error",
            )
            return

        binding = queryset.select_related("space_observation_project").first()
        if binding.enabled != 1:
            self.message_user(
                request,
                "只能将 enabled=1 的 binding 设为默认路由。",
                level="error",
            )
            return

        with transaction.atomic():
            group = SpaceDatasourceBinding.objects.select_for_update().filter(
                space_observation_project=binding.space_observation_project,
                datasource_type=binding.datasource_type,
                gmt_deleted__isnull=True,
            )
            group.exclude(pk=binding.pk).update(is_default=0)
            group.filter(pk=binding.pk).update(is_default=1, enabled=1)

        self.message_user(
            request,
            (
                f"已将 {binding.space_observation_project} / "
                f"{binding.datasource_type} / {binding.source_system} 设为默认路由。"
            ),
        )

    @admin.action(description="迁移到 AgentTypeConfig DataSource（按空间唯一 Agent）")
    def migrate_to_agent_type_datasource(self, request, queryset):
        from apps.core.services.credential_service import decrypt_secret

        migrated = 0
        updated = 0
        skipped = []

        bindings = queryset.select_related(
            "space_observation_project",
            "space_observation_project__space",
            "space_observation_project__endpoint_config",
        )

        for binding in bindings:
            project = binding.space_observation_project
            space = project.space
            agents = list(
                Agent.objects.select_related("agent_type")
                .filter(
                    space=space,
                    status__in=[1, 2],
                    gmt_deleted__isnull=True,
                )
                .order_by("id")
            )

            if len(agents) != 1:
                skipped.append(
                    f"{space.name}: 需要恰好 1 个 Agent，当前 {len(agents)} 个"
                )
                continue

            agent = agents[0]
            if not agent.agent_type_id or not agent.agent_type:
                skipped.append(
                    f"{space.name}: Agent(id={agent.id}, product={agent.agent_product}) 未回填 agent_type_id"
                )
                continue

            try:
                config = self._build_datasource_config_from_binding(
                    binding,
                    decrypt_secret=decrypt_secret,
                )
            except Exception as exc:
                skipped.append(f"{space.name}: 构建 DataSource config 失败: {exc}")
                continue

            with transaction.atomic():
                datasource, created = DataSource.objects.update_or_create(
                    agent_type=agent.agent_type,
                    datasource_type=binding.datasource_type,
                    source_system=binding.source_system,
                    defaults={
                        "enabled": binding.enabled,
                        "is_default": binding.is_default,
                        "config": config,
                        "gmt_deleted": None,
                    },
                )
            if created:
                migrated += 1
            else:
                updated += 1

        from ..cache import _delete_keys_by_prefix

        for prefix in ("agent_type:", "agent_types:"):
            _delete_keys_by_prefix(prefix)

        message = f"迁移完成：新建 {migrated}，更新 {updated}，跳过 {len(skipped)}。"
        if skipped:
            message += " 跳过原因：" + "；".join(skipped[:10])
            if len(skipped) > 10:
                message += f"；另有 {len(skipped) - 10} 条未展示"
        self.message_user(
            request,
            message,
            level="warning" if skipped else "success",
        )

    def _build_datasource_config_from_binding(self, binding, *, decrypt_secret):
        project = binding.space_observation_project
        endpoint = project.endpoint_config
        endpoint_config = (
            endpoint.config if endpoint and isinstance(endpoint.config, dict) else {}
        )
        binding_config = binding.config if isinstance(binding.config, dict) else {}

        if binding.source_system == "langfuse":
            if not endpoint:
                raise ValueError("缺少 ObservationEndpointConfig")
            config = {
                "queries": {
                    "fetch_traces": {
                        "mode": "simple",
                        "sdk_params": {
                            "method": "fetch_traces",
                            "param_mapping": {},
                        },
                        "value_type": "table",
                    }
                },
                "langfuse_host": endpoint.base_url.rstrip("/"),
                "response_format": "langfuse",
                "show_langfuse_link": bool(
                    binding_config.get("show_langfuse_link", False)
                ),
                "langfuse_project_id": "",
                "langfuse_public_key": "",
                "langfuse_secret_key": "",
            }
            if project.upstream_project_id:
                config["langfuse_project_id"] = project.upstream_project_id

            read_cred = project.credentials.filter(
                scope="READ",
                status=1,
                gmt_deleted__isnull=True,
            ).first()
            if not read_cred:
                raise ValueError("缺少 READ credential")
            config["langfuse_public_key"] = read_cred.public_key
            config["langfuse_secret_key"] = decrypt_secret(read_cred.secret_ciphertext)
            return config

        return dict(binding_config)


@admin.register(SpaceObservationCredential)
class SpaceObservationCredentialAdmin(SoftDeleteAdminMixin, admin.ModelAdmin):
    list_display = [
        "id",
        "space_observation_project",
        "scope",
        "public_key",
        "name",
        "status",
        "last_used_at",
        "is_deleted",
    ]
    list_filter = ["scope", "status"]
    search_fields = ["public_key", "name"]
    autocomplete_fields = ["space_observation_project"]
    readonly_fields = ["secret_hash", "gmt_create", "gmt_modified"]

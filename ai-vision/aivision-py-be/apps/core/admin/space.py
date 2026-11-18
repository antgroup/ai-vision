import logging

from django.contrib import admin

from .mixins import SoftDeleteAdminMixin
from ..models import (
    ApiKey,
    ObservationEndpointConfig,
    Space,
    SpaceDatasourceBinding,
    SpaceMember,
    SpaceObservationProject,
)

logger = logging.getLogger(__name__)


class SpaceMemberInline(admin.TabularInline):
    model = SpaceMember
    extra = 0
    autocomplete_fields = ["user"]


@admin.register(Space)
class SpaceAdmin(SoftDeleteAdminMixin, admin.ModelAdmin):
    list_display = [
        "uuid",
        "name",
        "type",
        "scope",
        "billing_owner",
        "creator",
        "status",
        "gmt_create",
        "is_deleted",
    ]
    list_filter = ["type", "scope", "status", "gmt_create"]
    search_fields = ["uuid", "name", "description", "billing_owner"]
    autocomplete_fields = ["creator"]
    readonly_fields = [
        "uuid",
        "gmt_create",
        "gmt_modified",
    ]
    inlines = [SpaceMemberInline]
    actions = [
        "initialize_observation_project",
        "bind_observation_project",
    ]

    def get_readonly_fields(self, request, obj=None):
        fields = list(super().get_readonly_fields(request, obj))
        return fields

    @admin.action(description="手动绑定 Observation Project（填入已有 Langfuse 信息）")
    def bind_observation_project(self, request, queryset):
        """
        手动绑定模式：用户先在 Langfuse UI 创建好 project 和 API Key，
        然后通过 Admin 表单填入信息完成绑定。
        """
        from django.http import HttpResponseRedirect
        from django.template.response import TemplateResponse

        # POST 提交：执行绑定
        if request.POST.get("_bind"):
            from ..services.credential_service import create_credential_records
            from ..services.credential_provider import CredentialPair

            results = {"success": 0, "skipped": 0, "failed": 0}

            upstream_project_id = request.POST.get("upstream_project_id", "").strip()
            upstream_project_name = request.POST.get(
                "upstream_project_name", ""
            ).strip()
            read_pk = request.POST.get("read_public_key", "").strip()
            read_sk = request.POST.get("read_secret_key", "").strip()
            write_pk = request.POST.get("write_public_key", "").strip()
            write_sk = request.POST.get("write_secret_key", "").strip()

            if not all([upstream_project_id, read_pk, read_sk, write_pk, write_sk]):
                self.message_user(request, "所有字段均为必填", level="error")
                return HttpResponseRedirect(request.get_full_path())

            endpoint = ObservationEndpointConfig.objects.filter(
                is_default=1, status=1, gmt_deleted__isnull=True
            ).first()
            if not endpoint:
                self.message_user(
                    request,
                    "未找到默认 ObservationEndpointConfig，请先创建。",
                    level="error",
                )
                return HttpResponseRedirect(request.get_full_path())

            skip_reasons = []
            for space in queryset:
                if space.type != 2:
                    results["skipped"] += 1
                    skip_reasons.append(f"「{space.name}」不是业务空间")
                    continue
                if SpaceObservationProject.objects.filter(
                    space=space, gmt_deleted__isnull=True
                ).exists():
                    results["skipped"] += 1
                    skip_reasons.append(f"「{space.name}」已绑定 Observation Project")
                    continue
                try:
                    project = SpaceObservationProject.objects.create(
                        space=space,
                        endpoint_config=endpoint,
                        tenant_name=endpoint.tenant_name,
                        upstream_project_id=upstream_project_id,
                        upstream_project_name=upstream_project_name
                        or f"aivision-{space.name}",
                        status=1,
                        provision_status=1,  # ACTIVE
                    )
                    read_pair = CredentialPair(public_key=read_pk, secret_key=read_sk)
                    write_pair = CredentialPair(
                        public_key=write_pk, secret_key=write_sk
                    )
                    create_credential_records(project, read_pair, write_pair)
                    SpaceDatasourceBinding.objects.update_or_create(
                        space_observation_project=project,
                        datasource_type="traces",
                        source_system="langfuse",
                        defaults={
                            "enabled": 1,
                            "is_default": 1,
                            "config": {},
                            "gmt_deleted": None,
                        },
                    )
                    results["success"] += 1
                except Exception as exc:
                    logger.exception(
                        "Failed to bind observation project for space %s", space.name
                    )
                    results["failed"] += 1

            msg = f"绑定完成：成功 {results['success']}，跳过 {results['skipped']}，失败 {results['failed']}"
            if skip_reasons:
                msg += "。跳过原因：" + "；".join(skip_reasons)
            self.message_user(
                request, msg, level="success" if results["failed"] == 0 else "warning"
            )
            return HttpResponseRedirect(request.get_full_path())

        # GET：展示表单
        return TemplateResponse(
            request,
            "admin/bind_observation_project.html",
            {
                "title": "手动绑定 Observation Project",
                "queryset": queryset,
                "opts": self.model._meta,
                "action_checkbox_name": admin.helpers.ACTION_CHECKBOX_NAME,
            },
        )

    @admin.action(description="一键初始化 Observation Project（仅业务空间）")
    def initialize_observation_project(self, request, queryset):
        from ..services.space_observation_provisioning import (
            SpaceObservationProvisioningError,
            provision_space_observation_project,
        )

        results = {"success": 0, "skipped": 0, "failed": 0}

        for space in queryset:
            # 仅处理业务空间
            if space.type != 2:
                results["skipped"] += 1
                continue

            # 已有 project 则跳过
            if SpaceObservationProject.objects.filter(
                space=space,
                gmt_deleted__isnull=True,
                provision_status=1,
            ).exists():
                results["skipped"] += 1
                continue

            try:
                provision_space_observation_project(space)
                results["success"] += 1
            except SpaceObservationProvisioningError:
                logger.exception(
                    "Failed to initialize observation project for space %s", space.name
                )
                results["failed"] += 1
            except Exception:
                logger.exception(
                    "Failed to initialize observation project for space %s", space.name
                )
                results["failed"] += 1

        self.message_user(
            request,
            f"初始化完成：成功 {results['success']}，跳过 {results['skipped']}，失败 {results['failed']}",
        )


@admin.register(SpaceMember)
class SpaceMemberAdmin(SoftDeleteAdminMixin, admin.ModelAdmin):
    list_display = ["id", "space", "user", "role", "joined_at", "is_deleted"]
    list_filter = ["role", "joined_at"]
    search_fields = ["space__name", "user__name", "user__email", "user__uuid"]
    autocomplete_fields = ["space", "user"]


@admin.register(ApiKey)
class ApiKeyAdmin(SoftDeleteAdminMixin, admin.ModelAdmin):
    list_display = [
        "id",
        "key_prefix",
        "name",
        "user",
        "space",
        "status",
        "gmt_create",
        "is_deleted",
    ]
    list_filter = ["status", "gmt_create"]
    search_fields = ["key_prefix", "name"]
    autocomplete_fields = ["user", "space"]
    readonly_fields = ["key_hash", "gmt_create", "gmt_modified"]

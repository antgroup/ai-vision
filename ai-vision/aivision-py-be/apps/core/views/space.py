"""Local-session Product APIs for spaces and memberships."""

import logging

from django.db import transaction
from django.utils import timezone
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.models import (
    ApiKey,
    ObservationSubjectBinding,
    Space,
    SpaceMember,
    SpaceObservationProject,
    SpaceOperationLog,
)
from apps.core.permissions.constants import (
    SPACE_LEAVE,
    SPACE_MEMBER_MANAGE,
    SPACE_MEMBER_READ,
    SPACE_READ,
    SPACE_SETTINGS_MANAGE,
)
from apps.core.permissions.space import (
    require_space_permission,
    lock_space_permission,
    require_member_authority,
    ownership_flags,
)
from apps.core.serializers import SpaceMemberSerializer, SpaceSerializer
from apps.core.services.credential_service import (
    create_space_gateway_project,
    mask_public_key,
)
from apps.core.services.operation_log_service import log_op
from apps.core.services.space_observation_provisioning import PROVISION_FAILURE_MESSAGE

from .common import error_response, get_active_observation_endpoint, success_response

logger = logging.getLogger(__name__)

ROLE_WHITELIST = {1, 2, 3}
ROLE_LABELS = {1: "管理员", 2: "开发者", 3: "成员"}


def _space_dto(space, membership):
    ctx = require_space_permission(
        user=membership.user, space_id=space.uuid, permission=SPACE_READ
    )
    data = SpaceSerializer(space).data
    data.update(ownership_flags(space, membership.user))
    data["role"] = membership.role
    data["permissions"] = sorted(ctx.permissions)
    data["collaboration_enabled"] = space.type == 2
    return data


class SpaceListView(APIView):
    def get(self, request):
        memberships = (
            SpaceMember.objects.select_related("space", "user")
            .filter(
                user=request.user,
                gmt_deleted__isnull=True,
                space__status=1,
                space__gmt_deleted__isnull=True,
            )
            .order_by("space__type", "-space__gmt_create")
        )
        return success_response(
            {"items": [_space_dto(item.space, item) for item in memberships]}
        )

    def post(self, request):
        name = str(request.data.get("name") or "").strip()
        if not name or len(name) > 128:
            return _error("INVALID_SPACE_NAME", status=422)
        if request.data.get("type") not in (None, 2, "2"):
            return _error("SPACE_TYPE_IMMUTABLE", status=422)

        endpoint_id = request.data.get("observation_endpoint_id")
        endpoint = get_active_observation_endpoint(endpoint_id) if endpoint_id else None
        if endpoint_id and endpoint is None:
            return _error("OBSERVATION_ENDPOINT_NOT_FOUND", status=422)

        try:
            with transaction.atomic():
                space = Space.objects.create(
                    name=name,
                    type=2,
                    scope=1,
                    creator=request.user,
                    owner=request.user,
                    description=str(request.data.get("description") or "")[:512],
                    status=1,
                )
                membership = SpaceMember.objects.create(
                    space=space, user=request.user, role=1
                )
                ObservationSubjectBinding.objects.create(
                    space=space,
                    user=request.user,
                    source_system="collector",
                    subject_id=request.user.uuid,
                    binding_source="space_creator_default",
                    status=1,
                    created_by=request.user,
                )
                if endpoint:
                    create_space_gateway_project(space, endpoint)
        except Exception:
            logger.exception("Business space provisioning failed")
            return _error("SPACE_PROVISION_FAILED", status=500)
        return Response(
            {"code": 0, "message": "success", "data": _space_dto(space, membership)},
            status=201,
        )


class SpaceCreateView(SpaceListView):
    """Compatibility symbol; its legacy route is intentionally not mounted."""


class SpaceDetailView(APIView):
    def get(self, request, space_id):
        ctx = require_space_permission(
            user=request.user, space_id=space_id, permission=SPACE_READ
        )
        return success_response(_space_dto(ctx.space, ctx.membership))

    @log_op(
        category=SpaceOperationLog.CATEGORY_SPACE_INFO_CHANGE,
        action="update_space",
    )
    def patch(self, request, space_id):
        ctx = require_space_permission(
            user=request.user,
            space_id=space_id,
            permission=SPACE_SETTINGS_MANAGE,
        )
        if "type" in request.data:
            return _error("SPACE_TYPE_IMMUTABLE", status=422)
        fields = []
        if "name" in request.data:
            name = str(request.data.get("name") or "").strip()
            if not name or len(name) > 128:
                return _error("INVALID_SPACE_NAME", status=422)
            ctx.space.name = name
            fields.append("name")
        if "description" in request.data:
            ctx.space.description = str(request.data.get("description") or "")[:512]
            fields.append("description")
        if fields:
            ctx.space.save(update_fields=[*fields, "gmt_modified"])
        return success_response(_space_dto(ctx.space, ctx.membership))


class SpacePermissionsView(APIView):
    def get(self, request, space_id):
        ctx = require_space_permission(
            user=request.user, space_id=space_id, permission=SPACE_READ
        )
        return success_response(
            {"role": ctx.membership.role, "permissions": sorted(ctx.permissions)}
        )


class SpaceMembersView(APIView):
    def get(self, request, space_id):
        ctx = require_space_permission(
            user=request.user,
            space_id=space_id,
            permission=SPACE_MEMBER_READ,
        )
        members = (
            SpaceMember.objects.filter(space=ctx.space, gmt_deleted__isnull=True)
            .select_related("user", "space")
            .order_by("role", "-joined_at")
        )
        if ctx.space.type == 1:
            members = members.filter(user=request.user)
        return success_response(
            {
                "items": SpaceMemberSerializer(members, many=True).data,
                "total": members.count(),
                "role": ctx.membership.role,
            }
        )


class SpaceMemberRoleUpdateView(APIView):
    @log_op(category=SpaceOperationLog.CATEGORY_MEMBER_CHANGE, action="change_role")
    def patch(self, request, space_id, user_uuid):
        new_role = request.data.get("role")
        if new_role not in ROLE_WHITELIST:
            return _error("INVALID_ROLE", status=422)
        with transaction.atomic():
            ctx = lock_space_permission(
                user=request.user,
                space_id=space_id,
                permission=SPACE_MEMBER_MANAGE,
            )
            target = _locked_member(ctx.space, user_uuid)
            if target is None:
                return _error("RESOURCE_NOT_IN_SPACE", status=404)
            require_member_authority(ctx, target=target, role=new_role)
            if target.user_id == request.user.id:
                return _error("SELF_ROLE_CHANGE_FORBIDDEN", status=403)
            if (
                target.role == 1
                and new_role != 1
                and _active_admin_count(ctx.space) <= 1
            ):
                return _last_admin_response()
            old_role = target.role
            target.role = new_role
            target.save(update_fields=["role", "gmt_modified"])
        request.op_log = {
            "target_display": target.user.name,
            "detail": f"{ROLE_LABELS[old_role]} -> {ROLE_LABELS[new_role]}",
        }
        return success_response(SpaceMemberSerializer(target).data)

    @log_op(category=SpaceOperationLog.CATEGORY_MEMBER_CHANGE, action="remove_member")
    def delete(self, request, space_id, user_uuid):
        with transaction.atomic():
            ctx = lock_space_permission(
                user=request.user,
                space_id=space_id,
                permission=SPACE_MEMBER_MANAGE,
            )
            target = _locked_member(ctx.space, user_uuid)
            if target is None:
                return _error("RESOURCE_NOT_IN_SPACE", status=404)
            require_member_authority(ctx, target=target)
            if target.user_id == request.user.id:
                return _error("SPACE_PERMISSION_DENIED", status=403)
            if target.role == 1 and _active_admin_count(ctx.space) <= 1:
                return _last_admin_response()
            target.gmt_deleted = timezone.now()
            target.save(update_fields=["gmt_deleted", "gmt_modified"])
        request.op_log = {"target_display": target.user.name, "detail": "移除成员"}
        return success_response(None)


class SpaceMemberExitView(APIView):
    @log_op(category=SpaceOperationLog.CATEGORY_MEMBER_CHANGE, action="member_leave")
    def post(self, request, space_id):
        with transaction.atomic():
            ctx = lock_space_permission(
                user=request.user, space_id=space_id, permission=SPACE_LEAVE
            )
            if ctx.space.owner_id == request.user.pk:
                return _error("SPACE_OWNER_TRANSFER_REQUIRED", status=409)
            member = SpaceMember.objects.select_for_update().get(pk=ctx.membership.pk)
            if member.role == 1 and _active_admin_count(ctx.space) <= 1:
                return _last_admin_response()
            member.gmt_deleted = timezone.now()
            member.save(update_fields=["gmt_deleted", "gmt_modified"])
        request.op_log = {"target_display": request.user.name, "detail": "主动退出空间"}
        return success_response(None)


def _locked_member(space, user_uuid):
    return (
        SpaceMember.objects.select_for_update()
        .select_related("user")
        .filter(
            space=space,
            user__uuid=user_uuid,
            gmt_deleted__isnull=True,
        )
        .first()
    )


def _active_admin_count(space):
    return SpaceMember.objects.filter(
        space=space,
        role=1,
        gmt_deleted__isnull=True,
        user__status=1,
        user__gmt_deleted__isnull=True,
    ).count()


def _error(error_key, *, status):
    return error_response(
        error_key,
        4000,
        status=status,
        data={"error_key": error_key},
    )


def _last_admin_response():
    return _error("LAST_ADMIN_REQUIRED", status=409)


class SpaceMemberBatchAddView(APIView):
    """Removed endpoint retained only as an import-compatible symbol."""


class SpaceSettingsView(APIView):
    def get(self, request, space_id):
        ctx = require_space_permission(
            user=request.user, space_id=space_id, permission=SPACE_READ
        )
        api_key = ApiKey.objects.filter(
            space=ctx.space, gmt_deleted__isnull=True
        ).first()
        data = {
            "space_id": ctx.space.uuid,
            "name": ctx.space.name,
            "type": ctx.space.type,
            "description": ctx.space.description,
            "billing_owner": ctx.space.billing_owner,
            "role": ctx.membership.role,
            "permissions": sorted(ctx.permissions),
        }
        if ctx.space.type != 2:
            data["api_key"] = (
                {"key_prefix": api_key.key_prefix, "name": api_key.name}
                if api_key
                else None
            )

        project = (
            SpaceObservationProject.objects.filter(
                space=ctx.space, gmt_deleted__isnull=True
            )
            .select_related("endpoint_config")
            .first()
        )
        if project:
            data["observation"] = {
                "endpoint_name": project.endpoint_config.name,
                "project_id": project.upstream_project_id,
                "project_name": project.upstream_project_name,
                "provision_status": project.provision_status,
                "last_error": PROVISION_FAILURE_MESSAGE if project.provision_status == 2 else None,
            }
            for credential in project.credentials.filter(
                status=1, gmt_deleted__isnull=True
            ):
                data[f"{credential.scope.lower()}_key"] = {
                    "id": credential.id,
                    "public_key": mask_public_key(credential.public_key),
                    "scope": credential.scope,
                    "name": credential.name,
                }
        return success_response(data)

    def patch(self, request, space_id):
        return SpaceDetailView().patch(request, space_id)


class SpaceOwnerTransferView(APIView):
    @log_op(category=SpaceOperationLog.CATEGORY_MEMBER_CHANGE, action="transfer_owner")
    def post(self, request, space_id):
        with transaction.atomic():
            ctx = lock_space_permission(user=request.user, space_id=space_id)
            if ctx.space.type != 2:
                return _error("PERSONAL_SPACE_SINGLE_USER_ONLY", status=422)
            if ctx.space.owner_id != request.user.pk:
                return _error("SPACE_OWNER_REQUIRED", status=403)
            target_uuid = request.data.get("target_user_uuid")
            if (
                not isinstance(target_uuid, str)
                or not target_uuid
                or target_uuid == request.user.uuid
            ):
                return _error("INVALID_OWNER_TARGET", status=422)
            target = _locked_member(ctx.space, target_uuid)
            if (
                target is None
                or target.user.status != 1
                or target.user.gmt_deleted is not None
            ):
                return _error("RESOURCE_NOT_IN_SPACE", status=404)
            target.role = 1
            target.save(update_fields=["role", "gmt_modified"])
            ctx.space.owner = target.user
            ctx.space.save(update_fields=["owner", "gmt_modified"])
        request.op_log = {
            "target_display": target.user.name,
            "detail": f"Owner: {request.user.uuid} -> {target.user.uuid}",
        }
        return success_response({"owner_uuid": target.user.uuid})

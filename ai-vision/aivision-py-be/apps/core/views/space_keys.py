import logging

from django.utils import timezone
from rest_framework.views import APIView

from ..models import SpaceKey
from ..permissions.constants import (
    AGENT_INGEST_TOKEN_MINT,
    SPACE_KEY_MANAGE,
    SPACE_KEY_READ_MASKED,
)
from ..permissions.space import require_space_permission
from ..serializers import SpaceKeyCreateSerializer, SpaceKeyListSerializer
from ..services.space_key_policy import (
    atomic_space_key_creation,
    key_count_exceeded,
    lock_space_key_creation,
)
from .common import (
    StandardPagination,
    success_response,
    error_response,
)

logger = logging.getLogger(__name__)


class SpaceKeyListCreateView(APIView):
    """
    GET  /api/v1/spaces/{space_id}/space-keys — list SpaceKeys (SpaceMember required)
    POST /api/v1/spaces/{space_id}/space-keys — create SpaceKey (ingest token minters)
    """

    def get(self, request, space_id):
        ctx = require_space_permission(
            user=request.user, space_id=space_id, permission=SPACE_KEY_READ_MASKED
        )
        space = ctx.space

        keys = SpaceKey.objects.filter(space=space).select_related("creator")
        paginator = StandardPagination()
        page = paginator.paginate_queryset(keys, request)
        serializer = SpaceKeyListSerializer(page, many=True)
        return success_response(
            {
                "items": serializer.data,
                "total": paginator.page.paginator.count,
                "page": paginator.page.number,
                "pageSize": paginator.get_page_size(request),
                "role": ctx.membership.role,
            }
        )

    @atomic_space_key_creation
    def post(self, request, space_id):
        ctx = require_space_permission(
            user=request.user,
            space_id=space_id,
            permission=AGENT_INGEST_TOKEN_MINT,
        )
        space = ctx.space

        lock_space_key_creation(space)
        if key_count_exceeded(space):
            return error_response("API Key 数量已达上限", 3033)

        serializer = SpaceKeyCreateSerializer(data=request.data)
        if not serializer.is_valid():
            return error_response(str(serializer.errors), 3031)

        raw_key, key_prefix, key_hash = SpaceKey.generate_key()

        space_key = SpaceKey(
            space=space,
            key_prefix=key_prefix,
            key_hash=key_hash,
            name=serializer.validated_data.get("name", ""),
            creator=request.user,
        )
        space_key.save()

        return success_response(
            {
                "id": space_key.id,
                "key": raw_key,
                "key_prefix": key_prefix,
                "name": space_key.name,
            }
        )


class SpaceKeyDetailView(APIView):
    """
    PUT    /api/v1/spaces/{space_id}/space-keys/{key_id} — toggle enabled
    DELETE /api/v1/spaces/{space_id}/space-keys/{key_id} — soft delete
    """

    def put(self, request, space_id, key_id):
        ctx = require_space_permission(
            user=request.user, space_id=space_id, permission=SPACE_KEY_MANAGE
        )
        space = ctx.space

        try:
            space_key = SpaceKey.objects.get(id=key_id, space=space)
        except SpaceKey.DoesNotExist:
            return error_response("Space Key not found", 3032)

        enabled = request.data.get("enabled")
        if enabled is not None:
            space_key.enabled = bool(enabled)
            space_key.save(update_fields=["enabled", "gmt_modified"])

        return success_response(
            {
                "id": space_key.id,
                "enabled": space_key.enabled,
            }
        )

    def delete(self, request, space_id, key_id):
        ctx = require_space_permission(
            user=request.user, space_id=space_id, permission=SPACE_KEY_MANAGE
        )
        space = ctx.space

        try:
            space_key = SpaceKey.objects.get(id=key_id, space=space)
        except SpaceKey.DoesNotExist:
            return error_response("Space Key not found", 3032)

        space_key.gmt_deleted = timezone.now()
        space_key.save(update_fields=["gmt_deleted", "gmt_modified"])

        return success_response({"id": space_key.id, "deleted": True})

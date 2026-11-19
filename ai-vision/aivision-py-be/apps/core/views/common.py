"""公共工具函数、异常类、分页器"""

from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response

from ..models import ObservationEndpointConfig, Space, SpaceMember


class SpaceObservationCredentialDecryptionError(Exception):
    """Raised when stored space observation credentials cannot be decrypted."""


class StandardPagination(PageNumberPagination):
    page_size = 20
    page_size_query_param = "pageSize"
    max_page_size = 100


def success_response(data, message="success"):
    return Response({"code": 0, "message": message, "data": data})


def error_response(message, code=1001, status=400, data=None):
    return Response({"code": code, "message": message, "data": data}, status=status)


def read_credential_error_response():
    return error_response("Failed to decrypt Langfuse READ credential for this space", 3017)


def get_active_observation_endpoint(endpoint_id) -> ObservationEndpointConfig | None:
    if endpoint_id in (None, ""):
        return None
    try:
        endpoint_id = int(endpoint_id)
    except (TypeError, ValueError):
        return None
    return ObservationEndpointConfig.objects.filter(
        id=endpoint_id,
        status=1,
        gmt_deleted__isnull=True,
    ).first()


def check_space_access(user, space):
    """Check if user has access to the space. Returns True if user is a member."""
    if not user or not user.is_authenticated:
        return False
    return SpaceMember.objects.filter(
        space=space, user=user, gmt_deleted__isnull=True
    ).exists()


def check_space_access_or_error(user, space):
    """Check if user has access to the space. Returns error response if not."""
    if not check_space_access(user, space):
        return error_response("You don't have access to this space", 3002)
    return None


def get_or_create_personal_space(user):
    """Get or create personal space for a user. Each user can only have one personal space."""
    from ..models import generate_uuid

    space = Space.objects.filter(creator=user, type=1).first()
    if space:
        if not space.uuid:
            space.uuid = generate_uuid()
            space.save(update_fields=["uuid"])
        return space

    space = Space.objects.create(
        name=f"{user.name}的空间",
        type=1,
        scope=1,
        creator=user,
        status=1,
    )
    SpaceMember.objects.create(space=space, user=user, role=1)
    return space


def build_auth_callback_url(redirect_url: str) -> str:
    from urllib.parse import urlencode

    return f"/auth/callback?{urlencode({'redirect': redirect_url or '/'})}"


def check_space_key_access(request, space_id):
    """Validate external auth (SpaceKey or Credential) and verify space match."""
    auth_obj = getattr(request, "auth", None)
    auth_type = getattr(request, "auth_type", None)

    if auth_type == "credential":
        if auth_obj is None:
            return None, error_response("Authentication required", 4001, status=401)
        if getattr(auth_obj, "scope", None) != "READ":
            return None, error_response(
                "Write key cannot access read-only API", 4003, status=403
            )
        project = getattr(auth_obj, "space_observation_project", None)
        space = getattr(project, "space", None) if project else None
        if not space:
            return None, error_response("Authentication required", 4001, status=401)
        if str(space.uuid) != space_id:
            return None, error_response(
                "Credential does not match the requested space", 4003, status=403
            )
        if getattr(space, "type", None) != 2:
            return None, error_response(
                "Credential authentication is not allowed for personal spaces",
                4003,
                status=403,
            )
        if getattr(space, "status", None) != 1 or getattr(space, "gmt_deleted", None) is not None:
            return None, error_response("Space not found", 3001, status=404)
        return space, None

    if auth_type == "user":
        user = getattr(request, "user", None)
        if not user or not getattr(user, "is_authenticated", False):
            return None, error_response("Authentication required", 4001, status=401)
        try:
            space = Space.objects.get(uuid=space_id, status=1)
        except Space.DoesNotExist:
            return None, error_response("Space not found", 3001, status=404)
        if not check_space_access(user, space):
            return None, error_response(
                "You don't have access to this space", 3002, status=403
            )
        return space, None

    if auth_type != "space_key" or not hasattr(auth_obj, "space"):
        return None, error_response("Authentication required", 4001, status=401)
    if str(auth_obj.space.uuid) != space_id:
        return None, error_response(
            "Space Key does not match the requested space", 4003, status=403
        )
    space = auth_obj.space
    if getattr(space, "status", None) != 1 or getattr(space, "gmt_deleted", None) is not None:
        return None, error_response("Space not found", 3001, status=404)
    return space, None

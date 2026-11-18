from dataclasses import dataclass, replace

from apps.core.models import Space, SpaceMember, User

from .constants import (
    DIAGNOSIS_JOB_MANAGE,
    ROLE_PERMISSIONS,
    SPACE_LEAVE,
    SPACE_MEMBER_MANAGE,
)


class SpaceAuthorizationError(Exception):
    def __init__(self, error_key, *, status):
        self.error_key = error_key
        self.status = status
        super().__init__(error_key)


@dataclass(frozen=True)
class SpaceAuthorizationContext:
    user: User
    space: Space
    membership: SpaceMember
    permissions: frozenset[str]


def require_space_permission(*, user, space_id, permission, lock=False):
    if (
        user is None
        or not getattr(user, "is_authenticated", False)
        or user.status != 1
        or user.gmt_deleted is not None
    ):
        raise SpaceAuthorizationError("SESSION_INVALID", status=401)
    memberships = SpaceMember.objects.select_related("space", "user").filter(
        user=user,
        user__status=1,
        user__gmt_deleted__isnull=True,
        space__uuid=space_id,
        space__status=1,
        space__gmt_deleted__isnull=True,
        gmt_deleted__isnull=True,
    )
    membership = (memberships.select_for_update() if lock else memberships).first()
    if membership is None:
        raise SpaceAuthorizationError("SPACE_NOT_FOUND", status=404)
    permissions = ROLE_PERMISSIONS.get(membership.role, frozenset())
    if membership.space.type == 1:
        permissions = permissions - {
            SPACE_MEMBER_MANAGE,
            SPACE_LEAVE,
            DIAGNOSIS_JOB_MANAGE,
        }
    if permission not in permissions:
        raise SpaceAuthorizationError("SPACE_PERMISSION_DENIED", status=403)
    return SpaceAuthorizationContext(
        user=user,
        space=membership.space,
        membership=membership,
        permissions=frozenset(permissions),
    )


def lock_space_permission(*, user, space_id, permission=SPACE_MEMBER_MANAGE):
    """Call inside atomic: serialize authorization with ownership changes."""
    # Reject outsiders before taking locks, then recheck after acquiring the lock.
    require_space_permission(user=user, space_id=space_id, permission=permission)
    space = Space.objects.select_for_update().get(uuid=space_id)
    ctx = require_space_permission(
        user=user, space_id=space_id, permission=permission, lock=True
    )
    ctx = replace(ctx, space=space)
    if space.type == 2 and not space.owner_id:
        raise SpaceAuthorizationError("SPACE_OWNER_UNRESOLVED", status=409)
    return ctx


def require_member_authority(ctx, *, target=None, role=None):
    if target is not None and target.user_id == ctx.space.owner_id:
        raise SpaceAuthorizationError("SPACE_OWNER_PROTECTED", status=403)
    if (
        role == 1 or (target is not None and target.role == 1)
    ) and ctx.user.pk != ctx.space.owner_id:
        raise SpaceAuthorizationError("SPACE_OWNER_REQUIRED", status=403)


def ownership_flags(space, user):
    is_owner = space.type == 2 and space.owner_id == user.pk
    return {
        "is_owner": is_owner,
        "can_manage_admins": is_owner,
        "can_transfer_ownership": is_owner,
    }

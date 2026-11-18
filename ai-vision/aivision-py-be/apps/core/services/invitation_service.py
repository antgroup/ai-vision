import hashlib
import secrets
from datetime import timedelta

from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.core.models import (
    ObservationSubjectBinding,
    Space,
    SpaceInvitation,
    SpaceMember,
    User,
)
from apps.core.permissions.space import (
    SpaceAuthorizationError,
    lock_space_permission,
    require_member_authority,
)

from .account_service import (
    AccountServiceError,
    activate_pending_account,
    create_local_account,
)


class InvitationServiceError(Exception):
    def __init__(self, error_key, *, status=400):
        self.error_key = error_key
        self.status = status
        super().__init__(error_key)


def _hash(raw_token):
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def _new_token():
    return secrets.token_urlsafe(48)


@transaction.atomic
def create_invitations(*, space, invited_by, emails, role):
    if space.type != 2:
        raise InvitationServiceError("PERSONAL_SPACE_SINGLE_USER_ONLY", status=409)
    ctx = lock_space_permission(user=invited_by, space_id=space.uuid)
    space = ctx.space
    require_member_authority(ctx, role=role)
    results = []
    for email in dict.fromkeys(User.normalize_email(value) for value in emails):
        if not email:
            raise InvitationServiceError("VALIDATION_ERROR", status=422)
        try:
            validate_email(email)
        except ValidationError as exc:
            raise InvitationServiceError("VALIDATION_ERROR", status=422) from exc
        raw_token = _new_token()
        try:
            with transaction.atomic():
                user = (
                    User.objects.select_for_update()
                    .filter(normalized_email=email, gmt_deleted__isnull=True)
                    .first()
                )
                target = None
                if user:
                    target = (
                        SpaceMember.objects.select_for_update()
                        .filter(space=space, user=user)
                        .first()
                    )
                    if target is not None:
                        require_member_authority(ctx, target=target, role=role)
                if user and target is not None and target.gmt_deleted is None:
                    raise InvitationServiceError("ALREADY_MEMBER", status=409)

                invitation = SpaceInvitation.objects.create(
                    space=space,
                    normalized_email=email,
                    role=role,
                    token_hash=_hash(raw_token),
                    invited_by=invited_by,
                    expires_at=timezone.now() + timedelta(hours=72),
                )

                if user is None:
                    user = User(
                        name=email.split("@", 1)[0],
                        email=email,
                        normalized_email=email,
                        status=User.STATUS_PENDING,
                    )
                    user.set_unusable_password()
                    user.save()

                if user.status == User.STATUS_PENDING:
                    membership, _ = SpaceMember.objects.get_or_create(
                        space=space,
                        user=user,
                        defaults={"role": role},
                    )
                    membership.role = role
                    membership.gmt_deleted = None
                    membership.save(
                        update_fields=["role", "gmt_deleted", "gmt_modified"]
                    )
        except IntegrityError as exc:
            raise InvitationServiceError(
                "INVITATION_ALREADY_PENDING", status=409
            ) from exc
        results.append((invitation, raw_token))
    return results


def get_valid_invitation(raw_token, *, lock=False):
    queryset = SpaceInvitation.objects.select_related("space")
    if lock:
        queryset = queryset.select_for_update()
    invitation = queryset.filter(token_hash=_hash(raw_token)).first()
    if (
        invitation is None
        or invitation.status != SpaceInvitation.STATUS_PENDING
        or invitation.pending_marker != 1
        or invitation.expires_at <= timezone.now()
        or invitation.space.type != 2
        or invitation.space.status != 1
        or invitation.space.gmt_deleted is not None
    ):
        raise InvitationServiceError("INVITATION_INVALID", status=400)
    return invitation


def resend_invitation(invitation, actor):
    with transaction.atomic():
        ctx = lock_space_permission(user=actor, space_id=invitation.space.uuid)
        require_member_authority(ctx, role=invitation.role)
        locked = SpaceInvitation.objects.select_for_update().get(pk=invitation.pk)
        if locked.status != SpaceInvitation.STATUS_PENDING:
            raise InvitationServiceError("INVITATION_INVALID", status=409)
        _require_inviter_authority(locked)
        raw_token = _new_token()
        locked.token_hash = _hash(raw_token)
        locked.expires_at = timezone.now() + timedelta(hours=72)
        locked.save(update_fields=["token_hash", "expires_at", "gmt_modified"])
        return locked, raw_token


def revoke_invitation(invitation, revoked_by):
    with transaction.atomic():
        ctx = lock_space_permission(user=revoked_by, space_id=invitation.space.uuid)
        require_member_authority(ctx, role=invitation.role)
        locked = SpaceInvitation.objects.select_for_update().get(pk=invitation.pk)
        if locked.status != SpaceInvitation.STATUS_PENDING:
            raise InvitationServiceError("INVITATION_INVALID", status=409)
        locked.status = SpaceInvitation.STATUS_REVOKED
        locked.pending_marker = None
        locked.revoked_by = revoked_by
        locked.revoked_at = timezone.now()
        locked.save(
            update_fields=[
                "status",
                "pending_marker",
                "revoked_by",
                "revoked_at",
                "gmt_modified",
            ]
        )
        pending_user = (
            User.objects.select_for_update()
            .filter(
                normalized_email=locked.normalized_email,
                status=User.STATUS_PENDING,
                gmt_deleted__isnull=True,
            )
            .first()
        )
        if pending_user is not None:
            target = (
                SpaceMember.objects.select_for_update()
                .filter(space=ctx.space, user=pending_user)
                .first()
            )
            require_member_authority(ctx, target=target)
            SpaceMember.objects.filter(
                space=locked.space,
                user=pending_user,
            ).delete()
            has_membership = SpaceMember.objects.filter(user=pending_user).exists()
            has_pending_invitation = SpaceInvitation.objects.filter(
                normalized_email=locked.normalized_email,
                status=SpaceInvitation.STATUS_PENDING,
                pending_marker=1,
            ).exists()
            if not has_membership and not has_pending_invitation:
                pending_user.delete()


def accept_invitation(
    *,
    raw_token,
    authenticated_user=None,
    name=None,
    password=None,
    source_ip=None,
    user_agent=None,
    accept_language="",
):
    tokens = None
    with transaction.atomic():
        initial = get_valid_invitation(raw_token)
        Space.objects.select_for_update().get(pk=initial.space_id)
        invitation = get_valid_invitation(raw_token, lock=True)
        _require_inviter_authority(invitation)
        if (
            SpaceMember.objects.select_for_update()
            .filter(
                space=invitation.space,
                user__normalized_email=invitation.normalized_email,
                user__status=1,
                user__gmt_deleted__isnull=True,
                gmt_deleted__isnull=True,
            )
            .exists()
        ):
            raise InvitationServiceError("ALREADY_MEMBER", status=409)
        user = authenticated_user
        if user is not None:
            if user.normalized_email != invitation.normalized_email:
                raise InvitationServiceError("INVITATION_EMAIL_MISMATCH", status=403)
        else:
            existing_user = (
                User.objects.select_for_update()
                .filter(
                    normalized_email=invitation.normalized_email,
                    gmt_deleted__isnull=True,
                )
                .first()
            )
            if existing_user and existing_user.status == User.STATUS_PENDING:
                try:
                    _personal_space, tokens = activate_pending_account(
                        user=existing_user,
                        name=name,
                        password=password,
                        source_ip=source_ip,
                        user_agent=user_agent,
                        accept_language=accept_language,
                    )
                except AccountServiceError as exc:
                    raise InvitationServiceError(
                        exc.error_key, status=exc.status
                    ) from exc
                user = existing_user
            elif existing_user is not None:
                raise InvitationServiceError("INVITATION_LOGIN_REQUIRED", status=401)
            elif not name or not password:
                raise InvitationServiceError("VALIDATION_ERROR", status=422)
            else:
                try:
                    user, _personal_space, tokens = create_local_account(
                        name=name,
                        email=invitation.normalized_email,
                        password=password,
                        source_ip=source_ip,
                        user_agent=user_agent,
                        accept_language=accept_language,
                    )
                except AccountServiceError as exc:
                    raise InvitationServiceError(
                        exc.error_key, status=exc.status
                    ) from exc

        membership, _ = SpaceMember.objects.get_or_create(
            space=invitation.space,
            user=user,
            defaults={"role": invitation.role},
        )
        membership.role = invitation.role
        membership.gmt_deleted = None
        membership.joined_at = timezone.now()
        membership.save(
            update_fields=["role", "gmt_deleted", "joined_at", "gmt_modified"]
        )
        ObservationSubjectBinding.objects.update_or_create(
            space=invitation.space,
            source_system="collector",
            subject_id=user.uuid,
            defaults={
                "user": user,
                "binding_source": "account_default",
                "status": 1,
                "created_by": user,
                "gmt_deleted": None,
            },
        )
        invitation.status = SpaceInvitation.STATUS_ACCEPTED
        invitation.pending_marker = None
        invitation.accepted_by = user
        invitation.accepted_at = timezone.now()
        invitation.save(
            update_fields=[
                "status",
                "pending_marker",
                "accepted_by",
                "accepted_at",
                "gmt_modified",
            ]
        )
        return invitation, user, tokens


def _require_inviter_authority(invitation):
    try:
        ctx = lock_space_permission(
            user=invitation.invited_by,
            space_id=invitation.space.uuid,
        )
        target = (
            SpaceMember.objects.select_for_update()
            .filter(
                space=ctx.space,
                user__normalized_email=invitation.normalized_email,
                user__gmt_deleted__isnull=True,
            )
            .first()
        )
        require_member_authority(ctx, role=invitation.role, target=target)
    except SpaceAuthorizationError as exc:
        if exc.error_key == "SPACE_OWNER_UNRESOLVED":
            raise
        raise InvitationServiceError("INVITATION_AUTHORITY_LOST", status=403) from exc

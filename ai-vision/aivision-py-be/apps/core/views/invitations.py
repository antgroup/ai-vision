from django.conf import settings
from rest_framework.authentication import CSRFCheck
from rest_framework.exceptions import PermissionDenied, Throttled
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from apps.core.auth_cookies import set_local_session_cookies
from apps.core.authentication import LocalSessionAuthentication
from apps.core.models import SpaceInvitation, User
from apps.core.permissions.constants import SPACE_MEMBER_MANAGE
from apps.core.permissions.space import (
    SpaceAuthorizationError,
    require_space_permission,
)
from apps.core.services.auth_audit_service import client_ip, write_auth_audit
from apps.core.services.invitation_service import (
    InvitationServiceError,
    accept_invitation,
    create_invitations,
    get_valid_invitation,
    resend_invitation,
    revoke_invitation,
)


def _success(data, status=200):
    return Response({"code": 0, "message": "success", "data": data}, status=status)


def _error(error_key, status):
    return Response(
        {"code": 4000, "message": error_key, "data": {"error_key": error_key}},
        status=status,
    )


def _invitation_dto(invitation):
    return {
        "invitation_uuid": invitation.invitation_uuid,
        "email": invitation.normalized_email,
        "role": invitation.role,
        "status": invitation.status,
        "expires_at": invitation.expires_at,
        "invited_at": invitation.gmt_create,
        "invited_by_uuid": invitation.invited_by.uuid,
    }


def _invite_url(raw_token):
    base = settings.SITE_URL.rstrip("/")
    return f"{base}/invitations/accept?token={raw_token}"


class InvitationExceptionMixin:
    def handle_exception(self, exc):
        if isinstance(exc, InvitationServiceError):
            return _error(exc.error_key, exc.status)
        if isinstance(exc, SpaceAuthorizationError):
            return _error(exc.error_key, exc.status)
        if isinstance(exc, Throttled):
            return _error("RATE_LIMITED", 429)
        return super().handle_exception(exc)


class SpaceInvitationListCreateView(InvitationExceptionMixin, APIView):
    authentication_classes = [LocalSessionAuthentication]
    permission_classes = [IsAuthenticated]

    def get(self, request, space_id):
        ctx = require_space_permission(
            user=request.user, space_id=space_id, permission=SPACE_MEMBER_MANAGE
        )
        invitations = SpaceInvitation.objects.filter(space=ctx.space).order_by(
            "-gmt_create"
        )
        return _success({"items": [_invitation_dto(item) for item in invitations]})

    def post(self, request, space_id):
        ctx = require_space_permission(
            user=request.user, space_id=space_id, permission=SPACE_MEMBER_MANAGE
        )
        emails = request.data.get("emails")
        role = request.data.get("role")
        if not isinstance(emails, list) or not emails or role not in {1, 2, 3}:
            raise InvitationServiceError("VALIDATION_ERROR", status=422)
        invitations = create_invitations(
            space=ctx.space,
            invited_by=request.user,
            emails=emails,
            role=role,
        )
        write_auth_audit(
            request,
            event="invitation_create",
            success=True,
            actor_user=request.user,
            space=ctx.space,
            session_uuid=request.auth.session_uuid,
        )
        return _success(
            {
                "items": [
                    _invitation_dto(invitation) | {"invite_url": _invite_url(raw_token)}
                    for invitation, raw_token in invitations
                ]
            },
            status=201,
        )


class SpaceInvitationActionView(InvitationExceptionMixin, APIView):
    authentication_classes = [LocalSessionAuthentication]
    permission_classes = [IsAuthenticated]
    action = None

    def post(self, request, space_id, invitation_uuid):
        ctx = require_space_permission(
            user=request.user, space_id=space_id, permission=SPACE_MEMBER_MANAGE
        )
        invitation = SpaceInvitation.objects.filter(
            invitation_uuid=invitation_uuid, space=ctx.space
        ).first()
        if invitation is None:
            raise InvitationServiceError("INVITATION_INVALID", status=404)
        data = {}
        if self.action == "revoke":
            revoke_invitation(invitation, request.user)
        elif self.action == "resend":
            invitation, raw_token = resend_invitation(invitation, request.user)
            data = _invitation_dto(invitation) | {"invite_url": _invite_url(raw_token)}
        write_auth_audit(
            request,
            event=f"invitation_{self.action}",
            success=True,
            actor_user=request.user,
            space=ctx.space,
            session_uuid=request.auth.session_uuid,
        )
        return _success(data)


class SpaceInvitationRevokeView(SpaceInvitationActionView):
    action = "revoke"


class SpaceInvitationResendView(SpaceInvitationActionView):
    action = "resend"


class PublicInvitationView(InvitationExceptionMixin, APIView):
    authentication_classes = [LocalSessionAuthentication]
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        check = CSRFCheck(lambda _request: None)
        check.process_request(request)
        reason = check.process_view(request, None, (), {})
        if reason:
            raise PermissionDenied(f"CSRF Failed: {reason}")


class InvitationCheckView(PublicInvitationView):
    throttle_scope = "invitation_check"

    def post(self, request):
        invitation = get_valid_invitation(request.data.get("token") or "")
        return _success(
            {
                "valid": True,
                "space_name": invitation.space.name,
                "email": invitation.normalized_email,
                "role": invitation.role,
                "account_exists": User.objects.filter(
                    normalized_email=invitation.normalized_email,
                    status=1,
                    gmt_deleted__isnull=True,
                ).exists(),
            }
        )


class InvitationAcceptView(PublicInvitationView):
    throttle_scope = "invitation_accept"

    def post(self, request):
        password = request.data.get("password")
        if password != request.data.get("password_confirm"):
            raise InvitationServiceError("VALIDATION_ERROR", status=422)
        invitation, user, tokens = accept_invitation(
            raw_token=request.data.get("token") or "",
            authenticated_user=(
                request.user
                if getattr(request.user, "is_authenticated", False)
                else None
            ),
            name=request.data.get("name"),
            password=password,
            source_ip=client_ip(request),
            user_agent=request.META.get("HTTP_USER_AGENT"),
            accept_language=request.headers.get("Accept-Language", ""),
        )
        write_auth_audit(
            request,
            event="invitation_accept",
            success=True,
            actor_user=user,
            space=invitation.space,
            session_uuid=(
                tokens.session.session_uuid if tokens else request.auth.session_uuid
            ),
        )
        response = _success(
            {
                "user_uuid": user.uuid,
                "space_id": invitation.space.uuid,
                "role": invitation.role,
            }
        )
        if tokens:
            set_local_session_cookies(response, tokens)
        return response

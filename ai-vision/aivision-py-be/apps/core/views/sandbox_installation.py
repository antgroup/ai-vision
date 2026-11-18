"""Space management sessions and exclusive outbound CLI progress ingress."""

import io
import re

from django.db import transaction
from django.utils import timezone
from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import APIException, AuthenticationFailed
from rest_framework.parsers import JSONParser
from rest_framework.views import APIView

from apps.core.models import SandboxInstallationSession
from apps.core.permissions.constants import SANDBOX_CONNECTION_MANAGE
from apps.core.permissions.space import (
    SpaceAuthorizationError,
    require_space_permission,
)
from apps.core.services.sandbox_installation import (
    InstallationError,
    MAX_REQUEST_BYTES,
    TERMINAL,
    TOKEN_PREFIX,
    create_installation,
    digest,
    receive_events,
    snapshot,
)
from .common import error_response, success_response


class InstallationPayloadTooLarge(APIException):
    status_code = 413
    default_detail = "SANDBOX_INSTALLATION_TOO_LARGE"


class BoundedInstallationParser(JSONParser):
    def parse(self, stream, media_type=None, parser_context=None):
        payload = stream.read(MAX_REQUEST_BYTES + 1)
        if len(payload) > MAX_REQUEST_BYTES:
            raise InstallationPayloadTooLarge()
        return super().parse(io.BytesIO(payload), media_type, parser_context)


class PrivateInstallationView(APIView):
    parser_classes = [BoundedInstallationParser]

    def finalize_response(self, request, response, *args, **kwargs):
        response = super().finalize_response(request, response, *args, **kwargs)
        response["Cache-Control"] = "no-store"
        return response


def _error(exc):
    return error_response(exc.code, status=exc.status)


def _authorize(request, space_id):
    return require_space_permission(
        user=request.user, space_id=space_id, permission=SANDBOX_CONNECTION_MANAGE
    )


class SandboxInstallationListView(PrivateInstallationView):
    def get(self, request, space_id):
        ctx = _authorize(request, space_id)
        rows = (
            SandboxInstallationSession.objects.select_related("space")
            .filter(space=ctx.space)
            .order_by("-gmt_create", "-id")[:20]
        )
        items = [snapshot(row) for row in rows]
        return success_response({"items": items, "total": len(items)})

    def post(self, request, space_id):
        _authorize(request, space_id)
        try:
            return success_response(
                create_installation(
                    user=request.user, space_id=space_id, body=request.data
                )
            )
        except InstallationError as exc:
            return _error(exc)


class SandboxInstallationDetailView(PrivateInstallationView):
    def get(self, request, space_id, installation_uuid):
        ctx = _authorize(request, space_id)
        session = (
            SandboxInstallationSession.objects.select_related("space")
            .filter(space=ctx.space, uuid=installation_uuid)
            .first()
        )
        if session is None:
            return error_response("SANDBOX_INSTALLATION_NOT_FOUND", status=404)
        return success_response(snapshot(session))

    @transaction.atomic
    def delete(self, request, space_id, installation_uuid):
        ctx = _authorize(request, space_id)
        session = (
            SandboxInstallationSession.objects.select_for_update()
            .select_related("space")
            .filter(space=ctx.space, uuid=installation_uuid)
            .first()
        )
        if session is None:
            return error_response("SANDBOX_INSTALLATION_NOT_FOUND", status=404)
        session.revoked_at = timezone.now()
        if session.status not in TERMINAL:
            session.status = "revoked"
        session.save(update_fields=["revoked_at", "status", "gmt_modified"])
        return success_response(snapshot(session))


class SandboxProgressAuthentication(BaseAuthentication):
    """This credential grants only ingress to one bounded progress session."""

    token_prefix = TOKEN_PREFIX
    token_field = "report_token_hash"

    def authenticate_header(self, request):
        return "Bearer"

    def authenticate(self, request):
        raw_header = request.META.get("HTTP_AUTHORIZATION", "")
        raw = raw_header[7:] if raw_header.startswith("Bearer ") else ""
        if request.META.get("HTTP_COOKIE") or not re.fullmatch(
            re.escape(self.token_prefix) + r"[A-Za-z0-9_-]{43}", raw
        ):
            raise AuthenticationFailed("SANDBOX_REPORT_UNAUTHORIZED")
        session = (
            SandboxInstallationSession.objects.select_related("creator", "space")
            .filter(
                uuid=request.parser_context["kwargs"]["installation_uuid"],
                **{self.token_field: digest(raw)},
            )
            .first()
        )
        if (
            session is None
            or session.revoked_at
            or session.expires_at <= timezone.now()
        ):
            raise AuthenticationFailed("SANDBOX_REPORT_UNAUTHORIZED")
        try:
            require_space_permission(
                user=session.creator,
                space_id=session.space.uuid,
                permission=SANDBOX_CONNECTION_MANAGE,
            )
        except SpaceAuthorizationError:
            raise AuthenticationFailed("SANDBOX_REPORT_UNAUTHORIZED") from None
        return session.creator, session


class SandboxBootstrapAuthentication(SandboxProgressAuthentication):
    token_prefix = "aiv-sandbox-bootstrap-"
    token_field = "bootstrap_token_hash"


class SandboxEnrollmentAuthentication(SandboxProgressAuthentication):
    token_prefix = "aiv-sandbox-enroll-"
    token_field = "enrollment_token_hash"


class SandboxBootstrapCapabilityView(PrivateInstallationView):
    def get(self, request, space_id):
        from apps.core.services.sandbox_bootstrap import capability

        _authorize(request, space_id)
        return success_response(capability())


class SandboxBootstrapClaimView(PrivateInstallationView):
    authentication_classes = [SandboxBootstrapAuthentication]

    def post(self, request, installation_uuid):
        from apps.core.services.sandbox_bootstrap import claim

        try:
            return success_response(
                claim(
                    session_id=request.auth.pk,
                    token_hash=request.auth.bootstrap_token_hash,
                    body=request.data,
                )
            )
        except InstallationError as exc:
            return _error(exc)
        except (SpaceAuthorizationError, SandboxInstallationSession.DoesNotExist):
            return error_response("SANDBOX_REPORT_UNAUTHORIZED", status=401)


class SandboxBootstrapEnrollView(PrivateInstallationView):
    authentication_classes = [SandboxEnrollmentAuthentication]

    def post(self, request, installation_uuid):
        from apps.core.services.sandbox_bootstrap import enroll

        try:
            return success_response(
                enroll(
                    session_id=request.auth.pk,
                    token_hash=request.auth.enrollment_token_hash,
                    body=request.data,
                )
            )
        except InstallationError as exc:
            return _error(exc)
        except (SpaceAuthorizationError, SandboxInstallationSession.DoesNotExist):
            return error_response("SANDBOX_REPORT_UNAUTHORIZED", status=401)


class SandboxInstallationEnableView(PrivateInstallationView):
    def post(self, request, space_id, installation_uuid):
        from apps.core.services.sandbox_bootstrap import enable

        ctx = _authorize(request, space_id)
        session = SandboxInstallationSession.objects.filter(
            space=ctx.space, uuid=installation_uuid
        ).first()
        if session is None:
            return error_response("SANDBOX_INSTALLATION_NOT_FOUND", status=404)
        try:
            return success_response(
                enable(session_id=session.pk, actor=request.user, body=request.data)
            )
        except InstallationError as exc:
            return _error(exc)


class SandboxInstallationEventsView(PrivateInstallationView):
    authentication_classes = [SandboxProgressAuthentication]

    def post(self, request, installation_uuid):
        try:
            return success_response(
                receive_events(
                    session_id=request.auth.pk,
                    token_hash=request.auth.report_token_hash,
                    body=request.data,
                )
            )
        except InstallationError as exc:
            return _error(exc)
        except (SpaceAuthorizationError, SandboxInstallationSession.DoesNotExist):
            return error_response("SANDBOX_REPORT_UNAUTHORIZED", status=401)

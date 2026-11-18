from django.conf import settings
from django.middleware.csrf import get_token
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import ensure_csrf_cookie
from rest_framework import status
from rest_framework.authentication import CSRFCheck
from rest_framework.exceptions import PermissionDenied, Throttled
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from apps.core.auth_cookies import (
    REFRESH_COOKIE,
    delete_local_session_cookies,
    set_local_session_cookies,
)
from apps.core.authentication import LocalSessionAuthentication
from apps.core.serializers_auth import (
    LoginSerializer,
    RegisterSerializer,
)
from apps.core.services.account_service import (
    AccountServiceError,
    authenticate_local_account,
    create_local_account,
)
from apps.core.services.auth_audit_service import client_ip, write_auth_audit
from apps.core.services.local_session_service import (
    LocalSessionError,
    revoke_session,
    rotate_refresh,
)


def _user_dto(user):
    return {
        "user_uuid": user.uuid,
        "name": user.name,
        "email": user.normalized_email,
        "avatar_url": user.avatar_url,
        "status": user.status,
    }


def _space_dto(space):
    return {"space_id": space.uuid, "name": space.name, "type": space.type}


def _success(data, *, response_status=200):
    return Response(
        {"code": 0, "message": "success", "data": data}, status=response_status
    )


def _error(error_key, *, response_status, fields=None):
    data = {"error_key": error_key}
    if fields:
        data["fields"] = fields
    return Response(
        {"code": 4000, "message": error_key, "data": data},
        status=response_status,
    )


class AnonymousLocalAuthView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        if request.method not in {"GET", "HEAD", "OPTIONS", "TRACE"}:
            check = CSRFCheck(lambda _request: None)
            check.process_request(request)
            reason = check.process_view(request, None, (), {})
            if reason:
                raise PermissionDenied(f"CSRF Failed: {reason}")

    def handle_exception(self, exc):
        if isinstance(exc, Throttled):
            return _error("RATE_LIMITED", response_status=429)
        if isinstance(exc, AccountServiceError):
            return _error(exc.error_key, response_status=exc.status, fields=exc.fields)
        if isinstance(exc, LocalSessionError):
            return _error(exc.error_key, response_status=exc.status)
        return super().handle_exception(exc)


@method_decorator(ensure_csrf_cookie, name="dispatch")
class CsrfView(AnonymousLocalAuthView):
    def get(self, request):
        get_token(request)
        return _success({})


class RegisterView(AnonymousLocalAuthView):
    throttle_scope = "local_register"

    def post(self, request):
        if not settings.PUBLIC_REGISTRATION_ENABLED:
            raise AccountServiceError("PUBLIC_REGISTRATION_DISABLED", status=403)
        serializer = RegisterSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            user, space, tokens = create_local_account(
                **serializer.validated_data,
                source_ip=client_ip(request),
                user_agent=request.META.get("HTTP_USER_AGENT"),
                accept_language=request.headers.get("Accept-Language", ""),
            )
        except AccountServiceError as exc:
            write_auth_audit(
                request, event="register", success=False, error_key=exc.error_key
            )
            raise
        write_auth_audit(
            request,
            event="register",
            success=True,
            actor_user=user,
            space=space,
            session_uuid=tokens.session.session_uuid,
        )
        response = _success(
            {"user": _user_dto(user), "personal_space": _space_dto(space)},
            response_status=status.HTTP_201_CREATED,
        )
        set_local_session_cookies(response, tokens)
        return response


class LoginView(AnonymousLocalAuthView):
    throttle_scope = "local_login"

    def post(self, request):
        serializer = LoginSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            user, tokens = authenticate_local_account(
                **serializer.validated_data,
                source_ip=client_ip(request),
                user_agent=request.META.get("HTTP_USER_AGENT"),
            )
        except AccountServiceError as exc:
            write_auth_audit(
                request, event="login", success=False, error_key=exc.error_key
            )
            raise
        write_auth_audit(
            request,
            event="login",
            success=True,
            actor_user=user,
            session_uuid=tokens.session.session_uuid,
        )
        response = _success({"user": _user_dto(user)})
        set_local_session_cookies(response, tokens)
        return response


class RefreshView(AnonymousLocalAuthView):
    throttle_scope = "local_refresh"

    def post(self, request):
        raw_refresh = request.COOKIES.get(REFRESH_COOKIE)
        if not raw_refresh:
            raise LocalSessionError("SESSION_INVALID")
        try:
            tokens = rotate_refresh(raw_refresh, source_ip=client_ip(request))
        except LocalSessionError as exc:
            write_auth_audit(
                request, event="refresh", success=False, error_key=exc.error_key
            )
            raise
        write_auth_audit(
            request,
            event="refresh",
            success=True,
            actor_user=tokens.session.user,
            session_uuid=tokens.session.session_uuid,
        )
        response = _success({})
        set_local_session_cookies(response, tokens)
        return response


class LogoutView(APIView):
    authentication_classes = [LocalSessionAuthentication]
    permission_classes = [IsAuthenticated]

    def post(self, request):
        revoke_session(request.auth)
        write_auth_audit(
            request,
            event="logout",
            success=True,
            actor_user=request.user,
            session_uuid=request.auth.session_uuid,
        )
        response = _success({})
        delete_local_session_cookies(response)
        return response


class MeView(APIView):
    authentication_classes = [LocalSessionAuthentication]
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return _success({"user": _user_dto(request.user)})

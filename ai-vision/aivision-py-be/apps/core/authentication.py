import base64
import hashlib
import logging
import time

from django.conf import settings
from django.core.cache import cache
from django.db import DatabaseError, OperationalError
from django.utils import timezone
from rest_framework import authentication, exceptions
from rest_framework.authentication import CSRFCheck
from rest_framework.exceptions import APIException
from rest_framework_simplejwt.tokens import AccessToken

from .auth_cookies import ACCESS_COOKIE
from .metrics import auth_events
from .models import AuthSession

logger = logging.getLogger(__name__)


class LocalSessionAuthentication(authentication.BaseAuthentication):
    """Authenticate a browser user only from the local access HttpOnly cookie."""

    conflicting_headers = (
        "HTTP_AUTHORIZATION",
        "HTTP_X_ONE_ID",
        "HTTP_X_AGENTPASS_CLI_ID",
        "HTTP_X_AUTHORIZATION",
    )

    def authenticate_header(self, request):  # noqa: ARG002
        return "Session"

    def authenticate(self, request):
        raw_token = request.COOKIES.get(ACCESS_COOKIE)
        if not raw_token:
            return None
        if any(request.META.get(name) for name in self.conflicting_headers):
            raise exceptions.AuthenticationFailed(
                "Conflicting authentication credentials"
            )
        try:
            token = AccessToken(raw_token)
        except Exception as exc:
            raise exceptions.AuthenticationFailed("Invalid local session") from exc
        if token.get("iss") != settings.LOCAL_AUTH_ISSUER:
            raise exceptions.AuthenticationFailed("Invalid local session issuer")
        user_id = token.get("user_id")
        session_uuid = token.get("session_id")
        if not user_id or not session_uuid:
            raise exceptions.AuthenticationFailed("Invalid local session claims")
        session = (
            AuthSession.objects.select_related("user")
            .filter(
                session_uuid=session_uuid,
                user_id=user_id,
                status=AuthSession.STATUS_ACTIVE,
                expires_at__gt=timezone.now(),
                user__status=1,
                user__gmt_deleted__isnull=True,
            )
            .first()
        )
        if session is None:
            raise exceptions.AuthenticationFailed("Invalid local session")
        if request.method not in {"GET", "HEAD", "OPTIONS", "TRACE"}:
            self.enforce_csrf(request)
        request.auth_type = "local_session"
        return session.user, session

    def enforce_csrf(self, request):
        check = CSRFCheck(lambda _request: None)
        check.process_request(request)
        reason = check.process_view(request, None, (), {})
        if reason:
            raise exceptions.PermissionDenied(f"CSRF Failed: {reason}")


def is_database_contention(exc):
    message = str(exc).lower()
    return any(
        marker in message
        for marker in (
            "database is locked",
            "database table is locked",
            "deadlock",
            "lock wait timeout",
        )
    ) or bool(exc.args and exc.args[0] in {1205, 1213})


class UnifiedExternalAuthentication(authentication.BaseAuthentication):
    """Machine-only auth for SpaceKey and observation Basic credentials."""

    _SPACE_KEY_PREFIX = "sk-aiv-"

    def authenticate_header(self, request):  # noqa: ARG002
        return "Bearer"

    def authenticate(self, request):
        auth_header = request.META.get("HTTP_AUTHORIZATION", "")
        if auth_header.startswith("Bearer "):
            raw_key = auth_header[7:].strip()
            if not raw_key.startswith(self._SPACE_KEY_PREFIX):
                raise exceptions.AuthenticationFailed("Invalid Space Key")
            return self._auth_space_key(request, raw_key)
        if auth_header.startswith("Basic "):
            return self._auth_credential(request, auth_header)
        return None

    def _auth_space_key(self, request, raw_key):
        from .services.key_cache import key_cache

        key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
        space_key = key_cache.get_space_key(key_hash)
        if space_key is None:
            auth_events.add(1, {"method": "space_key", "result": "failure"})
            raise exceptions.AuthenticationFailed("Invalid or disabled Space Key")
        key_cache.mark_space_key_used(space_key.id)
        request.auth_type = "space_key"
        return None, space_key

    def _auth_credential(self, request, auth_header):
        from .services.credential_service import verify_secret
        from .services.key_cache import key_cache

        try:
            decoded = base64.b64decode(auth_header[6:]).decode("utf-8")
            public_key, secret_key = decoded.split(":", 1)
        except (ValueError, UnicodeDecodeError):
            raise exceptions.AuthenticationFailed("Invalid Basic Auth format") from None
        credential = key_cache.get_credential(public_key)
        if credential is None or not verify_secret(secret_key, credential.secret_hash):
            raise exceptions.AuthenticationFailed("Invalid credentials")
        if credential.scope != "READ":
            raise exceptions.PermissionDenied("Write key cannot access read-only API")
        key_cache.mark_credential_used(
            credential.id, request.META.get("REMOTE_ADDR", "")
        )
        request.observation_credential = credential
        request.observation_project = credential.space_observation_project
        request.observation_endpoint = (
            credential.space_observation_project.endpoint_config
        )
        request.auth_type = "credential"
        return None, credential


class SpaceKeyAuthentication(UnifiedExternalAuthentication):
    """Authenticate Space OpenAPI exclusively with a SpaceKey bearer token."""

    def authenticate(self, request):
        auth_header = request.META.get("HTTP_AUTHORIZATION", "")
        if not auth_header.startswith("Bearer "):
            raise exceptions.AuthenticationFailed("Invalid or missing Space Key")
        raw_key = auth_header[7:].strip()
        if not raw_key.startswith(self._SPACE_KEY_PREFIX):
            raise exceptions.AuthenticationFailed("Invalid Space Key")
        return self._auth_space_key(request, raw_key)


class AgentIngestAuthentication(SpaceKeyAuthentication):
    """Agent credentials are accepted only by installation/ingest endpoints."""

    def authenticate(self, request):
        from .models import AgentInstallation
        from .services.agent_installation import (
            AGENT_CREDENTIAL_PREFIX,
            ingest_credential_is_active,
        )

        header = request.META.get("HTTP_AUTHORIZATION", "")
        raw = header[7:].strip() if header.startswith("Bearer ") else ""
        if not raw.startswith(AGENT_CREDENTIAL_PREFIX):
            return super().authenticate(request)
        credential = (
            AgentInstallation.objects.select_related("agent__space", "creator")
            .filter(credential_hash=AgentInstallation.digest(raw), enabled=True)
            .first()
        )
        if credential is None or not ingest_credential_is_active(credential):
            raise exceptions.AuthenticationFailed("Invalid or revoked Agent credential")
        request.auth_type = "agent_ingest"
        return None, credential

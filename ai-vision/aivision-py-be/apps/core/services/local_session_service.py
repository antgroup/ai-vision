import hashlib
import secrets
from dataclasses import dataclass
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from rest_framework_simplejwt.tokens import AccessToken

from apps.core.models import AuthRefreshToken, AuthSession


class LocalSessionError(Exception):
    def __init__(self, error_key, *, status=401):
        self.error_key = error_key
        self.status = status
        super().__init__(error_key)


@dataclass(frozen=True)
class SessionTokens:
    access_token: str
    refresh_token: str
    access_expires_at: object
    refresh_expires_at: object
    session: AuthSession


def hash_token(raw_token):
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def _new_access_token(user, session):
    token = AccessToken()
    token.set_exp(lifetime=timedelta(seconds=settings.LOCAL_ACCESS_TTL_SECONDS))
    token["user_id"] = user.pk
    token["session_id"] = session.session_uuid
    token["iss"] = settings.LOCAL_AUTH_ISSUER
    return token


def create_session(user, *, source_ip=None, user_agent=None):
    now = timezone.now()
    refresh_expires_at = now + timedelta(seconds=settings.LOCAL_REFRESH_TTL_SECONDS)
    raw_refresh = secrets.token_urlsafe(48)
    session = AuthSession.objects.create(
        user=user,
        expires_at=refresh_expires_at,
        created_ip=source_ip,
        user_agent=(user_agent or "")[:512] or None,
    )
    AuthRefreshToken.objects.create(
        session=session,
        token_hash=hash_token(raw_refresh),
        generation=0,
        expires_at=refresh_expires_at,
    )
    access = _new_access_token(user, session)
    return SessionTokens(
        access_token=str(access),
        refresh_token=raw_refresh,
        access_expires_at=now + timedelta(seconds=settings.LOCAL_ACCESS_TTL_SECONDS),
        refresh_expires_at=refresh_expires_at,
        session=session,
    )


def rotate_refresh(raw_refresh, *, source_ip=None):
    now = timezone.now()
    replay_error = None
    result = None
    with transaction.atomic():
        refresh = (
            AuthRefreshToken.objects.select_for_update()
            .select_related("session__user")
            .filter(token_hash=hash_token(raw_refresh))
            .first()
        )
        if refresh is None:
            raise LocalSessionError("SESSION_INVALID")
        session = AuthSession.objects.select_for_update().get(pk=refresh.session_id)
        user = refresh.session.user
        if (
            refresh.revoked_at is not None
            or refresh.expires_at <= now
            or session.status != AuthSession.STATUS_ACTIVE
            or session.expires_at <= now
            or user.status != 1
            or user.gmt_deleted is not None
        ):
            raise LocalSessionError("SESSION_INVALID")
        if refresh.consumed_at is not None:
            grace = timedelta(seconds=settings.LOCAL_REFRESH_REPLAY_GRACE_SECONDS)
            if now - refresh.consumed_at <= grace:
                raise LocalSessionError("REFRESH_ALREADY_ROTATED", status=409)
            session.status = AuthSession.STATUS_REVOKED
            session.revoked_at = now
            session.revoke_reason = "refresh_replay"
            session.save(
                update_fields=["status", "revoked_at", "revoke_reason", "gmt_modified"]
            )
            replay_error = LocalSessionError("REFRESH_REPLAY_DETECTED")
        else:
            refresh.consumed_at = now
            refresh.save(update_fields=["consumed_at"])
            raw_next = secrets.token_urlsafe(48)
            AuthRefreshToken.objects.create(
                session=session,
                token_hash=hash_token(raw_next),
                generation=refresh.generation + 1,
                expires_at=session.expires_at,
            )
            session.last_used_at = now
            session.last_used_ip = source_ip
            session.save(update_fields=["last_used_at", "last_used_ip", "gmt_modified"])
            access = _new_access_token(user, session)
            result = SessionTokens(
                access_token=str(access),
                refresh_token=raw_next,
                access_expires_at=now
                + timedelta(seconds=settings.LOCAL_ACCESS_TTL_SECONDS),
                refresh_expires_at=session.expires_at,
                session=session,
            )
    if replay_error:
        raise replay_error
    return result


def revoke_session(session, reason="logout"):
    now = timezone.now()
    with transaction.atomic():
        locked = AuthSession.objects.select_for_update().get(pk=session.pk)
        locked.status = AuthSession.STATUS_REVOKED
        locked.revoked_at = now
        locked.revoke_reason = reason
        locked.save(
            update_fields=["status", "revoked_at", "revoke_reason", "gmt_modified"]
        )
        locked.refresh_tokens.filter(revoked_at__isnull=True).update(revoked_at=now)

"""Cookie helpers for the local account session."""

from django.conf import settings
from django.utils import timezone


ACCESS_COOKIE = "aivision_access"
REFRESH_COOKIE = "aivision_refresh"


def _auth_cookie_attrs(path="/") -> dict:
    secure = getattr(
        settings,
        "AUTH_COOKIE_SECURE",
        not getattr(settings, "DEBUG", False),
    )
    return {"path": path, "secure": secure, "samesite": "Lax"}


def set_auth_cookie(response, key: str, value: str, *, httponly: bool = False) -> None:
    response.set_cookie(key, value, httponly=httponly, **_auth_cookie_attrs())


def delete_auth_cookie(response, key: str) -> None:
    attrs = _auth_cookie_attrs()
    response.delete_cookie(key, path=attrs["path"], samesite=attrs["samesite"])


def set_local_session_cookies(response, tokens):
    response.set_cookie(
        ACCESS_COOKIE,
        tokens.access_token,
        httponly=True,
        max_age=max(
            0, int((tokens.access_expires_at - timezone.now()).total_seconds())
        ),
        **_auth_cookie_attrs("/"),
    )
    response.set_cookie(
        REFRESH_COOKIE,
        tokens.refresh_token,
        httponly=True,
        max_age=max(
            0, int((tokens.refresh_expires_at - timezone.now()).total_seconds())
        ),
        **_auth_cookie_attrs("/api/v1/auth/refresh"),
    )


def delete_local_session_cookies(response):
    for key, path in ((ACCESS_COOKIE, "/"), (REFRESH_COOKIE, "/api/v1/auth/refresh")):
        attrs = _auth_cookie_attrs(path)
        response.delete_cookie(key, path=path, samesite=attrs["samesite"])

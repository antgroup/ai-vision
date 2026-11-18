"""Signing/verification of aivision OTLP ingest tokens (plan subsystem 3, 丙).

The control plane is the single authority on tenant identity. At agent
onboarding it issues a short-lived, self-describing token whose claims carry the
authoritative Space, organization, observation-subject, and Agent identities. The ingest gateway
(antcollector ``aivisionauth``) verifies these offline with the shared
``AIVISION_INGEST_SECRET`` and never trusts a client-reported ``space.id``, so
the storage/query partition can no longer drift when a client's config goes
stale — the root cause of the empty Codex dashboard.

Wire format (must stay byte-compatible with the Go verifier in
``antcollector/extension/aivisionauthextension/token.go``)::

    base64url( json(payload) ) + "." + base64url( HMAC_SHA256(secret, b64url(json)) )

base64url uses the URL-safe alphabet with padding stripped (Go RawURLEncoding).
Payload keys mirror the Go ``tokenPayload`` struct tags: sp/org/u/kid/exp.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json

from django.conf import settings
from django.utils import timezone


class IngestTokenError(Exception):
    """Raised when a token cannot be signed or verified."""


REQUIRED_CLAIMS = ("v", "sp", "u", "kid", "aid", "ap", "iat", "exp")


def _secret() -> bytes:
    secret = getattr(settings, "AIVISION_INGEST_SECRET", "") or ""
    if not secret:
        raise IngestTokenError("AIVISION_INGEST_SECRET is not configured")
    return secret.encode("utf-8")


def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("utf-8")


def _b64url_decode(value: str) -> bytes:
    padding = 4 - len(value) % 4
    if padding != 4:
        value += "=" * padding
    return base64.urlsafe_b64decode(value.encode("utf-8"))


def _sign(payload_b64: str, secret: bytes) -> str:
    digest = hmac.new(secret, payload_b64.encode("utf-8"), hashlib.sha256).digest()
    return _b64url_encode(digest)


def build_ingest_token(
    *,
    space_uuid: str,
    org_id: str = "",
    subject_id: str,
    key_id: str,
    agent_uuid: str,
    agent_product: str,
    ttl_seconds: int | None = None,
    secret: bytes | None = None,
) -> tuple[str, int]:
    """Sign an ingest token and return ``(token, expires_at_unix)``."""
    if not space_uuid:
        raise IngestTokenError("space_uuid is required")
    key = secret if secret is not None else _secret()
    ttl = (
        ttl_seconds
        if ttl_seconds is not None
        else getattr(settings, "AIVISION_INGEST_TOKEN_TTL_SECONDS", 15 * 60)
    )
    if not all((subject_id, key_id, agent_uuid, agent_product)):
        raise IngestTokenError("v2 ingest identity claims are required")
    iat = int(timezone.now().timestamp())
    exp = iat + int(ttl)
    claims = {
        "v": 2,
        "sp": space_uuid,
        "u": subject_id,
        "kid": key_id,
        "aid": agent_uuid,
        "ap": agent_product,
        "iat": iat,
        "exp": exp,
    }
    if org_id:
        claims["org"] = org_id

    payload_b64 = _b64url_encode(
        json.dumps(claims, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    )
    token = f"{payload_b64}.{_sign(payload_b64, key)}"
    return token, exp


def verify_ingest_token(token: str, secret: bytes | None = None) -> dict:
    """Verify an ingest token offline and return its claims.

    Raises ``IngestTokenError`` on empty/malformed/tampered/expired input,
    mirroring the gateway's aivisionauth behaviour. Used by tests and the
    transitional verify endpoint.
    """
    key = secret if secret is not None else _secret()
    token = (token or "").strip()
    if token.lower().startswith("bearer "):
        token = token[7:].strip()
    if not token:
        raise IngestTokenError("empty token")

    payload_b64, _, sig = token.partition(".")
    if not payload_b64 or not sig:
        raise IngestTokenError("malformed token, expected payload.signature")

    if not hmac.compare_digest(_sign(payload_b64, key), sig):
        raise IngestTokenError("signature mismatch")

    try:
        claims = json.loads(_b64url_decode(payload_b64).decode("utf-8"))
    except (ValueError, json.JSONDecodeError) as exc:
        raise IngestTokenError("cannot parse claims") from exc
    if not isinstance(claims, dict):
        raise IngestTokenError("claims must be an object")

    for field in REQUIRED_CLAIMS:
        if claims.get(field) in (None, ""):
            raise IngestTokenError(f"missing claim: {field}")
    try:
        version = int(claims["v"])
        issued_at = int(claims["iat"])
        expires_at = int(claims["exp"])
    except (TypeError, ValueError) as exc:
        raise IngestTokenError("invalid numeric claim") from exc
    if version != 2:
        raise IngestTokenError("unsupported token version")
    now = int(timezone.now().timestamp())
    if issued_at > now:
        raise IngestTokenError("token issued in the future")
    if expires_at <= now:
        raise IngestTokenError("token expired")
    return claims

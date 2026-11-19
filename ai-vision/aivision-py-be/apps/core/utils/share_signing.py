import base64
import hashlib
import hmac
import json
import logging

from django.conf import settings
from django.utils import timezone

from ..models import PlatformConfig

logger = logging.getLogger("aivision.share")

SHARE_LINK_VERSION = 1
REQUIRED_CLAIMS = ("t", "id", "s", "a", "uid", "sub", "iat", "exp", "v")



class ShareLinkValidationError(ValueError):
    """A share-link validation error safe to present to its reader."""

    def __init__(self, public_message: str):
        super().__init__(public_message)
        self.public_message = public_message


def get_share_link_ttl() -> int:
    try:
        config = PlatformConfig.objects.filter(
            platform_code="share_link",
            status=1,
        ).first()
        if config and config.config:
            ttl = config.config.get("ttl_seconds")
            if isinstance(ttl, int) and ttl > 0:
                return ttl
    except Exception:
        pass
    return getattr(settings, "SHARE_LINK_TTL_SECONDS", 7 * 24 * 3600)


def _get_signing_key() -> bytes:
    key = getattr(settings, "SHARE_LINK_SIGNING_KEY", settings.SECRET_KEY)
    return key.encode("utf-8")


def build_share_payload(claims: dict) -> str:
    payload_bytes = json.dumps(
        claims, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")
    return base64.urlsafe_b64encode(payload_bytes).rstrip(b"=").decode("utf-8")


def sign_share_payload(payload_raw: str) -> str:
    return hmac.new(
        _get_signing_key(),
        payload_raw.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def _decode_base64url(value: str) -> bytes:
    if not value:
        raise ShareLinkValidationError("Empty payload")
    padding = 4 - len(value) % 4
    if padding != 4:
        value += "=" * padding
    return base64.urlsafe_b64decode(value)


def parse_share_payload(payload_raw: str) -> dict:
    try:
        data = json.loads(_decode_base64url(payload_raw).decode("utf-8"))
    except (ValueError, json.JSONDecodeError) as e:
        raise ShareLinkValidationError("Invalid share link payload") from e
    if not isinstance(data, dict):
        raise ShareLinkValidationError("Invalid share link payload")
    return data


def validate_share_claims(claims: dict) -> None:
    for field in REQUIRED_CLAIMS:
        val = claims.get(field)
        if val is None or val == "":
            raise ShareLinkValidationError(f"Missing required field: {field}")

    if claims["v"] != SHARE_LINK_VERSION:
        raise ShareLinkValidationError("Unsupported share link version")

    iat = claims["iat"]
    exp = claims["exp"]
    if not isinstance(iat, int) or not isinstance(exp, int):
        raise ShareLinkValidationError("iat and exp must be integers")
    if exp <= iat:
        raise ShareLinkValidationError("Invalid expiry window")

    now_ts = int(timezone.now().timestamp())
    if iat > now_ts or exp <= now_ts:
        raise ShareLinkValidationError("Share link expired")


def verify_share_signature(payload_raw: str, sig: str) -> bool:
    expected = sign_share_payload(payload_raw)
    return hmac.compare_digest(expected, sig)


def parse_and_verify_share(payload_raw: str, sig: str) -> dict:
    if not payload_raw or not sig:
        raise ShareLinkValidationError("Missing payload or signature")

    claims = parse_share_payload(payload_raw)
    validate_share_claims(claims)

    if not verify_share_signature(payload_raw, sig):
        raise ShareLinkValidationError("Invalid share link signature")

    return claims


def log_share_event(action: str, claims: dict, user, request=None):
    resource_type = claims.get("t", "?")
    resource_id = claims.get("id", "?")
    space_id = claims.get("s", "?")
    agent_product = claims.get("a", "?")
    operator = getattr(user, "name", "") or getattr(user, "uuid", "anonymous")

    logger.info(
        "share_link_%s operator=%s type=%s id=%s space=%s agent=%s",
        action,
        operator,
        resource_type,
        resource_id,
        space_id,
        agent_product,
        extra={
            "action": action,
            "resource_type": resource_type,
            "resource_id": resource_id,
            "space_id": space_id,
            "agent_product": agent_product,
            "operator_id": getattr(user, "uuid", ""),
            "operator_name": getattr(user, "name", ""),
            "expires_at": claims.get("exp"),
        },
    )

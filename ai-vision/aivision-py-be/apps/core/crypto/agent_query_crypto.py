"""AES-GCM decryption for V2 agent query encrypted payloads.

Protocol version: v1
Key management: multiple keys via ``kid`` for rotation support.
"""

from __future__ import annotations

import base64
import json
import logging

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from django.conf import settings

from ..query.errors import AgentQueryDecryptFailedError, AgentQueryInvalidPayloadError

logger = logging.getLogger(__name__)


def is_encryption_enabled() -> bool:
    """Return whether encrypted query mode is enabled."""
    return getattr(settings, "AGENT_QUERY_ENCRYPTION_ENABLED", True)


def is_plain_allowed() -> bool:
    """Return whether plain (unencrypted) query mode is allowed."""
    return getattr(settings, "AGENT_QUERY_ALLOW_PLAIN", False)


def get_encryption_key(kid: str) -> bytes:
    """Retrieve a 256-bit AES key by key id.

    The key is stored in settings as a base64url-encoded string inside
    ``AGENT_QUERY_ENCRYPTION_KEYS`` which is a JSON dict mapping kid to
    the encoded key.

    Raises ``AgentQueryDecryptFailedError`` if the kid is not found.
    """
    keys: dict = getattr(settings, "AGENT_QUERY_ENCRYPTION_KEYS", {})
    if kid not in keys:
        raise AgentQueryDecryptFailedError(
            message=f"Unknown key id: {kid}",
            details={"kid": kid},
        )
    try:
        return base64url_decode(keys[kid])
    except Exception as exc:
        raise AgentQueryDecryptFailedError(
            message="Failed to process request data",
            details={"kid": kid},
        ) from exc


def decrypt_agent_query_payload(encrypted_data: dict) -> dict:
    """Decrypt an AES-GCM encrypted query payload.

    Expected ``encrypted_data`` structure::

        {
            "payloadVersion": "v1",
            "kid": "dashboard-query-v1",
            "iv": "<base64url-encoded 12-byte nonce>",
            "encryptedPayload": "<base64url-encoded ciphertext + auth tag>"
        }

    Returns the decrypted payload as a dict.

    Raises:
        AgentQueryDecryptFailedError: on crypto or key lookup failure.
        AgentQueryInvalidPayloadError: on structural validation failure.
    """
    # --- Validate structure ---
    payload_version = encrypted_data.get("payloadVersion")
    if payload_version != "v1":
        raise AgentQueryInvalidPayloadError(
            message=f"Unsupported payloadVersion: {payload_version!r}",
            details={"payloadVersion": payload_version},
        )

    kid = encrypted_data.get("kid")
    if not kid:
        raise AgentQueryInvalidPayloadError(
            message="Missing required field: kid",
            details={},
        )

    iv_b64 = encrypted_data.get("iv")
    ciphertext_b64 = encrypted_data.get("encryptedPayload")

    if not iv_b64 or not ciphertext_b64:
        raise AgentQueryInvalidPayloadError(
            message="Missing required fields: iv, encryptedPayload",
            details={},
        )

    # --- Decode iv ---
    try:
        iv = base64url_decode(iv_b64)
    except Exception as exc:
        raise AgentQueryDecryptFailedError(
            message="Failed to decode request data",
            details={},
        ) from exc

    if len(iv) != 12:
        raise AgentQueryDecryptFailedError(
            message="Invalid request data format",
            details={},
        )

    # --- Decode ciphertext ---
    try:
        ciphertext = base64url_decode(ciphertext_b64)
    except Exception as exc:
        raise AgentQueryDecryptFailedError(
            message="Failed to decode encryptedPayload",
            details={},
        ) from exc

    # --- Retrieve key and decrypt ---
    key = get_encryption_key(kid)

    try:
        aesgcm = AESGCM(key)
        plaintext = aesgcm.decrypt(iv, ciphertext, None)
    except Exception as exc:
        raise AgentQueryDecryptFailedError(
            message="Decryption failed",
            details={},
        ) from exc

    # --- Parse JSON ---
    try:
        payload = json.loads(plaintext.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise AgentQueryInvalidPayloadError(
            message="Decrypted payload is not valid JSON",
            details={},
        ) from exc

    if not isinstance(payload, dict):
        raise AgentQueryInvalidPayloadError(
            message="Decrypted payload must be a JSON object",
            details={},
        )

    return payload


def base64url_decode(s: str) -> bytes:
    """Decode a base64url-encoded string (no padding required)."""
    # Add padding if needed
    padding = 4 - len(s) % 4
    if padding != 4:
        s += "=" * padding
    return base64.urlsafe_b64decode(s)
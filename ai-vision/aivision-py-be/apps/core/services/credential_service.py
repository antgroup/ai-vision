"""
凭证管理服务。

负责 hash 比对、加密解密、凭证记录创建和重新生成。
"""

import base64
import hashlib
import hmac
import logging
import secrets

from cryptography.fernet import Fernet, MultiFernet
from django.conf import settings
from django.utils import timezone

from apps.core.models import SpaceObservationCredential, SpaceObservationProject
from apps.core.services.credential_provider import CredentialPair, CredentialProvider

logger = logging.getLogger(__name__)

# Tracked Django settings, environment templates and deployment blueprints.
_PUBLIC_CREDENTIAL_MATERIAL = frozenset(
    {
        "aivision-container-change-me",
        "aivision-local-django-secret-change-me",
        "django-insecure-change-this-in-production",
        "your-secret-key-change-in-production",
        "your-shared-credential-encryption-key",
    }
)


def _new_gateway_credential_pair(public_prefix: str, secret_prefix: str) -> CredentialPair:
    return CredentialPair(
        public_key=f"{public_prefix}_{secrets.token_urlsafe(24)}",
        secret_key=f"{secret_prefix}_{secrets.token_urlsafe(36)}",
    )


def _derive_fernet(material: str) -> Fernet:
    """Derive a Fernet key from arbitrary key material."""
    key_material = material.encode()
    derived = hashlib.sha256(key_material).digest()
    fernet_key = base64.urlsafe_b64encode(derived)
    return Fernet(fernet_key)


def _get_primary_encryption_material() -> str:
    """
    Primary key material for credential encryption.

    Prefer the dedicated credential key. Fall back to Django SECRET_KEY for
    backward compatibility and local environments that have not been migrated.
    """
    return getattr(settings, "CREDENTIAL_ENCRYPTION_KEY", "") or settings.SECRET_KEY


def _get_decryption_materials() -> list[str]:
    """
    Candidate key materials for decrypting stored credentials.

    Order matters:
    1. Dedicated credential encryption key
    2. Django SECRET_KEY fallback for legacy ciphertext
    """
    candidates = []
    credential_key = getattr(settings, "CREDENTIAL_ENCRYPTION_KEY", "")
    if credential_key:
        candidates.append(credential_key)
    if settings.SECRET_KEY not in candidates:
        candidates.append(settings.SECRET_KEY)
    return candidates


def project_onboarding_credential_cipher() -> MultiFernet | None:
    """Use private material when storing or revealing a recoverable SpaceKey."""
    ciphers = []
    for material in _get_decryption_materials():
        trimmed = material.strip()
        # Allow compact encodings such as unpadded base64url for 128-bit keys.
        if (
            len(trimmed) < 22
            or len(set(trimmed)) < 8
            or trimmed.casefold() in _PUBLIC_CREDENTIAL_MATERIAL
        ):
            continue
        ciphers.append(_derive_fernet(material))
    # Encrypt with the first eligible key and retain reads of ciphertext
    # written with a private Django secret before a dedicated key was set.
    return MultiFernet(ciphers) if ciphers else None


def hash_secret(secret_key: str) -> str:
    return hashlib.sha256(secret_key.encode()).hexdigest()


def encrypt_secret(secret_key: str) -> str:
    f = _derive_fernet(_get_primary_encryption_material())
    return f.encrypt(secret_key.encode()).decode()


def decrypt_secret(ciphertext: str) -> str:
    last_error: Exception | None = None
    for material in _get_decryption_materials():
        try:
            return _derive_fernet(material).decrypt(ciphertext.encode()).decode()
        except Exception as exc:
            last_error = exc
    if last_error:
        raise last_error
    raise ValueError("No encryption material available for credential decryption")


def verify_secret(secret_key: str, secret_hash: str) -> bool:
    computed = hash_secret(secret_key)
    return hmac.compare_digest(computed, secret_hash)


def create_credential_records(
    project: SpaceObservationProject,
    read_pair: CredentialPair,
    write_pair: CredentialPair,
) -> tuple[SpaceObservationCredential, SpaceObservationCredential]:
    """为空间 project 创建或覆盖 READ + WRITE 两条凭证记录"""
    read_defaults = {
        "public_key": read_pair.public_key,
        "secret_hash": hash_secret(read_pair.secret_key),
        "secret_ciphertext": encrypt_secret(read_pair.secret_key),
        "name": "default-read",
        "status": 1,
        "gmt_deleted": None,
    }
    write_defaults = {
        "public_key": write_pair.public_key,
        "secret_hash": hash_secret(write_pair.secret_key),
        "secret_ciphertext": encrypt_secret(write_pair.secret_key),
        "name": "default-write",
        "status": 1,
        "gmt_deleted": None,
    }
    read_cred, _ = SpaceObservationCredential.objects.update_or_create(
        space_observation_project=project,
        scope="READ",
        defaults=read_defaults,
    )
    write_cred, _ = SpaceObservationCredential.objects.update_or_create(
        space_observation_project=project,
        scope="WRITE",
        defaults=write_defaults,
    )
    return read_cred, write_cred


def create_space_gateway_project(space, endpoint) -> SpaceObservationProject:
    """Create the local AIVision gateway project without local credentials."""
    project = SpaceObservationProject.objects.create(
        space=space,
        endpoint_config=endpoint,
        tenant_name=endpoint.tenant_name,
        upstream_project_id="",
        upstream_project_name="",
        upstream_project_slug="",
        status=1,
        provision_status=1,
    )
    return project


def reveal_credential(credential: SpaceObservationCredential) -> dict:
    """解密并返回完整的 PK/SK"""
    secret_key = decrypt_secret(credential.secret_ciphertext)
    return {
        "public_key": credential.public_key,
        "secret_key": secret_key,
        "scope": credential.scope,
    }


def regenerate_credential(
    credential: SpaceObservationCredential,
    provider: CredentialProvider | None = None,
) -> dict:
    """
    重新生成凭证，兼容两种模式：
    - 有 upstream_project_id：调 Provider 在 Langfuse 侧创建新 Key
    - 无 upstream_project_id（本地 Gateway 模式）：本地重新生成随机 Key

    返回新生成的完整 Key（仅本次返回）。
    """
    project = credential.space_observation_project

    if project.upstream_project_id:
        if not provider:
            raise ValueError("upstream 模式需要提供 CredentialProvider")
        new_pair = provider.create_api_key(project.upstream_project_id)
    else:
        scope = credential.scope.upper()
        if scope == "READ":
            new_pair = _new_gateway_credential_pair("pk_aiv_read", "sk_aiv_read")
        else:
            new_pair = _new_gateway_credential_pair("pk_aiv_write", "sk_aiv_write")

    credential.public_key = new_pair.public_key
    credential.secret_hash = hash_secret(new_pair.secret_key)
    credential.secret_ciphertext = encrypt_secret(new_pair.secret_key)
    credential.rotated_at = timezone.now()
    credential.save(
        update_fields=[
            "public_key",
            "secret_hash",
            "secret_ciphertext",
            "rotated_at",
            "gmt_modified",
        ]
    )

    return {
        "public_key": new_pair.public_key,
        "secret_key": new_pair.secret_key,
        "scope": credential.scope,
    }


def mask_public_key(public_key: str) -> str:
    """脱敏展示 public_key：保留前 8 位 + ***"""
    if len(public_key) <= 8:
        return public_key
    return f"{public_key[:8]}{'*' * 8}"

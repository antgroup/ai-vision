"""Credential auth cache and batched key usage updates.

SpaceKey validity is read from the database on every request so creation,
revocation and space state changes take effect across workers immediately.
Observation credentials refresh every 60 seconds. last_used_at writes remain
batched to avoid per-request DB writes.
"""

import logging
import threading
import time

from django.utils import timezone

logger = logging.getLogger(__name__)

_REFRESH_INTERVAL = 60


class _KeyCache:
    def __init__(self):
        self._credentials: dict[str, object] = {}
        self._last_refresh: float = 0
        self._lock = threading.Lock()
        self._pending_space_key_used: dict[int, object] = {}
        self._pending_credential_used: dict[int, tuple[object, str]] = {}

    def _refresh_if_needed(self):
        now = time.monotonic()
        if now - self._last_refresh < _REFRESH_INTERVAL:
            return
        with self._lock:
            if now - self._last_refresh < _REFRESH_INTERVAL:
                return
            self._do_refresh()
            self._last_refresh = time.monotonic()

    def _do_refresh(self):
        try:
            self._flush_pending_used()
        except Exception:
            logger.exception("key_cache: failed to flush pending used")

        try:
            from apps.core.models import SpaceObservationCredential
            qs = SpaceObservationCredential.objects.select_related(
                "space_observation_project__endpoint_config",
                "space_observation_project__space",
                "space_observation_project__space__creator",
            ).filter(
                status=1, gmt_deleted__isnull=True,
                space_observation_project__space__status=1,
                space_observation_project__space__gmt_deleted__isnull=True,
            )
            self._credentials = {c.public_key: c for c in qs}
            logger.debug("key_cache: loaded %d credentials", len(self._credentials))
        except Exception:
            logger.exception("key_cache: failed to load credentials")

    def _flush_pending_used(self):
        from apps.core.models import SpaceKey, SpaceObservationCredential

        sk_pending = self._pending_space_key_used
        self._pending_space_key_used = {}
        for sk_id, used_at in sk_pending.items():
            SpaceKey.all_objects.filter(id=sk_id).update(last_used_at=used_at)

        cred_pending = self._pending_credential_used
        self._pending_credential_used = {}
        for cred_id, (used_at, ip) in cred_pending.items():
            SpaceObservationCredential.objects.filter(id=cred_id).update(
                last_used_at=used_at, last_used_ip=ip
            )

    def get_space_key(self, key_hash: str):
        from apps.core.models import SpaceKey

        self._refresh_if_needed()
        # A process-local cache cannot observe revocation handled by another
        # worker. Use the unique key_hash index and fail closed on DB errors.
        return SpaceKey.all_objects.select_related("space", "creator").filter(
            key_hash=key_hash,
            enabled=True,
            gmt_deleted__isnull=True,
            space__status=1,
            space__gmt_deleted__isnull=True,
        ).first()

    def get_credential(self, public_key: str):
        self._refresh_if_needed()
        return self._credentials.get(public_key)

    def mark_space_key_used(self, sk_id: int):
        with self._lock:
            self._pending_space_key_used[sk_id] = timezone.now()

    def mark_credential_used(self, cred_id: int, ip: str):
        with self._lock:
            self._pending_credential_used[cred_id] = (timezone.now(), ip)

    def invalidate(self):
        with self._lock:
            self._last_refresh = 0


key_cache = _KeyCache()

"""Renew short-lived forwarding credentials without touching native OTLP data."""
from copy import deepcopy
import math
import threading
import time


class RenewingIngestHeaders:
    def __init__(self, target, refresh, refresh_interval=60, clock=time.monotonic):
        if (type(refresh_interval) not in (int, float) or not math.isfinite(refresh_interval)
                or refresh_interval <= 0):
            raise ValueError("Ingestion refresh interval must be positive")
        self._target = deepcopy(target)
        self._headers = dict(self._target["collector_headers"])
        self._refresh = refresh
        self._interval = refresh_interval
        self._clock = clock
        self._renew_at = clock() + refresh_interval
        self._lock = threading.Lock()

    def __call__(self):
        with self._lock:
            if self._clock() >= self._renew_at:
                # The adapter owns scope validation and uses the public mint API.
                # A failed or mutating adapter cannot poison the committed cache.
                updated = deepcopy(self._refresh(deepcopy(self._target)))
                if (not isinstance(updated, dict)
                        or not isinstance(updated.get("collector_headers"), dict)
                        or not updated["collector_headers"]):
                    raise ValueError("Ingestion renewal returned no forwarding headers")
                headers = dict(updated["collector_headers"])
                self._target, self._headers = updated, headers
                self._renew_at = self._clock() + self._interval
            # Do not advance the deadline or return stale headers after failure.
            # The next export retries renewal, and each caller owns its header copy.
            return dict(self._headers)

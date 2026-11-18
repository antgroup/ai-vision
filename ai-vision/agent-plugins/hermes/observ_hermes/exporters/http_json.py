"""Bounded standard-library OTLP/HTTP JSON transport."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from typing import Any, Mapping

from aivision_hermes_core.otlp_json import OtlpJsonEncoder
from aivision_hermes_core.semantic import SemanticObservation


class HttpJsonSink:
    """POST encoded OTLP/JSON requests without adding a runtime dependency."""

    def __init__(
        self,
        resource: Mapping[str, Any],
        endpoints: Mapping[str, str],
        headers: Mapping[str, Mapping[str, str]] | None = None,
        *,
        timeout: float = 10.0,
        aggregation_temporality: int = 2,
    ) -> None:
        self.encoder = OtlpJsonEncoder(
            resource,
            aggregation_temporality=aggregation_temporality,
            metric_start_time_unix_nano=time.time_ns(),
        )
        self.endpoints = dict(endpoints)
        self.headers = {
            signal: dict(values) for signal, values in (headers or {}).items()
        }
        self.timeout = timeout
        self.errors: list[dict[str, Any]] = []
        self._shutdown = False

    def __call__(self, observation: SemanticObservation) -> None:
        if self._shutdown:
            return
        encoded = self.encoder.encode(observation)
        if encoded is None:
            return
        signal, request_body = encoded
        if signal == "diagnostic":
            return
        endpoint = self.endpoints.get(signal, "")
        if not endpoint:
            return
        body = json.dumps(request_body, ensure_ascii=False, sort_keys=True).encode(
            "utf-8"
        )
        request = urllib.request.Request(
            endpoint,
            data=body,
            method="POST",
            headers={
                **self.headers.get(signal, {}),
                "content-type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout):
                pass
        except urllib.error.HTTPError as error:
            self.errors.append({"signal": signal, "status": error.code})
            error.close()
        except (OSError, urllib.error.URLError) as error:
            self.errors.append({"signal": signal, "error": type(error).__name__})

    def force_flush(self) -> None:
        """Synchronous HTTP exports need no queued flush."""

    def shutdown(self) -> None:
        if self._shutdown:
            return
        self.encoder.abandon_pending()
        self._shutdown = True

"""Langfuse metadata encoding provenance retained by the OTLP ingress."""

import re


OTLP_SCOPE_NAME = "aivision.otlp.scope.name"
OTLP_SCOPE_VERSION = "aivision.otlp.scope.version"
OTLP_SDK_LANGUAGE = "aivision.otlp.sdk.language"
OTLP_PROVENANCE_KEYS = (OTLP_SCOPE_NAME, OTLP_SCOPE_VERSION, OTLP_SDK_LANGUAGE)

_VERSION = re.compile(r"^(\d{1,9})\.\d{1,9}\.\d{1,9}(?:[-+][0-9A-Za-z.-]+)?$")


def langfuse_metadata_is_json(attributes):
    """Return True/False for known SDKs, None for historical/unknown producers.

    Python 5 / JS 6 JSON-encode every metadata value, strings included. Earlier
    SDKs sent strings as-is and serialized other values into strings, losing
    the original type. Do not guess those types from JSON-looking text.

    The instrumentation scope version identifies Langfuse. The resource's
    telemetry.sdk.version identifies OpenTelemetry and is intentionally unused.
    """
    if attributes.get(OTLP_SCOPE_NAME) != "langfuse-sdk":
        return None
    language = attributes.get(OTLP_SDK_LANGUAGE)
    if not isinstance(language, str):
        return None
    threshold = {"python": 5, "javascript": 6, "nodejs": 6}.get(language)
    version = attributes.get(OTLP_SCOPE_VERSION)
    match = _VERSION.fullmatch(version) if isinstance(version, str) else None
    if threshold is None or match is None:
        return None
    return int(match.group(1)) >= threshold

"""Compatibility import; privacy projection lives in aivision-hermes-core."""

from ._core_bootstrap import ensure_core_importable

ensure_core_importable()

from aivision_hermes_core.privacy import MAX_COLLECTION_ITEMS, MAX_DEPTH, MAX_STRING_LENGTH, REDACTED, ProjectionResult, canonical_json, hide_content, json_safe, privacy_marker, sanitize  # noqa: E402, E501, F401, I001  # fmt: skip

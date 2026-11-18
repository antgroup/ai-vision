"""Compatibility import; normalization lives in aivision-hermes-core."""

from ._core_bootstrap import ensure_core_importable

ensure_core_importable()

from aivision_hermes_core.normalize import CORRELATION_FIELDS, SUPPORTED_SOURCE_SCHEMA, CanonicalNormalizer, classify_error, classify_tool, safe_artifact_part  # noqa: E402, E501, F401, I001  # fmt: skip

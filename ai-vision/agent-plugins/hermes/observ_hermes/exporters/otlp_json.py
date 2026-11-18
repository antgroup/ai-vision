"""Compatibility import; OTLP JSON encoding lives in aivision-hermes-core."""

from .._core_bootstrap import ensure_core_importable

ensure_core_importable()

from aivision_hermes_core.otlp_json import OtlpJsonEncoder, attr_value, attributes, unix_nano  # noqa: E402, E501, F401, I001  # fmt: skip

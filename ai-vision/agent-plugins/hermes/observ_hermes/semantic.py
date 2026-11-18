"""Compatibility import; semantic engine lives in aivision-hermes-core."""

from ._core_bootstrap import ensure_core_importable

ensure_core_importable()

from aivision_hermes_core.semantic import ERROR_CATEGORIES, MemorySink, SemanticConfig, SemanticEngine, SemanticObservation  # noqa: E402, E501, F401, I001  # fmt: skip

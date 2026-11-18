"""Compatibility import; canonical implementation lives in aivision-hermes-core."""

from ._core_bootstrap import ensure_core_importable

ensure_core_importable()

from aivision_hermes_core.canonical import CANONICAL_SCHEMA_VERSION, GENESIS_CHECKSUM, CanonicalEvent, CanonicalFormatError, canonical_checksum, checksum_base, migrate_record, verify_event  # noqa: E402, E501, F401, I001  # fmt: skip

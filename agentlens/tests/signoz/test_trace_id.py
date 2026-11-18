from __future__ import annotations

from datetime import UTC, datetime

from agentlens.service.datasource.trace_id import (
    TraceIdQueryNormalization,
    is_sofa_trace_id_format,
    normalize_trace_id_for_query,
)

# A real SOFA-shaped id: 8-hex IP + 13-digit ms timestamp + 4-digit seq + pid
# IP: 7f000001 (127.0.0.1)  ts: 1716811200000 (2024-05-27 12:00:00 UTC)  seq: 1001  pid: 12345
SOFA_ID = "7f0000011716811200000100112345"


def test_is_sofa_true_for_valid_id() -> None:
    assert is_sofa_trace_id_format(SOFA_ID) is True


def test_is_sofa_false_for_random_hex() -> None:
    assert is_sofa_trace_id_format("019e39bc817e21b5f9370000051522ad") is False


def test_is_sofa_false_for_too_short() -> None:
    assert is_sofa_trace_id_format("abc") is False


def test_is_sofa_false_for_empty() -> None:
    assert is_sofa_trace_id_format("") is False
    assert is_sofa_trace_id_format(None) is False  # type: ignore[arg-type]


def test_normalize_passes_through_non_sofa_id() -> None:
    raw = "019e39bc817e21b5f9370000051522ad"
    result = normalize_trace_id_for_query(raw)
    assert isinstance(result, TraceIdQueryNormalization)
    assert result.original_trace_id == raw
    assert result.query_trace_id == raw
    assert result.is_sofa_trace_id is False
    assert result.period_from is None
    assert result.period_to is None


def test_normalize_sofa_id_extracts_otel_hex_and_period() -> None:
    result = normalize_trace_id_for_query(SOFA_ID)
    assert result.is_sofa_trace_id is True
    assert result.original_trace_id == SOFA_ID
    assert len(result.query_trace_id) == 32
    # Hex string, decodable as 16 bytes
    assert bytes.fromhex(result.query_trace_id)
    # Period starts at the SOFA timestamp (UTC) and spans 1 hour
    assert result.period_from == datetime(2024, 5, 27, 12, 0, 0, tzinfo=UTC)
    assert result.period_to == datetime(2024, 5, 27, 13, 0, 0, tzinfo=UTC)


def test_normalize_sofa_id_with_alphanumeric_pid() -> None:
    # Format check requires all hex chars, so pid prefix must be a hex letter (a-f).
    # 8 IP + 13 ts + 4 seq + 5 pid (prefix 'a' + 4 chars used) = 30 chars
    # pid_part[0]='a' is non-digit, so the alphanumeric pid branch is exercised.
    alpha_pid_id = "7f00000117168112000001001abcde"
    result = normalize_trace_id_for_query(alpha_pid_id)
    assert result.is_sofa_trace_id is True
    assert len(result.query_trace_id) == 32

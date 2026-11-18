from __future__ import annotations

import binascii
import struct
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

SOFA_TRACE_ID_MIN_LENGTH = 25
SOFA_TRACE_ID_MAX_LENGTH = 30
SOFA_TIMESTAMP_START = 8
SOFA_TIMESTAMP_END = 21
SOFA_INCREMENT_START = 21
SOFA_INCREMENT_END = 25
SOFA_PID_START = 25
MIN_VALID_SOFA_TIMESTAMP_MS = 1262304000000
MAX_VALID_SOFA_TIMESTAMP_MS = 1924997999000
MIN_VALID_SOFA_SEQUENCE = 1000
MAX_VALID_SOFA_SEQUENCE = 9000


@dataclass(frozen=True)
class TraceIdQueryNormalization:
    original_trace_id: str
    query_trace_id: str
    is_sofa_trace_id: bool
    period_from: datetime | None = None
    period_to: datetime | None = None
    error: str | None = None


def is_sofa_trace_id_format(value: str) -> bool:
    """Return whether a value matches the Langfuse PR6 SofaTraceId shape."""
    if not value:
        return False

    trace_id = value.strip()
    length = len(trace_id)
    if length < SOFA_TRACE_ID_MIN_LENGTH or length > SOFA_TRACE_ID_MAX_LENGTH:
        return False

    if not all(char in "0123456789abcdefABCDEF" for char in trace_id):
        return False

    ip_hex = trace_id[:8]
    if len(ip_hex) != 8:
        return False

    timestamp_raw = trace_id[SOFA_TIMESTAMP_START:SOFA_TIMESTAMP_END]
    if len(timestamp_raw) != 13 or not timestamp_raw.isdigit():
        return False
    timestamp_ms = int(timestamp_raw)
    if timestamp_ms < MIN_VALID_SOFA_TIMESTAMP_MS or timestamp_ms > MAX_VALID_SOFA_TIMESTAMP_MS:
        return False

    sequence_raw = trace_id[SOFA_INCREMENT_START:SOFA_INCREMENT_END]
    if len(sequence_raw) != 4 or not sequence_raw.isdigit():
        return False
    sequence = int(sequence_raw)
    if sequence < MIN_VALID_SOFA_SEQUENCE or sequence > MAX_VALID_SOFA_SEQUENCE:
        return False

    return True


def generate_trace_id_by_sofa_trace_id(value: str) -> tuple[bytes, str | None]:
    """Generate the 16-byte OTel trace id using the provided SOFA layout."""
    trace_id = value.strip()
    if len(trace_id) < SOFA_TRACE_ID_MIN_LENGTH:
        return (
            b"",
            f"gen TraceID error, invalid sofa trace id length, origin TraceId is {value}",
        )

    output = bytearray(16)

    try:
        ip_bytes = binascii.unhexlify(trace_id[:8])
        if len(ip_bytes) != 4:
            raise ValueError("IP hex part did not decode to 4 bytes")
        output[6:10] = ip_bytes
    except Exception as exc:
        return (
            b"",
            f"gen TraceID error, invalid ip, origin TraceId is {value}, error is {exc}",
        )

    try:
        timestamp = int(trace_id[SOFA_TIMESTAMP_START:SOFA_TIMESTAMP_END])
        output[0] = (timestamp >> 40) & 0xFF
        output[1] = (timestamp >> 32) & 0xFF
        output[2] = (timestamp >> 24) & 0xFF
        output[3] = (timestamp >> 16) & 0xFF
        output[4] = (timestamp >> 8) & 0xFF
        output[5] = timestamp & 0xFF
    except Exception as exc:
        return (
            b"",
            f"gen TraceID parse timestamp error, origin TraceId is {value}, error is {exc}",
        )

    try:
        inc_id = int(trace_id[SOFA_INCREMENT_START:SOFA_INCREMENT_END])
        if not (0 <= inc_id <= 65535):
            raise ValueError("incremental id out of uint16 range")
        output[14:16] = struct.pack(">H", inc_id)
    except Exception as exc:
        return (
            b"",
            f"gen TraceID error, invalid incremental id, origin TraceId is {value}, err is {exc}",
        )

    pid_part = trace_id[SOFA_PID_START:]
    if not pid_part:
        return (
            b"",
            f"gen TraceID error, pid part is empty, origin TraceId is {value}",
        )

    if "0" <= pid_part[0] <= "9":
        try:
            pid_num = int(pid_part)
            if not (0 <= pid_num <= 0xFFFFFFFF):
                raise ValueError("numeric pid out of uint32 range")
            output[10:14] = struct.pack(">I", pid_num)
        except Exception as exc:
            return (
                b"",
                f"gen TraceID error, invalid numeric pid, origin TraceId is {value}, err is {exc}",
            )
    else:
        pid_clean = pid_part[1:]
        if len(pid_clean) < 4:
            return (
                b"",
                "gen TraceID error, alphanumeric pid too short after prefix, "
                f"origin TraceId is {value}",
            )
        for index in range(4):
            output[10 + index] = ord(pid_clean[index])

    return bytes(output), None


def _extract_sofa_period(value: str) -> tuple[datetime, datetime]:
    timestamp_ms = int(value.strip()[SOFA_TIMESTAMP_START:SOFA_TIMESTAMP_END])
    period_from = datetime.fromtimestamp(timestamp_ms / 1000, tz=UTC)
    return period_from, period_from + timedelta(hours=1)


def normalize_trace_id_for_query(value: str) -> TraceIdQueryNormalization:
    trace_id = value.strip()
    if not is_sofa_trace_id_format(trace_id):
        return TraceIdQueryNormalization(
            original_trace_id=value,
            query_trace_id=trace_id,
            is_sofa_trace_id=False,
        )

    otel_trace_id, error = generate_trace_id_by_sofa_trace_id(trace_id)
    if error:
        return TraceIdQueryNormalization(
            original_trace_id=value,
            query_trace_id=trace_id,
            is_sofa_trace_id=False,
            error=error,
        )

    period_from, period_to = _extract_sofa_period(trace_id)
    return TraceIdQueryNormalization(
        original_trace_id=value,
        query_trace_id=otel_trace_id.hex(),
        is_sofa_trace_id=True,
        period_from=period_from,
        period_to=period_to,
    )

"""Custom Rule result contract."""

from typing import Any

from agentlens.diagnoser.base import parse_user_metrics
from agentlens.domain.diagnosis_record import Metric


def validate_result(raw: Any, declared_names: list[str]) -> list[Metric]:
    """Validate the shared untrusted metrics protocol."""
    return parse_user_metrics(raw, declared_names)

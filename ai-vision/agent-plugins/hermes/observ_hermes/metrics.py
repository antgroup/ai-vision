"""Hermes business metric instruments from the repository semconv contract."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class MetricsRuntime:
    instruments: dict[str, Any]

    def record(self, name: str, value: int | float, attributes: dict[str, Any]) -> None:
        instrument = self.instruments.get(name)
        if instrument is None:
            return
        if name.endswith(".duration"):
            instrument.record(value, attributes)
        else:
            instrument.add(value, attributes)


def create_metrics(meter: Any) -> MetricsRuntime:
    return MetricsRuntime(
        {
            "agent.session.count": meter.create_counter(
                "agent.session.count",
                unit="{count}",
                description="Agent session start count",
            ),
            "agent.token.usage": meter.create_counter(
                "agent.token.usage", unit="{token}", description="Token usage count"
            ),
            "agent.api_request.count": meter.create_counter(
                "agent.api_request.count",
                unit="{count}",
                description="Completed API requests",
            ),
            "agent.api_request.error.count": meter.create_counter(
                "agent.api_request.error.count",
                unit="{count}",
                description="Failed API requests",
            ),
            "agent.api_request.duration": meter.create_histogram(
                "agent.api_request.duration",
                unit="ms",
                description="API request duration",
                explicit_bucket_boundaries_advisory=[
                    500,
                    1000,
                    2000,
                    5000,
                    10000,
                    20000,
                    30000,
                    60000,
                ],
            ),
            "agent.tool_call.count": meter.create_counter(
                "agent.tool_call.count",
                unit="{count}",
                description="Completed tool calls",
            ),
            "agent.tool_call.error.count": meter.create_counter(
                "agent.tool_call.error.count",
                unit="{count}",
                description="Failed tool calls",
            ),
            "agent.tool_call.duration": meter.create_histogram(
                "agent.tool_call.duration",
                unit="ms",
                description="Tool call duration",
                explicit_bucket_boundaries_advisory=[
                    50,
                    100,
                    200,
                    500,
                    1000,
                    2000,
                    5000,
                    10000,
                    30000,
                ],
            ),
            "agent.cache.read_tokens": meter.create_counter(
                "agent.cache.read_tokens",
                unit="{token}",
                description="Cache read token count",
            ),
            "agent.cache.write_tokens": meter.create_counter(
                "agent.cache.write_tokens",
                unit="{token}",
                description="Cache write token count",
            ),
        }
    )

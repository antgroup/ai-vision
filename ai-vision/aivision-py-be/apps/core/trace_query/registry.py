from __future__ import annotations

from dataclasses import dataclass, field

from apps.core.trace_query.types import TraceProvider


@dataclass(frozen=True)
class TraceProviderDescriptor:
    name: str
    provider: TraceProvider
    source_systems: tuple[str, ...] = field(default_factory=tuple)


class TraceProviderRegistry:
    """In-memory provider registry used by the trace query service."""

    def __init__(self) -> None:
        self._providers: dict[str, TraceProviderDescriptor] = {}

    def register(
        self,
        name: str,
        provider: TraceProvider,
        *,
        source_systems: tuple[str, ...] | None = None,
    ) -> None:
        self._providers[name] = TraceProviderDescriptor(
            name=name,
            provider=provider,
            source_systems=source_systems or (),
        )

    def get(self, name: str) -> TraceProvider:
        return self._providers[name].provider

    def get_descriptor(self, name: str) -> TraceProviderDescriptor:
        return self._providers[name]

    def has(self, name: str) -> bool:
        return name in self._providers

    def find_name_by_source_system(self, source_system: str) -> str | None:
        for descriptor in self._providers.values():
            if source_system in descriptor.source_systems:
                return descriptor.name
        return None

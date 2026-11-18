from __future__ import annotations

from typing import Any

from apps.core.trace_query.types import TraceProviderNotConfiguredError

from .common import metadata_without_events
from .routing import get_event_datasource
from .signoz import SignozEventProvider
from .types import EventFacetQuery, EventListQuery, EventMetadataQuery, EventRawSqlQuery


class EventQueryService:
    """Dispatch typed OpenAPI event queries to the configured logs datasource."""

    def _provider(
        self,
        query: EventMetadataQuery | EventListQuery | EventFacetQuery | EventRawSqlQuery,
    ):
        datasource = get_event_datasource(
            query.space,
            agent_product=query.agent_product,
            agent_uuid=query.get_param("agent_uuid"),
        )
        if datasource is None:
            return None
        provider_class = {
            "signoz": SignozEventProvider,
        }.get(getattr(datasource, "source_system", ""))
        if provider_class is None:
            raise TraceProviderNotConfiguredError(
                "Event datasource provider is not configured", 3011
            )
        return provider_class(datasource)

    def get_metadata(self, query: EventMetadataQuery) -> dict[str, Any]:
        provider = self._provider(query)
        if provider is None:
            return metadata_without_events(query.raw_sql_enabled)
        return provider.get_metadata(query)

    def get_schema_metadata(self, query: EventMetadataQuery) -> dict[str, Any]:
        provider = self._provider(query)
        if provider is None:
            raise TraceProviderNotConfiguredError(
                "Event datasource is not configured", 3011
            )
        method = getattr(provider, "get_schema_metadata", None)
        if not callable(method):
            raise TraceProviderNotConfiguredError(
                "Event datasource schema metadata is not configured", 3011
            )
        return method(query)

    def list_events(self, query: EventListQuery) -> dict[str, Any]:
        provider = self._provider(query)
        if provider is None:
            raise TraceProviderNotConfiguredError(
                "Event datasource is not configured", 3011
            )
        return provider.list_events(query)

    def list_facets(self, query: EventFacetQuery) -> dict[str, Any]:
        provider = self._provider(query)
        if provider is None:
            raise TraceProviderNotConfiguredError(
                "Event datasource is not configured", 3011
            )
        return provider.list_facets(query)

    def query_raw_sql(self, query: EventRawSqlQuery) -> dict[str, Any]:
        provider = self._provider(query)
        if provider is None:
            raise TraceProviderNotConfiguredError(
                "Event datasource is not configured", 3011
            )
        return provider.query_raw_sql(query)


_DEFAULT_SERVICE = EventQueryService()


def get_default_event_query_service() -> EventQueryService:
    return _DEFAULT_SERVICE

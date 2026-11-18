from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class BaseEventQuery:
    space: Any
    user: Any
    query_params: Mapping[str, Any]

    def get_param(self, key: str, default: Any = None) -> Any:
        getter = getattr(self.query_params, "get", None)
        if callable(getter):
            return getter(key, default)
        return default

    @property
    def agent_product(self) -> str | None:
        value = self.get_param("agent_product")
        return value if isinstance(value, str) else None


@dataclass(frozen=True)
class EventMetadataQuery(BaseEventQuery):
    raw_sql_enabled: bool = False


@dataclass(frozen=True)
class EventListQuery(BaseEventQuery):
    pass


@dataclass(frozen=True)
class EventFacetQuery(BaseEventQuery):
    pass


@dataclass(frozen=True)
class EventRawSqlQuery(BaseEventQuery):
    raw_sql: str
    ref_id: str = "A"
    result_format: str = "table"


class EventProvider(Protocol):
    def get_metadata(self, query: EventMetadataQuery) -> dict[str, Any]:
        ...

    def list_events(self, query: EventListQuery) -> dict[str, Any]:
        ...

    def list_facets(self, query: EventFacetQuery) -> dict[str, Any]:
        ...

    def query_raw_sql(self, query: EventRawSqlQuery) -> dict[str, Any]:
        ...

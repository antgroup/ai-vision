from dataclasses import dataclass
from datetime import datetime
from typing import Any

from agentlens.domain.enums import DatasetType


@dataclass
class TraceQuerySpec:
    table: str
    where: str = ""
    time_range: str = "1d"
    start_time: str | None = None
    end_time: str | None = None
    limit: int = 1000

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "TraceQuerySpec":
        query = d["query"]
        return cls(
            table=query["table"],
            where=query.get("where", ""),
            time_range=query.get("time_range", "1d"),
            start_time=d.get("start_time"),
            end_time=d.get("end_time"),
            limit=query.get("limit", 1000),
        )


@dataclass
class Dataset:
    uuid: str
    space_uuid: str
    agent_uuid: str
    name: str
    description: str
    type: DatasetType
    spec: dict[str, Any]
    gmt_create: datetime
    gmt_modified: datetime
    creator: str | None
    modifier: str | None

    def query_spec(self) -> dict[str, Any]:
        query = self.spec.get("query")
        if not isinstance(query, dict):
            raise ValueError("dataset spec missing object field 'query'")
        return query

    def trace_query_spec(self) -> TraceQuerySpec:
        return TraceQuerySpec.from_dict(self.spec)

from datetime import datetime
from typing import Annotated, Generic, TypeVar

from pydantic import BaseModel, Field, PlainSerializer, WithJsonSchema, field_validator

from agentlens.domain.scope import require_concrete_agent_uuid

T = TypeVar("T")


def _serialize_local_datetime(value: datetime) -> str:
    # Persisted naive values use the service's local clock. Attach that clock's
    # actual offset only at the HTTP boundary; storage and scheduling stay naive.
    if value.utcoffset() is None:
        value = value.astimezone()
    return value.isoformat()


LocalDateTime = Annotated[
    datetime,
    PlainSerializer(_serialize_local_datetime, when_used="json"),
    WithJsonSchema({"type": "string", "format": "date-time"}, mode="serialization"),
]


class ListResponse(BaseModel, Generic[T]):
    items: list[T]
    total: int


class ResourceScopeCreate(BaseModel):
    space_uuid: str = Field(min_length=1)
    agent_uuid: str = Field(min_length=1)

    @field_validator("agent_uuid")
    @classmethod
    def validate_agent_uuid(cls, value: str) -> str:
        return require_concrete_agent_uuid(value)

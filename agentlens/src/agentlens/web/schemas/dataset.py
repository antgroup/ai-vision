from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from agentlens.domain.enums import DatasetType
from agentlens.web.schemas.common import ResourceScopeCreate


class DatasetCreate(ResourceScopeCreate):
    name: str = Field(min_length=1, max_length=128)
    description: str = ""
    type: DatasetType
    spec: dict[str, Any]
    creator: str | None = None
    modifier: str | None = None


class DatasetUpdate(BaseModel):
    description: str | None = None
    spec: dict[str, Any] | None = None
    creator: str | None = None
    modifier: str | None = None


class DatasetOut(BaseModel):
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

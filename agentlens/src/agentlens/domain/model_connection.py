from dataclasses import dataclass
from datetime import datetime

from agentlens.domain.enums import ModelConnectionStatus


@dataclass
class ModelConnection:
    uuid: str
    space_uuid: str
    name: str
    endpoint: str
    model_name: str
    api_key: str
    status: ModelConnectionStatus
    gmt_create: datetime
    gmt_modified: datetime
    creator: str | None
    modifier: str | None
    diagnostic_validation: dict | None = None
    config_revision: int = 1

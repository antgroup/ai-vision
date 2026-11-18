from dataclasses import dataclass
from datetime import datetime
from typing import Any

from agentlens.domain.enums import DiagnoserMode, DiagnoserType


@dataclass
class Diagnoser:
    uuid: str
    space_uuid: str
    agent_uuid: str
    name: str
    description: str
    type: DiagnoserType
    mode: DiagnoserMode
    spec: dict[str, Any]
    version: str
    gmt_create: datetime
    gmt_modified: datetime
    creator: str | None
    modifier: str | None

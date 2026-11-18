from dataclasses import dataclass
from datetime import datetime


@dataclass
class Heartbeat:
    uuid: str
    hostname: str
    ip: str
    gmt_create: datetime
    gmt_modified: datetime

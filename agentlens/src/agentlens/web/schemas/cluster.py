from datetime import datetime

from pydantic import BaseModel


class InstanceOut(BaseModel):
    uuid: str
    hostname: str
    ip: str
    gmt_create: datetime
    gmt_modified: datetime


class LeaderOut(BaseModel):
    holder_uuid: str
    acquired_at: datetime
    expires_at: datetime
    fenced_token: int

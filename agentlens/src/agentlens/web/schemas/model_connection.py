from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from agentlens.domain.enums import ModelConnectionStatus, ModelConnectionTestError
from agentlens.domain.model_validation import DiagnosticValidation, ValidationOptions


class ModelConnectionCreate(BaseModel):
    space_uuid: str = Field(min_length=1)
    name: str = Field(min_length=1, max_length=128)
    endpoint: str = Field(min_length=1, max_length=1024)
    api_key: str = Field(min_length=1)
    model_name: str = Field(min_length=1, max_length=256)
    creator: str | None = None
    modifier: str | None = None


class ModelConnectionUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    endpoint: str | None = Field(default=None, min_length=1, max_length=1024)
    api_key: str | None = None
    model_name: str | None = Field(default=None, min_length=1, max_length=256)
    modifier: str | None = None


class ModelConnectionOut(BaseModel):
    uuid: str
    space_uuid: str
    name: str
    endpoint: str
    model_name: str
    api_key_configured: bool
    diagnostic_validation: DiagnosticValidation | None = None
    status: ModelConnectionStatus
    gmt_create: datetime
    gmt_modified: datetime
    creator: str | None
    modifier: str | None


class ModelConnectionTestResponse(ModelConnectionOut):
    test_error: ModelConnectionTestError | None = None


class ModelConnectionReferenceOut(BaseModel):
    uuid: str
    name: str
    agent_uuid: str
    type: str
    mode: str


class ModelDiscoveryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    space_uuid: str = Field(min_length=1, max_length=128)
    endpoint: str = Field(min_length=1, max_length=1024)
    api_key: str | None = Field(default=None, max_length=8192, repr=False)
    connection_uuid: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_-]{1,64}$")


class DiscoveredModelOut(BaseModel):
    id: str


class ModelConnectionValidateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    space_uuid: str = Field(min_length=1, max_length=128)
    generation_options: ValidationOptions = Field(default_factory=ValidationOptions)

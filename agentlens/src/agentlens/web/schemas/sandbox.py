from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from agentlens.domain.sandbox import SandboxStatus
from agentlens.infra.sandbox import normalize_endpoint


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class SandboxConnectionCreate(_Strict):
    space_uuid: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=128)
    provider: Literal["openshell"] = "openshell"
    endpoint: str = Field(min_length=1, max_length=1024)
    api_key: str = Field(min_length=1, max_length=8192, repr=False)
    image: str = Field(min_length=1, max_length=512, pattern=r"^[A-Za-z0-9][A-Za-z0-9._/@:-]*$")
    cpu: int = Field(default=1, ge=1, le=64)
    memory: str = Field(default="1Gi", pattern=r"^[1-9][0-9]{0,5}(Mi|Gi)$")
    timeout_seconds: int = Field(default=30, ge=1, le=300)
    creator: str | None = Field(default=None, max_length=64)
    modifier: str | None = Field(default=None, max_length=64)

    @field_validator("endpoint")
    @classmethod
    def valid_endpoint(cls, value: str) -> str:
        try:
            return normalize_endpoint(value)
        except Exception as error:
            raise ValueError(
                "Sandbox plugin endpoint must be an HTTP(S) URL without credentials."
            ) from error

    @field_validator("api_key")
    @classmethod
    def valid_key(cls, value: str) -> str:
        if any(not 33 <= ord(char) <= 126 for char in value):
            raise ValueError("Sandbox plugin token must contain visible ASCII characters.")
        return value


class SandboxConnectionUpdate(_Strict):
    expected_config_revision: int | None = Field(default=None, ge=1)
    space_uuid: str = Field(min_length=1, max_length=64)
    name: str | None = Field(default=None, min_length=1, max_length=128)
    provider: Literal["openshell"] | None = None
    endpoint: str | None = Field(default=None, min_length=1, max_length=1024)
    api_key: str | None = Field(default=None, min_length=1, max_length=8192, repr=False)
    image: str | None = Field(
        default=None, min_length=1, max_length=512, pattern=r"^[A-Za-z0-9][A-Za-z0-9._/@:-]*$"
    )
    cpu: int | None = Field(default=None, ge=1, le=64)
    memory: str | None = Field(default=None, pattern=r"^[1-9][0-9]{0,5}(Mi|Gi)$")
    timeout_seconds: int | None = Field(default=None, ge=1, le=300)
    status: Literal["disabled", "untested"] | None = None
    modifier: str | None = Field(default=None, max_length=64)

    @field_validator("endpoint")
    @classmethod
    def valid_endpoint(cls, value):
        return SandboxConnectionCreate.valid_endpoint(value) if value is not None else None

    @field_validator("api_key")
    @classmethod
    def valid_key(cls, value):
        return SandboxConnectionCreate.valid_key(value) if value is not None else None


class SandboxConnectionOut(BaseModel):
    uuid: str
    space_uuid: str
    name: str
    provider: str
    endpoint: str
    api_key_configured: bool
    image: str
    cpu: int
    memory: str
    timeout_seconds: int
    config_revision: int
    status: SandboxStatus
    gmt_create: datetime
    gmt_modified: datetime
    creator: str | None
    modifier: str | None


class SandboxTestRequest(_Strict):
    space_uuid: str = Field(min_length=1, max_length=64)


class SandboxConnectionRuntimeOut(BaseModel):
    connection_uuid: str
    config_revision: int
    image: str
    ready: bool
    capabilities: list[str]
    runtime_manifests: list[dict]
    effective_timeout_seconds: float = Field(gt=0, le=300)
    effective_input_bytes: int | None = Field(default=None, ge=1024, le=16 * 1024**2)


class SandboxEnrollmentEnable(SandboxTestRequest):
    modifier: str | None = Field(default=None, max_length=64)
    expected_config_revision: int | None = Field(default=None, ge=1, le=9223372036854775807)


class SandboxTestOut(SandboxConnectionOut):
    test_error: str | None = None


class SandboxBindingOut(_Strict):
    space_uuid: str
    connection_uuid: str | None = None
    require_sandbox: bool = False


class SandboxBindingUpdate(SandboxBindingOut):
    space_uuid: str = Field(min_length=1, max_length=64)
    connection_uuid: str | None = Field(default=None, min_length=1, max_length=32)
    modifier: str | None = Field(default=None, max_length=64)


class SandboxProviderOut(BaseModel):
    id: str
    name: str
    protocol_version: int
    capabilities: list[str]

from datetime import datetime
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from agentlens.domain.enums import (
    DatasetType,
    DiagnoserMode,
    DiagnoserType,
    ExecutionStatus,
    Verdict,
)
from agentlens.web.schemas.common import ResourceScopeCreate


class DiagnoserCreate(ResourceScopeCreate):
    name: str = Field(min_length=1, max_length=128)
    description: str = ""
    type: DiagnoserType
    mode: DiagnoserMode
    spec: dict[str, Any] = Field(default_factory=dict)
    version: str = "v1"


class DiagnoserUpdate(BaseModel):
    name: (
        Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=128)]
        | None
    ) = None
    description: str | None = None
    spec: dict[str, Any] | None = None
    version: str | None = None


class BuiltinRuleCopy(ResourceScopeCreate):
    model_config = ConfigDict(extra="forbid")

    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=128)]


class BuiltinSyncRequest(ResourceScopeCreate):
    pass


class BuiltinSyncResponse(BaseModel):
    created: int
    updated: int
    unchanged: int
    diagnosers: list["DiagnoserOut"]


class DiagnoserOut(BaseModel):
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


class DiagnoserBulkDeleteResponse(BaseModel):
    deleted: int
    diagnoser_uuids: list[str]


BuiltinSyncResponse.model_rebuild()


class RuleTrialRequest(BaseModel):
    space_uuid: str | None = Field(default=None, min_length=1, max_length=128)
    spec: dict[str, Any]
    test_input: dict[str, Any]


class RuleInputPreviewResponse(BaseModel):
    valid: bool
    mode: str = "sandbox"
    request_bytes: int | None = None
    worker_input_bytes: int | None = None
    max_input_bytes: int | None = None
    transport: str | None = None
    code: str = ""
    details: dict[str, Any] = Field(default_factory=dict)
    validation_message: str = Field(default="", max_length=1000)
    validation_line: int | None = Field(default=None, ge=1)


class TraceSampleRequest(BaseModel):
    dataset_type: DatasetType
    spec: dict[str, Any]
    trace_id: str | None = Field(default=None, max_length=512)

    recent_limit: int = Field(default=1, ge=1, le=3)


class TraceSampleItem(BaseModel):
    trace_id: str
    test_input: dict[str, Any]


class TraceSampleResponse(BaseModel):
    test_input: dict[str, Any]
    samples: list[TraceSampleItem] = Field(default_factory=list)


class TrialMetric(BaseModel):
    name: str
    verdict: Verdict
    reason: str = ""
    evidence: dict[str, Any] = Field(default_factory=dict)


class TrialExecution(BaseModel):
    status: ExecutionStatus
    code: str = ""
    msg: str = ""
    evidence: dict[str, Any] = Field(default_factory=dict)


class TrialResponse(BaseModel):
    verdict: Verdict | None
    skipped: bool = False
    skip_reason: str | None = None
    skip_reason_detail: str | None = None
    summary: str
    metrics: list[TrialMetric]
    execution: TrialExecution


class LlmTrialRequest(BaseModel):
    builtin_key: str | None = None
    canonical_variant: str | None = None
    space_uuid: str = Field(min_length=1)
    name: str = ""
    spec: dict[str, Any]
    test_input: dict[str, Any]


RuleTrialResponse = TrialResponse
LlmTrialResponse = TrialResponse

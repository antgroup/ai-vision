"""Trusted platform-only finite Specialist wire inputs."""

from pydantic import BaseModel, ConfigDict, Field, model_validator


class SpecialistBody(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class SpecialistSelection(SpecialistBody):
    key: str = Field(min_length=1, max_length=64)
    variant: str = Field(min_length=1, max_length=64)
    configuration: dict


class SpecialistSyncInput(SpecialistSelection):
    space_uuid: str = Field(min_length=1, max_length=64)
    agent_uuid: str = Field(min_length=1, max_length=64)
    operator: str = Field(min_length=1, max_length=64)


class SpecialistConfigurationInput(SpecialistBody):
    space_uuid: str = Field(min_length=1, max_length=64)
    agent_uuid: str = Field(min_length=1, max_length=64)
    operator: str = Field(min_length=1, max_length=64)
    expected_configuration_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    configuration: dict


class SpecialistTrialInput(SpecialistBody):
    space_uuid: str = Field(min_length=1, max_length=64)
    agent_uuid: str = Field(min_length=1, max_length=64)
    dataset_spec: dict
    trace_id: str = Field(pattern=r"^[A-Za-z0-9._:-]{1,128}$")
    diagnoser_uuid: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_-]{1,32}$")
    draft: SpecialistSelection | None = None

    @model_validator(mode="after")
    def exclusive_source(self):
        if (self.diagnoser_uuid is None) == (self.draft is None):
            raise ValueError("saved resource XOR finite draft required")
        if (
            "diagnoser_uuid" in self.model_fields_set
            and self.diagnoser_uuid is None
            or "draft" in self.model_fields_set
            and self.draft is None
        ):
            raise ValueError("explicit null resource/draft is not accepted")
        return self

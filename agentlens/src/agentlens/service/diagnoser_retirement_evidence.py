"""Pinned, local build/material attestations. No caller-provided `ready=True` shortcut."""

from __future__ import annotations

import hashlib
import os
import stat
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field

from agentlens.service.diagnoser_retirement import (
    BuildUnavailable,
    BusinessUUID,
    Digest,
    Reference,
    RetirementError,
    StrictModel,
    digest,
    safe_image,
)
from agentlens.service.diagnoser_retirement_journal import read_private_json


class Artifact(StrictModel):
    role: Literal["worker_build", "source_manifest", "catalog", "schema", "materials"]
    path: str = Field(min_length=1, max_length=4096)
    sha256: Digest


class JobEvidence(StrictModel):
    job_uuid: BusinessUUID
    dataset_digest: Digest
    binding_digest: Digest
    configuration_digests: dict[BusinessUUID, Digest]
    model_digests: dict[BusinessUUID, Digest]
    runtime_type: str = Field(min_length=1, max_length=64)
    selector_digest: Digest
    materials_ref: Reference
    materials_sha256: Digest


class EvidenceBundle(StrictModel):
    schema_version: Literal[1]
    environment_sha256: Digest
    ownership_sha256: Digest
    plan_sha256: Digest
    abi: Literal["1.3.0", "1.4.0"]
    source_sha256: Digest
    build_sha256: Digest
    catalog_sha256: Digest
    schema_sha256: Digest
    artifacts: list[Artifact] = Field(min_length=4, max_length=64)
    jobs: list[JobEvidence] = Field(min_length=1, max_length=1000)


def _file_sha256(path: str) -> str:
    if not Path(path).is_absolute():
        raise RetirementError("attested artifact path must be absolute")
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
                raise RetirementError("artifact must be an owned regular file")
            return hashlib.file_digest(stream, "sha256").hexdigest()
    except OSError as exc:
        raise BuildUnavailable("attested artifact is not actually available") from exc


def _model_is_configured(row: dict) -> bool:
    try:
        endpoint = urlsplit(row["endpoint"])
        # Parsing the port also rejects malformed/out-of-range connection URLs.
        _ = endpoint.port
        return bool(
            endpoint.scheme in {"http", "https"}
            and endpoint.hostname
            and not endpoint.username
            and not endpoint.password
            and not endpoint.query
            and not endpoint.fragment
            and row["model_name"].strip()
            and row["api_key"].strip()
        )
    except (KeyError, TypeError, ValueError, AttributeError):
        return False


def _uses_advisory_core(row: dict) -> bool:
    from agentlens.diagnoser.definitions.catalog import (
        CORE_EXECUTOR,
        CORE_VERSION,
        validate_core_spec,
    )

    spec = row.get("spec") or {}
    if row["type"] != "builtin" or spec.get("executor") != CORE_EXECUTOR:
        return False
    try:
        validate_core_spec(spec)
        if row.get("version") != CORE_VERSION:
            raise ValueError("stale Core")
    except ValueError as exc:
        raise RetirementError("bound Core definition is not canonical") from exc
    return True


class LocalEvidenceVerifier:
    """The root supplies the sealed SHA, independently of the 0600 evidence file.

    This validates local artifacts and frozen material/reference mappings. It does
    not infer a live Worker is healthy; the controlled build/material receipt must
    have been produced by the operator's actual readiness checks before sealing.
    A historical source archive alone lacks build/material proofs and fails here.
    """

    def __init__(self, path: Path, *, expected_sha256: str, source=False):
        self.path, self.expected_sha256, self.source = path, expected_sha256, source

    def __call__(self, state, ownership, plan):
        raw = read_private_json(self.path)
        if digest(raw) != self.expected_sha256:
            raise RetirementError("sealed readiness evidence changed")
        bundle = EvidenceBundle.model_validate(raw)
        build = plan.source_build if self.source else plan.target_build
        if (
            bundle.environment_sha256 != ownership.environment_sha256
            or bundle.ownership_sha256 != plan.ownership_sha256
            or bundle.plan_sha256 != digest(plan.model_dump())
            or bundle.abi != build.abi
            or bundle.source_sha256 != build.source_sha256
            or bundle.build_sha256 != build.build_sha256
            or bundle.catalog_sha256 != build.catalog_sha256
            or bundle.schema_sha256 != build.schema_sha256
        ):
            raise RetirementError("readiness environment/source/target/schema fingerprints differ")
        artifact_groups = {}
        for artifact in bundle.artifacts:
            if _file_sha256(artifact.path) != artifact.sha256:
                raise BuildUnavailable("readiness artifact digest drifted")
            artifact_groups.setdefault(artifact.role, set()).add(artifact.sha256)
        for role, expected in (
            ("worker_build", build.build_sha256),
            ("source_manifest", build.source_sha256),
            ("catalog", build.catalog_sha256),
            ("schema", build.schema_sha256),
        ):
            if expected not in artifact_groups.get(role, set()):
                raise BuildUnavailable("required actual build artifact missing")
        if len({row.job_uuid for row in bundle.jobs}) != len(bundle.jobs) or {
            row.job_uuid for row in bundle.jobs
        } != set(ownership.job_uuids):
            raise RetirementError("readiness must cover each owned Job exactly once")
        image = safe_image(state)
        result = {}
        for evidence in bundle.jobs:
            job = state["jobs"][evidence.job_uuid]
            dataset = state["datasets"][job["dataset_uuid"]]
            if (
                evidence.dataset_digest != image["datasets"][dataset["uuid"]]["row_digest"]
                or evidence.binding_digest != digest(job["diagnoser_uuids"])
                or evidence.runtime_type != (dataset.get("spec") or {}).get("runtime_type")
                or evidence.selector_digest != digest((dataset.get("spec") or {}).get("selector"))
                or evidence.materials_sha256 not in artifact_groups.get("materials", set())
            ):
                raise RetirementError(
                    "material/runtime/selector mapping differs from current resources"
                )
            from agentlens.diagnoser.definitions.specialists import configuration_from_spec
            from agentlens.diagnoser.registry import is_specialist_builtin
            from agentlens.domain.specialist_policy import configuration_digest

            required_models, available_models, required_specialists = set(), set(), set()
            for uuid in job["diagnoser_uuids"]:
                definition = state["diagnosers"].get(uuid)
                if definition is None:
                    raise RetirementError("bound Diagnoser dependency is missing")
                spec = definition.get("spec") or {}
                specialist = is_specialist_builtin(definition["type"], spec)
                if specialist:
                    try:
                        config = configuration_from_spec(spec)
                        config_digest = configuration_digest(config)
                    except ValueError as exc:
                        raise RetirementError("Specialist configuration proof is invalid") from exc
                    if evidence.configuration_digests.get(uuid) != config_digest:
                        raise RetirementError("Specialist configuration proof drifted")
                    required_specialists.add(uuid)
                if definition["mode"] == "llm":
                    model_uuid = spec.get("model_connection_uuid")
                    if not isinstance(model_uuid, str) or not model_uuid.strip():
                        raise RetirementError("bound LLM model reference is missing")
                    required_models.add(model_uuid)
                    if specialist or not _uses_advisory_core(definition):
                        available_models.add(model_uuid)
            if (
                set(evidence.configuration_digests) != required_specialists
                or set(evidence.model_digests) != required_models
            ):
                raise RetirementError("bound configuration/model evidence coverage differs")
            for uuid in required_models:
                row = state["models"].get(uuid)
                if (
                    row is None
                    or image["models"][uuid]["row_digest"] != evidence.model_digests[uuid]
                    or row["space_uuid"] != ownership.space_uuid
                ):
                    raise RetirementError("model proof differs from current same-space connection")
                if not _model_is_configured(row):
                    raise RetirementError("bound LLM model configuration is incomplete")
                if uuid in available_models and row["status"] != "available":
                    raise RetirementError("bound LLM model is not AVAILABLE")
            result[evidence.job_uuid] = {
                "ready": True,
                "reasons": [],
                "evidence_sha256": self.expected_sha256,
                "materials_sha256": evidence.materials_sha256,
                "build_sha256": build.build_sha256,
                "abi": build.abi,
            }
        return result

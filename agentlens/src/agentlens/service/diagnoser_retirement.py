"""Owned diagnoser retirement: read-only previews and explicit short transactional phases.

Only Job status and ordered bindings are changed. Definitions are created through
an injected flush-only Specialist primitive. No Task, ledger, historical diagnosis,
Dataset, existing Diagnoser or model credential is rewritten by this service.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator
from sqlalchemy import select, text

from agentlens.diagnoser.lifecycle import has_unsupported_projection_identity, is_retired_builtin
from agentlens.infra.ob.models import (
    DatasetModel,
    DiagnoserModel,
    JobModel,
    ModelConnectionModel,
    TaskModel,
)
from agentlens.service.diagnoser_retirement_journal import Journal

SCHEMA_VERSION = 1
# New plans use TOOL_VERSION. Existing version-one retirement plans remain readable.
# Preview copies the plan value so a stored journal digest does not change.
TOOL_VERSION = "retired-diagnosis-v1"
ACTIVE_STATUSES = {"pending", "dispatched", "running"}
TERMINAL_STATUSES = {"succeeded", "failed", "canceled"}
Identifier = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_-]{1,64}$")]
BusinessUUID = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_-]{1,32}$")]
Digest = Annotated[str, StringConstraints(pattern=r"^[a-f0-9]{64}$")]
Reference = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9._:/-]{1,256}$")]
PlanToolVersion = Annotated[
    str,
    StringConstraints(
        max_length=64,
        pattern=r"^(?:retired-diagnosis-v1|[a-z][a-z0-9-]*-retirement-v1)$",
    ),
]


class RetirementError(ValueError):
    """Safe messages never include private source, configuration or connection values."""


class BuildUnavailable(RetirementError):
    """A pinned old build/material dependency is no longer actually available."""


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    @model_validator(mode="before")
    @classmethod
    def strict_schema_version(cls, value):
        if (
            isinstance(value, dict)
            and "schema_version" in value
            and type(value["schema_version"]) is not int
        ):
            raise ValueError("schema_version must be an integer")
        return value


class BuildIdentity(StrictModel):
    abi: Literal["1.3.0", "1.4.0"]
    source_sha256: Digest
    build_sha256: Digest
    catalog_sha256: Digest
    schema_sha256: Digest
    evidence_ref: Reference


class BuildReadiness(StrictModel):
    ready: bool
    reasons: list[Annotated[str, StringConstraints(pattern=r"^[a-z0-9_]{1,128}$")]] = Field(
        default_factory=list
    )
    abi: Literal["1.3.0", "1.4.0"] | None = None
    build_sha256: Digest | None = None
    evidence_sha256: Digest | None = None
    materials_sha256: Digest | None = None

    @model_validator(mode="after")
    def seal_required(self):
        if self.ready and any(
            value is None
            for value in (self.abi, self.build_sha256, self.evidence_sha256, self.materials_sha256)
        ):
            raise ValueError("ready requires sealed build and materials evidence")
        return self


class Ownership(StrictModel):
    schema_version: Literal[1]
    environment_sha256: Digest
    authorization_ref: Reference
    space_uuid: Identifier
    agent_uuid: Identifier
    job_uuids: list[BusinessUUID] = Field(min_length=1, max_length=1000)
    dataset_uuids: list[BusinessUUID] = Field(min_length=1, max_length=1000)
    diagnoser_uuids: list[BusinessUUID] = Field(min_length=1, max_length=2000)

    @model_validator(mode="after")
    def exact_scope(self):
        if self.space_uuid.lower() in {
            "default",
            "shared",
            "global",
            "0",
        } or self.agent_uuid.lower() in {"default", "shared", "global", "0"}:
            raise ValueError("shared/default scope cannot be migrated")
        for values in (self.job_uuids, self.dataset_uuids, self.diagnoser_uuids):
            if len(values) != len(set(values)):
                raise ValueError("ownership allowlists must be unique")
        return self


class Target(StrictModel):
    diagnoser_uuid: BusinessUUID
    builtin_key: Identifier
    canonical_variant: Identifier
    version: Literal["1.0.0"]
    definition_digest: Digest
    configuration_digest: Digest
    configuration_ref: Reference
    expected_configuration_digest: Digest | None = None


class Substitution(StrictModel):
    old_uuid: BusinessUUID
    target_uuid: BusinessUUID | None


class JobMapping(StrictModel):
    job_uuid: BusinessUUID
    purpose_ref: Reference
    substitutions: list[Substitution] = Field(min_length=1, max_length=1000)

    @model_validator(mode="after")
    def unique_sources(self):
        if len({value.old_uuid for value in self.substitutions}) != len(self.substitutions):
            raise ValueError("mapping sources must be unique")
        return self


class Plan(StrictModel):
    schema_version: Literal[1]
    tool_version: PlanToolVersion
    migration_id: BusinessUUID
    ownership_sha256: Digest
    source_build: BuildIdentity
    target_build: BuildIdentity
    mappings: list[JobMapping] = Field(min_length=1, max_length=1000)
    targets: list[Target] = Field(max_length=2000)

    @model_validator(mode="after")
    def unique_plan(self):
        if self.source_build.abi != "1.3.0" or self.target_build.abi != "1.4.0":
            raise ValueError("retirement requires exact source 1.3.0 and target 1.4.0")
        if len({row.job_uuid for row in self.mappings}) != len(self.mappings):
            raise ValueError("duplicate Job mapping")
        if len({row.diagnoser_uuid for row in self.targets}) != len(self.targets):
            raise ValueError("duplicate planned target")
        return self


def digest(value) -> str:
    return hashlib.sha256(
        json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode("utf-8")
    ).hexdigest()


def _iso(value):
    return value.isoformat() if isinstance(value, datetime) else value


def _row(model):
    return {
        column.key: _iso(getattr(model, column.key)) for column in model.__mapper__.column_attrs
    }


def retired_definition(row: dict) -> bool:
    return is_retired_builtin(row.get("type"), row.get("spec") or {})


def custom_dependency(row: dict) -> str:
    if row.get("type") != "custom":
        return "not_custom"
    spec = row.get("spec") or {}
    if row.get("mode") == "rule" or spec.get("code"):
        return "manual_review"
    projection_identity = spec
    runtime = spec.get("runtime_type")
    if isinstance(runtime, str) and runtime.strip().lower() in {"other", "raw"}:
        # Both are existing raw CUSTOM defaults, not a dependency on a retired Projector.
        projection_identity = {key: value for key, value in spec.items() if key != "runtime_type"}
    if has_unsupported_projection_identity(projection_identity):
        return "manual_review"
    prompt = spec.get("prompt")
    if not isinstance(prompt, str) or re.search(r"{{|{%", prompt):
        return "manual_review"
    return "unchanged_raw_custom"


def _safe_row(kind: str, row: dict) -> dict:
    safe = {
        key: value
        for key, value in row.items()
        if key
        not in {
            "id",
            "spec",
            "api_key",
            "endpoint",
            "model_name",
            "execution_snapshot",
            "judge_request_ledger",
            "stats",
            "msg",
            "description",
            "name",
        }
    }
    safe["row_digest"] = digest(row)
    if "spec" in row:
        safe["spec_digest"] = digest(row["spec"])
    if kind == "diagnosers":
        safe["retired"] = retired_definition(row)
        safe["dependency"] = custom_dependency(row)
    if kind == "datasets":
        spec = row.get("spec") or {}
        safe["runtime_type"] = spec.get("runtime_type")
        selector = spec.get("selector")
        safe["selector_digest"] = digest(selector)
        safe["legacy_selector"] = bool(selector)
    if kind == "tasks":
        snapshot = row.get("execution_snapshot")
        safe["snapshot_digest"] = digest(snapshot)
        safe["legacy_snapshot"] = snapshot is None
        safe["executor_revision"] = (snapshot or {}).get("executor_revision")
        safe["ledger_digest"] = digest(row.get("judge_request_ledger"))
    return safe


def safe_image(state: dict) -> dict:
    return {
        kind: {uuid: _safe_row(kind, row) for uuid, row in sorted(rows.items())}
        for kind, rows in state.items()
    }


def inventory(store, ownership: Ownership) -> dict:
    """No flush, commit, pause, sync, Provider or external Trace call."""
    return safe_image(store.read(ownership))


class RetirementStore:
    """SQLAlchemy access confined to declared owned resources; no table commit helpers."""

    def __init__(self, session_factory, *, environment_sha256: str, target_model_uuids=()):
        self.session_factory = session_factory
        self.environment_sha256 = environment_sha256
        self.target_model_uuids = set(target_model_uuids)

    def _authorize(self, ownership):
        if ownership.environment_sha256 != self.environment_sha256:
            raise RetirementError("ownership environment fingerprint differs")

    def read(self, ownership: Ownership, *, session=None, lock=False) -> dict:
        self._authorize(ownership)
        if session is None:
            with self.session_factory() as current:
                return self.read(ownership, session=current, lock=False)
        state = {}
        for kind, model, allowlist in (
            ("jobs", JobModel, ownership.job_uuids),
            ("datasets", DatasetModel, ownership.dataset_uuids),
            ("diagnosers", DiagnoserModel, ownership.diagnoser_uuids),
        ):
            stmt = select(model).where(model.uuid.in_(allowlist)).order_by(model.uuid)
            if lock:
                stmt = stmt.with_for_update().execution_options(populate_existing=True)
            rows = list(session.scalars(stmt))
            if any(
                row.space_uuid != ownership.space_uuid or row.agent_uuid != ownership.agent_uuid
                for row in rows
            ):
                raise RetirementError("cross-scope resource rejected")
            state[kind] = {row.uuid: _row(row) for row in rows}
        tasks = session.scalars(
            select(TaskModel)
            .where(TaskModel.job_uuid.in_(ownership.job_uuids))
            .order_by(TaskModel.uuid)
            .execution_options(populate_existing=True)
        )
        state["tasks"] = {row.uuid: _row(row) for row in tasks}
        model_ids = {
            row["spec"].get("model_connection_uuid")
            for row in state["diagnosers"].values()
            if isinstance(row.get("spec"), dict)
        } - {None, ""}
        model_ids.update(self.target_model_uuids)
        models = session.scalars(
            select(ModelConnectionModel)
            .where(ModelConnectionModel.uuid.in_(model_ids))
            .order_by(ModelConnectionModel.uuid)
        )
        state["models"] = {row.uuid: _row(row) for row in models}
        return state

    @contextmanager
    def transaction(self):
        with self.session_factory() as session:
            with session.begin():
                if session.scalar(text("SELECT @@session.autocommit")) != 0:
                    raise RetirementError("migration requires a real autocommit=0 transaction")
                yield session

    def set_job(self, session, uuid: str, *, status: str, bindings: list[str], modified: str):
        row = session.scalar(
            select(JobModel)
            .where(JobModel.uuid == uuid)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if row is None:
            raise RetirementError("owned Job disappeared")
        row.status = status
        row.diagnoser_uuids = list(bindings)
        row.gmt_modified = datetime.fromisoformat(modified)
        session.flush()

    def pool_census(self) -> dict:
        """Read-only publication evidence; it does not grant permission to pause other Jobs."""
        with self.session_factory() as session:
            rows = session.scalars(
                select(TaskModel)
                .where(TaskModel.status.in_(ACTIVE_STATUSES))
                .order_by(TaskModel.uuid)
            )
            return {row.uuid: _safe_row("tasks", _row(row)) for row in rows}


class RetirementMigration:
    """One exact scope per instance. Preview is pure; apply owns phase transactions."""

    def __init__(
        self,
        store,
        ownership: Ownership,
        plan: Plan,
        journal: Journal,
        *,
        configuration_loader: Callable | None = None,
        specialist_adapter=None,
        readiness_verifier: Callable | None = None,
        source_build_verifier: Callable | None = None,
    ):
        self.store, self.ownership, self.plan, self.journal = store, ownership, plan, journal
        self.configuration_loader = configuration_loader
        self.specialist_adapter = specialist_adapter
        self.readiness_verifier = readiness_verifier
        self.source_build_verifier = source_build_verifier
        if plan.ownership_sha256 != digest(ownership.model_dump()):
            raise RetirementError("plan ownership digest differs")
        if {row.job_uuid for row in plan.mappings} != set(ownership.job_uuids):
            raise RetirementError("every owned Job requires exactly one explicit purpose mapping")
        if not {row.diagnoser_uuid for row in plan.targets} <= set(ownership.diagnoser_uuids):
            raise RetirementError("planned targets need explicit Diagnoser ownership")
        target_ids = {row.diagnoser_uuid for row in plan.targets}
        for mapping in plan.mappings:
            for change in mapping.substitutions:
                if change.old_uuid not in ownership.diagnoser_uuids:
                    raise RetirementError("mapping source is not explicitly owned")
                if change.target_uuid is not None and change.target_uuid not in target_ids:
                    raise RetirementError("mapping target has no exact planned identity")

    def _manifest(self, *, required=True):
        if not self.journal.path.exists() and not self.journal.path.is_symlink():
            if required:
                raise RetirementError("pause manifest is required")
            return None
        value = self.journal.read()
        if (
            value.get("schema_version") != SCHEMA_VERSION
            or value.get("plan_sha256") != digest(self.plan.model_dump())
            or value.get("ownership_sha256") != self.plan.ownership_sha256
        ):
            raise RetirementError("manifest identity differs")
        phases = value.get("phases")
        if not isinstance(phases, list) or not phases:
            raise RetirementError("manifest requires its original pause intent")
        first = phases[0]
        if first.get("phase") != "pause" or value.get("original") != first.get("before"):
            raise RetirementError("manifest original before-image differs")
        if value.get("initial_preview_sha256") != first.get("preview_sha256"):
            raise RetirementError("manifest original preview identity differs")
        seen = set()
        for event in phases:
            preview = event.get("preview")
            if not isinstance(preview, dict) or event.get("status") not in {"intent", "committed"}:
                raise RetirementError("invalid manifest phase")
            self._validate_preview(preview, event.get("preview_sha256"))
            if event.get("before") != preview.get("before") or event.get("phase") != preview.get(
                "phase"
            ):
                raise RetirementError("manifest phase before-image differs")
            if event["preview_sha256"] in seen:
                raise RetirementError("duplicate manifest phase identity")
            seen.add(event["preview_sha256"])
            if event["status"] == "committed" and (
                not isinstance(event.get("after"), dict)
                or event.get("receipt_sha256") != digest(event["after"])
            ):
                raise RetirementError("manifest commit receipt differs")
        return value

    def _check_owned(self, state):
        if set(state["jobs"]) != set(self.ownership.job_uuids):
            raise RetirementError("owned Job missing")
        if set(state["datasets"]) != set(self.ownership.dataset_uuids):
            raise RetirementError("owned Dataset missing")
        for kind in ("jobs", "datasets", "diagnosers"):
            for row in state[kind].values():
                if (
                    row["space_uuid"] != self.ownership.space_uuid
                    or row["agent_uuid"] != self.ownership.agent_uuid
                ):
                    raise RetirementError("cross-scope resource rejected")
        for job in state["jobs"].values():
            if job["dataset_uuid"] not in self.ownership.dataset_uuids or not set(
                job["diagnoser_uuids"]
            ) <= set(self.ownership.diagnoser_uuids):
                raise RetirementError("Job references a resource outside its explicit allowlists")
            if not set(job["diagnoser_uuids"]) <= set(state["diagnosers"]):
                raise RetirementError("bound Diagnoser missing")

    def _activities(self, state):
        return {
            uuid: _safe_row("tasks", row)
            for uuid, row in sorted(state["tasks"].items())
            if row["status"] in ACTIVE_STATUSES
        }

    def _dispositions(self, state):
        result = {}
        for mapping in self.plan.mappings:
            job = state["jobs"][mapping.job_uuid]
            reasons = []
            dataset = state["datasets"][job["dataset_uuid"]]
            dataset_spec = dataset.get("spec") or {}
            if dataset_spec.get("runtime_type") not in {
                "cc",
                "claudecode",
                "codex",
                "codefuse",
                "antcc",
                "antcodex",
                "dsh",
                "langchain",
                "langgraph",
                "deepagents",
                "deep-agents",
            }:
                reasons.append("runtime_mapping_unavailable")
            if "selector" in dataset_spec and dataset_spec["selector"] is not None:
                reasons.append("legacy_selector_requires_manual_review")
            bindings = list(job["diagnoser_uuids"])
            changes = {row.old_uuid: row.target_uuid for row in mapping.substitutions}
            for old_uuid, target_uuid in changes.items():
                old = state["diagnosers"].get(old_uuid)
                if old is None or not retired_definition(old):
                    reasons.append("mapping_source_not_retired_builtin")
                if old_uuid not in bindings:
                    reasons.append("mapping_source_not_bound")
                if target_uuid is not None:
                    if self.specialist_adapter is None:
                        reasons.append("specialist_primitive_not_frozen")
                    elif self.configuration_loader is None:
                        reasons.append("target_configuration_unavailable")
                    else:
                        target = next(
                            row for row in self.plan.targets if row.diagnoser_uuid == target_uuid
                        )
                        try:
                            configuration = self.configuration_loader(target.configuration_ref)
                            self.specialist_adapter.validate(
                                None, target, configuration, state, self.ownership
                            )
                        except (ValueError, TypeError, KeyError):
                            reasons.append("target_configuration_or_model_unavailable")
            for uuid in bindings:
                if uuid not in changes and retired_definition(state["diagnosers"][uuid]):
                    reasons.append("retired_binding_without_explicit_mapping")
                if custom_dependency(state["diagnosers"][uuid]) == "manual_review":
                    reasons.append("custom_dependency_manual_review")
            after = [
                changes.get(uuid, uuid) for uuid in bindings if changes.get(uuid, uuid) is not None
            ]
            if len(after) != len(set(after)):
                reasons.append("duplicate_after_binding")
            if not after:
                reasons.append("no_remaining_checks")
            result[mapping.job_uuid] = {
                "purpose_ref": mapping.purpose_ref,
                "disposition": (
                    "retire_unbound"
                    if reasons == ["no_remaining_checks"]
                    else "blocked"
                    if reasons
                    else "remap"
                ),
                "reasons": sorted(set(reasons)),
                "before_bindings": bindings,
                "after_bindings": after,
            }
        return result

    def _configurations(self, state, *, session=None):
        if not self.plan.targets:
            return {}
        if self.specialist_adapter is None or self.configuration_loader is None:
            raise RetirementError("Specialist primitive/configuration loader is not frozen")
        configs = {}
        for target in self.plan.targets:
            configuration = self.configuration_loader(target.configuration_ref)
            self.specialist_adapter.validate(session, target, configuration, state, self.ownership)
            configs[target.diagnoser_uuid] = configuration
        return configs

    def _readiness(self, state):
        if self.readiness_verifier is None:
            return {
                uuid: {"ready": False, "reasons": ["target_build_material_proof_unavailable"]}
                for uuid in state["jobs"]
            }
        result = self.readiness_verifier(state, self.ownership, self.plan)
        if not isinstance(result, dict) or set(result) != set(state["jobs"]):
            raise RetirementError("readiness evidence must cover exact owned Jobs")
        for row in result.values():
            try:
                validated = BuildReadiness.model_validate(row)
            except ValueError as exc:
                raise RetirementError("readiness requires sealed build/material proof") from exc
            if validated.ready and (
                validated.abi != "1.4.0"
                or validated.build_sha256 != self.plan.target_build.build_sha256
            ):
                raise RetirementError("readiness sealed target build differs")
        return result

    def _source_ready(self, state):
        if self.source_build_verifier is None:
            return False
        restored = deepcopy(state)
        original = self._manifest()["original"]["jobs"]
        for uuid, job in restored["jobs"].items():
            job["diagnoser_uuids"] = list(original[uuid]["diagnoser_uuids"])
        try:
            proof = self.source_build_verifier(restored, self.ownership, self.plan)
        except BuildUnavailable:
            return False
        if not isinstance(proof, dict) or set(proof) != set(state["jobs"]):
            raise RetirementError("source build proof must cover exact owned Jobs")
        validated = []
        for row in proof.values():
            try:
                validated.append(BuildReadiness.model_validate(row))
            except ValueError as exc:
                raise RetirementError(
                    "source rollback requires sealed build/material proof"
                ) from exc
        return all(
            row.ready
            and row.abi == "1.3.0"
            and row.build_sha256 == self.plan.source_build.build_sha256
            for row in validated
        )

    @staticmethod
    def _prior(manifest, phase):
        for event in reversed(manifest["phases"]):
            if event["phase"] == phase and event["status"] == "committed":
                return event
        raise RetirementError("required prior phase is not committed")

    @staticmethod
    def _stable_resources(image):
        return {kind: rows for kind, rows in image.items() if kind != "tasks"}

    def _verify_drain_transition(self, before, current):
        if self._stable_resources(before) != self._stable_resources(current):
            raise RetirementError(
                "resource drift since pause; re-preview cannot silently change plan"
            )
        for uuid, old in before["tasks"].items():
            new = current["tasks"].get(uuid)
            if new is None:
                raise RetirementError("observed Task disappeared before drain evidence")
            immutable = (
                "uuid",
                "job_uuid",
                "scheduled_at",
                "gmt_create",
                "creator",
                "snapshot_digest",
                "legacy_snapshot",
                "executor_revision",
            )
            if any(old.get(key) != new.get(key) for key in immutable):
                raise RetirementError("Task immutable execution facts drifted")
            if old["status"] in TERMINAL_STATUSES and old != new:
                raise RetirementError("historical terminal Task drifted")
            if old["status"] in ACTIVE_STATUSES and new["status"] not in (
                ACTIVE_STATUSES | TERMINAL_STATUSES
            ):
                raise RetirementError("Task did not reach a supported lifecycle state")
        if set(current["tasks"]) - set(before["tasks"]):
            raise RetirementError("new Task appeared while Job was paused")

    def preview(self, phase: str = "pause") -> dict:
        if phase not in {"pause", "ready", "remap", "resume", "rollback_pause", "rollback_restore"}:
            raise RetirementError("unsupported migration phase")
        state = self.store.read(self.ownership)
        self._check_owned(state)
        before = safe_image(state)
        manifest = self._manifest(required=phase != "pause")
        dispositions = self._dispositions(state) if phase in {"pause", "ready", "remap"} else {}
        readiness = {}
        source_ready = False
        root_preview = None
        if phase == "pause":
            if manifest is not None and manifest["phases"]:
                raise RetirementError(
                    "migration already started; use exact existing phase recovery"
                )
        else:
            root_preview = manifest["initial_preview_sha256"]
            latest = manifest["phases"][-1]
            if latest["status"] != "committed":
                raise RetirementError("incomplete journal intent; recover it before a new preview")
            if phase == "ready":
                paused = self._prior(manifest, "pause")
                self._verify_drain_transition(paused["after"], before)
            elif phase == "remap":
                ready = self._prior(manifest, "ready")
                if ready["after"] != before:
                    raise RetirementError("ready phase after-image drifted")
                self._configurations(state)
            elif phase == "resume":
                remapped = self._prior(manifest, "remap")
                if remapped["after"] != before:
                    raise RetirementError("remap after-image drifted")
                readiness = self._readiness(state)
            elif phase == "rollback_pause":
                if latest["phase"] not in {"resume", "remap"} or self._stable_resources(
                    latest["after"]
                ) != self._stable_resources(before):
                    raise RetirementError("rollback requires the exact migration after-image")
                for uuid, previous_task in latest["after"]["tasks"].items():
                    if (
                        previous_task["status"] in TERMINAL_STATUSES
                        and before["tasks"].get(uuid) != previous_task
                    ):
                        raise RetirementError("historical terminal Task drifted before rollback")
            elif phase == "rollback_restore":
                paused = self._prior(manifest, "rollback_pause")
                self._verify_drain_transition(paused["after"], before)
                source_ready = self._source_ready(state)
        activities = self._activities(state)
        if phase in {"ready", "remap", "resume", "rollback_restore"} and activities:
            raise RetirementError(
                "all pending/dispatched/running Tasks must drain; Jobs remain disabled"
            )
        if phase not in {"pause", "rollback_pause"} and any(
            row["status"] != "disabled" for row in state["jobs"].values()
        ):
            raise RetirementError("Jobs must remain disabled for this phase")
        changes = {}
        original = manifest["original"]["jobs"] if manifest else before["jobs"]
        for uuid, job in state["jobs"].items():
            status, bindings = "disabled", list(job["diagnoser_uuids"])
            if phase == "remap":
                row = dispositions[uuid]
                # A blocked mapping never silently changes bindings or unmasks runtime/selector.
                if row["disposition"] in {"remap", "retire_unbound"}:
                    bindings = row["after_bindings"]
            elif phase == "resume":
                remapped_disposition = self._prior(manifest, "remap")["preview"]["dispositions"][
                    uuid
                ]
                if (
                    original[uuid]["status"] == "enabled"
                    and readiness[uuid].get("ready") is True
                    and remapped_disposition["disposition"] == "remap"
                ):
                    status = "enabled"
            elif phase == "rollback_restore":
                bindings = list(original[uuid]["diagnoser_uuids"])
                if source_ready and original[uuid]["status"] == "enabled":
                    status = "enabled"
            if phase != "ready":
                changes[uuid] = {"status": status, "diagnoser_uuids": bindings}
        retired_dispositions = {}
        if dispositions:
            for uuid, definition in state["diagnosers"].items():
                if not retired_definition(definition):
                    continue
                uses = [
                    job_uuid
                    for job_uuid, job in state["jobs"].items()
                    if uuid in job["diagnoser_uuids"]
                ]
                proposed = {}
                for mapping in self.plan.mappings:
                    for substitution in mapping.substitutions:
                        if substitution.old_uuid == uuid:
                            proposed[mapping.job_uuid] = substitution.target_uuid
                disposition = "retire_unbound"
                if uses:
                    if any(
                        dispositions[job_uuid]["disposition"] == "blocked"
                        or job_uuid not in proposed
                        for job_uuid in uses
                    ):
                        disposition = "blocked"
                    elif any(proposed[job_uuid] is not None for job_uuid in uses):
                        disposition = "remap"
                retired_dispositions[uuid] = {
                    "disposition": disposition,
                    "owned_job_uuids": sorted(uses),
                    "planned_targets_by_job": proposed,
                }
        result = {
            "schema_version": SCHEMA_VERSION,
            "tool_version": self.plan.tool_version,
            "migration_id": self.plan.migration_id,
            "phase": phase,
            "ownership_sha256": self.plan.ownership_sha256,
            "environment_sha256": self.ownership.environment_sha256,
            "plan_sha256": digest(self.plan.model_dump()),
            "source_build": self.plan.source_build.model_dump(),
            "target_build": self.plan.target_build.model_dump(),
            "root_preview_sha256": root_preview,
            "before": before,
            "actions": changes,
            "dispositions": dispositions,
            "retired_diagnoser_dispositions": retired_dispositions,
            "activities": activities,
            "targets": [row.model_dump() for row in self.plan.targets],
            "readiness": readiness,
            "source_build_ready": source_ready,
        }
        result["preview_sha256"] = digest(result)
        return result

    def _validate_preview(self, preview, expected):
        value = deepcopy(preview)
        declared = value.pop("preview_sha256", None)
        if declared != expected or digest(value) != expected:
            raise RetirementError("preview SHA256 differs")
        if (
            value["plan_sha256"] != digest(self.plan.model_dump())
            or value["ownership_sha256"] != self.plan.ownership_sha256
        ):
            raise RetirementError("preview identity differs")

    def apply(self, preview: dict, *, expected_preview_sha256: str, apply: bool = False) -> dict:
        """Without apply=True, no journal or resource write is attempted."""
        self._validate_preview(preview, expected_preview_sha256)
        if not apply:
            return {"applied": False, "preview_sha256": expected_preview_sha256}
        with self.journal.locked():
            manifest = self._manifest(required=preview["phase"] != "pause")
            if manifest is None:
                manifest = {
                    "schema_version": SCHEMA_VERSION,
                    "migration_id": self.plan.migration_id,
                    "ownership_sha256": self.plan.ownership_sha256,
                    "plan_sha256": digest(self.plan.model_dump()),
                    "initial_preview_sha256": expected_preview_sha256,
                    "original": preview["before"],
                    "phases": [],
                }
            existing = next(
                (
                    event
                    for event in manifest["phases"]
                    if event["preview_sha256"] == expected_preview_sha256
                ),
                None,
            )
            if existing is not None:
                return self._recover(manifest, existing)
            if manifest["phases"] and manifest["phases"][-1]["status"] != "committed":
                raise RetirementError("recover incomplete phase before advancing")
            fresh = self.preview(preview["phase"])
            if fresh != preview:
                raise RetirementError("preview is stale; no resource write performed")
            event = {
                "phase": preview["phase"],
                "preview_sha256": expected_preview_sha256,
                "status": "intent",
                "preview": preview,
                "before": preview["before"],
                "after": None,
                "modified_at": datetime.now().replace(microsecond=0).isoformat(),
            }
            manifest["phases"].append(event)
            # First durable write precedes the first DB mutation. A failed journal halts here.
            self.journal.write(manifest)
            return self._execute(manifest, event)

    def _execute(self, manifest, event):
        preview = event["preview"]
        with self.store.transaction() as session:
            state = self.store.read(self.ownership, session=session, lock=True)
            self._check_owned(state)
            if safe_image(state) != event["before"]:
                raise RetirementError("before-image drift under Job locks; stopped")
            if preview["phase"] in {
                "ready",
                "remap",
                "resume",
                "rollback_restore",
            } and self._activities(state):
                raise RetirementError("Task appeared before phase locks; keep disabled")
            if preview["phase"] == "remap":
                configurations = self._configurations(state, session=session)
                used_targets = {
                    uuid
                    for action in preview["actions"].values()
                    for uuid in action["diagnoser_uuids"]
                }
                for target in self.plan.targets:
                    if target.diagnoser_uuid in used_targets:
                        self.specialist_adapter.sync(
                            session, target, configurations[target.diagnoser_uuid], self.ownership
                        )
            if preview["phase"] == "resume" and self._readiness(state) != preview["readiness"]:
                raise RetirementError("target build/material/model proof drifted")
            if preview["phase"] == "rollback_restore":
                source_ready = self._source_ready(state)
                if source_ready != preview["source_build_ready"]:
                    raise RetirementError("source build rollback proof drifted")
            for uuid, action in sorted(preview["actions"].items()):
                self.store.set_job(
                    session,
                    uuid,
                    status=action["status"],
                    bindings=action["diagnoser_uuids"],
                    modified=event["modified_at"],
                )
            # Save the complete safe after-image before commit. Commit may fail; recovery
            # compares the real DB to this exact image, never assumes journal means success.
            if hasattr(session, "expire_all"):
                session.expire_all()
            event["after"] = safe_image(self.store.read(self.ownership, session=session, lock=True))
            self.journal.write(manifest)
        return self._finish(manifest, event)

    def _finish(self, manifest, event):
        current = safe_image(self.store.read(self.ownership))
        if current != event["after"]:
            if event["phase"] not in {"pause", "rollback_pause"}:
                raise RetirementError("fresh read-back differs; keep intent for recovery")
            self._verify_drain_transition(event["after"], current)
            event["after"] = current
        event["status"] = "committed"
        event["receipt_sha256"] = digest(current)
        self.journal.write(manifest)
        return {
            "applied": True,
            "phase": event["phase"],
            "preview_sha256": event["preview_sha256"],
            "receipt_sha256": event["receipt_sha256"],
            "recovered": False,
        }

    def _recover(self, manifest, event):
        with self.store.transaction() as session:
            state = self.store.read(self.ownership, session=session, lock=True)
            self._check_owned(state)
            image = safe_image(state)
            if event["status"] == "committed":
                if image != event["after"]:
                    raise RetirementError("already-applied phase after-image drifted")
                return {
                    "applied": True,
                    "phase": event["phase"],
                    "recovered": True,
                    "receipt_sha256": event["receipt_sha256"],
                    "preview_sha256": event["preview_sha256"],
                }
            if image == event["before"]:
                retry = True
            elif event["after"] is not None and image == event["after"]:
                retry = False
            elif event["after"] is not None and event["phase"] in {"pause", "rollback_pause"}:
                self._verify_drain_transition(event["after"], image)
                event["after"] = image
                retry = False
            else:
                raise RetirementError("incomplete phase conflicts with current resources")
        if retry:
            return self._execute(manifest, event)
        result = self._finish(manifest, event)
        return {**result, "recovered": True}

    def wait_for_drain(
        self,
        *,
        timeout_sec: float,
        poll_sec: float = 1.0,
        monotonic: Callable | None = None,
        sleep: Callable | None = None,
    ) -> dict:
        """Only bounded read sessions while waiting; never mutate a Task to finish drain."""
        import time

        monotonic, sleep = monotonic or time.monotonic, sleep or time.sleep
        if not 0 <= timeout_sec <= 3600 or not 0.01 <= poll_sec <= 60:
            raise RetirementError("drain wait is outside supported bounds")
        paused = self._prior(self._manifest(), "pause")
        deadline = monotonic() + timeout_sec
        while True:
            state = self.store.read(self.ownership)
            self._verify_drain_transition(paused["after"], safe_image(state))
            activity = self._activities(state)
            if not activity:
                return {"drained": True, "activities": {}}
            if monotonic() >= deadline:
                return {
                    "drained": False,
                    "activities": activity,
                    "reason": "timeout_jobs_remain_disabled",
                }
            sleep(min(poll_sec, max(0, deadline - monotonic())))


class FrozenSpecialistAdapter:
    """Consumes the root-frozen Specialist primitive; never invokes retired builtin sync."""

    def __init__(self, session_factory):
        self.session_factory = session_factory

    def validate(self, session, target: Target, configuration, state, ownership: Ownership):
        from agentlens.diagnoser.definitions.specialists import (
            configuration_from_spec,
            get_specialist_definition,
            validate_specialist_spec,
        )
        from agentlens.domain.specialist_policy import (
            configuration_digest,
            normalize_specialist_configuration,
        )
        from agentlens.service.specialists import require_specialist_model

        if session is None:
            with self.session_factory() as current:
                return self.validate(current, target, configuration, state, ownership)
        try:
            definition = get_specialist_definition(target.builtin_key, target.canonical_variant)
            normalized = normalize_specialist_configuration(
                target.builtin_key, target.canonical_variant, configuration
            )
            if (
                definition["definition_digest"] != target.definition_digest
                or definition["version"] != target.version
                or configuration_digest(normalized) != target.configuration_digest
            ):
                raise RetirementError("target definition/configuration fingerprint drifted")
            require_specialist_model(
                session, space_uuid=ownership.space_uuid, configuration=normalized
            )
            rows = list(
                session.scalars(
                    select(DiagnoserModel).where(
                        DiagnoserModel.space_uuid == ownership.space_uuid,
                        DiagnoserModel.agent_uuid == ownership.agent_uuid,
                    )
                )
            )
            identities = [
                row
                for row in rows
                if row.type == "builtin"
                and isinstance(row.spec, dict)
                and row.spec.get("builtin_key") == target.builtin_key
                and row.spec.get("canonical_variant") == target.canonical_variant
            ]
            if len(identities) > 1 or any(row.uuid != target.diagnoser_uuid for row in identities):
                raise RetirementError("target managed identity differs from planned UUID")
            if any(
                row.name == definition["name"] and row.uuid != target.diagnoser_uuid for row in rows
            ):
                raise RetirementError("target canonical name collision")
            current = session.scalar(
                select(DiagnoserModel).where(DiagnoserModel.uuid == target.diagnoser_uuid)
            )
            if current is None:
                if target.expected_configuration_digest is not None:
                    raise RetirementError(
                        "missing existing target for expected configuration digest"
                    )
            else:
                if (
                    (
                        current.space_uuid,
                        current.agent_uuid,
                        current.type,
                        current.mode,
                        current.version,
                    )
                    != (
                        ownership.space_uuid,
                        ownership.agent_uuid,
                        "builtin",
                        definition["mode"],
                        target.version,
                    )
                    or current.spec.get("builtin_key") != target.builtin_key
                    or current.spec.get("canonical_variant") != target.canonical_variant
                ):
                    raise RetirementError("planned target UUID is occupied by another identity")
                validate_specialist_spec(current.spec)
                existing_digest = configuration_digest(configuration_from_spec(current.spec))
                if (
                    target.expected_configuration_digest is not None
                    and target.expected_configuration_digest != existing_digest
                ):
                    raise RetirementError("existing target configuration drifted")
                if existing_digest != target.configuration_digest:
                    if target.expected_configuration_digest is None:
                        raise RetirementError("configuration change requires exact prior digest")
                    for job in session.scalars(select(JobModel)):
                        if (
                            target.diagnoser_uuid in (job.diagnoser_uuids or [])
                            and job.uuid not in ownership.job_uuids
                        ):
                            raise RetirementError("target configuration affects an unowned Job")
                    for task in session.scalars(
                        select(TaskModel).where(TaskModel.status.in_(ACTIVE_STATUSES))
                    ):
                        frozen = (task.execution_snapshot or {}).get("diagnosers", [])
                        if any(
                            isinstance(row, dict) and row.get("uuid") == target.diagnoser_uuid
                            for row in frozen
                        ):
                            raise RetirementError(
                                "target configuration still has an active frozen Task"
                            )
        except RetirementError:
            raise
        except (ValueError, TypeError, KeyError) as exc:
            raise RetirementError("target canonical finite configuration is invalid") from exc

    def sync(self, session, target: Target, configuration, ownership: Ownership):
        from agentlens.domain.specialist_policy import normalize_specialist_configuration
        from agentlens.service.specialists import sync_specialist_builtin

        normalized = normalize_specialist_configuration(
            target.builtin_key, target.canonical_variant, configuration
        )
        result = sync_specialist_builtin(
            session,
            space_uuid=ownership.space_uuid,
            agent_uuid=ownership.agent_uuid,
            builtin_key=target.builtin_key,
            canonical_variant=target.canonical_variant,
            planned_uuid=target.diagnoser_uuid,
            expected_definition_digest=target.definition_digest,
            configuration=normalized,
            expected_configuration_digest=target.expected_configuration_digest,
        )
        if (
            result.diagnoser_uuid != target.diagnoser_uuid
            or result.definition_digest != target.definition_digest
            or result.configuration_digest != target.configuration_digest
        ):
            raise RetirementError("Specialist sync returned a different planned identity")
        return result

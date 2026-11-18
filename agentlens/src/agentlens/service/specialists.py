"""Specialist orchestration with transaction-owned, flush-only canonical sync."""

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from sqlalchemy import Connection, select

from agentlens.diagnoser.definitions.specialists import (
    configuration_from_spec,
    get_specialist_definition,
    specialist_spec,
    validate_specialist_spec,
)
from agentlens.domain.enums import ModelConnectionStatus
from agentlens.domain.scope import is_concrete_scope
from agentlens.domain.specialist_policy import (
    NormalizedSpecialistConfiguration,
    configuration_digest,
    normalize_specialist_configuration,
)
from agentlens.infra.ob.models import DiagnoserModel, ModelConnectionModel


@dataclass(frozen=True)
class SpecialistSyncResult:
    diagnoser_uuid: str
    action: Literal["created", "updated", "unchanged"]
    definition_digest: str
    configuration_digest: str


def require_specialist_model(session, *, space_uuid: str, configuration):
    if configuration.model_connection_uuid is None:
        return None
    model = session.scalar(
        select(ModelConnectionModel)
        .where(
            ModelConnectionModel.uuid == configuration.model_connection_uuid,
            ModelConnectionModel.space_uuid == space_uuid,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if model is None or model.status != ModelConnectionStatus.AVAILABLE.value:
        raise ValueError("specialist requires same-space AVAILABLE model")
    return model


def sync_specialist_builtin(
    session,
    *,
    space_uuid: str,
    agent_uuid: str,
    builtin_key: str,
    canonical_variant: str,
    planned_uuid: str,
    expected_definition_digest: str,
    configuration: NormalizedSpecialistConfiguration | None,
    expected_configuration_digest: str | None,
) -> SpecialistSyncResult:
    """Caller owns the transaction, conflict recovery and final read-back.

    No commit/rollback/provider/file side effects. Migration callers must keep
    the preview UUID and digest; this function never adopts a race winner.
    """
    import re

    if (
        not is_concrete_scope(space_uuid, agent_uuid)
        or not all(
            isinstance(v, str) and 1 <= len(v) <= 64 and v not in {"*", "0"}
            for v in (space_uuid, agent_uuid)
        )
        or not re.fullmatch(r"[A-Za-z0-9_-]{1,32}", planned_uuid)
    ):
        raise ValueError("invalid concrete specialist scope or planned UUID")
    definition = get_specialist_definition(builtin_key, canonical_variant)
    if expected_definition_digest != definition["definition_digest"]:
        raise ValueError("specialist definition digest drift")
    rows = list(
        session.scalars(
            select(DiagnoserModel)
            .where(
                DiagnoserModel.space_uuid == space_uuid,
                DiagnoserModel.agent_uuid == agent_uuid,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    )
    identity = [
        row
        for row in rows
        if isinstance(row.spec, dict)
        and row.spec.get("builtin_key") == builtin_key
        and row.spec.get("canonical_variant") == canonical_variant
        and row.type == "builtin"
    ]
    if len(identity) > 1:
        raise ValueError("duplicate managed specialist identity")
    current = identity[0] if identity else None
    if any(row.name == definition["name"] and row is not current for row in rows):
        raise ValueError("specialist canonical name collision")
    if current is not None:
        if (
            current.uuid != planned_uuid
            or current.mode != definition["mode"]
            or current.version != definition["version"]
        ):
            raise ValueError("specialist planned identity drift")
        validate_specialist_spec(current.spec)
        old_configuration = configuration_from_spec(current.spec)
        old_digest = configuration_digest(old_configuration)
        if (
            expected_configuration_digest is not None
            and expected_configuration_digest != old_digest
        ):
            raise ValueError("SPECIALIST_CONFIGURATION_CONFLICT")
        normalized = (
            old_configuration
            if configuration is None
            else normalize_specialist_configuration(builtin_key, canonical_variant, configuration)
        )
        if configuration_digest(normalized) != old_digest and expected_configuration_digest is None:
            raise ValueError("SPECIALIST_CONFIGURATION_CONFLICT")
    else:
        if configuration is None or expected_configuration_digest is not None:
            raise ValueError("new specialist requires full configuration and no prior digest")
        if (
            session.scalar(
                select(DiagnoserModel).where(DiagnoserModel.uuid == planned_uuid).with_for_update()
            )
            is not None
        ):
            raise ValueError("specialist planned UUID occupied")
        normalized = normalize_specialist_configuration(
            builtin_key, canonical_variant, configuration
        )
    require_specialist_model(session, space_uuid=space_uuid, configuration=normalized)
    target_spec = specialist_spec(definition, normalized)
    action = "unchanged"
    if current is None:
        now = datetime.now()
        current = DiagnoserModel(
            uuid=planned_uuid,
            space_uuid=space_uuid,
            agent_uuid=agent_uuid,
            name=definition["name"],
            description=definition["description"],
            type="builtin",
            mode=definition["mode"],
            version=definition["version"],
            spec=deepcopy(target_spec),
            creator=None,
            modifier=None,
            gmt_create=now,
            gmt_modified=now,
        )
        session.add(current)
        action = "created"
    elif (
        current.spec != target_spec
        or current.name != definition["name"]
        or current.description != definition["description"]
    ):
        current.spec = deepcopy(target_spec)
        current.name = definition["name"]
        current.description = definition["description"]
        current.gmt_modified = datetime.now()
        action = "updated"
    if action != "unchanged":
        session.flush()
    return SpecialistSyncResult(
        current.uuid, action, definition["definition_digest"], configuration_digest(normalized)
    )


class SpecialistError(ValueError):
    """Only safe public codes cross the service boundary."""

    def __init__(self, code: str, status: int = 422):
        super().__init__(code)
        self.code = code
        self.status = status


def _scope(space_uuid, agent_uuid):
    import re

    if not is_concrete_scope(space_uuid, agent_uuid) or any(
        not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", value)
        for value in (space_uuid, agent_uuid)
    ):
        raise SpecialistError("SPECIALIST_INPUT_INVALID")


def specialist_runtime_supported(runtime_type: str, *, key: str | None = None) -> bool:
    from agentlens.service.datasource.runtime import supports_specialist_runtime

    return isinstance(runtime_type, str) and supports_specialist_runtime(runtime_type, key=key)


def _definition(key, variant):
    from agentlens.diagnoser.definitions.specialists import UNAVAILABLE_SPECIALISTS

    try:
        return get_specialist_definition(key, variant)
    except (TypeError, ValueError):
        raise SpecialistError(
            "SPECIALIST_UNAVAILABLE"
            if key in UNAVAILABLE_SPECIALISTS
            else "SPECIALIST_INPUT_INVALID"
        ) from None


def _configuration(key, variant, raw):
    try:
        return normalize_specialist_configuration(key, variant, raw)
    except (TypeError, ValueError):
        raise SpecialistError("SPECIALIST_INPUT_INVALID") from None


def _owned(session, uuid, space_uuid, agent_uuid):
    _scope(space_uuid, agent_uuid)
    row = session.scalar(
        select(DiagnoserModel)
        .where(
            DiagnoserModel.uuid == uuid,
            DiagnoserModel.space_uuid == space_uuid,
            DiagnoserModel.agent_uuid == agent_uuid,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if row is None:
        raise SpecialistError("SPECIALIST_AGENT_UNAVAILABLE", 404)
    try:
        if row.type != "builtin" or row.version != "1.0.0":
            raise ValueError("not specialist")
        validate_specialist_spec(row.spec)
        definition = get_specialist_definition(
            row.spec["builtin_key"], row.spec["canonical_variant"]
        )
        if row.mode != definition["mode"]:
            raise ValueError("mode mismatch")
    except (TypeError, ValueError):
        raise SpecialistError("SPECIALIST_INPUT_INVALID") from None
    return row


def specialist_public_result(row, action):
    from agentlens.domain.secrets import redact_spec
    from agentlens.domain.specialist_policy import policy_summary

    spec = row.spec
    return {
        "diagnoser": {
            "uuid": row.uuid,
            "space_uuid": row.space_uuid,
            "agent_uuid": row.agent_uuid,
            "name": row.name,
            "description": row.description or "",
            "type": row.type,
            "mode": row.mode,
            "version": row.version,
            "spec": redact_spec(spec),
            "gmt_create": row.gmt_create.astimezone().isoformat(),
            "gmt_modified": row.gmt_modified.astimezone().isoformat(),
            "creator": row.creator,
            "modifier": row.modifier,
        },
        "action": action,
        "specialist": {
            "key": spec["builtin_key"],
            "variant": spec["canonical_variant"],
            "version": row.version,
            "definition_digest": spec["definition_digest"],
            "configuration_digest": spec["configuration_digest"],
            "policy_summary": policy_summary(spec["specialist_policy"]),
        },
    }


def specialist_catalog(*, space_uuid, agent_uuid, runtime_type):
    from agentlens.diagnoser.definitions.specialists import (
        UNAVAILABLE_SPECIALISTS,
        load_specialist_definitions,
    )

    _scope(space_uuid, agent_uuid)
    items = {}
    for definition in load_specialist_definitions():
        spec, key = definition["spec"], definition["key"]
        mapped = specialist_runtime_supported(runtime_type, key=key)
        item = items.setdefault(
            key,
            {
                "key": key,
                "name": definition["name"],
                "selectable": mapped,
                "requires_configuration": True,
                "supported_variants": [],
                "unavailable_reason": None if mapped else "runtime_mapping_unavailable",
            },
        )
        item["supported_variants"].append(
            {
                "variant": definition["variant"],
                "version": definition["version"],
                "mode": definition["mode"],
                "executor": spec["executor"],
                "category": spec["category"],
                "score_role": spec["score_role"],
                "required_materials": deepcopy(spec["required_evidence"]["materials"]),
                "result_boundary": spec["result_boundary"],
                "definition_digest": definition["definition_digest"],
            }
        )
    for key in UNAVAILABLE_SPECIALISTS:
        items[key] = {
            "key": key,
            "name": key,
            "selectable": False,
            "requires_configuration": True,
            "supported_variants": [],
            "unavailable_reason": "required_fact_mapping_unavailable",
        }
    return {"items": list(items.values()), "runtime_type": runtime_type}


def sync_specialist_public(session, payload):
    """Short public transaction; unique conflict recovery never retargets migrations."""
    from sqlalchemy.exc import IntegrityError

    from agentlens.utils.uuid_gen import new_diagnoser_uuid

    _scope(payload.space_uuid, payload.agent_uuid)
    definition = _definition(payload.key, payload.variant)
    configuration = _configuration(payload.key, payload.variant, payload.configuration)

    def winner():
        rows = list(
            session.scalars(
                select(DiagnoserModel)
                .where(
                    DiagnoserModel.space_uuid == payload.space_uuid,
                    DiagnoserModel.agent_uuid == payload.agent_uuid,
                )
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        )
        matches = [
            row
            for row in rows
            if row.type == "builtin"
            and isinstance(row.spec, dict)
            and row.spec.get("builtin_key") == payload.key
            and row.spec.get("canonical_variant") == payload.variant
        ]
        if len(matches) > 1:
            raise SpecialistError("SPECIALIST_CONFIGURATION_CONFLICT", 409)
        return matches[0] if matches else None

    def write(planned_uuid):
        result = sync_specialist_builtin(
            session,
            space_uuid=payload.space_uuid,
            agent_uuid=payload.agent_uuid,
            builtin_key=payload.key,
            canonical_variant=payload.variant,
            planned_uuid=planned_uuid,
            expected_definition_digest=definition["definition_digest"],
            configuration=configuration,
            expected_configuration_digest=None,
        )
        row = _owned(session, result.diagnoser_uuid, payload.space_uuid, payload.agent_uuid)
        if result.action != "unchanged":
            row.modifier = payload.operator
            if result.action == "created":
                row.creator = payload.operator
        session.commit()
        session.expire_all()
        fresh = _owned(session, result.diagnoser_uuid, payload.space_uuid, payload.agent_uuid)
        return specialist_public_result(fresh, result.action)

    try:
        existing = winner()
        return write(existing.uuid if existing else new_diagnoser_uuid())
    except IntegrityError:
        session.rollback()
        try:
            existing = winner()
            if existing is None:
                raise SpecialistError("SPECIALIST_CONFIGURATION_CONFLICT", 409)
            return write(existing.uuid)
        except Exception:
            session.rollback()
            raise SpecialistError("SPECIALIST_CONFIGURATION_CONFLICT", 409) from None
    except Exception as exc:
        session.rollback()
        if isinstance(exc, SpecialistError):
            raise
        raise SpecialistError(
            "SPECIALIST_CONFIGURATION_CONFLICT"
            if str(exc) == "SPECIALIST_CONFIGURATION_CONFLICT"
            else "SPECIALIST_INPUT_INVALID",
            409 if str(exc) == "SPECIALIST_CONFIGURATION_CONFLICT" else 422,
        ) from None


def configure_specialist_public(session, uuid, payload):
    try:
        row = _owned(session, uuid, payload.space_uuid, payload.agent_uuid)
        raw = deepcopy(payload.configuration)
        if raw.get("policy") == {"kind": "preserve_existing_policy"}:
            raw["policy"] = deepcopy(row.spec["specialist_policy"])
        if row.mode == "llm":
            from agentlens.domain.specialist_policy import configuration_dict

            existing = configuration_dict(configuration_from_spec(row.spec))
            for key in ("model_connection_uuid", "llm_options"):
                if key not in raw:
                    raw[key] = existing[key]
        configuration = _configuration(row.spec["builtin_key"], row.spec["canonical_variant"], raw)
        result = sync_specialist_builtin(
            session,
            space_uuid=payload.space_uuid,
            agent_uuid=payload.agent_uuid,
            builtin_key=row.spec["builtin_key"],
            canonical_variant=row.spec["canonical_variant"],
            planned_uuid=row.uuid,
            expected_definition_digest=row.spec["definition_digest"],
            configuration=configuration,
            expected_configuration_digest=payload.expected_configuration_digest,
        )
        if result.action != "unchanged":
            row.modifier = payload.operator
        session.commit()
        session.expire_all()
        return specialist_public_result(
            _owned(session, row.uuid, payload.space_uuid, payload.agent_uuid), result.action
        )
    except Exception as exc:
        session.rollback()
        if isinstance(exc, SpecialistError):
            raise
        raise SpecialistError(
            "SPECIALIST_CONFIGURATION_CONFLICT"
            if str(exc) == "SPECIALIST_CONFIGURATION_CONFLICT"
            else "SPECIALIST_INPUT_INVALID",
            409 if str(exc) == "SPECIALIST_CONFIGURATION_CONFLICT" else 422,
        ) from None


def trial_specialist(session, traces, payload):
    """Full server-authorized Trace only; no Task, Record, receipt or write."""
    from agentlens.diagnoser.base import (
        ApplicabilitySkipped,
        ExecutorFailure,
        execute_with_applicability,
    )
    from agentlens.diagnoser.registry import ExecutorRegistry
    from agentlens.domain.diagnosis_record import DiagnosisResult, Execution, Metric
    from agentlens.domain.enums import Verdict
    from agentlens.infra.llm_judge.attempts import TrialAttempts
    from agentlens.service.datasource.diagnosis_input import read_authorized_diagnosis_rows
    from agentlens.service.datasource.projection.service import project_trace
    from agentlens.service.datasource.trace import Trace

    _scope(payload.space_uuid, payload.agent_uuid)
    dataset = payload.dataset_spec
    if (
        not isinstance(dataset, dict)
        or not isinstance(dataset.get("runtime_type"), str)
        or not 1 <= len(dataset["runtime_type"]) <= 64
        or not isinstance(dataset.get("query"), dict)
    ):
        raise SpecialistError("SPECIALIST_INPUT_INVALID")
    if not isinstance(dataset["query"].get("table"), str) or not dataset["query"]["table"]:
        raise SpecialistError("SPECIALIST_INPUT_INVALID")
    if not specialist_runtime_supported(dataset["runtime_type"]):
        raise SpecialistError("SPECIALIST_UNAVAILABLE")
    # Own this short read transaction; never publish or roll back caller work.
    bind = session.get_bind()
    if session.in_transaction() or (isinstance(bind, Connection) and bind.in_transaction()):
        raise SpecialistError("SPECIALIST_TRANSACTION_ACTIVE", 409)
    model_config = None
    with session.begin():
        if payload.diagnoser_uuid:
            row = _owned(session, payload.diagnoser_uuid, payload.space_uuid, payload.agent_uuid)
            spec = deepcopy(row.spec)
        else:
            definition = _definition(payload.draft.key, payload.draft.variant)
            configuration = _configuration(
                payload.draft.key, payload.draft.variant, payload.draft.configuration
            )
            spec = specialist_spec(definition, configuration)
        if not specialist_runtime_supported(dataset["runtime_type"], key=spec["builtin_key"]):
            raise SpecialistError("SPECIALIST_UNAVAILABLE")
        if spec["executor"] == "specialist_llm":
            try:
                model = require_specialist_model(
                    session,
                    space_uuid=payload.space_uuid,
                    configuration=configuration_from_spec(spec),
                )
            except ValueError:
                # Preserve material-first SKIP; report unavailable only if needed.
                pass
            else:
                model_config = {
                    "endpoint": model.endpoint,
                    "api_key": model.api_key,
                    "model_id": model.model_name,
                }
    attempts = TrialAttempts()
    executor = None
    state = "available"
    try:
        try:

            class SourceReader:
                def read_explicit_trace_rows(self, *args, **kwargs):
                    try:
                        return traces.read_explicit_trace_rows(*args, **kwargs)
                    except Exception:
                        raise ExecutorFailure("source_query_failed", "专项来源读取失败") from None

            rows = read_authorized_diagnosis_rows(
                SourceReader(),
                dataset_spec=dataset,
                trace_ids=[payload.trace_id],
                expected_space_uuid=payload.space_uuid,
            )
        except ValueError:
            raise SpecialistError("SPECIALIST_TRACE_SCOPE_REJECTED") from None
        trace = project_trace(
            Trace(
                payload.trace_id,
                dataset["runtime_type"],
                rows[payload.trace_id],
                source_meta={"source_complete": True},
            ),
            require_text_evidence=False,
        )
        executor = ExecutorRegistry.create(
            "llm" if spec["executor"] == "specialist_llm" else "rule", spec["executor"], spec
        )
        # The complete material gate precedes model binding/any HTTP request.
        skip = executor.applicability_skip_reason(trace)
        if skip:
            raise ApplicabilitySkipped(skip)
        if spec["executor"] == "specialist_llm":
            if model_config is None:
                raise ExecutorFailure("model_unavailable", "专项需要同 Space AVAILABLE 模型")
            executor.spec = {**spec, "model": model_config}
            executor.attempt_hooks_for = lambda _: attempts
        result = execute_with_applicability(executor, trace)
    except ApplicabilitySkipped as skipped:
        state = (
            "unsupported"
            if any(
                token in skipped.detail
                for token in ("unsupported", "limit_exceeded", "precision_policy")
            )
            else "missing"
        )
        result = skipped
    except SpecialistError:
        raise
    except Exception as exc:
        code, msg, evidence = (
            (exc.code, exc.msg, exc.evidence)
            if isinstance(exc, ExecutorFailure)
            else ("trial_failed", "专项试运行失败", {"error_type": type(exc).__name__})
        )
        result = DiagnosisResult(
            [Metric(spec["builtin_key"], Verdict.FAILED, msg)],
            Execution.failed(code, msg, evidence),
        )
    finally:
        if executor is not None:
            executor.close()
    if isinstance(result, ApplicabilitySkipped):
        response = {
            "verdict": None,
            "skipped": True,
            "skip_reason": result.code,
            "skip_reason_detail": result.detail or None,
            "summary": result.detail or result.code,
            "metrics": [],
            "execution": {"status": "succeeded", "code": "", "msg": "", "evidence": {}},
        }
    else:
        response = {
            "verdict": result.verdict.value,
            "skipped": False,
            "skip_reason": None,
            "skip_reason_detail": None,
            "summary": result.summary,
            "metrics": [
                {
                    "name": metric.name,
                    "verdict": metric.verdict.value,
                    "reason": metric.reason,
                    "evidence": metric.evidence,
                }
                for metric in result.metrics
            ],
            "execution": {
                "status": result.execution.status.value,
                "code": result.execution.code,
                "msg": result.execution.msg,
                "evidence": result.execution.evidence,
            },
        }
    response.update(
        specialist={
            "key": spec["builtin_key"],
            "variant": spec["canonical_variant"],
            "version": "1.0.0",
            "definition_digest": spec["definition_digest"],
            "configuration_digest": spec["configuration_digest"],
            "material_state": state,
        },
        request_attempts={
            "reserved": attempts.reserved,
            "max": 3 if spec["executor"] == "specialist_llm" else 0,
        },
    )
    return response

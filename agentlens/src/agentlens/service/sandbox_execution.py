"""One Task/attempt-owned rule execution with teardown verified before result release."""

import hashlib
import json
import math
from dataclasses import dataclass
from uuid import uuid4

from agentlens.config import sandbox_input_limit
from agentlens.diagnoser.executors.rule.custom.enhanced_contract import validate_runtime_manifest
from agentlens.domain.sandbox import SandboxConnection, SandboxError, SandboxExecutionContext
from agentlens.infra.ob.tables.sandbox_table import SandboxTable
from agentlens.infra.sandbox import (
    SandboxClient,
    SandboxCreateNotSent,
    effective_input_limit,
    encode_request,
)
from agentlens.service.rule_runtime import normalize_rule_runtime, require_rule_runtime
from agentlens.service.sandbox import resolve_sandbox_credentials


@dataclass
class _PreparedExecution:
    connection: SandboxConnection
    identity: dict
    lease_id: str
    create: dict
    execute: dict
    runtime: dict
    fingerprint: str
    input_budget: dict


class SandboxExecutionService:
    def __init__(self, session_factory, *, client_factory=SandboxClient) -> None:
        self.session_factory = session_factory
        self.client_factory = client_factory

    def _prepare(
        self, code: str, trace_dict: dict, context: SandboxExecutionContext
    ) -> _PreparedExecution:
        """The preview and actual execution share the exact bounded wire construction."""
        profile = context.frozen_profile
        runtime_spec = {}
        if any(
            value is not None
            for value in (context.rule_runtime, context.rule_runtime_manifest, context.materials)
        ):
            try:
                runtime_spec = normalize_rule_runtime(
                    {
                        "rule_runtime": context.rule_runtime,
                        "rule_runtime_manifest": context.rule_runtime_manifest,
                        "materials": context.materials,
                    }
                )
            except ValueError as exc:
                raise SandboxError("sandbox_configuration_invalid") from exc
            if profile.get("mode") != "sandbox":
                raise SandboxError("sandbox_runtime_required")
        if (
            context.space_uuid != profile.get("space_uuid")
            or not context.task_uuid
            or not context.diagnoser_uuid
            or type(context.attempt) is not int
            or context.attempt < 0
            or not isinstance(code, str)
            or not isinstance(trace_dict, dict)
        ):
            raise SandboxError("sandbox_configuration_invalid")
        with self.session_factory() as session:
            connection = resolve_sandbox_credentials(SandboxTable(session), profile)
        timeout = (
            context.timeout_seconds
            if context.timeout_seconds is not None
            else connection.timeout_seconds
        )
        if (
            isinstance(timeout, bool)
            or not isinstance(timeout, (int, float))
            or not math.isfinite(timeout)
            or timeout <= 0
        ):
            raise SandboxError("sandbox_configuration_invalid")
        timeout = min(timeout, connection.timeout_seconds)
        identity = {
            "space_uuid": context.space_uuid,
            "task_uuid": context.task_uuid,
            "attempt": context.attempt,
            "diagnoser_uuid": context.diagnoser_uuid,
            "execution_uuid": context.execution_uuid or uuid4().hex,
        }
        lease_id = hashlib.sha256(encode_request(identity)).hexdigest()
        create_payload = {
            "lease_id": lease_id,
            "connection_uuid": connection.uuid,
            **{
                key: identity[key]
                for key in ("space_uuid", "task_uuid", "attempt", "diagnoser_uuid")
            },
            **{
                key: getattr(connection, key)
                for key in ("image", "cpu", "memory", "timeout_seconds")
            },
        }
        execution_payload = {"code": code, "trace": trace_dict, "timeout_seconds": timeout}
        if runtime_spec:
            create_payload.update(
                {key: runtime_spec[key] for key in ("rule_runtime", "rule_runtime_manifest")}
            )
            execution_payload.update(runtime_spec)
        # Release DB reads before probing; validation never admits a lease.
        preflight = self.client_factory(connection)
        try:
            health = preflight.health()
            require_rule_runtime(runtime_spec, profile, health)
            limit = effective_input_limit(health, connection.image, sandbox_input_limit())
            raw = encode_request(
                {"create": create_payload, "execute": execution_payload}, max_bytes=limit
            )
            measured = preflight.validate_execution(create_payload, execution_payload)
            limit = min(limit, measured["max_input_bytes"])
            if len(raw) > limit:
                raise SandboxError(
                    "sandbox_input_limit",
                    details={
                        "stage": "request",
                        "actual_bytes": len(raw),
                        "allowed_bytes": limit,
                    },
                )
            if measured["input_bytes"] > limit:
                raise SandboxError(
                    "sandbox_input_limit",
                    details={
                        "stage": "worker_stdin",
                        "actual_bytes": measured["input_bytes"],
                        "allowed_bytes": limit,
                    },
                )
        except SandboxError:
            raise
        except Exception as exc:
            raise SandboxError("sandbox_transport_error") from exc
        finally:
            preflight.close()
        return _PreparedExecution(
            connection,
            identity,
            lease_id,
            create_payload,
            execution_payload,
            runtime_spec,
            hashlib.sha256(raw).hexdigest(),
            {
                "valid": True,
                "request_bytes": len(raw),
                "worker_input_bytes": measured["input_bytes"],
                "max_input_bytes": limit,
                "transport": measured["transport"],
            },
        )

    def preview(self, code: str, trace_dict: dict, context: SandboxExecutionContext) -> dict:
        return self._prepare(code, trace_dict, context).input_budget

    def execute(self, code: str, trace_dict: dict, context: SandboxExecutionContext) -> dict:
        """Return the worker's raw result only after independently verified teardown."""
        prepared = self._prepare(code, trace_dict, context)
        connection, identity, lease_id = prepared.connection, prepared.identity, prepared.lease_id
        create_payload, execution_payload = prepared.create, prepared.execute
        runtime_spec, fingerprint = prepared.runtime, prepared.fingerprint
        profile = context.frozen_profile
        with self.session_factory() as session:
            SandboxTable(session).reserve_lease(
                {
                    "uuid": lease_id,
                    "connection_uuid": connection.uuid,
                    **{
                        key: identity[key]
                        for key in ("space_uuid", "task_uuid", "attempt", "diagnoser_uuid")
                    },
                    "request_fingerprint": fingerprint,
                },
                profile=profile,
                worker_uuid=context.worker_uuid,
                runtime_spec=runtime_spec,
            )
        # Never retain a database session or row lock across plugin I/O.
        client = None
        cleanup_verified = False
        create_not_sent = False
        error = None
        result = None
        try:
            client = self.client_factory(connection)
            client.create(create_payload)
            result = _worker_result(
                client.execute(lease_id, execution_payload),
                expected_manifest=runtime_spec.get("rule_runtime_manifest"),
            )
        except SandboxCreateNotSent as failure:
            # A failed connection cannot have admitted a create. Closing only
            # this local reference avoids pinning the profile on an unknown ID.
            create_not_sent = True
            cleanup_verified = True
            error = failure
        except SandboxError as failure:
            error = failure
        except Exception:
            # Transport/provider exceptions must not expose endpoints, keys or rule content.
            error = SandboxError("sandbox_transport_error")
        finally:
            if client is not None:
                try:
                    if not create_not_sent:
                        cleanup_verified = client.delete(lease_id) is True
                except Exception:
                    cleanup_verified = False
                finally:
                    try:
                        client.close()
                    except Exception:
                        pass
            with self.session_factory() as session:
                SandboxTable(session).finish_lease(lease_id, cleanup_verified=cleanup_verified)
        if not cleanup_verified:
            raise SandboxError("sandbox_cleanup_pending")
        if error is not None:
            raise error
        return result

    def reconcile_pending(self, batch_limit: int = 20) -> dict[str, int]:
        """Bounded retry of whole-sandbox cleanup, including plugin-restart orphans.

        Profile mutation is blocked while any lease lacks verified cleanup, so
        the current token is never sent to a different endpoint during recovery.
        This can run concurrently; verified closure is monotonic in the table.
        """
        batch_limit = max(1, min(int(batch_limit), 100))
        with self.session_factory() as session:
            pending = SandboxTable(session).pending_cleanup(batch_limit)
        closed = 0
        for lease_id, connection in pending:
            client = None
            verified = False
            try:
                client = self.client_factory(connection)
                verified = client.delete(lease_id) is True
            except Exception:
                pass
            finally:
                if client is not None:
                    try:
                        client.close()
                    except Exception:
                        pass
            with self.session_factory() as session:
                SandboxTable(session).finish_lease(lease_id, cleanup_verified=verified)
            closed += int(verified)
        return {"checked": len(pending), "closed": closed, "pending": len(pending) - closed}


def _worker_result(response: dict, *, expected_manifest: str | None = None) -> dict:
    if not isinstance(response, dict):
        raise SandboxError("sandbox_protocol_error")
    if response.get("timed_out") is True:
        raise SandboxError("exec_timeout")
    if response.get("truncated") is True:
        raise SandboxError("sandbox_output_limit")
    if (
        type(response.get("exit_code")) is not int
        or type(response.get("timed_out")) is not bool
        or type(response.get("truncated")) is not bool
        or type(response.get("cleanup_verified")) is not bool
        or not isinstance(response.get("stdout"), str)
        or not isinstance(response.get("stderr"), str)
    ):
        raise SandboxError("sandbox_protocol_error")
    if response["exit_code"] != 0:
        raise SandboxError("rule_runtime_error")
    # Do not accept arbitrary leading prints or multiple worker responses.
    lines = response["stdout"].strip().splitlines()
    if len(lines) != 1:
        raise SandboxError("sandbox_protocol_error")
    try:
        worker = json.loads(lines[0])
    except (ValueError, RecursionError) as error:
        raise SandboxError("sandbox_protocol_error") from error
    if not isinstance(worker, dict):
        raise SandboxError("sandbox_protocol_error")
    if worker.get("status") == "error":
        code = worker.get("failure_code")
        enhanced_codes = {
            "rule_test_harness_error",
            "rule_test_incomplete",
            "rule_timeout",
            "rule_output_too_large",
            "rule_runtime_unavailable",
            "rule_validation_error",
        }
        allowed = {"exec_timeout"} | (enhanced_codes if expected_manifest else set())
        raise SandboxError(code if code in allowed else "rule_runtime_error")
    if worker.get("status") != "ok" or not isinstance(worker.get("result"), dict):
        raise SandboxError("sandbox_protocol_error")
    result = dict(worker["result"])
    if expected_manifest is not None:
        try:
            manifest = validate_runtime_manifest(worker.get("runtime_manifest"))
        except ValueError as exc:
            raise SandboxError("sandbox_runtime_unavailable") from exc
        if manifest["manifest_sha256"] != expected_manifest:
            raise SandboxError("sandbox_runtime_unavailable")
        evidence = {"rule_runtime": manifest["id"], "rule_runtime_manifest": expected_manifest}
        for key in ("captured_stdout", "captured_stderr"):
            value = worker.get(key, "")
            if not isinstance(value, str) or len(value.encode()) > 65536:
                raise SandboxError("sandbox_protocol_error")
            evidence[key] = value
        if type(worker.get("output_truncated", False)) is not bool:
            raise SandboxError("sandbox_protocol_error")
        evidence["output_truncated"] = worker.get("output_truncated", False)
        result["_sandbox_execution"] = evidence
    return result

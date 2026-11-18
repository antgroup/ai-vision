"""Quick endpoints and authorization for existing quick result routes."""

import math

from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.models import DiagnosisQuickRunRequest
from apps.core.permissions.constants import DIAGNOSIS_MANAGE, DIAGNOSIS_READ
from apps.core.permissions.space import (
    SpaceAuthorizationError,
    require_space_permission,
)
from apps.core.services import diagnosis_quick_run as service
from apps.core.services.diagnosis_quick_input import QuickRunError

from .common import error_response, success_response


def quick_error(exc):
    return error_response(
        exc.key,
        exc.status * 10,
        status=exc.status,
        data={"error_key": exc.key, "retryable": exc.retryable},
    )


def _record_definition(row):
    """Accept only the service's bounded, own-record public projection."""
    definition = row.get("diagnoser_definition")
    if not isinstance(definition, dict) or any(
        definition.get(key) != row.get(field)
        for key, field in (("uuid", "diagnoser_uuid"), ("version", "diagnoser_version"))
    ):
        return None
    required = ("uuid", "key", "name", "version", "revision", "digest")
    if any(
        not isinstance(definition.get(key), str) or not 1 <= len(definition[key]) <= 512
        for key in required
    ):
        return None
    if definition.get("category") not in (
        "outcome",
        "process",
        "safety",
        "system",
    ) or definition.get("score_role") not in ("quality", "signal"):
        return None
    weight = definition.get("weight")
    if (
        isinstance(weight, bool)
        or not isinstance(weight, (int, float))
        or not math.isfinite(weight)
        or weight < 0
    ):
        return None
    metrics = definition.get("metrics")
    if not isinstance(metrics, list) or not 1 <= len(metrics) <= 100:
        return None
    if any(
        not isinstance(metric, dict)
        or not isinstance(metric.get("name"), str)
        or not 1 <= len(metric["name"]) <= 512
        or not isinstance(metric.get("desc", ""), str)
        or len(metric.get("desc", "")) > 512
        for metric in metrics
    ):
        return None
    return {
        **{
            key: definition[key]
            for key in (*required, "category", "score_role", "weight")
        },
        "metrics": [
            {"name": metric["name"], "desc": metric.get("desc", "")}
            for metric in metrics
        ],
        **{
            key: definition[key]
            for key in (
                "builtin_key",
                "canonical_variant",
                "prompt_version",
                "result_boundary",
            )
            if isinstance(definition.get(key), str) and len(definition[key]) <= 512
        },
    }


class QuickAPIView(APIView):
    def handle_exception(self, exc):
        if isinstance(exc, QuickRunError):
            return quick_error(exc)
        return super().handle_exception(exc)


class QuickRunOptionsView(QuickAPIView):
    def get(self, request, space_id):
        return success_response(
            service.options(
                request.user, space_id, request.query_params.get("agent_uuid", "")
            )
        )


class QuickRunView(QuickAPIView):
    saved_diagnoser = False

    def post(self, request, space_id):
        result = service.submit(
            request.user,
            space_id,
            request.query_params.get("agent_uuid", ""),
            request.data,
            **({"saved_diagnoser": True} if self.saved_diagnoser else {}),
        )
        return Response(
            {"code": 0, "message": "success", "data": result},
            status=202 if result["state"] == "provisioning" else 200,
        )


class DiagnosisRunOptionsView(QuickAPIView):
    def get(self, request, space_id):
        return success_response(
            service.run_options(
                request.user, space_id, request.query_params.get("agent_uuid", "")
            )
        )


class DiagnosisRunView(QuickRunView):
    saved_diagnoser = True


class QuickGuardedAPIView(QuickAPIView):
    """Guard before handlers/cache; enrich only verified resources after forwarding."""

    quick_mutation_permission = DIAGNOSIS_MANAGE

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        self.quick_receipts = {}
        self.quick_contexts = {}
        space_id = kwargs.get("space_id")
        if not space_id:
            return
        # Check the operation's permission before looking up remote resources.
        # An unavailable AgentLens must never mask a local authorization denial.
        permission = (
            DIAGNOSIS_READ
            if request.method in {"GET", "HEAD", "OPTIONS"}
            else self.quick_mutation_permission
        )
        try:
            require_space_permission(
                user=request.user, space_id=space_id, permission=permission
            )
        except SpaceAuthorizationError as exc:
            raise QuickRunError(exc.error_key, exc.status) from None
        self.quick_space_id = space_id
        jobs = list(request.query_params.getlist("job_uuid"))
        direct = {
            "job_uuid": kwargs.get("job_uuid"),
            "dataset_uuid": kwargs.get("dataset_uuid"),
            "task_uuid": kwargs.get("task_uuid")
            or request.query_params.get("task_uuid"),
        }
        record_uuid = kwargs.get("record_uuid")
        if record_uuid:
            record = service.remote("GET", f"diagnosis-record/{record_uuid}")
            self.quick_record = record
            jobs.append(record.get("job_uuid"))
            direct["task_uuid"] = record.get("task_uuid")
        for field, value in direct.items():
            if value:
                receipt = (
                    DiagnosisQuickRunRequest.objects.select_related("space", "agent")
                    .filter(**{field: value})
                    .first()
                )
                if receipt:
                    self._guard(receipt, request)
                    jobs.append(receipt.job_uuid)
        if direct["job_uuid"]:
            jobs.append(direct["job_uuid"])
        for job_uuid in set(jobs):
            if not job_uuid:
                raise QuickRunError("QUICK_PERMISSION_DENIED", 403)
            receipt = (
                DiagnosisQuickRunRequest.objects.select_related("space", "agent")
                .filter(job_uuid=job_uuid)
                .first()
            )
            if receipt:
                self._guard(receipt, request)
                if direct["task_uuid"] and direct["task_uuid"] != receipt.task_uuid:
                    raise QuickRunError("QUICK_PERMISSION_DENIED", 403)
            else:
                job = service.remote("GET", f"job/{job_uuid}")
                if job.get("space_uuid") != space_id:
                    raise QuickRunError("RESOURCE_NOT_IN_SPACE", 404)
                if (job.get("spec") or {}).get(
                    "managed_by"
                ) == "aivision.quick_diagnosis":
                    raise QuickRunError("QUICK_PERMISSION_DENIED", 403)
                self._guard_personal_legacy(request, space_id, job.get("agent_uuid"))
        # An exact personal Agent must be owned even when the result list is empty.
        agent_uuid = request.query_params.get("agent_uuid")
        if agent_uuid:
            self._guard_personal_legacy(request, space_id, agent_uuid)
        if getattr(self, "quick_record", None) and self.quick_receipts:
            rec = self.quick_record
            receipt = self.quick_receipts.get(rec.get("job_uuid"))
            if (
                receipt is None
                or rec.get("task_uuid") != receipt.task_uuid
                or rec.get("target_id") not in receipt.canonical_input["trace_ids"]
            ):
                raise QuickRunError("QUICK_PERMISSION_DENIED", 403)
        if kwargs.get("trace_id") and self.quick_receipts:
            if any(
                kwargs["trace_id"] not in r.canonical_input["trace_ids"]
                for r in self.quick_receipts.values()
            ):
                raise QuickRunError("QUICK_PERMISSION_DENIED", 403)
        request.quick_receipts = self.quick_receipts
        if self.quick_receipts and request.method not in {"GET", "HEAD", "OPTIONS"}:
            if not record_uuid or not request.path.endswith("/feedback"):
                raise QuickRunError("QUICK_PERMISSION_DENIED", 403)
            try:
                require_space_permission(
                    user=request.user, space_id=space_id, permission=DIAGNOSIS_MANAGE
                )
            except SpaceAuthorizationError:
                raise QuickRunError("QUICK_PERMISSION_DENIED", 403) from None

    def _guard_personal_legacy(self, request, space_id, agent_uuid):
        from apps.core.models import Space

        # Ordinary business history keeps its existing READ/MANAGE contract.
        if Space.objects.filter(uuid=space_id, type=1).exists():
            service.authorize(
                request.user, space_id, agent_uuid, permission=DIAGNOSIS_READ
            )

    def _guard(self, receipt, request):
        if receipt.job_uuid in self.quick_receipts:
            return
        ctx, context = service.receipt_context(
            request.user, self.quick_space_id, receipt
        )
        requested_agent = request.query_params.get("agent_uuid")
        if requested_agent and requested_agent != str(receipt.agent.uuid):
            raise QuickRunError("QUICK_PERMISSION_DENIED", 403)
        self.quick_receipts[receipt.job_uuid] = receipt
        self.quick_contexts[receipt.task_uuid] = (ctx, context)

    def _record_metadata(self, row, request):
        row["diagnoser_definition"] = _record_definition(row)
        receipt = self.quick_receipts.get(row.get("job_uuid"))
        if receipt:
            if (
                row.get("task_uuid") != receipt.task_uuid
                or row.get("target_id") not in receipt.canonical_input["trace_ids"]
            ):
                raise QuickRunError("QUICK_PERMISSION_DENIED", 403)
            ctx, context = self.quick_contexts[receipt.task_uuid]
            row["definition_source"] = context["definition_source"]
            row["definition"] = context["definitions"].get(row.get("diagnoser_uuid"))
            row["can_feedback"] = DIAGNOSIS_MANAGE in ctx.permissions and row.get(
                "review_status"
            ) not in {"confirmed", "falsePositive"}
        else:
            try:
                context = service.remote(
                    "GET", f"quick-runs/tasks/{row['task_uuid']}/context"
                )
            except QuickRunError as exc:
                if exc.key != "QUICK_RESOURCE_EXPIRED":
                    raise
                context = None
            if context is not None and (
                context.get("space_uuid") != self.quick_space_id
                or context.get("job_uuid") != row.get("job_uuid")
            ):
                raise QuickRunError("QUICK_PERMISSION_DENIED", 403)
            row["definition_source"] = (
                context["definition_source"] if context else "unsupported_snapshot"
            )
            row["definition"] = (
                context["definitions"].get(row.get("diagnoser_uuid"))
                if context
                else None
            )
            if context is None:
                # Task retention does not delete Records. Authorize the retained
                # row's Job before using only its own vetted frozen definition.
                job = service.remote("GET", f"job/{row['job_uuid']}")
                if (
                    job.get("uuid") != row.get("job_uuid")
                    or job.get("space_uuid") != self.quick_space_id
                    or (job.get("spec") or {}).get("managed_by")
                    == "aivision.quick_diagnosis"
                    or (
                        row.get("space_uuid")
                        and row["space_uuid"] != self.quick_space_id
                    )
                    or (
                        request.query_params.get("agent_uuid")
                        and request.query_params["agent_uuid"] != job.get("agent_uuid")
                    )
                ):
                    raise QuickRunError("QUICK_PERMISSION_DENIED", 403)
                self._guard_personal_legacy(
                    request, self.quick_space_id, job.get("agent_uuid")
                )
                definition = _record_definition(row)
                if (
                    definition is not None
                    and row.get("space_uuid") == self.quick_space_id
                ):
                    row["definition_source"] = "record_snapshot"
                    row["definition"] = definition
            try:
                require_space_permission(
                    user=request.user,
                    space_id=self.quick_space_id,
                    permission=DIAGNOSIS_MANAGE,
                )
                row["can_feedback"] = row.get("review_status") not in {
                    "confirmed",
                    "falsePositive",
                }
            except SpaceAuthorizationError:
                row["can_feedback"] = False

    def _decorate(self, data, request):
        if not isinstance(data, dict):
            return
        if "items" in data:
            items = []
            managed_seen = False
            for row in data["items"]:
                if not isinstance(row, dict):
                    continue
                # List Jobs/Datasets may contain another caller's quick resources.
                managed = (row.get("spec") or {}).get(
                    "managed_by"
                ) == "aivision.quick_diagnosis"
                if managed and row.get("uuid"):
                    managed_seen = True
                    field = "dataset_uuid" if "type" in row else "job_uuid"
                    receipt = (
                        DiagnosisQuickRunRequest.objects.select_related(
                            "space", "agent"
                        )
                        .filter(**{field: row["uuid"]})
                        .first()
                    )
                    if receipt is None or receipt.user_id != request.user.pk:
                        continue
                    try:
                        self._guard(receipt, request)
                    except QuickRunError as exc:
                        # Collection visibility must not revoke otherwise valid
                        # legacy READ access. Explicit resources fail in initial.
                        if exc.key in {
                            "QUICK_PERMISSION_DENIED",
                            "QUICK_AGENT_UNAVAILABLE",
                            "QUICK_RESOURCE_EXPIRED",
                        }:
                            continue
                        raise
                self._decorate(row, request)
                items.append(row)
            data["items"] = items
            if managed_seen:
                data["total"] = len(items)
        elif "diagnoser_uuid" in data and "task_uuid" in data:
            self._record_metadata(data, request)
        elif "job_uuid" in data and "status" in data:
            receipt = self.quick_receipts.get(data["job_uuid"])
            data["quick_run"] = None
            if receipt:
                if data.get("uuid") != receipt.task_uuid:
                    raise QuickRunError("QUICK_PERMISSION_DENIED", 403)
                ctx, context = self.quick_contexts[receipt.task_uuid]
                data["quick_run"] = {
                    "request_id": receipt.request_id,
                    "agent_uuid": str(receipt.agent.uuid),
                    "trace_ids": context["trace_ids"],
                    "can_feedback": DIAGNOSIS_MANAGE in ctx.permissions,
                    "can_retest": context["can_retest"],
                    "retest_denial_reason": None
                    if context["can_retest"]
                    else "QUICK_BEFORE_UNSUPPORTED",
                    "comparison": context["comparison"],
                }

    def finalize_response(self, request, response, *args, **kwargs):
        if (
            hasattr(self, "quick_receipts")
            and response.status_code < 400
            and isinstance(getattr(response, "data", None), dict)
        ):
            try:
                self._decorate(response.data.get("data"), request)
            except QuickRunError as exc:
                response = quick_error(exc)
        return super().finalize_response(request, response, *args, **kwargs)

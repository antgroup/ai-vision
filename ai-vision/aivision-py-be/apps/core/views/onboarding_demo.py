"""Consent-gated setup of the built-in observability example."""

import logging

import requests
from django.db import transaction
from django.utils import timezone
from google.protobuf.message import DecodeError
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import (
    ExportTraceServiceResponse,
)
from rest_framework.views import APIView

from apps.core.models import Agent, SpaceOperationLog
from apps.core.services.diagnosis_quick_input import QuickRunError
from apps.core.services.diagnosis_quick_run import remote
from apps.core.services.ingest_token import IngestTokenError, build_ingest_token
from apps.core.services.observation_subject_service import require_agent_subject
from apps.core.services.onboarding_demo import (
    DEMO_AGENT_NAMES,
    DEMO_METADATA_KEY,
    OnboardingDemoError,
    authorize_demo,
    load_demo_sample,
    rebase_demo_sample,
    reserve_demo_agent,
)
from apps.core.services.operation_log_service import log_op
from apps.core.services.signoz_integration import current_ingest_org_id
from apps.core.trace_query.defaults import get_default_trace_query_service
from apps.core.trace_query.types import TraceQueryServiceError

from .common import error_response, success_response
from .langfuse_ingest import (
    _upstream_endpoint,
    forward_otlp_traces,
    normalize_langfuse_otlp_payload,
)

logger = logging.getLogger(__name__)
_INGEST_SETTLING_SECONDS = 45


def _prepare_rule(agent, space_id):
    scope = {"space_uuid": space_id, "agent_uuid": agent.uuid}
    result = remote("POST", "diagnosers/sync-common-builtins", json=scope)
    for row in result.get("diagnosers", []):
        if (
            row.get("space_uuid") == space_id
            and row.get("agent_uuid") == agent.uuid
            and row.get("type") == "builtin"
            and row.get("mode") == "rule"
            and (row.get("spec") or {}).get("executor") == "tool_success"
            and row.get("uuid")
        ):
            return row["uuid"]
    raise OnboardingDemoError("Sample rule is unavailable")


def _import_sample(agent, upstream, payload):
    normalized = normalize_langfuse_otlp_payload(payload, None, agent=agent)
    token, _ = build_ingest_token(
        space_uuid=agent.space.uuid,
        org_id=current_ingest_org_id(),
        subject_id=require_agent_subject(agent),
        key_id="onboarding-demo",
        agent_uuid=agent.uuid,
        agent_product=agent.agent_product,
    )
    response = forward_otlp_traces(
        upstream, normalized, token, content_type="application/x-protobuf"
    )
    if not 200 <= response.status_code < 300:
        raise OnboardingDemoError("Collector rejected the sample Trace export")
    receipt = ExportTraceServiceResponse.FromString(response.content)
    if receipt.partial_success.rejected_spans:
        raise OnboardingDemoError("Collector did not accept every sample span")


def _needs_import(agent, user, state, trace_id, span_count):
    accepted_at = state["accepted_at"]
    if accepted_at is None:
        return True
    if timezone.now().timestamp() - accepted_at < _INGEST_SETTLING_SECONDS:
        # The gateway can acknowledge before storage has flushed the batch.
        return False
    anchor_ms = int(state["anchor_ns"]) // 1_000_000
    detail = get_default_trace_query_service().get_trace_detail(
        space=agent.space,
        user=user,
        trace_id=trace_id,
        query_params={
            "agent_product": agent.agent_product,
            "agent_uuid": agent.uuid,
            "from": anchor_ms - 86_400_000,
            "to": anchor_ms + 1,
        },
        source_system_override="signoz",
    )
    meta = detail.get("meta") or {}
    return not (
        meta.get("complete") is True and meta.get("expectedSpanCount") == span_count
    )


@transaction.atomic
def _prepare_demo(user, space_id, agent_id, sample, digest, upstream):
    agent = (
        Agent.objects.select_for_update()
        .filter(pk=agent_id, gmt_deleted__isnull=True)
        .first()
    )
    authorize_demo(user, space_id)
    if agent is None or agent.space.uuid != space_id:
        raise OnboardingDemoError("Sample Agent is unavailable")
    state = agent.metadata[DEMO_METADATA_KEY]
    if state["sample_digest"] != digest:
        raise OnboardingDemoError("Sample version changed; retry the current version")
    receipt, traces_to_check = rebase_demo_sample(sample, agent.uuid, state)
    diagnoser_uuid = _prepare_rule(agent, space_id)
    changed = agent.status != 1
    # Trace routing recognizes pending Agents; a disabled example may be
    # restored after the user explicitly confirms this action again.
    if agent.status == 0:
        agent.status = 2
        agent.save(update_fields=["status", "gmt_modified"])
    if any(
        _needs_import(agent, user, state, trace_id, count)
        for trace_id, count in traces_to_check.items()
    ):
        _import_sample(agent, upstream, sample.SerializeToString())
        state["accepted_at"] = timezone.now().timestamp()
        changed = True
    if changed:
        agent.status = 1
        agent.save(update_fields=["status", "metadata", "gmt_modified"])
    return {
        "agent_uuid": agent.uuid,
        "agent_product": agent.agent_product,
        **receipt,
        "diagnoser_uuid": diagnoser_uuid,
    }


class OnboardingDemoView(APIView):
    @log_op(
        category=SpaceOperationLog.CATEGORY_AGENT_ACCESS,
        action="prepare_onboarding_demo",
    )
    def post(self, request, space_id):
        if (
            not isinstance(request.data, dict)
            or set(request.data) - {"consent", "language"}
            or request.data.get("consent") is not True
        ):
            return error_response("DEMO_CONSENT_REQUIRED", 1002)
        language = request.data.get("language", "en-US")
        if not isinstance(language, str) or language not in DEMO_AGENT_NAMES:
            return error_response("DEMO_LANGUAGE_UNSUPPORTED", 1002)

        authorize_demo(request.user, space_id)
        try:
            upstream = _upstream_endpoint()
            if upstream is None:
                raise OnboardingDemoError("Collector is not configured")
            sample, digest = load_demo_sample()
            agent = reserve_demo_agent(
                request.user, space_id, digest, language=language
            )
            result = _prepare_demo(
                request.user, space_id, agent.pk, sample, digest, upstream
            )
        except (
            DecodeError,
            IngestTokenError,
            OnboardingDemoError,
            QuickRunError,
            TraceQueryServiceError,
            requests.RequestException,
        ) as exc:
            logger.warning("Onboarding sample setup failed (%s)", type(exc).__name__)
            if isinstance(exc, TraceQueryServiceError) and exc.code == 3037:
                return error_response("DEMO_BUSY", 3037, status=429)
            return error_response("DEMO_UNAVAILABLE", 5001, status=503)

        request.op_log = {
            "target_display": agent.name,
            "detail": "Prepared the observability sample Agent",
        }
        return success_response(result)

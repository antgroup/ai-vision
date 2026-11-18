"""Reserve a sample Agent and rebase a bundled SDK export for replay."""

from collections import Counter
from hashlib import sha256
from pathlib import Path
from re import sub
from uuid import NAMESPACE_URL, uuid5

from django.db import transaction
from django.utils import timezone
from google.protobuf.message import DecodeError
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import (
    ExportTraceServiceRequest,
)
from opentelemetry.proto.trace.v1.trace_pb2 import Status

from apps.core.models import Agent, AgentTypeConfig, ObservationSubjectBinding, Space
from apps.core.permissions.constants import (
    AGENT_INGEST_TOKEN_MINT,
    AGENT_MANAGE,
    DIAGNOSIS_EXECUTE,
)
from apps.core.permissions.space import (
    SpaceAuthorizationError,
    require_space_permission,
)
from apps.core.services.observation_subject_service import (
    ObservationSubjectError,
    require_agent_subject,
    require_default_subject,
)

DEMO_METADATA_KEY = "onboarding_demo"
DEMO_AGENT_NAMES = {
    "zh-CN": "示例 Agent",
    "en-US": "Example Agent",
    "ja-JP": "サンプル Agent",
}
_SAMPLE = Path(__file__).resolve().parent.parent / "fixtures" / "onboarding_demo.otlp"
_SECOND_NS = 1_000_000_000
_SCENARIO_ATTRIBUTE = "aivision.demo.scenario"
_SCENARIOS = {"session", "mcp", "skill", "decision", "memory"}
_SESSION_ATTRIBUTES = ("gen_ai.conversation.id", "langfuse.session.id", "session.id")
# Keep the sample's roughly six-hour history inside the 24-hour demo dashboard.
_MAX_SAMPLE_AGE_NS = 16 * 60 * 60 * _SECOND_NS


class OnboardingDemoError(Exception):
    """The demo could not be prepared; the same scope can safely retry."""


def authorize_demo(user, space_id, *, lock=False):
    ctx = require_space_permission(
        user=user, space_id=space_id, permission=AGENT_MANAGE, lock=lock
    )
    if not {AGENT_INGEST_TOKEN_MINT, DIAGNOSIS_EXECUTE} <= ctx.permissions:
        raise SpaceAuthorizationError("SPACE_PERMISSION_DENIED", status=403)
    return ctx


def load_demo_sample():
    try:
        raw = _SAMPLE.read_bytes()
        request = ExportTraceServiceRequest.FromString(raw)
    except (OSError, DecodeError) as exc:
        raise OnboardingDemoError("Sample Trace export is unavailable") from exc
    return request, sha256(raw).hexdigest()


def _demo_binding(space, user):
    subject_id = require_default_subject(space=space, user=user)
    binding = ObservationSubjectBinding.objects.filter(
        user=user,
        space=space,
        source_system="collector",
        subject_id=subject_id,
        status=1,
        gmt_deleted__isnull=True,
    ).first()
    if binding is None:
        raise ObservationSubjectError()
    return binding


def demo_agent_uuid(space_uuid):
    return str(uuid5(NAMESPACE_URL, f"urn:ai-vision:onboarding-demo:{space_uuid}"))


def reserved_demo_agent_uuid(space):
    # A deleted Agent's retained telemetry is still sample data.
    agent_uuid = demo_agent_uuid(space.uuid)
    metadata = (
        Agent.objects.filter(uuid=agent_uuid, space=space)
        .values_list("metadata", flat=True)
        .first()
    )
    marker = metadata.get(DEMO_METADATA_KEY) if isinstance(metadata, dict) else None
    digest = marker.get("sample_digest") if isinstance(marker, dict) else None
    return agent_uuid if isinstance(digest, str) and digest else None


@transaction.atomic
def reserve_demo_agent(user, space_id, sample_digest, *, language="en-US"):
    # Reserve the identity and immutable replay anchor before calling downstream
    # services. A failed or ambiguous collector response can retry the same batch.
    ctx = authorize_demo(user, space_id)
    space = Space.objects.select_for_update().get(pk=ctx.space.pk)
    authorize_demo(user, space_id, lock=True)
    agent_type = (
        AgentTypeConfig.objects.active_shared_runtime()
        .filter(agent_product="langfuse", runtime_type="langfuse-sdk")
        .first()
    )
    if agent_type is None:
        raise OnboardingDemoError("Langfuse runtime is unavailable")

    agent_uuid = demo_agent_uuid(space.uuid)
    agent = (
        Agent.objects.select_for_update().filter(uuid=agent_uuid, space=space).first()
    )
    now_ns = int(timezone.now().timestamp() * _SECOND_NS)
    # The SDK export is anchored in the recent dashboard window. Periodically
    # renew old samples while rapid retries and concurrent confirms reuse them.
    state = {
        "sample_digest": sample_digest,
        "anchor_ns": str(now_ns - 2 * _SECOND_NS),
        "accepted_at": None,
    }
    if agent is None:
        return Agent.objects.create(
            uuid=agent_uuid,
            space=space,
            agent_type=agent_type,
            agent_product=agent_type.agent_product,
            name=DEMO_AGENT_NAMES[language],
            metadata={"framework": "LangChain", DEMO_METADATA_KEY: state},
            observation_subject_binding=_demo_binding(space, user),
            status=2,
        )

    if agent.gmt_deleted is not None or agent.agent_type_id != agent_type.pk:
        raise OnboardingDemoError("Sample Agent is unavailable")
    previous = agent.metadata[DEMO_METADATA_KEY]
    previous_binding = agent.observation_subject_binding_id
    try:
        require_agent_subject(agent)
    except ObservationSubjectError:
        agent.observation_subject_binding = _demo_binding(space, user)
    if (
        previous["sample_digest"] != sample_digest
        or now_ns - int(previous["anchor_ns"]) >= _MAX_SAMPLE_AGE_NS
        or agent.observation_subject_binding_id != previous_binding
    ):
        agent.metadata = {**agent.metadata, DEMO_METADATA_KEY: state}
        agent.save(
            update_fields=["metadata", "observation_subject_binding", "gmt_modified"]
        )
    return agent


def _has_failed_tool(span):
    return span.status.code == Status.STATUS_CODE_ERROR and any(
        (
            attribute.key == "langfuse.observation.type"
            and attribute.value.string_value.lower() == "tool"
        )
        or (
            attribute.key == "gen_ai.operation.name"
            and attribute.value.string_value == "execute_tool"
        )
        for attribute in span.attributes
    )


def _string_attribute(span, name):
    return next(
        (attr.value.string_value for attr in span.attributes if attr.key == name),
        "",
    )


def _session_id(span):
    return next(
        (
            value
            for name in _SESSION_ATTRIBUTES
            if (value := _string_attribute(span, name))
        ),
        "",
    )


def _scenario_anchors(spans, failing_span):
    anchors = {}
    for span in spans:
        scenario = _string_attribute(span, _SCENARIO_ATTRIBUTE)
        if not scenario:
            continue
        if scenario not in _SCENARIOS or scenario in anchors:
            raise OnboardingDemoError("Sample scenario anchors are invalid")
        anchors[scenario] = span
    if set(anchors) != _SCENARIOS or anchors["mcp"] != failing_span:
        raise OnboardingDemoError("Sample scenario anchors are incomplete")
    start = anchors["session"]
    session = _session_id(start)
    roots = {
        span.trace_id
        for span in spans
        if not span.parent_span_id and _session_id(span) == session
    }
    if not session or start.parent_span_id or len(roots) < 3:
        raise OnboardingDemoError("Sample session does not contain three turns")
    return anchors, roots


def rebase_demo_sample(request, agent_uuid, state):
    """Rebase the bundled SDK export and resolve anchors in the rebased scope."""
    spans = [
        span
        for resource in request.resource_spans
        for scope in resource.scope_spans
        for span in scope.spans
    ]
    failing_span = next((span for span in spans if _has_failed_tool(span)), None)
    trace_counts = Counter(span.trace_id for span in spans)
    error_traces = {
        span.trace_id for span in spans if span.status.code == Status.STATUS_CODE_ERROR
    }
    comparison = next(
        (trace_id for trace_id in trace_counts if trace_id not in error_traces), None
    )
    if failing_span is None or comparison is None:
        raise OnboardingDemoError("Sample Trace export is incomplete")

    anchors, session_traces = _scenario_anchors(spans, failing_span)
    target = failing_span.trace_id
    anchor = int(state["anchor_ns"])
    namespace = f"{agent_uuid}:{state['sample_digest']}:{anchor}:".encode()

    def rebase_id(original):
        if not original:
            return b""
        return sha256(namespace + original).digest()[: len(original)]

    def rebase_session_id(original):
        label = sub(r"[^a-z0-9-]+", "-", original.lower()).strip("-")[:48]
        suffix = sha256(namespace + original.encode()).hexdigest()[:12]
        return f"{label or 'demo-session'}-{suffix}"

    traces_to_check = {
        trace_id: trace_counts[trace_id] for trace_id in (target, comparison)
    }
    scenarios = {}
    for name, span in anchors.items():
        traces_to_check[span.trace_id] = trace_counts[span.trace_id]
        entry = {
            "trace_id": rebase_id(span.trace_id).hex(),
            "span_count": trace_counts[span.trace_id],
        }
        if name == "session":
            entry["session_id"] = rebase_session_id(_session_id(span))
            entry["turns"] = [
                {
                    "trace_id": rebase_id(trace_id).hex(),
                    "span_count": trace_counts[trace_id],
                }
                for trace_id in trace_counts
                if trace_id in session_traces
            ]
            for trace_id in session_traces:
                traces_to_check[trace_id] = trace_counts[trace_id]
        else:
            entry["span_id"] = rebase_id(span.span_id).hex()
        scenarios[name] = entry

    offset = anchor - max(span.end_time_unix_nano for span in spans)
    for resource in request.resource_spans:
        resource.resource.attributes.add(
            key="aivision.onboarding.demo", value={"bool_value": True}
        )
    for span in spans:
        span.trace_id = rebase_id(span.trace_id)
        span.span_id = rebase_id(span.span_id)
        span.parent_span_id = rebase_id(span.parent_span_id)
        session = _session_id(span)
        if session:
            for attr in span.attributes:
                if attr.key in _SESSION_ATTRIBUTES:
                    attr.value.string_value = rebase_session_id(session)
        span.start_time_unix_nano += offset
        span.end_time_unix_nano += offset
        for event in span.events:
            event.time_unix_nano += offset
        for link in span.links:
            link.trace_id = rebase_id(link.trace_id)
            link.span_id = rebase_id(link.span_id)

    return {
        "trace_id": rebase_id(target).hex(),
        "span_count": trace_counts[target],
        "comparison_trace_id": rebase_id(comparison).hex(),
        "comparison_span_count": trace_counts[comparison],
        "scenarios": scenarios,
    }, {rebase_id(trace_id).hex(): count for trace_id, count in traces_to_check.items()}

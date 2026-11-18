"""Native Langfuse tracing read API; shares the OTLP AgentUUID:SpaceKey identity."""

import hashlib
import logging
from urllib.parse import urlencode

import requests
from apps.core.services.model_pricing import get_space_model_prices
from django.http import HttpResponseRedirect
from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import AuthenticationFailed, PermissionDenied
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.authentication import (
    LocalSessionAuthentication,
    UnifiedExternalAuthentication,
)
from apps.core.models import Agent, SpaceMember, SpaceObservationProject
from apps.core.services.agent_installation import active_ingest_agents
from apps.core.services.key_cache import key_cache
from apps.core.throttling import UnifiedExternalRateThrottle
from apps.core.trace_query.langfuse.native import NativeLangfuseService
from apps.core.trace_query.semantics.observations import iso_timestamp, timestamp_ns
from apps.core.trace_query.types import TraceQueryServiceError
from apps.core.views.langfuse_ingest import _basic_credentials
from apps.core.views.observation import (
    PublicObservationDetailView,
    PublicObservationListView,
    PublicProjectsView,
    PublicSessionDetailView,
    PublicSessionListView,
    PublicTraceDetailView,
    PublicTraceListView,
)

logger = logging.getLogger(__name__)


class NativeLangfuseAuthentication(BaseAuthentication):
    def authenticate_header(self, request):
        return 'Basic realm="AI Vision tracing"'

    def authenticate(self, request):
        public_key, secret = _basic_credentials(request)
        if public_key and secret and secret.startswith("sk-aiv-"):
            space_key = key_cache.get_space_key(
                hashlib.sha256(secret.encode()).hexdigest()
            )
            if space_key is None:
                raise AuthenticationFailed("Invalid or disabled tracing credentials")
            agent = (
                active_ingest_agents(space_key.space).filter(uuid=public_key).first()
            )
            if agent is None:
                raise AuthenticationFailed("Invalid tracing project")
            key_cache.mark_space_key_used(space_key.id)
            request.native_space, request.native_agent = space_key.space, agent
            request.auth_type = "space_key"
            return None, space_key
        # Legacy credentials keep their explicitly provisioned upstream project.
        # Native SDK credentials above always select the authenticated Agent.
        result = UnifiedExternalAuthentication().authenticate(request)
        if result is None:
            raise AuthenticationFailed("Tracing credentials are required")
        _, credential = result
        space = (
            credential.space
            if request.auth_type == "space_key"
            else credential.space_observation_project.space
        )
        project = getattr(request, "observation_project", None)
        if project is None:
            project = (
                SpaceObservationProject.objects.select_related("endpoint_config")
                .filter(space=space, gmt_deleted__isnull=True)
                .first()
            )
        if (
            project is not None
            and project.upstream_project_id
            and project.endpoint_config.backend_type == "langfuse"
        ):
            request.observation_project = project
            request.observation_endpoint = project.endpoint_config
            request.external_langfuse_project = project
            return result
        request.native_space = space
        requested_agent = request.query_params.get("agent_uuid")
        agents = active_ingest_agents(space)
        if requested_agent:
            agent = agents.filter(uuid=requested_agent).first()
            if agent is None:
                raise PermissionDenied("Agent does not belong to the authorized Space")
        else:
            candidates = list(agents[:2])
            agent = candidates[0] if len(candidates) == 1 else None
            if agent is None and space.type != 2:
                raise ValueError(
                    "agent_uuid is required for personal Space reads unless "
                    "exactly one active Agent is available"
                )
        request.native_agent = agent
        return result


class NativePublicAPIView(APIView):
    authentication_classes = [NativeLangfuseAuthentication]
    permission_classes = [AllowAny]
    throttle_classes = [UnifiedExternalRateThrottle]
    throttle_scope = "external_api"
    external_view_class = None

    def external_response(self, request, **kwargs):
        if getattr(request, "external_langfuse_project", None) is not None:
            return self.external_view_class().get(request, **kwargs)
        return None

    def service(self, request):
        agent = request.native_agent
        user = agent.observation_subject_binding.user if agent is not None else None
        return NativeLangfuseService(
            space=request.native_space,
            agent=agent,
            user=user,
            prices=get_space_model_prices(request.native_space),
        )

    def handle_exception(self, exc):
        if isinstance(exc, ValueError):
            return Response(
                {"message": str(exc), "error": "Invalid request"}, status=400
            )
        if isinstance(exc, TraceQueryServiceError):
            status = {3019: 404, 3027: 400, 3036: 413, 3037: 429}.get(exc.code, 503)
            return Response(
                {"message": exc.message, "error": "Trace query failed"}, status=status,
                headers={'Retry-After': '1'} if exc.code == 3037 else None,
            )
        if isinstance(exc, requests.RequestException):
            logger.warning("Native Langfuse query storage unavailable", exc_info=True)
            return Response(
                {
                    "message": "Telemetry storage unavailable",
                    "error": "Service unavailable",
                },
                status=503,
            )
        return super().handle_exception(exc)


class NativePublicProjectsView(NativePublicAPIView):
    external_view_class = PublicProjectsView

    def get(self, request):
        external = self.external_response(request)
        if external is not None:
            return external
        space, agent = request.native_space, request.native_agent
        return Response(
            {
                "data": [
                    {
                        "id": str(agent.uuid if agent else space.uuid),
                        "name": agent.name if agent else space.name,
                        "organization": {"id": str(space.uuid), "name": space.name},
                        "metadata": {},
                    }
                ]
            }
        )


class NativePublicTraceListView(NativePublicAPIView):
    external_view_class = PublicTraceListView

    def get(self, request):
        external = self.external_response(request)
        if external is not None:
            return external
        return Response(self.service(request).traces(request.query_params))


class NativePublicTraceDetailView(NativePublicAPIView):
    external_view_class = PublicTraceDetailView

    def get(self, request, trace_id):
        external = self.external_response(request, trace_id=trace_id)
        if external is not None:
            return external
        return Response(self.service(request).traces(request.query_params, trace_id))


class NativePublicObservationListView(NativePublicAPIView):
    external_view_class = PublicObservationListView

    def get(self, request):
        external = self.external_response(request)
        if external is not None:
            return external
        return Response(self.service(request).observations(request.query_params))


class NativePublicObservationDetailView(NativePublicAPIView):
    external_view_class = PublicObservationDetailView

    def get(self, request, observation_id):
        external = self.external_response(request, observation_id=observation_id)
        if external is not None:
            return external
        return Response(
            self.service(request).observations(request.query_params, observation_id)
        )


class NativePublicSessionListView(NativePublicAPIView):
    external_view_class = PublicSessionListView

    def get(self, request):
        external = self.external_response(request)
        if external is not None:
            return external
        return Response(self.service(request).sessions(request.query_params))


class NativePublicSessionDetailView(NativePublicAPIView):
    external_view_class = PublicSessionDetailView

    def get(self, request, session_id):
        external = self.external_response(request, session_id=session_id)
        if external is not None:
            return external
        return Response(
            self.service(request).sessions(request.query_params, session_id)
        )


class NativeTraceURLView(NativePublicAPIView):
    """SDK-generated links enter the existing authenticated trace UI."""

    authentication_classes = [LocalSessionAuthentication]
    permission_classes = [AllowAny]
    throttle_classes = []

    def get(self, request, project_id, trace_id):
        if not request.user or not request.user.is_authenticated:
            return HttpResponseRedirect(
                "/login?" + urlencode({"redirect": request.get_full_path()})
            )
        agent = (
            Agent.objects.select_related("space")
            .filter(
                uuid=project_id,
                status__in=(1, 2),
                gmt_deleted__isnull=True,
                space__status=1,
                space__gmt_deleted__isnull=True,
                observation_subject_binding__status=1,
                observation_subject_binding__gmt_deleted__isnull=True,
            )
            .first()
        )
        if agent is None:
            return Response({"message": "Trace project not found"}, status=404)
        space = agent.space
        member = SpaceMember.objects.filter(
            space=space, user=request.user, gmt_deleted__isnull=True
        ).exists()
        if space.creator_id != request.user.id and not member:
            return Response({"message": "Trace project not found"}, status=404)
        request.native_space, request.native_agent = space, agent
        trace = self.service(request).traces({}, trace_id)
        start = timestamp_ns(trace["timestamp"])
        end = max(
            (
                timestamp_ns(item.get("endTime")) or timestamp_ns(item["startTime"])
                for item in trace["observations"]
            ),
            default=start,
        )
        return HttpResponseRedirect(
            "/trace?"
            + urlencode(
                {
                    "spaceId": space.uuid,
                    "agent": agent.agent_product,
                    "agent_uuid": agent.uuid,
                    "traceId": trace_id,
                    "trace_from": iso_timestamp(start - 1000000000),
                    "trace_to": iso_timestamp(end + 1000000000),
                }
            )
        )

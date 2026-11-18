"""Authorization proxy for SigNoz-owned trace media. No telemetry ORM access."""
import hashlib
import re

import requests
from django.http import HttpResponse
from rest_framework.parsers import MultiPartParser
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.permissions import SpaceAuthorizationError, require_space_permission
from apps.core.permissions.constants import TELEMETRY_TRACE_READ_SENSITIVE
from apps.core.services.agent_installation import active_ingest_agents
from apps.core.services.key_cache import key_cache
from apps.core.trace_query.types import BaseTraceQuery, TraceQueryServiceError
from apps.core.trace_query.signoz.provider import SignozTraceProvider
from apps.core.views.langfuse_ingest import _basic_credentials


def media_client(space, user, agent, params=None):
    query = BaseTraceQuery(space, user, {**(params or {}), 'agent_product': agent.agent_product, 'agent_uuid': str(agent.uuid)})
    client, config = SignozTraceProvider._context(query)
    return client, SignozTraceProvider._params(query, config)


def upstream_response(response):
    try:
        result = HttpResponse(response.content, status=response.status_code, content_type=response.headers.get('Content-Type', 'application/octet-stream'))
        for header in ('Cache-Control', 'X-Content-Type-Options', 'Content-Security-Policy', 'Content-Disposition'):
            if header in response.headers:
                result[header] = response.headers[header]
        return result
    finally:
        response.close()


class TraceAttachmentLimitsView(APIView):
    def get(self, request):
        # Protocol ceiling, also enforced by SigNoz. No content is queried.
        return Response({'code': 0, 'message': 'success', 'data': {'maxBytes': 16 * 1024 * 1024}})


class TraceAttachmentContentView(APIView):
    def get(self, request, attachment_id):
        space_id = request.query_params.get('space_id')
        try:
            ctx = require_space_permission(user=request.user, space_id=space_id, permission=TELEMETRY_TRACE_READ_SENSITIVE)
        except SpaceAuthorizationError as exc:
            return Response(status=exc.status)
        agent = active_ingest_agents(ctx.space).filter(uuid=request.query_params.get('agent_uuid')).first()
        if agent is None or (ctx.space.type != 2 and agent.observation_subject_binding.user_id != request.user.pk):
            return Response(status=404)
        if not re.fullmatch(r'[0-9a-f]{64}', str(attachment_id)):
            return Response({'error': 'Legacy attachment requires data-plane migration'}, status=410)
        try:
            client, params = media_client(ctx.space, request.user, agent, dict(request.query_params.items()))
            # SigNoz verifies current span/reference association before bytes.
            with requests.get(f'{client.endpoint}/api/v1/ai-vision/media/{attachment_id}/content', params=params, headers=client.headers, timeout=60, stream=True) as upstream:
                if int(upstream.headers.get('Content-Length', 0)) > 16 * 1024 * 1024:
                    return Response(status=413)
                chunks, size = [], 0
                for chunk in upstream.iter_content(65536):
                    size += len(chunk)
                    if size > 16 * 1024 * 1024:
                        return Response(status=413)
                    chunks.append(chunk)
                result = HttpResponse(b''.join(chunks), status=upstream.status_code, content_type=upstream.headers.get('Content-Type', 'application/octet-stream'))
                for header in ('Cache-Control', 'X-Content-Type-Options', 'Content-Security-Policy', 'Content-Disposition'):
                    if header in upstream.headers:
                        result[header] = upstream.headers[header]
                return result
        except (requests.RequestException, TraceQueryServiceError, ValueError):
            return Response({'error': 'media data plane unavailable'}, status=503)


class TraceAttachmentUploadView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]
    parser_classes = [MultiPartParser]

    def credentials(self, request):
        agent_uuid, secret = _basic_credentials(request)
        key = key_cache.get_space_key(hashlib.sha256(secret.encode()).hexdigest()) if secret else None
        agent = active_ingest_agents(key.space).filter(uuid=agent_uuid).first() if key else None
        return key, agent

    def post(self, request):
        key, agent = self.credentials(request)
        if agent is None:
            return Response(status=401)
        length = request.META.get('CONTENT_LENGTH', '')
        if not length.isdigit() or int(length) > 16 * 1024 * 1024 + 65536:
            return Response(status=413)
        file = request.FILES.get('file')
        if not file:
            return Response(status=400)
        if file.size > 16 * 1024 * 1024:
            return Response({'error': 'attachment exceeds 16 MiB'}, status=413)
        try:
            client, params = media_client(key.space, agent.observation_subject_binding.user, agent)
            # Machine writes are scoped using the authenticated Agent, never a
            # browser-supplied space/subject or file path.
            params['agent_uuid'] = str(agent.uuid)
            upstream = requests.post(f'{client.endpoint}/api/v1/ai-vision/media', params=params, headers=client.headers,
                data={name: request.data.get(name, '') for name in ('trace_id', 'span_id', 'direction')},
                files={'file': (file.name, file, file.content_type)}, timeout=60)
            key_cache.mark_space_key_used(key.id)
            if upstream.ok:
                try:
                    payload = upstream.json()
                    reference = payload.get('data') if payload.get('status') == 'success' else payload
                    return Response(reference, status=upstream.status_code)
                finally:
                    upstream.close()
            return upstream_response(upstream)
        except (requests.RequestException, TraceQueryServiceError):
            return Response({'error': 'media data plane unavailable'}, status=503)

    def delete(self, request):
        key, agent = self.credentials(request)
        if agent is None:
            return Response(status=401)
        identifier = request.query_params.get('id', '')
        if not re.fullmatch(r'[0-9a-f]{64}', identifier):
            return Response(status=400)
        try:
            client, params = media_client(key.space, agent.observation_subject_binding.user, agent)
            params['agent_uuid'] = str(agent.uuid)
            return upstream_response(requests.delete(f'{client.endpoint}/api/v1/ai-vision/media/{identifier}', params=params, headers=client.headers, timeout=30))
        except (requests.RequestException, TraceQueryServiceError):
            return Response(status=503)

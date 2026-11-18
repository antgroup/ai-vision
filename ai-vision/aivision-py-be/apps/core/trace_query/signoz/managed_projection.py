"""Discover live native routes and continuously maintain canonical summaries.

No database credentials or raw SQL enter this process. Multiple backend replicas
may work on a scope safely: the data plane owns enrollment locks and revision
CAS publication. Lost in-memory cursors only repeat bounded idempotent work.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
import logging

import requests
from apps.core.services.model_pricing import get_space_model_prices
from django.db import close_old_connections

from ..types import BaseTraceQuery, TraceQueryServiceError
from .provider import SignozTraceProvider
from .read_model import PATH, projection_context
from .read_model_projection import ProjectionDeferred, project_batch

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ProjectionTarget:
    key: str
    space_id: str
    client: object
    prices: list = field(default_factory=list)


def discover_targets():
    from apps.core.models import Agent

    agents = (Agent.objects.select_related('space', 'agent_type')
              .filter(status__in=(1, 2), gmt_deleted__isnull=True, space__status=1, space__gmt_deleted__isnull=True,
                      agent_type__status=1, agent_type__gmt_deleted__isnull=True,
                      agent_type__datasources__source_system='signoz',
                      agent_type__datasources__datasource_type='traces',
                      agent_type__datasources__enabled=1,
                      agent_type__datasources__gmt_deleted__isnull=True)
              .order_by('space_id', 'id').distinct())
    targets = {}
    for agent in agents.iterator(chunk_size=200):
        try:
            client, _ = SignozTraceProvider._context(BaseTraceQuery(agent.space, None, {
                'agent_product': agent.agent_type.agent_product, 'agent_uuid': str(agent.uuid)}))
        except TraceQueryServiceError as exc:
            # A disabled/reconfigured route must not prevent other spaces from
            # making progress. Never log credential-bearing config or errors.
            logger.warning('Projection route unavailable: agent=%s code=%s', agent.uuid, exc.code)
            continue
        prices = get_space_model_prices(agent.space)
        identity = [str(agent.space.uuid), client.endpoint, client.api_key, projection_context(prices)]
        key = hashlib.sha256(json.dumps(identity, separators=(',', ':')).encode()).hexdigest()
        targets.setdefault(key, ProjectionTarget(key, str(agent.space.uuid), client, prices))
    return list(targets.values())


class ManagedProjector:
    def __init__(self, *, limit=500):
        self.limit = limit
        self.cursors = {}

    def resume_maintenance(self, internal_key):
        """Explicit operator recovery only; ordinary iterations never resume."""
        if not internal_key:
            raise TraceQueryServiceError('SIGNOZ_INGEST_INTERNAL_KEY is required for explicit recovery', 3011)
        seen = set()
        resumed = 0
        for target in discover_targets():
            identity = (target.client.endpoint, target.client.api_key)
            if identity in seen:
                continue
            seen.add(identity)
            result = target.client.post(PATH + '/maintenance/resume', payload={'space_id': target.space_id},
                                        headers={'X-Signoz-Ingest-Key': internal_key})
            if result.get('supported') is not True or result.get('resumed') is not True:
                raise TraceQueryServiceError('Projection maintenance could not resume', 3035)
            resumed += 1
        return resumed

    def iteration(self):
        close_old_connections()
        targets = discover_targets()
        active = {target.key for target in targets}
        self.cursors = {key: cursor for key, cursor in self.cursors.items() if key in active}
        results = []
        for target in targets:
            try:
                result = project_batch(target.client, space_id=target.space_id,
                                       prices=target.prices, limit=self.limit,
                                       after_trace_id=self.cursors.get(target.key, ''), backfill=True)
                self.cursors[target.key] = result['afterTraceId']
                results.append({'space_id': target.space_id, 'state': 'ready', **result})
            except (ProjectionDeferred, TraceQueryServiceError, requests.RequestException, ValueError) as exc:
                results.append({'space_id': target.space_id, 'state': 'retry', 'error': type(exc).__name__})
                logger.warning('Projection maintenance will retry: space=%s error=%s',
                               target.space_id, type(exc).__name__)
        return results

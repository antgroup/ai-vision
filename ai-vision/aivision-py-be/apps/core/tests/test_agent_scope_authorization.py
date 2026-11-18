from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.core.event_query.routing import (
    _select_event_route_record,
    get_event_datasource,
)
from apps.core.tests.helpers import create_test_agent

from apps.core.models import (
    Agent,
    AgentTypeConfig,
    DataSource,
    ObservationSubjectBinding,
    Space,
    SpaceMember,
    User,
)
from apps.core.services.agent_routing import (
    list_routable_space_agents,
    resolve_routable_space_agent,
    resolve_space_agent_for_query,
)
from apps.core.trace_query.routing import _select_route_record, get_trace_datasource
from apps.core.trace_query.types import TraceProviderNotConfiguredError
from apps.core.views.v2_query import _load_space_agent_query_route


class AgentScopeAuthorizationTests(TestCase):
    def setUp(self):
        self.user = User.objects.create(
            name="Scope Owner",
            email="scope-owner@example.com",
            normalized_email="scope-owner@example.com",
            password="!",
        )
        self.space = Space.objects.create(
            name="Scoped Space",
            type=2,
            scope=1,
            creator=self.user,
            status=1,
        )
        self.other_space = Space.objects.create(
            name="Other Space",
            type=2,
            scope=1,
            creator=self.user,
            status=1,
        )
        self.personal_space = Space.objects.create(
            name="Personal Scope",
            type=1,
            scope=1,
            creator=self.user,
            status=1,
        )

    def _create_agent_type(
        self,
        *,
        agent_name: str,
        agent_product: str,
        status: int = 1,
        deleted: bool = False,
    ) -> AgentTypeConfig:
        return AgentTypeConfig.objects.create(
            agent_name=agent_name,
            agent_product=agent_product,
            status=status,
            gmt_deleted=timezone.now() if deleted else None,
        )

    def _bind_agent(
        self,
        *,
        space: Space | None = None,
        agent_type: AgentTypeConfig | None = None,
        agent_product: str | None = None,
        status: int = 1,
        deleted: bool = False,
    ) -> Agent:
        agent_type = agent_type or self._create_agent_type(
            agent_name=f"{agent_product}-type",
            agent_product=agent_product or "ScopedProduct",
        )
        target_space = space or self.space
        subject_binding, _ = ObservationSubjectBinding.objects.get_or_create(
            space=target_space,
            source_system="collector",
            subject_id=f"subject-{target_space.uuid}",
            defaults={
                "user": self.user,
                "binding_source": "test",
                "created_by": self.user,
            },
        )
        return create_test_agent(
            space=target_space,
            agent_type=agent_type,
            agent_product=agent_product or agent_type.agent_product,
            observation_subject_binding=subject_binding,
            name=agent_type.agent_product,
            status=status,
            gmt_deleted=timezone.now() if deleted else None,
        )

    def _create_datasource(
        self,
        *,
        agent_type: AgentTypeConfig,
        datasource_type: str = "traces",
        source_system: str = "signoz",
        enabled: int = 1,
        deleted: bool = False,
    ) -> DataSource:
        return DataSource.objects.create(
            agent_type=agent_type,
            datasource_type=datasource_type,
            source_system=source_system,
            enabled=enabled,
            is_default=1,
            config={
                "endpoint": "https://stargazer.example.test",
                "access_id": "test-access-id",
                "access_token": "test-access-token",
                "table_name": "test_table",
                "time_field": "period",
                "user_column": "user_id",
                "agent_product_filter": agent_type.agent_product,
            },
            gmt_deleted=timezone.now() if deleted else None,
        )

    def _assert_route_not_found(self, func, *args, **kwargs):
        with self.assertRaises(TraceProviderNotConfiguredError) as ctx:
            func(*args, **kwargs)
        self.assertEqual(ctx.exception.code, 3011)
        self.assertEqual(ctx.exception.message, "Agent query route not found")

    def test_get_trace_datasource_returns_datasource_for_active_space_agent(self):
        agent_type = self._create_agent_type(
            agent_name="active-agent",
            agent_product="ActiveProduct",
        )
        self._bind_agent(agent_type=agent_type)
        datasource = self._create_datasource(agent_type=agent_type)

        resolved = get_trace_datasource(self.space, agent_product="ActiveProduct")

        self.assertEqual(resolved, datasource)

    def test_get_trace_datasource_allows_pending_business_space_agent(self):
        agent_type = self._create_agent_type(
            agent_name="pending-business-agent",
            agent_product="PendingBusinessProduct",
        )
        self._bind_agent(agent_type=agent_type, status=2)
        datasource = self._create_datasource(agent_type=agent_type)

        resolved = get_trace_datasource(
            self.space,
            agent_product="PendingBusinessProduct",
        )

        self.assertEqual(resolved, datasource)

    def test_get_trace_datasource_allows_pending_personal_space_agent(self):
        agent_type = self._create_agent_type(
            agent_name="pending-personal-agent",
            agent_product="PendingPersonalProduct",
        )
        self._bind_agent(
            space=self.personal_space,
            agent_type=agent_type,
            status=2,
        )
        datasource = self._create_datasource(agent_type=agent_type)

        resolved = get_trace_datasource(
            self.personal_space,
            agent_product="PendingPersonalProduct",
        )

        self.assertEqual(resolved, datasource)

    def test_get_trace_datasource_denies_disabled_space_agent(self):
        agent_type = self._create_agent_type(
            agent_name="disabled-trace-agent",
            agent_product="DisabledTraceProduct",
        )
        self._bind_agent(agent_type=agent_type, status=0)
        self._create_datasource(agent_type=agent_type)

        self._assert_route_not_found(
            get_trace_datasource,
            self.space,
            agent_product="DisabledTraceProduct",
        )

    def test_get_trace_datasource_denies_unbound_global_datasource(self):
        agent_type = self._create_agent_type(
            agent_name="global-only-agent",
            agent_product="GlobalOnly",
        )
        self._create_datasource(agent_type=agent_type)

        self._assert_route_not_found(
            get_trace_datasource,
            self.space,
            agent_product="GlobalOnly",
        )

    def test_get_trace_datasource_denies_cross_space_agent(self):
        agent_type = self._create_agent_type(
            agent_name="cross-space-agent",
            agent_product="CrossSpace",
        )
        self._bind_agent(space=self.other_space, agent_type=agent_type)
        self._create_datasource(agent_type=agent_type)

        self._assert_route_not_found(
            get_trace_datasource,
            self.space,
            agent_product="CrossSpace",
        )

    def test_resolve_routable_space_agent_denies_pending_agent(self):
        agent_type = self._create_agent_type(
            agent_name="pending-agent",
            agent_product="PendingProduct",
        )
        self._bind_agent(agent_type=agent_type, status=2)

        self.assertIsNone(resolve_routable_space_agent(self.space, "PendingProduct"))

    def test_resolve_routable_space_agent_denies_disabled_agent(self):
        agent_type = self._create_agent_type(
            agent_name="disabled-agent",
            agent_product="DisabledProduct",
        )
        self._bind_agent(agent_type=agent_type, status=0)

        self.assertIsNone(resolve_routable_space_agent(self.space, "DisabledProduct"))

    def test_resolve_routable_space_agent_denies_deleted_agent(self):
        agent_type = self._create_agent_type(
            agent_name="deleted-agent",
            agent_product="DeletedProduct",
        )
        self._bind_agent(agent_type=agent_type, deleted=True)

        self.assertIsNone(resolve_routable_space_agent(self.space, "DeletedProduct"))

    def test_resolve_routable_space_agent_denies_disabled_agent_type(self):
        agent_type = self._create_agent_type(
            agent_name="disabled-type",
            agent_product="DisabledTypeProduct",
            status=0,
        )
        self._bind_agent(agent_type=agent_type)

        self.assertIsNone(
            resolve_routable_space_agent(self.space, "DisabledTypeProduct")
        )

    def test_resolve_routable_space_agent_denies_deleted_agent_type(self):
        agent_type = self._create_agent_type(
            agent_name="deleted-type",
            agent_product="DeletedTypeProduct",
            deleted=True,
        )
        self._bind_agent(agent_type=agent_type)

        self.assertIsNone(
            resolve_routable_space_agent(self.space, "DeletedTypeProduct")
        )

    def test_resolve_routable_space_agent_denies_missing_agent_type(self):
        subject_binding = ObservationSubjectBinding.objects.create(
            space=self.space,
            user=self.user,
            source_system="collector",
            subject_id="missing-type-subject",
            binding_source="test",
            created_by=self.user,
        )
        create_test_agent(
            space=self.space,
            agent_type=None,
            agent_product="MissingType",
            observation_subject_binding=subject_binding,
            name="MissingType",
            status=1,
        )

        self.assertIsNone(resolve_routable_space_agent(self.space, "MissingType"))

    def test_resolve_routable_space_agent_requires_exact_case_match(self):
        agent_type = self._create_agent_type(
            agent_name="case-agent",
            agent_product="CaseProduct",
        )
        self._bind_agent(agent_type=agent_type)

        self.assertIsNone(resolve_routable_space_agent(self.space, "caseproduct"))

    def test_resolve_routable_space_agent_allows_space_local_agent_name_alias(self):
        agent_type = self._create_agent_type(
            agent_name="alias-agent",
            agent_product="CanonicalProduct",
        )
        agent = self._bind_agent(agent_type=agent_type)

        self.assertEqual(
            resolve_routable_space_agent(self.space, "alias-agent"),
            agent,
        )

    def test_resolve_routable_space_agent_fails_closed_on_duplicate_matches(self):
        first_type = self._create_agent_type(
            agent_name="duplicate-agent-a",
            agent_product="DuplicateProduct",
        )
        second_type = self._create_agent_type(
            agent_name="duplicate-agent-b",
            agent_product="DuplicateProduct",
        )
        self._bind_agent(agent_type=first_type)
        self._bind_agent(agent_type=second_type)

        self.assertIsNone(resolve_routable_space_agent(self.space, "DuplicateProduct"))

    def test_get_trace_datasource_denies_when_datasource_disabled(self):
        agent_type = self._create_agent_type(
            agent_name="disabled-datasource-agent",
            agent_product="DisabledDatasourceProduct",
        )
        self._bind_agent(agent_type=agent_type)
        self._create_datasource(agent_type=agent_type, enabled=0)

        self._assert_route_not_found(
            get_trace_datasource,
            self.space,
            agent_product="DisabledDatasourceProduct",
        )

    def test_get_trace_datasource_denies_when_datasource_deleted(self):
        agent_type = self._create_agent_type(
            agent_name="deleted-datasource-agent",
            agent_product="DeletedDatasourceProduct",
        )
        self._bind_agent(agent_type=agent_type)
        self._create_datasource(agent_type=agent_type, deleted=True)

        self._assert_route_not_found(
            get_trace_datasource,
            self.space,
            agent_product="DeletedDatasourceProduct",
        )

    def test_trace_datasource_selector_rejects_same_source_ambiguity(self):
        records = [
            SimpleNamespace(is_default=0, source_system="signoz"),
            SimpleNamespace(is_default=0, source_system="signoz"),
        ]

        with self.assertRaises(TraceProviderNotConfiguredError):
            _select_route_record(records, error_message="ambiguous trace route")

    def test_event_datasource_selector_rejects_same_source_ambiguity(self):
        records = [
            SimpleNamespace(is_default=1, source_system="signoz"),
            SimpleNamespace(is_default=1, source_system="signoz"),
        ]

        with self.assertRaises(TraceProviderNotConfiguredError):
            _select_event_route_record(records, error_message="ambiguous event route")

    def test_trace_route_has_bounded_queries_without_global_fallback(self):
        agent_type = self._create_agent_type(
            agent_name="bounded-query-agent",
            agent_product="BoundedQueryProduct",
        )
        self._bind_agent(agent_type=agent_type)
        datasource = self._create_datasource(agent_type=agent_type)

        with self.assertNumQueries(2):
            resolved = get_trace_datasource(
                self.space,
                agent_product="BoundedQueryProduct",
            )

        self.assertEqual(resolved, datasource)

    def test_trace_route_rechecks_agent_before_datasource_selection(self):
        agent_type = self._create_agent_type(
            agent_name="race-agent",
            agent_product="RaceProduct",
        )
        agent = self._bind_agent(agent_type=agent_type)
        self._create_datasource(agent_type=agent_type)

        def disable_after_resolve(space, agent_product, **kwargs):
            resolved = resolve_space_agent_for_query(
                space,
                agent_product,
                **kwargs,
            )
            Agent.objects.filter(pk=agent.pk).update(status=0)
            return resolved

        with patch(
            "apps.core.trace_query.routing.resolve_space_agent_for_query",
            side_effect=disable_after_resolve,
        ):
            self._assert_route_not_found(
                get_trace_datasource,
                self.space,
                agent_product="RaceProduct",
            )

    def test_trace_route_is_uncached_and_observes_agent_disable(self):
        agent_type = self._create_agent_type(
            agent_name="uncached-agent",
            agent_product="UncachedProduct",
        )
        agent = self._bind_agent(agent_type=agent_type)
        self._create_datasource(agent_type=agent_type)

        self.assertIsNotNone(
            get_trace_datasource(self.space, agent_product="UncachedProduct")
        )
        Agent.objects.filter(pk=agent.pk).update(status=0)
        self._assert_route_not_found(
            get_trace_datasource,
            self.space,
            agent_product="UncachedProduct",
        )

    def test_v2_route_rechecks_agent_before_datasource_selection(self):
        agent_type = self._create_agent_type(
            agent_name="v2-race-agent",
            agent_product="V2RaceProduct",
        )
        agent = self._bind_agent(agent_type=agent_type)
        self._create_datasource(
            agent_type=agent_type,
            datasource_type="logs",
            source_system="signoz",
        )

        def disable_after_resolve(space, agent_product, **kwargs):
            resolved = list_routable_space_agents(space, agent_product, **kwargs)
            Agent.objects.filter(pk=agent.pk).update(status=0)
            return resolved

        with patch(
            "apps.core.views.v2_query.list_routable_space_agents",
            side_effect=disable_after_resolve,
        ):
            route = _load_space_agent_query_route(
                self.space,
                "V2RaceProduct",
            )

        self.assertEqual(route["agent"], agent)
        self.assertEqual(route["agent_type"], agent_type)
        self.assertIsNone(route["datasource"])

    def test_get_event_datasource_denies_unbound_global_datasource(self):
        agent_type = self._create_agent_type(
            agent_name="event-global-only-agent",
            agent_product="EventGlobalOnly",
        )
        self._create_datasource(
            agent_type=agent_type,
            datasource_type="logs",
            source_system="signoz",
        )

        self._assert_route_not_found(
            get_event_datasource,
            self.space,
            agent_product="EventGlobalOnly",
        )

    def test_resolve_routable_space_agent_allows_personal_space_agent(self):
        agent_type = self._create_agent_type(
            agent_name="personal-agent",
            agent_product="PersonalProduct",
        )
        agent = self._bind_agent(space=self.personal_space, agent_type=agent_type)

        self.assertEqual(
            resolve_routable_space_agent(
                self.personal_space,
                "PersonalProduct",
            ),
            agent,
        )

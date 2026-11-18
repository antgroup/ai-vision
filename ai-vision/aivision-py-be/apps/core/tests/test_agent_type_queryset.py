"""Manager and chained QuerySet access must apply the same runtime filters."""
from django.test import TestCase
from django.utils import timezone

from apps.core.models import AgentTypeConfig


class AgentTypeConfigQuerySetTests(TestCase):
    def setUp(self):
        self.active = AgentTypeConfig.objects.create(agent_name="ACTIVE", agent_product="Active", status=1)
        self.disabled = AgentTypeConfig.objects.create(agent_name="DISABLED", agent_product="Disabled", status=0)
        AgentTypeConfig.objects.create(
            agent_name="DELETED", agent_product="Deleted", status=1,
            gmt_deleted=timezone.now(),
        )

    def test_manager_runtime_filters_materialize_expected_rows(self):
        self.assertSetEqual(
            set(AgentTypeConfig.objects.runtime().values_list("pk", flat=True)),
            {self.active.pk, self.disabled.pk},
        )
        self.assertSetEqual(
            set(AgentTypeConfig.objects.shared_runtime().values_list("pk", flat=True)),
            {self.active.pk, self.disabled.pk},
        )
        self.assertEqual(list(AgentTypeConfig.objects.active_shared_runtime()), [self.active])

    def test_chained_queryset_keeps_runtime_methods_and_database_alias(self):
        queryset = AgentTypeConfig.objects.db_manager("default").filter(agent_name="ACTIVE")
        self.assertEqual(queryset.db, "default")
        self.assertEqual(list(queryset.shared_runtime().active_shared_runtime()), [self.active])

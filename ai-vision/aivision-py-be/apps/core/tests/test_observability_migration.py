"""Upgrade an old schema without altering Langfuse configuration or credentials."""

from importlib import import_module

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


class ObservabilityMigrationTests(TransactionTestCase):
    before = [("core", "0022_agent_installation_authorization")]
    after = [("core", "0023_observability_cleanup")]

    def setUp(self):
        executor = MigrationExecutor(connection)
        self.latest = executor.loader.graph.leaf_nodes()
        executor.migrate(self.before)
        self.old = executor.loader.project_state(self.before).apps
        User = self.old.get_model("core", "User")
        self.user = User.objects.create(
            name="Migration",
            email="migration@example.test",
            normalized_email="migration@example.test",
            password="!",
        )
        self.space = self.old.get_model("core", "Space").objects.create(
            name="Migration", type=2, creator=self.user
        )
        self.agent_type = self.old.get_model("core", "AgentTypeConfig").objects.create(
            agent_name="MIGRATION_CODEX", agent_product="Codex"
        )

    def tearDown(self):
        # A rejected preflight leaves the historical schema intact. Remove only
        # this test's blocker before restoring the latest schema for other tests.
        if (
            self.before[0] in MigrationExecutor(connection).loader.applied_migrations
            and self.after[0]
            not in MigrationExecutor(connection).loader.applied_migrations
        ):
            self.old.get_model("core", "DataSource").objects.filter(
                source_system="stargazer"
            ).delete()
        MigrationExecutor(connection).migrate(self.latest)
        super().tearDown()

    def test_upgrade_preserves_langfuse_and_changes_only_signoz_format(self):
        source = self.old.get_model("core", "DataSource")
        lf_config = {
            "response_format": "langfuse",
            "langfuse_public_key": "pk-kept",
            "langfuse_secret_key": "sk-kept",
            "queries": {"trace_count": {"query": "kept-template"}},
        }
        lf = source.objects.create(
            agent_type=self.agent_type,
            datasource_type="traces",
            source_system="langfuse",
            config=lf_config,
        )
        signoz = source.objects.create(
            agent_type=self.agent_type,
            datasource_type="logs",
            source_system="signoz",
            config={
                "endpoint": "http://signoz.test",
                "response_format": "stargazer",
                "queries": {"unused": "SELECT 1"},
            },
        )
        endpoint = self.old.get_model(
            "core", "ObservationEndpointConfig"
        ).objects.create(
            name="LF", backend_type="langfuse", base_url="http://langfuse.test"
        )
        project = self.old.get_model("core", "SpaceObservationProject").objects.create(
            space=self.space,
            endpoint_config=endpoint,
            upstream_project_id="project-kept",
        )
        credential = self.old.get_model(
            "core", "SpaceObservationCredential"
        ).objects.create(
            space_observation_project=project,
            scope="READ",
            public_key="pk-kept",
            secret_hash="hash-kept",
            secret_ciphertext="ciphertext-kept",
        )
        binding = self.old.get_model("core", "SpaceDatasourceBinding").objects.create(
            space_observation_project=project,
            datasource_type="traces",
            source_system="langfuse",
            config=lf_config,
        )
        executor = MigrationExecutor(connection)
        executor.migrate(self.after)
        # Assert against this migration's schema, not fields introduced later.
        after_apps = executor.loader.project_state(self.after).apps
        DataSource = after_apps.get_model("core", "DataSource")
        Space = after_apps.get_model("core", "Space")
        SpaceObservationCredential = after_apps.get_model(
            "core", "SpaceObservationCredential"
        )
        SpaceDatasourceBinding = after_apps.get_model(
            "core", "SpaceDatasourceBinding"
        )

        self.assertEqual(DataSource.objects.get(pk=lf.pk).config, lf_config)
        self.assertEqual(
            SpaceDatasourceBinding.objects.get(pk=binding.pk).config, lf_config
        )
        self.assertEqual(
            SpaceObservationCredential.objects.get(pk=credential.pk).secret_ciphertext,
            "ciphertext-kept",
        )
        self.assertEqual(Space.objects.get(pk=self.space.pk).uuid, self.space.uuid)
        self.assertEqual(
            DataSource.objects.get(pk=signoz.pk).config,
            {"endpoint": "http://signoz.test", "response_format": "table"},
        )
        self.assertNotIn("aivision_platform", connection.introspection.table_names())

    def test_preflight_reports_real_retired_records_before_ddl(self):
        source = self.old.get_model("core", "DataSource").objects.create(
            agent_type=self.agent_type,
            datasource_type="traces",
            source_system="stargazer",
            config={"namespace": "retired"},
        )
        migration = import_module("apps.core.migrations.0023_observability_cleanup")
        with connection.schema_editor() as editor:
            with self.assertRaisesRegex(RuntimeError, f"DataSource: \\[{source.pk}\\]"):
                migration.preflight(self.old, editor)
        self.assertIn("aivision_platform", connection.introspection.table_names())

from io import StringIO
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.core.management import CommandError, call_command
from django.test import SimpleTestCase, TestCase

from apps.core.management.commands.configure_signoz_datasource import Command, _read_api_key
from apps.core.models import AgentTypeConfig, DataSource


class SigNozQueryKeyRecoveryTests(SimpleTestCase):
    endpoint = "http://query:8090"

    def read(self, configs, explicit=""):
        with patch.dict("os.environ", {"SIGNOZ_API_KEY": explicit}), patch(
            "apps.core.management.commands.configure_signoz_datasource.DataSource.objects"
        ) as manager:
            manager.filter.return_value.values_list.return_value = configs
            value = _read_api_key(self.endpoint)
            if explicit.strip():
                manager.filter.assert_not_called()
            else:
                manager.filter.assert_called_once_with(
                    source_system="signoz",
                    enabled=1,
                    gmt_deleted__isnull=True,
                    agent_type__status=1,
                    agent_type__gmt_deleted__isnull=True,
                )
            return value

    def test_recovers_unique_key_for_same_endpoint(self):
        self.assertEqual(self.read([
            {"endpoint": self.endpoint + "/", "api_key": "existing-key"},
            {"endpoint": self.endpoint, "api_key": "existing-key"},
            {"endpoint": self.endpoint},
        ]), "existing-key")

    def test_explicit_key_overrides_recovery_without_database_lookup(self):
        self.assertEqual(self.read([], "  explicit-key  "), "explicit-key")

    def test_different_endpoint_and_malformed_configs_cannot_supply_key(self):
        self.assertEqual(self.read([
            {"endpoint": "http://different:8090", "api_key": "different-key"},
            {"endpoint": "http://user:password@query:8090", "api_key": "unsafe-key"},
            {"endpoint": self.endpoint, "api_key": 123},
            {"endpoint": self.endpoint, "api_key": " "},
            {"endpoint": None, "api_key": "bad-endpoint-key"},
            None,
            [],
        ]), "")

    def test_conflicting_keys_fail_without_disclosing_credentials(self):
        with self.assertRaises(CommandError) as error:
            self.read([
                {"endpoint": self.endpoint, "api_key": "first-private-key"},
                {"endpoint": self.endpoint, "api_key": "second-private-key"},
            ])
        self.assertNotIn("first-private-key", str(error.exception))
        self.assertNotIn("second-private-key", str(error.exception))
        self.assertIn("SIGNOZ_API_KEY", str(error.exception))

    def configure_existing(self, endpoint, explicit="", recovered=None):
        old_endpoint = "http://old-query"
        agent = SimpleNamespace(display_scope="ALL")
        old_config = {"endpoint": old_endpoint, "api_key": "old-private-key",
                      "queries": {"request_count": {"mode": "simple"}}}
        with patch.dict("os.environ", {"SIGNOZ_API_KEY": explicit}), patch(
            "apps.core.management.commands.configure_signoz_datasource.AgentTypeConfig.objects"
        ) as agents, patch(
            "apps.core.management.commands.configure_signoz_datasource.DataSource.objects"
        ) as routes:
            agents.filter.return_value.order_by.return_value.__getitem__.return_value = [agent]

            def filtered(**kwargs):
                query = Mock()
                if "agent_type" not in kwargs:
                    query.values_list.return_value = [old_config] + (recovered or [])
                elif kwargs.get("source_system") == "signoz":
                    query.first.return_value = SimpleNamespace(config=old_config, is_default=1)
                else:
                    query.exclude.return_value.order_by.return_value = []
                return query

            routes.filter.side_effect = filtered
            routes.update_or_create.return_value = (SimpleNamespace(id=1), False)
            command = Command(stdout=StringIO())
            # Exercise the real command body with mocked ORM; the decorator's
            # transaction belongs to the database-backed command tests below.
            Command.handle.__wrapped__(
                command, agent_product="Codex", endpoint=endpoint,
                agent_product_filter=None, make_default=False, replace_existing=False,
            )
            return [call.kwargs["defaults"]["config"]
                    for call in routes.update_or_create.call_args_list]

    def test_endpoint_change_does_not_forward_old_credentials(self):
        for config in self.configure_existing("http://new-query"):
            self.assertNotIn("api_key", config)
            self.assertEqual(config["endpoint"], "http://new-query")

    def test_same_endpoint_preserves_existing_key(self):
        for config in self.configure_existing("http://old-query/"):
            self.assertEqual(config["api_key"], "old-private-key")

    def test_explicit_rotation_supplies_new_endpoint_key(self):
        for config in self.configure_existing("http://new-query", "explicit-new-key"):
            self.assertEqual(config["api_key"], "explicit-new-key")

    def test_endpoint_change_can_recover_only_new_endpoint_key(self):
        for config in self.configure_existing("http://new-query", recovered=[
            {"endpoint": "http://new-query", "api_key": "new-endpoint-key"},
        ]):
            self.assertEqual(config["api_key"], "new-endpoint-key")


class ConfigureSigNozDatasourceTests(TestCase):
    def setUp(self):
        self.agent_type = AgentTypeConfig.objects.create(
            agent_name="CODEX",
            agent_product="Codex",
            status=1,
        )
        DataSource.objects.create(
            agent_type=self.agent_type,
            datasource_type="traces",
            source_system="langfuse",
            enabled=1,
            is_default=1,
            config={"response_format": "stargazer"},
        )

    @patch.dict(
        "os.environ",
        {"SIGNOZ_API_KEY": "test-key"},
        clear=False,
    )
    def test_configures_all_signals_and_makes_signoz_default(self):
        output = StringIO()
        call_command(
            "configure_signoz_datasource",
            agent_product="Codex",
            endpoint="http://127.0.0.1:18091/",
            display_scope="ALL",
            make_default=True,
            replace_existing=True,
            stdout=output,
        )

        routes = DataSource.objects.filter(
            agent_type=self.agent_type,
            source_system="signoz",
        )
        self.assertEqual(
            set(routes.values_list("datasource_type", flat=True)),
            {"traces", "logs", "metrics"},
        )
        previous = DataSource.objects.get(source_system="langfuse")
        self.assertFalse(previous.is_default)
        self.assertEqual(previous.enabled, 1)
        self.agent_type.refresh_from_db()
        self.assertEqual(self.agent_type.display_scope, "ALL")
        for route in routes:
            self.assertEqual(route.enabled, 1)
            self.assertEqual(route.is_default, 1)
            self.assertEqual(route.config["endpoint"], "http://127.0.0.1:18091")
            self.assertEqual(route.config["api_key"], "test-key")

        # The command is deliberately repeatable.
        call_command(
            "configure_signoz_datasource",
            agent_product="Codex",
            endpoint="http://127.0.0.1:18091",
            make_default=True,
            replace_existing=True,
            stdout=StringIO(),
        )
        self.assertEqual(routes.count(), 3)

    @patch.dict(
        "os.environ",
        {"SIGNOZ_API_KEY": ""},
        clear=False,
    )
    def test_endpoint_change_preserves_queries_without_forwarding_api_key(self):
        query = {
            "mode": "simple",
            "metric": "codex.requests",
            "aggregation": "sum",
            "value_type": "number",
        }
        DataSource.objects.create(
            agent_type=self.agent_type,
            datasource_type="metrics",
            source_system="signoz",
            enabled=1,
            config={
                "endpoint": "http://old-query",
                "api_key": "existing-secret",
                "response_format": "prometheus",
                "queries": {"request_count": query},
            },
        )

        call_command(
            "configure_signoz_datasource",
            agent_product="Codex",
            endpoint="http://new-query:8090",
            stdout=StringIO(),
        )

        route = DataSource.objects.get(
            agent_type=self.agent_type,
            datasource_type="metrics",
            source_system="signoz",
        )
        self.assertEqual(route.config["endpoint"], "http://new-query:8090")
        self.assertNotIn("api_key", route.config)
        self.assertEqual(route.config["queries"], {"request_count": query})

    @patch.dict("os.environ", {"SIGNOZ_API_KEY": ""})
    def test_new_product_reuses_existing_deployment_key_without_external_login(self):
        DataSource.objects.create(
            agent_type=self.agent_type,
            datasource_type="traces",
            source_system="signoz",
            enabled=1,
            config={"endpoint": "http://query:8090/", "api_key": "internal-key"},
        )
        new_agent_type = AgentTypeConfig.objects.create(
            agent_name="HERMES", agent_product="HermesAgent", status=1,
        )
        output = StringIO()
        with patch("requests.Session") as session:
            call_command(
                "configure_signoz_datasource",
                agent_product="HermesAgent", endpoint="http://query:8090",
                make_default=True, stdout=output,
            )
            session.assert_not_called()
        routes = DataSource.objects.filter(
            agent_type=new_agent_type, source_system="signoz",
        )
        self.assertEqual(routes.count(), 3)
        for route in routes:
            self.assertEqual(route.config["api_key"], "internal-key")
            self.assertEqual(route.config["agent_product_filter"], "HermesAgent")
        self.assertNotIn("internal-key", output.getvalue())

    @patch.dict("os.environ", {"SIGNOZ_API_KEY": ""})
    def test_disabled_deleted_and_inactive_routes_cannot_supply_key(self):
        from django.utils import timezone

        inactive = AgentTypeConfig.objects.create(
            agent_name="INACTIVE", agent_product="inactive", status=0,
        )
        for kind, fields in (
            ("traces", {"enabled": 0, "agent_type": self.agent_type}),
            ("logs", {"enabled": 1, "agent_type": self.agent_type,
                      "gmt_deleted": timezone.now()}),
            ("metrics", {"enabled": 1, "agent_type": inactive}),
        ):
            DataSource.objects.create(
                datasource_type=kind, source_system="signoz",
                config={"endpoint": "http://query:8090", "api_key": "excluded-key"},
                **fields,
            )
        new_agent_type = AgentTypeConfig.objects.create(
            agent_name="NEW", agent_product="new", status=1,
        )
        call_command(
            "configure_signoz_datasource", agent_product="new",
            endpoint="http://query:8090", stdout=StringIO(),
        )
        for route in DataSource.objects.filter(agent_type=new_agent_type):
            self.assertNotIn("api_key", route.config)

    @patch.dict(
        "os.environ",
        {"SIGNOZ_API_KEY": ""},
        clear=False,
    )
    def test_migrates_supported_ceresdb_promql_queries(self):
        DataSource.objects.create(
            agent_type=self.agent_type,
            datasource_type="metrics",
            source_system="ceresdb",
            enabled=1,
            is_default=1,
            config={
                "response_format": "prometheus",
                "queries": {
                    "token_usage_by_model_trend": {
                        "mode": "template",
                        "template": (
                            "sum(codex.token.usage{username='{{ username }}',"
                            'type=~"input|output"}) by(model)'
                        ),
                        "value_type": "timeseries",
                    }
                },
            },
        )

        call_command(
            "configure_signoz_datasource",
            agent_product="Codex",
            endpoint="http://query:8090",
            make_default=True,
            replace_existing=True,
            stdout=StringIO(),
        )

        route = DataSource.objects.get(
            agent_type=self.agent_type,
            datasource_type="metrics",
            source_system="signoz",
        )
        query = route.config["queries"]["token_usage_by_model_trend"]
        self.assertEqual(query["mode"], "simple")
        self.assertEqual(query["metric"], "codex.token.usage")
        self.assertEqual(query["aggregation"], "sum")
        self.assertEqual(query["group_by"], ["model"])
        self.assertEqual(query["filters"]["user_id"], "$username")
        self.assertEqual(query["filters"]["type"], ["input", "output"])

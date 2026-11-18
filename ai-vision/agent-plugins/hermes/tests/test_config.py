from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from observ_hermes.config import (
    ConfigError,
    _build_config,
    config_to_public_dict,
    load_config,
    production_config_path,
    validate_public_config,
)


class ConfigBehaviorTests(unittest.TestCase):
    def test_production_path_uses_hermes_home(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.assertEqual(
                production_config_path(environ={"HERMES_HOME": str(root)}),
                root / "observ-hermes.json",
            )
            self.assertEqual(
                production_config_path(environ={}, home=root),
                root / ".hermes" / "observ-hermes.json",
            )

    def test_scalar_map_and_signal_precedence_is_deterministic(self) -> None:
        edition = {
            "serviceName": "edition",
            "collectionMode": "full",
            "logDir": "/edition/logs",
            "otelExporter": {
                "endpoint": "https://edition",
                "headers": {"edition": "1", "collision": "edition"},
                "attributes": {"source": "edition"},
                "resourceAttributes": {"resource": "edition"},
            },
            "tracesConfig": {
                "endpoint": "https://edition/traces",
                "headers": {"edition-signal": "1", "collision": "edition-signal"},
            },
        }
        user = {
            "serviceName": "user",
            "collectionMode": "full",
            "logDir": "/user/logs",
            "otelExporter": {
                "endpoint": "https://user",
                "headers": {"user": "1", "collision": "user"},
                "attributes": {"source": "user"},
                "resourceAttributes": {"resource": "user"},
            },
            "tracesConfig": {
                "endpoint": "https://user/traces",
                "headers": {"user-signal": "1", "collision": "user-signal"},
            },
        }
        env = {
            "OTEL_SERVICE_NAME": "standard",
            "OBSERV_HERMES_SERVICE_NAME": "plugin",
            "AIVISION_COLLECTION_MODE": "full",
            "OBSERV_HERMES_COLLECTION_MODE": "privacy",
            "OTEL_EXPORTER_OTLP_ENDPOINT": "https://standard",
            "OBSERV_HERMES_OTLP_ENDPOINT": "https://plugin",
            "OTEL_EXPORTER_OTLP_HEADERS": "standard=1,collision=standard",
            "OBSERV_HERMES_OTLP_HEADERS": "plugin=1,collision=plugin",
            "AIVISION_OTEL_ATTRIBUTES": "shared=1,source=shared",
            "OBSERV_HERMES_OTEL_ATTRIBUTES": "plugin-attr=1,source=plugin",
            "OTEL_RESOURCE_ATTRIBUTES": "standard-resource=1,resource=standard",
            "AIVISION_OTEL_RESOURCE_ATTRIBUTES": "shared-resource=1,resource=shared",
            "OBSERV_HERMES_OTEL_RESOURCE_ATTRIBUTES": (
                "plugin-resource=1,resource=plugin"
            ),
            "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT": "https://standard/traces",
            "OBSERV_HERMES_OTLP_TRACES_ENDPOINT": "https://plugin/traces",
            "OTEL_EXPORTER_OTLP_TRACES_HEADERS": (
                "standard-signal=1,collision=standard-signal"
            ),
            "OBSERV_HERMES_OTLP_TRACES_HEADERS": (
                "plugin-signal=1,collision=plugin-signal"
            ),
            "AIVISION_LOG_DIR": "/shared/logs",
            "OBSERV_HERMES_LOG_DIR": "/plugin/logs",
        }
        config = _build_config(edition, user, environ=env)
        self.assertEqual(config.service_name, "plugin")
        self.assertEqual(config.collection_mode, "privacy")
        self.assertEqual(config.otel_exporter.endpoint, "https://plugin")
        self.assertEqual(config.otel_exporter.headers["collision"], "plugin")
        self.assertEqual(
            set(config.otel_exporter.headers),
            {"edition", "user", "standard", "plugin", "collision"},
        )
        self.assertEqual(config.otel_exporter.attributes["source"], "plugin")
        self.assertEqual(config.otel_exporter.resource_attributes["resource"], "plugin")
        self.assertEqual(config.traces_config.endpoint, "https://plugin/traces")
        self.assertEqual(config.traces_config.headers["collision"], "plugin-signal")
        self.assertEqual(config.log_dir, "/plugin/logs")
        self.assertEqual(_build_config(edition, user, environ={}).log_dir, "/user/logs")

    def test_edition_default_log_directories_are_production_owned(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            common = load_config(environ={}, home=home, edition_name="common")
            aivision = load_config(
                environ={"AIVISION_HOME": str(home / "artifacts")},
                home=home,
                edition_name="aivision",
            )
        self.assertEqual(common.log_dir, str(home / ".hermes/logs"))
        self.assertEqual(aivision.log_dir, str(home / "artifacts/hermes/logs"))

    def test_load_config_reads_json_and_never_loads_legacy_env(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            hermes_root = root / "hermes"
            hermes_root.mkdir()
            (hermes_root / "observ-hermes.json").write_text(
                json.dumps(
                    {
                        "enableTelemetry": False,
                        "serviceName": "json-service",
                        "collectionMode": "privacy",
                    }
                )
            )
            (hermes_root / "observ-hermes.env").write_text(
                "AGENT_ENABLE_TELEMETRY=true\nOTEL_SERVICE_NAME=legacy-service\n"
            )
            config = load_config(
                environ={"HERMES_HOME": str(hermes_root)},
                configs_dir=Path(__file__).resolve().parents[1]
                / "observ_hermes/configs",
            )
        self.assertFalse(config.enable_telemetry)
        self.assertEqual(config.service_name, "json-service")
        self.assertEqual(config.collection_mode, "privacy")

    def test_unknown_legacy_replay_and_invalid_values_fail_closed(self) -> None:
        invalid_values = [
            ({"ant": {}}, r"\.ant"),
            ({"log": {}}, r"\.log"),
            ({"sessionDir": "x"}, r"\.sessionDir"),
            ({"replayDir": "x"}, r"\.replayDir"),
            ({"captureEnabled": True}, r"\.captureEnabled"),
            ({"collectionMode": "metadata"}, r"collectionMode"),
            ({"otelExporter": {"protocol": "grpc"}}, r"protocol"),
            ({"metricExportInterval": 0}, r"metricExportInterval"),
        ]
        for value, message in invalid_values:
            with (
                self.subTest(value=value),
                self.assertRaisesRegex(ConfigError, message),
            ):
                validate_public_config(value, "test")

    def test_privacy_config_exposes_no_internal_or_artifact_fields(self) -> None:
        config = _build_config(
            {"collectionMode": "privacy", "debug": True},
            environ={},
        )
        self.assertFalse(hasattr(config, "log"))
        self.assertFalse(hasattr(config, "replay_dir"))
        self.assertFalse(hasattr(config, "session_dir"))

    def test_headers_do_not_appear_in_repr(self) -> None:
        config = _build_config(
            {},
            environ={"OTEL_EXPORTER_OTLP_HEADERS": "Authorization=Bearer secret"},
        )
        rendered = repr(config)
        self.assertNotIn("Bearer secret", rendered)
        self.assertNotIn("Authorization", rendered)

    def test_public_round_trip_contains_no_internal_fields(self) -> None:
        config = _build_config({}, environ={})
        public = config_to_public_dict(config)
        validate_public_config(public)
        for key in ("ant", "log", "sessionDir", "replayDir"):
            self.assertNotIn(key, public)


if __name__ == "__main__":
    unittest.main()

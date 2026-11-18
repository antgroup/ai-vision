from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from schema_subset import SchemaValidationError, validate

REPO_ROOT = Path(__file__).resolve().parents[2]
SCHEMA_PATH = REPO_ROOT / "docs" / "config" / "common.schema.json"


def load_json(path: Path) -> dict:
    value = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise AssertionError(f"{path} must contain a JSON object")
    return value


def load_jsonc(path: Path) -> dict:
    source = "\n".join(
        line
        for line in path.read_text().splitlines()
        if not line.lstrip().startswith("//")
    )
    value = json.loads(source)
    if not isinstance(value, dict):
        raise AssertionError(f"{path} must contain a JSON object")
    return value


class CommonSchemaAlignmentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.schema = load_json(SCHEMA_PATH)

    def test_schema_is_strict_at_public_object_boundaries(self) -> None:
        self.assertFalse(self.schema["additionalProperties"])
        self.assertFalse(
            self.schema["properties"]["otelExporter"]["additionalProperties"]
        )
        self.assertFalse(
            self.schema["$defs"]["SignalEndpointConfig"]["additionalProperties"]
        )
        self.assertEqual(
            load_json(REPO_ROOT / "docs" / "config" / "pi.schema.json"), self.schema
        )

    def test_all_peer_examples_and_edition_configs_validate(self) -> None:
        examples = [
            "docs/config/openclaw.example.jsonc",
            "docs/config/opencode.example.jsonc",
            "docs/config/observ-pi.example.jsonc",
            "docs/config/hermes.example.jsonc",
        ]
        editions = [
            "openclaw/observ-openclaw/configs/common.config.json",
            "openclaw/observ-openclaw/configs/aivision.config.json",
            "opencode/observ-opencode/configs/aivision.config.json",
            "opencode/observ-opencode/configs/cowork.config.json",
            "pi/observ-pi/configs/common.config.json",
            "pi/observ-pi/configs/aivision.config.json",
            "hermes/observ_hermes/configs/common.json",
            "hermes/observ_hermes/configs/aivision.json",
        ]
        for relative in examples:
            with self.subTest(path=relative):
                validate(load_jsonc(REPO_ROOT / relative), self.schema, self.schema)
        for relative in editions:
            with self.subTest(path=relative):
                validate(load_json(REPO_ROOT / relative), self.schema, self.schema)

    def test_production_schema_rejects_legacy_and_replay_fields(self) -> None:
        for key in ("ant", "log", "sessionDir", "replayDir", "captureEnabled"):
            with (
                self.subTest(key=key),
                self.assertRaisesRegex(
                    SchemaValidationError, rf"\$\.{key}: additional property"
                ),
            ):
                validate(
                    {key: {} if key in {"ant", "log"} else "value"},
                    self.schema,
                    self.schema,
                )
        for invalid in (
            {"otelExporter": {"unknown": True}},
            {"tracesConfig": {"unknown": True}},
        ):
            with (
                self.subTest(invalid=invalid),
                self.assertRaises(SchemaValidationError),
            ):
                validate(invalid, self.schema, self.schema)

    def test_cowork_metadata_is_separate_from_public_projection(self) -> None:
        public_config = load_json(
            REPO_ROOT
            / "opencode"
            / "observ-opencode"
            / "configs"
            / "cowork.config.json"
        )
        metadata = load_json(
            REPO_ROOT
            / "opencode"
            / "observ-opencode"
            / "configs"
            / "cowork.metadata.json"
        )
        self.assertEqual(metadata, {"standaloneConfigDir": "cowork"})
        self.assertNotIn("standaloneConfigDir", public_config)
        self.assertNotIn("log", public_config)
        validate(public_config, self.schema, self.schema)

    def test_hermes_replay_schema_is_strict_and_separate(self) -> None:
        replay_schema = load_json(
            REPO_ROOT / "docs" / "config" / "hermes-replay.schema.json"
        )
        replay_example = load_jsonc(
            REPO_ROOT / "docs" / "config" / "hermes-replay.example.jsonc"
        )
        self.assertFalse(replay_schema["additionalProperties"])
        self.assertEqual(
            set(replay_schema["properties"]),
            {"captureEnabled", "collectionMode", "sessionDir", "sessionName", "debug"},
        )
        validate(replay_example, replay_schema, replay_schema)
        with self.assertRaises(SchemaValidationError):
            validate(
                {**replay_example, "enableTelemetry": True},
                replay_schema,
                replay_schema,
            )


if __name__ == "__main__":
    unittest.main()

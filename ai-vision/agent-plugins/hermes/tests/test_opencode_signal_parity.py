from __future__ import annotations

import json
import sys
import tempfile
import unittest
from collections import Counter, defaultdict
from collections.abc import Mapping
from pathlib import Path
from typing import Any

HERMES_ROOT = Path(__file__).resolve().parents[1]
TESTS_DIR = Path(__file__).resolve().parent
for root in (
    TESTS_DIR,
    HERMES_ROOT / "hermes-replay",
    HERMES_ROOT / "hermes-core",
    HERMES_ROOT,
):
    value = str(root)
    if value not in sys.path:
        sys.path.insert(0, value)

from aivision_hermes_core.normalize import CanonicalNormalizer  # noqa: E402
from aivision_hermes_core.semantic import (  # noqa: E402
    MemorySink,
    SemanticConfig,
    SemanticEngine,
)
from hermes_replay.capture import (  # noqa: E402
    GENESIS_CHECKSUM,
    RAW_HOOK_CORRELATION_FIELDS,
    RAW_SCHEMA_VERSION,
    _checksum,
    _raw_error,
    _raw_output,
    project_raw,
    read_raw_transcript,
)
from hermes_replay.replay import replay_envelopes, replay_transcript  # noqa: E402
from parity_oracle import (  # noqa: E402
    PARITY_SCHEMA_VERSION,
    ParityFixtureError,
    canonical_json,
    compare_records,
    load_fixture,
    normalized_records,
)

from observ_hermes.config import AntIdentity, ObservHermesConfig  # noqa: E402
from observ_hermes.resource import build_resource_attributes  # noqa: E402

PARITY_DIR = TESTS_DIR / "fixtures" / "parity"
OPENCODE_FIXTURE = PARITY_DIR / "opencode-shared-signals.v1.json"
CAPABILITY_MAP = PARITY_DIR / "hermes-opencode-capability-map.v1.json"
COMBINED_EDGE_SCENARIO = PARITY_DIR / "parity-idless-zero-cache-privacy.v1.json"
SCENARIO_PATHS = (
    PARITY_DIR / "parity-two-turn-success.v1.json",
    PARITY_DIR / "parity-two-turn-error.v1.json",
)
HERMES_PRODUCER = {
    "runtime": "hermes",
    "version": "v0.18.2",
    "source_revision": None,
    "source": "fixture",
    "generated_by": "tests.test_opencode_signal_parity",
}
CONTENT_FRAGMENTS = (
    "input.messages",
    "output.messages",
    "system_instructions",
    "tool.call.arguments",
    "tool.call.result",
    "error.message",
    "child.goal",
    "child.summary",
)
IDENTITY_PLACEHOLDERS = {
    "agentic.runtime.name": "<runtime>",
    "ant.agent.type": "<agent-type>",
    "ant.username": "<username>",
    "user.id": "<user-id>",
}
RESOURCE_VALUE_PLACEHOLDERS = {
    "service.name": "<service-name>",
    "service.version": "<service-version>",
    "ant.agent.type": "<agent-type>",
    "ant.agent.product": "<agent-product>",
    "ant.agent.instance": "<agent-instance>",
}


def load_scenario(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("schema_version") != "aivision.hermes-parity-scenario.v1":
        raise ParityFixtureError(f"invalid scenario schema: {path}")
    if not isinstance(data.get("hooks"), list) or not data["hooks"]:
        raise ParityFixtureError(f"scenario has no hooks: {path}")
    if not isinstance(data.get("session_id"), str) or not data["session_id"]:
        raise ParityFixtureError(f"scenario has no session_id: {path}")
    for index, item in enumerate(data["hooks"]):
        if set(item) != {"hook", "captured_at", "raw"}:
            raise ParityFixtureError(f"{path}: hooks[{index}] has invalid fields")
        if item["raw"].get("session_id") != data["session_id"]:
            raise ParityFixtureError(f"{path}: hooks[{index}] changes session identity")
    return data


def scenario_envelopes(
    scenario: Mapping[str, Any], *, mode: str | None = None
) -> list[dict[str, Any]]:
    collection_mode = mode or str(scenario["collection_mode"])
    previous = GENESIS_CHECKSUM
    envelopes: list[dict[str, Any]] = []
    for sequence, item in enumerate(scenario["hooks"], 1):
        hook = str(item["hook"])
        projected, redacted = project_raw(hook, item["raw"], collection_mode)
        envelope: dict[str, Any] = {
            "schemaVersion": RAW_SCHEMA_VERSION,
            "agent": "hermes",
            "plugin": "hermes-replay",
            "hook": hook,
            "capturedAt": item["captured_at"],
            "sessionId": scenario["session_id"],
            "correlation": {
                key: projected[key]
                for key in RAW_HOOK_CORRELATION_FIELDS[hook]
                if key in projected
            },
            "rawInput": projected,
            "rawContext": projected.get("context", {}),
            "rawOutput": _raw_output(hook, projected),
            "rawError": _raw_error(hook, projected),
            "redaction": {
                "collectionMode": collection_mode,
                "credentialsRedacted": redacted,
                "contentRedacted": collection_mode == "privacy",
            },
            "integrity": {"sequence": sequence, "previousChecksum": previous},
        }
        checksum = _checksum(envelope)
        envelope["integrity"]["checksum"] = checksum
        previous = checksum
        envelopes.append(envelope)
    return envelopes


def transcript_text(scenario: Mapping[str, Any], *, mode: str | None = None) -> str:
    return "".join(
        json.dumps(envelope, ensure_ascii=False, sort_keys=True) + "\n"
        for envelope in scenario_envelopes(scenario, mode=mode)
    )


def run_live(scenario: Mapping[str, Any], *, mode: str = "full") -> list[Any]:
    normalizer = CanonicalNormalizer(mode)
    sink = MemorySink()
    engine = SemanticEngine(
        SemanticConfig(collection_mode=mode),
        sink,
    )
    for item in scenario["hooks"]:
        engine.apply(
            normalizer.normalize(
                str(item["hook"]),
                item["raw"],
                timestamp=str(item["captured_at"]),
                provenance={
                    "runtime": "hermes",
                    "observer_schema": "hermes.observer.v1",
                },
            )
        )
    engine.shutdown()
    return sink.observations


def run_replay(scenario: Mapping[str, Any], *, mode: str = "full") -> list[Any]:
    sink = MemorySink()
    replay_envelopes(scenario_envelopes(scenario, mode=mode), sink)
    return sink.observations


def decode_otlp_value(value: Mapping[str, Any]) -> Any:
    if len(value) != 1:
        raise ParityFixtureError(f"OTLP attribute value is not singular: {value}")
    kind, encoded = next(iter(value.items()))
    if kind == "intValue":
        return int(encoded)
    if kind in {"stringValue", "doubleValue", "boolValue"}:
        return encoded
    raise ParityFixtureError(f"unsupported OTLP attribute value: {value}")


def actual_replay_resource(
    scenario: Mapping[str, Any], *, mode: str = "full"
) -> dict[str, Any]:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        transcript = root / f"session_{scenario['session_id']}.jsonl"
        transcript.write_text(transcript_text(scenario, mode=mode), encoding="utf-8")
        outputs = replay_transcript(transcript, root / "replays")
        resources: list[dict[str, Any]] = []
        for output in outputs.values():
            for line in output.read_text(encoding="utf-8").splitlines():
                request = json.loads(line)
                resource_groups = next(
                    (
                        request[key]
                        for key in ("resourceSpans", "resourceLogs", "resourceMetrics")
                        if key in request
                    ),
                    None,
                )
                if not isinstance(resource_groups, list):
                    raise ParityFixtureError(
                        f"replay OTLP request has no resource: {request}"
                    )
                for group in resource_groups:
                    attributes = group.get("resource", {}).get("attributes")
                    if not isinstance(attributes, list):
                        raise ParityFixtureError(
                            "replay OTLP resource attributes must be an array"
                        )
                    resources.append(
                        {
                            item["key"]: decode_otlp_value(item["value"])
                            for item in attributes
                        }
                    )
        if not resources:
            raise ParityFixtureError("replay OTLP output contains no Resource")
        first = resources[0]
        if any(resource != first for resource in resources[1:]):
            raise ParityFixtureError("replay OTLP Resources differ across signals")
        return first


def actual_live_resource(
    scenario: Mapping[str, Any], *, mode: str = "full"
) -> dict[str, Any]:
    return build_resource_attributes(
        ObservHermesConfig(
            service_name="hermes-parity",
            collection_mode=mode,
            ant=AntIdentity(agent_instance="parity-fixture"),
        )
    )


def comparable_attributes(attributes: Mapping[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in attributes.items():
        if any(fragment in key for fragment in CONTENT_FRAGMENTS):
            continue
        result[key] = IDENTITY_PLACEHOLDERS.get(key, value)
    return result


def parity_records(
    scenario: Mapping[str, Any],
    observations: list[Any],
    producer: Mapping[str, Any],
    capability_map: Mapping[str, Any],
    resource: Mapping[str, Any],
) -> list[dict[str, Any]]:
    shared_metrics = set(capability_map["shared"]["metrics"])
    shared_events = set(capability_map["shared"]["events"])
    shared_span_kinds = set(capability_map["shared"]["span_kinds"])
    shared_resource_attributes = set(capability_map["shared"]["resource_attributes"])
    span_starts: dict[str, dict[str, Any]] = {}
    missing_resource_attributes = shared_resource_attributes - set(resource)
    if missing_resource_attributes:
        missing = sorted(missing_resource_attributes)
        raise ParityFixtureError(
            f"Hermes resource is missing shared attributes: {missing}"
        )
    comparable_resource = {
        key: RESOURCE_VALUE_PLACEHOLDERS.get(key, resource[key])
        for key in sorted(shared_resource_attributes)
    }
    records: list[dict[str, Any]] = [
        {
            "signal_kind": "resource",
            "record_kind": "resource",
            "name": "otel.resource",
            "session_id": scenario["session_id"],
            "turn_id": None,
            "conversation_id": None,
            "chat_id": None,
            "instrument_kind": None,
            "attributes": comparable_resource,
            "value": None,
            "unit": None,
            "status": None,
            "producer": dict(producer),
        }
    ]
    for observation in observations:
        if observation.type == "diagnostic":
            continue
        if observation.type == "metric" and observation.name not in shared_metrics:
            continue
        if observation.type == "event" and observation.name not in shared_events:
            continue
        if observation.type == "span.start":
            if observation.span_kind not in shared_span_kinds or not observation.key:
                continue
            span_starts[observation.key] = dict(observation.attributes)
        if observation.type == "span.end" and (
            not observation.key or observation.key not in span_starts
        ):
            raise ParityFixtureError(f"span end has no shared start: {observation.key}")
        if observation.type not in {"metric", "event", "span.start", "span.end"}:
            continue

        attributes = dict(observation.attributes)
        if observation.type == "span.end":
            attributes = {**span_starts[observation.key], **attributes}
        comparable = comparable_attributes(attributes)
        signal_kind = (
            "span" if observation.type.startswith("span.") else observation.type
        )
        record_kind = {
            "metric": "data_point",
            "event": "event",
            "span.start": "span_start",
            "span.end": "span_end",
        }[observation.type]
        turn_id = comparable.get("agentic.turn.id") or comparable.get("gen_ai.chat.id")
        records.append(
            {
                "signal_kind": signal_kind,
                "record_kind": record_kind,
                "name": observation.name,
                "session_id": scenario["session_id"],
                "turn_id": turn_id,
                "conversation_id": comparable.get("gen_ai.conversation.id"),
                "chat_id": comparable.get("gen_ai.chat.id"),
                "instrument_kind": observation.metric_kind,
                "attributes": comparable,
                "value": observation.value,
                "unit": observation.unit,
                "status": observation.status,
                "producer": dict(producer),
            }
        )
    return normalized_records(records)


def metric_summary(observations: list[Any]) -> dict[str, dict[str, float | int]]:
    values: dict[str, list[float | int]] = defaultdict(list)
    for observation in observations:
        if observation.type == "metric":
            values[observation.name].append(observation.value)
    return {
        name: {"count": len(items), "sum": sum(items)} for name, items in values.items()
    }


class OpenCodeSignalParityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.capability_map = json.loads(CAPABILITY_MAP.read_text(encoding="utf-8"))
        cls.opencode_fixture = load_fixture(OPENCODE_FIXTURE)
        cls.scenarios = [load_scenario(path) for path in SCENARIO_PATHS]

    def test_opencode_fixture_pins_version_revision_and_strict_schema(self) -> None:
        fixture = self.opencode_fixture
        self.assertEqual(fixture["schema_version"], PARITY_SCHEMA_VERSION)
        self.assertEqual(fixture["producer"]["runtime"], "opencode")
        self.assertEqual(fixture["producer"]["version"], "0.3.3")
        self.assertEqual(
            fixture["producer"]["source_revision"],
            "ec2b20937ef159de64376fb5e73ab92903304855",
        )
        self.assertTrue(fixture["records"])
        self.assertTrue(
            all(
                record["producer"]["source_revision"]
                == fixture["producer"]["source_revision"]
                for record in fixture["records"]
            )
        )

    def test_capability_map_has_exact_shared_and_gap_taxonomy(self) -> None:
        data = self.capability_map
        self.assertEqual(data["schema_version"], "aivision.signal-capability-map.v1")
        self.assertEqual(len(data["shared"]["metrics"]), 9)
        self.assertEqual(
            set(data["shared"]["events"]),
            {
                "session_start",
                "session_end",
                "user_prompt",
                "api_request",
                "tool_call",
                "tool_result",
            },
        )
        self.assertEqual(data["hermes_only"]["metrics"], ["agent.tool_call.duration"])
        self.assertEqual(
            set(data["hermes_only"]["events"]),
            {
                "subagent_spawned",
                "subagent_ended",
            },
        )
        self.assertEqual(data["declared_unemitted"]["events"], ["tool_decision"])
        self.assertEqual(
            data["opencode_only_host_gap"]["events"], ["compaction_result"]
        )
        self.assertEqual(len(data["shared"]["resource_attributes"]), 9)

    def test_opencode_fixture_contains_resource_records_for_both_sessions(self) -> None:
        resources = [
            record
            for record in self.opencode_fixture["records"]
            if record["signal_kind"] == "resource"
        ]
        self.assertEqual(
            {record["session_id"] for record in resources},
            {
                "parity-success",
                "parity-error",
            },
        )
        self.assertTrue(
            all(
                set(record["attributes"])
                == set(self.capability_map["shared"]["resource_attributes"])
                for record in resources
            )
        )

    def test_versioned_scenarios_produce_valid_raw_transcripts(self) -> None:
        # The JSON scenarios are tracked; runtime transcripts are deliberately
        # ignored. A fresh checkout must not require a developer's local capture.
        with tempfile.TemporaryDirectory() as tmp:
            for scenario in self.scenarios:
                path = Path(tmp) / f"session_{scenario['session_id']}_v1.jsonl"
                path.write_text(transcript_text(scenario), encoding="utf-8")
                records = read_raw_transcript(path)
                self.assertEqual(len(records), len(scenario["hooks"]))
                self.assertEqual({record["sessionId"] for record in records}, {scenario["session_id"]})
                self.assertEqual([record["hook"] for record in records], [item["hook"] for item in scenario["hooks"]])

    def test_live_and_replay_match_frozen_opencode_shared_fact_snapshot(self) -> None:
        expected_by_session: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for record in self.opencode_fixture["records"]:
            expected_by_session[record["session_id"]].append(record)
        for scenario in self.scenarios:
            live = run_live(scenario)
            replay = run_replay(scenario)
            live_records = parity_records(
                scenario,
                live,
                HERMES_PRODUCER,
                self.capability_map,
                actual_live_resource(scenario),
            )
            replay_records = parity_records(
                scenario,
                replay,
                HERMES_PRODUCER,
                self.capability_map,
                actual_replay_resource(scenario),
            )
            self.assertEqual(live_records, replay_records)
            diff = compare_records(
                expected_by_session[scenario["session_id"]], live_records
            )
            self.assertEqual(diff["added"], [])
            self.assertEqual(diff["removed"], [])
            # The frozen peer has no native API-call correlation attribute.
            # Hermes' old normalized cache zeros also have no reportedness
            # proof. Preserve the frozen reference and the full actual records;
            # check all source values, with exactly those zero buckets unknown.
            ambiguous_cache_fields = {
                "gen_ai.usage.cache_read.input_tokens", "agentic.usage.cache_read_tokens",
                "gen_ai.usage.cache_creation.input_tokens", "agentic.usage.cache_write_tokens",
                "gen_ai.usage.details.cache_read_input_tokens", "gen_ai.usage.details.cache_creation_input_tokens",
            }
            canonical_cache_aliases = {
                "gen_ai.usage.cache_read.input_tokens": "gen_ai.usage.details.cache_read_input_tokens",
                "gen_ai.usage.cache_creation.input_tokens": "gen_ai.usage.details.cache_creation_input_tokens",
            }
            for difference in diff["mismatched"]:
                expected = difference["expected"]
                actual = difference["actual"]
                self.assertEqual(
                    {key: value for key, value in expected.items() if key not in {"producer", "attributes"}},
                    {key: value for key, value in actual.items() if key not in {"producer", "attributes"}},
                )
                unknown = set(expected["attributes"]) - set(actual["attributes"])
                missing_directions = {f"gen_ai.usage.{direction}_tokens" for direction in ("input", "output")
                    if expected["attributes"].get(f"agentic.usage.{direction}_tokens.missing") == "true"
                    and actual["attributes"].get(f"agentic.usage.{direction}_tokens.missing") == "true"}
                self.assertTrue(unknown <= ambiguous_cache_fields | missing_directions)
                self.assertTrue(all(type(expected["attributes"][key]) is int and expected["attributes"][key] == 0 for key in unknown))
                known = {key: value for key, value in expected["attributes"].items() if key not in unknown}
                self.assertEqual(known, {key: actual["attributes"][key] for key in known})
                additional = set(actual["attributes"]) - set(expected["attributes"])
                self.assertIn("agentic.model_call.id", additional)
                self.assertTrue(additional <= {"agentic.model_call.id", *canonical_cache_aliases})
                for key in additional - {"agentic.model_call.id"}:
                    original = expected["attributes"][canonical_cache_aliases[key]]
                    self.assertIs(type(original), int)
                    self.assertGreater(original, 0)
                    self.assertEqual(actual["attributes"][key], original)
            self.assertEqual(diff["capability_gap_accepted"], [])

    def test_hermes_native_call_ids_are_independently_verified_in_live_and_replay(self) -> None:
        for scenario in self.scenarios:
            expected = [item["raw"]["api_request_id"] for item in scenario["hooks"] if item["hook"] == "pre_api_request"]
            for observations in (run_live(scenario), run_replay(scenario)):
                for kind in ("span.start", "span.end", "event"):
                    actual = [item.attributes.get("agentic.model_call.id") for item in observations
                              if item.type == kind and (item.key or "").startswith("api:")]
                    self.assertEqual(actual, expected)

    def test_two_turn_metric_event_and_identity_contracts(self) -> None:
        for scenario in self.scenarios:
            observations = run_live(scenario)
            summary = metric_summary(observations)
            self.assertEqual(
                {
                    name: summary.get(name, {"count": 0, "sum": 0})
                    for name in scenario["expected"]["metrics"]
                },
                scenario["expected"]["metrics"],
            )
            event_counts = Counter(
                observation.name
                for observation in observations
                if observation.type == "event"
                and observation.name in self.capability_map["shared"]["events"]
            )
            self.assertEqual(dict(event_counts), scenario["expected"]["shared_events"])
            turn_starts = [
                item
                for item in observations
                if item.type == "span.start" and item.span_kind == "CHAT"
            ]
            self.assertEqual(
                [item.attributes["gen_ai.chat.id"] for item in turn_starts],
                [
                    "turn-1",
                    "turn-2",
                ],
            )
            self.assertEqual(
                {item.attributes["gen_ai.conversation.id"] for item in turn_starts},
                {
                    scenario["session_id"],
                },
            )
            event_names = {item.name for item in observations if item.type == "event"}
            self.assertFalse({"tool_decision", "compaction_result"} & event_names)

    def test_privacy_mode_preserves_parity_and_removes_canaries(self) -> None:
        scenario = self.scenarios[0]
        live = run_live(scenario, mode="privacy")
        replay = run_replay(scenario, mode="privacy")
        self.assertEqual(
            parity_records(
                scenario,
                live,
                HERMES_PRODUCER,
                self.capability_map,
                actual_live_resource(scenario, mode="privacy"),
            ),
            parity_records(
                scenario,
                replay,
                HERMES_PRODUCER,
                self.capability_map,
                actual_replay_resource(scenario, mode="privacy"),
            ),
        )
        self.assertNotIn(
            "CANARY_",
            canonical_json(
                [
                    {
                        "type": item.type,
                        "name": item.name,
                        "attributes": item.attributes,
                    }
                    for item in replay
                ]
            ),
        )

    def test_idless_zero_cache_privacy_fixture_is_end_to_end_and_fail_closed(
        self,
    ) -> None:
        scenario = load_scenario(COMBINED_EDGE_SCENARIO)
        self.assertEqual(scenario["collection_mode"], "privacy")
        for item in scenario["hooks"]:
            self.assertFalse(
                {"turn_id", "parent_turn_id", "task_id"} & set(item["raw"])
            )

        live = run_live(scenario, mode="privacy")
        replay = run_replay(scenario, mode="privacy")
        live_records = parity_records(
            scenario,
            live,
            HERMES_PRODUCER,
            self.capability_map,
            actual_live_resource(scenario, mode="privacy"),
        )
        replay_records = parity_records(
            scenario,
            replay,
            HERMES_PRODUCER,
            self.capability_map,
            actual_replay_resource(scenario, mode="privacy"),
        )
        self.assertEqual(live_records, replay_records)

        turn_ids = {
            record["turn_id"]
            for record in live_records
            if record["turn_id"] is not None
        }
        self.assertEqual(len(turn_ids), 1)
        self.assertRegex(next(iter(turn_ids)), r"^canonical-turn-[0-9a-f]{24}$")

        summary = metric_summary(live)
        self.assertEqual(
            {
                name: summary.get(name, {"count": 0, "sum": 0})
                for name in scenario["expected"]["metrics"]
            },
            scenario["expected"]["metrics"],
        )
        self.assertNotIn(
            "CANARY_",
            canonical_json(
                [
                    {
                        "type": item.type,
                        "name": item.name,
                        "attributes": item.attributes,
                    }
                    for item in replay
                ]
            ),
        )

    def test_oracle_fails_closed_on_malformed_record(self) -> None:
        with self.assertRaises(ParityFixtureError):
            normalized_records([{"signal_kind": "metric"}])


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

HERMES_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERMES_ROOT))
sys.path.insert(0, str(HERMES_ROOT / "hermes-core"))

from aivision_hermes_core.privacy import REDACTED  # noqa: E402
from aivision_hermes_core.semantic import SemanticObservation  # noqa: E402

from observ_hermes.config import (  # noqa: E402
    AntIdentity,
    ObservHermesConfig,
    OtelExporterConfig,
    SignalEndpointConfig,
)
from observ_hermes.pipeline import get_pipeline  # noqa: E402
from observ_hermes.telemetry import (  # noqa: E402
    build_resource_attributes,
    init_telemetry,
    project_observation,
)
from tests.replay_test_support import write_raw_transcript  # noqa: E402
from tests.test_protocol_spike import (  # noqa: E402
    ProtocolCaptureHandler,
    ProtocolCaptureServer,
)

CANARY = "PRIVACY-CANARY-7f84c2"
SECRET = "Bearer privacy-secret-token"
GENERIC_CREDENTIALS = (
    "-----BEGIN PRIVATE KEY-----\ngeneric-private-key\n-----END PRIVATE KEY-----",
    "Cookie: session=generic-cookie-secret; theme=dark",
    "Set-Cookie: refresh=generic-set-cookie-secret; HttpOnly",
    "ghp_abcdefghijklmnopqrstuvwxyz123456",
    "xoxb-ci-fixture",
    "AKIAABCDEFGHIJKLMNOP",
    "AIzaabcdefghijklmnopqrstuvwxyz123456789",
    "sk_test_fixtureonly00000",
)
GENERIC_SECRET_FRAGMENTS = (
    "generic-private-key",
    "generic-cookie-secret",
    "generic-set-cookie-secret",
    "ghp_",
    "xoxb-",
    "AKIA",
    "AIza",
    "sk_test_",
)
OVERSIZED_CREDENTIALS = (
    "-----BEGIN PRIVATE KEY-----\n" + "P" * 70_000 + "\n-----END PRIVATE KEY-----",
    "Cookie: session=" + "C" * 40_000 + "; Secure",
    "Bearer " + "B" * 40_000,
    "ghp_" + "G" * 40_000,
)
OVERSIZED_SECRET_FRAGMENTS = (b"P" * 64, b"C" * 64, b"B" * 64, b"G" * 64)


def privacy_config(base: str, protocol: str, mode: str) -> ObservHermesConfig:
    return ObservHermesConfig(
        enable_telemetry=True,
        service_name=f"service-{CANARY}",
        collection_mode=mode,
        debug=True,
        ant=AntIdentity(
            username="static-user",
            uid="static-uid",
            agent_type="hermes",
            agent_product=f"product-{CANARY}",
            agent_instance=f"instance-{CANARY}",
            agent_env=f"env-{CANARY}",
            agent_scene=f"scene-{CANARY}",
        ),
        otel_exporter=OtelExporterConfig(
            protocol=protocol,
            headers={"authorization": SECRET},
            attributes={
                "ant.username": "static-user",
                "ant.uid": "static-uid",
                "custom.metadata": CANARY,
            },
            resource_attributes={"custom.resource": CANARY},
        ),
        traces_config=SignalEndpointConfig(endpoint=base, enabled=True),
        metrics_config=SignalEndpointConfig(endpoint=base, enabled=True),
        events_config=SignalEndpointConfig(endpoint=base, enabled=True),
        metric_export_interval=60_000,
        logs_export_interval=10,
    )


def emit_hostile_scenario(runtime: object) -> None:
    pipeline = get_pipeline(runtime)
    schema = {
        "telemetry_schema_version": "hermes.observer.v1",
        "session_id": "privacy-session",
    }
    pipeline.process(
        "on_session_start", {**schema, "model": "model", "platform": "cli"}
    )
    pipeline.process(
        "pre_llm_call",
        {
            **schema,
            "turn_id": "turn-1",
            "task_id": "task-1",
            "model": "model",
            "user_message": f"prompt {CANARY} {SECRET}\n"
            + "\n".join(GENERIC_CREDENTIALS),
            "conversation_history": [{"role": "system", "content": f"system {CANARY}"}],
        },
    )
    pipeline.process(
        "post_llm_call",
        {
            **schema,
            "turn_id": "turn-1",
            "task_id": "task-1",
            "model": "model",
            "assistant_response": f"output {CANARY}",
            "conversation_history": [],
        },
    )
    pipeline.process(
        "pre_api_request",
        {
            **schema,
            "turn_id": "turn-1",
            "task_id": "task-1",
            "api_request_id": "api-1",
            "model": "model",
            "provider": "provider",
            "base_url": f"https://{CANARY}.example",
            "request": {"messages": [CANARY], "authorization": SECRET},
            "started_at": 1_750_000_000.0,
        },
    )
    pipeline.process(
        "post_api_request",
        {
            **schema,
            "turn_id": "turn-1",
            "task_id": "task-1",
            "api_request_id": "api-1",
            "model": "model",
            "provider": "provider",
            "response": {"body": CANARY},
            "usage": {"input_tokens": 11, "output_tokens": 7},
            "api_duration": 0.25,
        },
    )
    pipeline.process(
        "pre_tool_call",
        {
            **schema,
            "turn_id": "turn-1",
            "task_id": "task-1",
            "tool_call_id": "tool-1",
            "tool_name": "read",
            "args": {"path": CANARY, "password": SECRET},
        },
    )
    pipeline.process(
        "post_tool_call",
        {
            **schema,
            "turn_id": "turn-1",
            "task_id": "task-1",
            "tool_call_id": "tool-1",
            "tool_name": "read",
            "duration_ms": 5,
            "status": "error",
            "error_type": "ToolError",
            "error_message": f"failed {CANARY}\n" + "\n".join(GENERIC_CREDENTIALS),
            "result": {"error": CANARY},
        },
    )
    pipeline.process(
        "subagent_start",
        {
            **schema,
            "turn_id": "turn-1",
            "task_id": "task-1",
            "child_session_id": "child-1",
            "child_role": "worker",
            "child_goal": f"goal {CANARY}",
        },
    )
    pipeline.process(
        "subagent_stop",
        {
            **schema,
            "turn_id": "turn-1",
            "task_id": "task-1",
            "child_session_id": "child-1",
            "child_role": "worker",
            "child_status": "completed",
            "child_summary": f"summary {CANARY}",
            "duration_ms": 10,
        },
    )
    pipeline.process(
        "on_session_end", {**schema, "turn_id": "turn-1", "completed": True}
    )
    pipeline.process("on_session_finalize", {**schema, "reason": f"done {CANARY}"})


class PrivacyBoundaryTests(unittest.TestCase):
    def test_privacy_json_removes_canaries_at_serialized_boundary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, ProtocolCaptureServer() as base:
            root = Path(tmp)
            runtime = init_telemetry(privacy_config(base, "http/json", "privacy"))
            emit_hostile_scenario(runtime)
            runtime.shutdown()
            self.assertFalse((root / "transcripts").exists())
            self.assertFalse((root / "replays").exists())

        rendered = "\n".join(
            bytes(record["body"]).decode("utf-8")
            for record in ProtocolCaptureHandler.records
        )
        self.assertNotIn(CANARY, rendered)
        self.assertNotIn(SECRET, rendered)
        self.assertIn("[REDACTED:sha256:", rendered)
        self.assertIn("privacy-session", rendered)
        self.assertIn("input_tokens", rendered)

    def test_privacy_protobuf_removes_canaries_at_wire_boundary(self) -> None:
        with ProtocolCaptureServer() as base:
            runtime = init_telemetry(privacy_config(base, "http/protobuf", "privacy"))
            emit_hostile_scenario(runtime)
            runtime.shutdown()
        payload = b"\n".join(
            bytes(record["body"]) for record in ProtocolCaptureHandler.records
        )
        self.assertNotIn(CANARY.encode(), payload)
        self.assertNotIn(SECRET.encode(), payload)
        self.assertIn(b"privacy-session", payload)

    def test_full_mode_keeps_content_but_redacts_generic_credentials_on_both_wires(
        self,
    ) -> None:
        for protocol in ("http/json", "http/protobuf"):
            with self.subTest(protocol=protocol), ProtocolCaptureServer() as base:
                runtime = init_telemetry(privacy_config(base, protocol, "full"))
                emit_hostile_scenario(runtime)
                runtime.shutdown()
            payload = b"\n".join(
                bytes(record["body"]) for record in ProtocolCaptureHandler.records
            )
            self.assertIn(CANARY.encode(), payload)
            self.assertNotIn(SECRET.encode(), payload)
            for credential in GENERIC_CREDENTIALS:
                self.assertNotIn(credential.encode(), payload)
            for fragment in GENERIC_SECRET_FRAGMENTS:
                self.assertNotIn(fragment.encode(), payload)

    def test_full_mode_replay_disk_redacts_credentials_in_generic_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            transcript = write_raw_transcript(
                Path(tmp),
                "credential-session",
                [
                    (
                        "post_tool_call",
                        {
                            "telemetry_schema_version": "hermes.observer.v1",
                            "session_id": "credential-session",
                            "turn_id": "turn-1",
                            "tool_call_id": "tool-1",
                            "tool_name": "read",
                            "status": "error",
                            "error_message": "\n".join(GENERIC_CREDENTIALS),
                            "result": {"message": "\n".join(GENERIC_CREDENTIALS)},
                        },
                    )
                ],
                collection_mode="full",
            )
            rendered = transcript.read_text()
        for credential in GENERIC_CREDENTIALS:
            self.assertNotIn(credential, rendered)
        for fragment in GENERIC_SECRET_FRAGMENTS:
            self.assertNotIn(fragment, rendered)
        self.assertIn('"collectionMode": "full"', rendered)

    def test_full_mode_replay_disk_fully_redacts_oversized_credentials(self) -> None:
        hostile = "\n".join(OVERSIZED_CREDENTIALS)
        with tempfile.TemporaryDirectory() as tmp:
            transcript = write_raw_transcript(
                Path(tmp),
                "oversized-credential-session",
                [
                    (
                        "post_tool_call",
                        {
                            "telemetry_schema_version": "hermes.observer.v1",
                            "session_id": "oversized-credential-session",
                            "turn_id": "turn-1",
                            "tool_call_id": "tool-1",
                            "tool_name": "read",
                            "status": "error",
                            "error_message": hostile,
                            "result": {"message": hostile},
                        },
                    )
                ],
                collection_mode="full",
            )
            payload = transcript.read_bytes()
        for credential in OVERSIZED_CREDENTIALS:
            self.assertNotIn(credential.encode(), payload)
        for fragment in OVERSIZED_SECRET_FRAGMENTS:
            self.assertNotIn(fragment, payload)
        self.assertIn(REDACTED.encode(), payload)

    def test_full_mode_live_wires_fully_redact_oversized_credentials(self) -> None:
        hostile = "\n".join(OVERSIZED_CREDENTIALS)
        for protocol in ("http/json", "http/protobuf"):
            with self.subTest(protocol=protocol), ProtocolCaptureServer() as base:
                runtime = init_telemetry(privacy_config(base, protocol, "full"))
                pipeline = get_pipeline(runtime)
                pipeline.process(
                    "on_session_start",
                    {
                        "telemetry_schema_version": "hermes.observer.v1",
                        "session_id": "oversized-wire-session",
                        "model": "model",
                        "platform": "cli",
                    },
                )
                pipeline.process(
                    "pre_llm_call",
                    {
                        "telemetry_schema_version": "hermes.observer.v1",
                        "session_id": "oversized-wire-session",
                        "turn_id": "turn-1",
                        "task_id": "task-1",
                        "model": "model",
                        "user_message": hostile,
                        "conversation_history": [],
                    },
                )
                runtime.shutdown()
            payload = b"\n".join(
                bytes(record["body"]) for record in ProtocolCaptureHandler.records
            )
            for fragment in OVERSIZED_SECRET_FRAGMENTS:
                self.assertNotIn(fragment, payload)
            self.assertIn(REDACTED.encode(), payload)

    def test_dynamic_identity_is_resolved_after_privacy_projection_per_emission(
        self,
    ) -> None:
        config = privacy_config("http://127.0.0.1:1", "http/json", "privacy")
        config.dynamic_username = True
        config.dynamic_uid = True
        observation = SemanticObservation(
            type="metric",
            name="agent.test.count",
            timestamp="2025-06-15T15:06:40+00:00",
            metric_kind="counter",
            value=1,
            attributes={"ant.username": "stale", "user.id": "stale"},
        )
        first = project_observation(
            config,
            observation,
            {"AIVISION_ANT_USERNAME": "dynamic-1", "AIVISION_ANT_UID": "uid-1"},
        )
        second = project_observation(
            config,
            observation,
            {"AIVISION_ANT_USERNAME": "dynamic-2", "AIVISION_ANT_UID": "uid-2"},
        )
        self.assertEqual(first.attributes["ant.username"], "dynamic-1")
        self.assertEqual(first.attributes["ant.uid"], "uid-1")
        self.assertEqual(first.attributes["user.id"], "uid-1")
        self.assertEqual(second.attributes["ant.username"], "dynamic-2")
        self.assertEqual(second.attributes["user.id"], "uid-2")

    def test_static_identity_ignores_emission_time_environment_changes(self) -> None:
        config = privacy_config("http://127.0.0.1:1", "http/json", "full")
        observation = SemanticObservation(
            type="metric",
            name="agent.test.count",
            timestamp="2025-06-15T15:06:40+00:00",
            metric_kind="counter",
            value=1,
        )
        first = project_observation(
            config,
            observation,
            {"AIVISION_ANT_USERNAME": "dynamic-1", "AIVISION_ANT_UID": "uid-1"},
        )
        second = project_observation(
            config,
            observation,
            {"AIVISION_ANT_USERNAME": "dynamic-2", "AIVISION_ANT_UID": "uid-2"},
        )
        self.assertEqual(first.attributes["ant.username"], "static-user")
        self.assertEqual(first.attributes["user.id"], "static-uid")
        self.assertEqual(first.attributes, second.attributes)

    def test_privacy_resource_strips_identity_and_hides_configured_metadata(
        self,
    ) -> None:
        config = privacy_config("http://127.0.0.1:1", "http/json", "privacy")
        config.otel_exporter.resource_attributes["username"] = "must-not-be-resource"
        resource = build_resource_attributes(config)
        rendered = json.dumps(resource)
        self.assertNotIn(CANARY, rendered)
        self.assertNotIn("ant.username", resource)
        self.assertNotIn("ant.uid", resource)
        self.assertNotIn("username", resource)
        self.assertEqual(resource["ant.agent.type"], "hermes")

    def test_final_identity_and_resource_values_redact_credentials_on_both_wires(
        self,
    ) -> None:
        credential = "Bearer identity-secret"
        for protocol in ("http/json", "http/protobuf"):
            with self.subTest(protocol=protocol), ProtocolCaptureServer() as base:
                config = privacy_config(base, protocol, "full")
                config.service_name = credential
                config.ant.agent_type = credential
                config.ant.agent_product = credential
                config.ant.agent_instance = credential
                config.ant.agent_env = credential
                config.ant.agent_scene = credential
                config.otel_exporter.resource_attributes["custom.resource"] = credential
                config.otel_exporter.attributes["ant.username"] = credential
                config.otel_exporter.attributes["ant.uid"] = credential
                config.dynamic_username = True
                config.dynamic_uid = True
                with patch.dict(
                    os.environ,
                    {
                        "AIVISION_ANT_USERNAME": credential,
                        "AIVISION_ANT_UID": credential,
                    },
                    clear=False,
                ):
                    runtime = init_telemetry(config)
                    emit_hostile_scenario(runtime)
                    runtime.shutdown()
            payload = b"\n".join(
                bytes(record["body"]) for record in ProtocolCaptureHandler.records
            )
            self.assertNotIn(b"identity-secret", payload)
            self.assertIn(b"[REDACTED]", payload)


if __name__ == "__main__":
    unittest.main()

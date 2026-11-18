from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from observ_hermes.canonical import GENESIS_CHECKSUM, canonical_checksum
from observ_hermes.normalize import CanonicalNormalizer, classify_error, classify_tool


class NormalizeTests(unittest.TestCase):
    def test_api_input_and_output_preserve_each_explicit_media_type(self):
        import base64
        data = base64.b64encode(b"media fixture" * 2000).decode()
        for role, hook, field in (
            ("user", "pre_api_request", "request_messages"),
            ("assistant", "post_api_request", "assistant_message"),
        ):
            for mime in ("image/png", "audio/wav", "video/webm", "application/pdf"):
                with self.subTest(role=role, mime=mime):
                    message = {"role": role, "content": [
                        {"type": "text", "text": "Keep the caption"},
                        {"type": "media", "mime_type": mime, "data": data},
                    ]}
                    raw = {"session_id": "media-session", "model": "fixture/model", field: [message] if role == "user" else message}
                    event = CanonicalNormalizer().normalize(hook, raw, timestamp=1_750_000_000.0)
                    stored = event.payload[field][0] if role == "user" else event.payload[field]
                    self.assertEqual(stored["content"][0]["text"], "Keep the caption")
                    self.assertEqual(stored["content"][1]["source"]["media_type"], mime)
                    self.assertEqual(stored["content"][1]["source"]["data"], data)
                    private = CanonicalNormalizer("privacy").normalize(hook, raw, timestamp=1_750_000_000.0)
                    self.assertNotIn(data, private.to_json())

    def test_structured_tool_schema_keeps_the_event_checksum_chain(self):
        normalizer = CanonicalNormalizer()
        schema = {"type": ["integer", "null"], "title": "Nullable tool output"}
        first = normalizer.normalize("post_tool_call", {
            "telemetry_schema_version": "hermes.observer.v1",
            "session_id": "schema-session",
            "tool_name": "schema",
            "result": schema,
        }, timestamp=1_750_000_000.0)
        second = normalizer.normalize("on_session_finalize", {
            "telemetry_schema_version": "hermes.observer.v1",
            "session_id": "schema-session",
        }, timestamp=1_750_000_001.0)
        self.assertEqual(first.payload["result"], schema)
        self.assertEqual(second.sequence, 2)
        self.assertEqual(second.previous_checksum, first.checksum)

    def test_all_pinned_v0182_hook_envelopes_normalize(self) -> None:
        fixture = json.loads(
            (Path(__file__).parent / "fixtures/hooks/v0.18.2/observer_envelopes.json").read_text()
        )
        for index, captured in enumerate(fixture["hooks"]):
            with self.subTest(hook=captured["hook"]):
                event = CanonicalNormalizer().normalize(
                    captured["hook"],
                    captured["envelope"],
                    timestamp=1_750_000_000.0 + index,
                    provenance={"source": captured["source"]},
                )
                self.assertEqual(event.hook, captured["hook"])
                self.assertEqual(event.source_schema_version, "hermes.observer.v1")
                self.assertNotIn("telemetry_schema_version", event.payload)
                self.assertEqual(event.provenance["source"], captured["source"])

    def test_allowlist_determinism_and_chain(self) -> None:
        raw = {
            "telemetry_schema_version": "hermes.observer.v1",
            "session_id": "session-1",
            "turn_id": "turn-1",
            "model": "model",
            "platform": "cli",
            "unknown": "must-drop",
        }
        first_normalizer = CanonicalNormalizer()
        first = first_normalizer.normalize("on_session_start", raw, timestamp=1_750_000_000.0)
        second_normalizer = CanonicalNormalizer()
        same = second_normalizer.normalize("on_session_start", raw, timestamp=1_750_000_000.0)
        self.assertEqual(first, same)
        self.assertEqual(first.previous_checksum, GENESIS_CHECKSUM)
        self.assertEqual(first.checksum, canonical_checksum(first.to_dict()))
        self.assertNotIn("unknown", first.payload)

        next_event = first_normalizer.normalize("on_session_finalize", {
            "telemetry_schema_version": "hermes.observer.v1",
            "session_id": "session-1",
            "reason": "shutdown",
        }, timestamp=1_750_000_001.0)
        self.assertEqual(next_event.sequence, 2)
        self.assertEqual(next_event.previous_checksum, first.checksum)

    def test_privacy_mode_redacts_content_but_preserves_model(self) -> None:
        event = CanonicalNormalizer("privacy").normalize("pre_llm_call", {
            "telemetry_schema_version": "hermes.observer.v1",
            "session_id": "session-1",
            "model": "model",
            "user_message": "private prompt",
            "conversation_history": [{"role": "user", "content": "private prompt"}],
        }, timestamp=1_750_000_000.0)
        self.assertEqual(event.payload["model"], "model")
        self.assertNotIn("private prompt", event.to_json())
        self.assertTrue(event.projection["redacted"])

    def test_rejects_unknown_hook_schema_and_empty_session(self) -> None:
        normalizer = CanonicalNormalizer()
        with self.assertRaisesRegex(ValueError, "unsupported Hermes hook"):
            normalizer.normalize("future_hook", {"session_id": "s"})
        with self.assertRaisesRegex(ValueError, "observer schema"):
            normalizer.normalize("on_session_start", {
                "session_id": "s", "telemetry_schema_version": "hermes.observer.v2"
            })
        with self.assertRaisesRegex(ValueError, "non-empty session"):
            normalizer.normalize("on_session_start", {})

    def test_explicit_invalid_source_times_are_not_replaced_with_wall_clock_time(self) -> None:
        raw = {
            "telemetry_schema_version": "hermes.observer.v1",
            "session_id": "session-1",
        }
        for timestamp in ("not-a-time", "2025-01-01T00:00:00", -1, True):
            with self.subTest(timestamp=timestamp), self.assertRaisesRegex(ValueError, "source timestamp"):
                CanonicalNormalizer().normalize("on_session_start", raw, timestamp=timestamp)

        with self.assertRaisesRegex(ValueError, "source timestamp"):
            CanonicalNormalizer().normalize(
                "on_session_start",
                {**raw, "started_at": "not-a-time"},
            )

    def test_tool_and_error_classification_are_bounded(self) -> None:
        self.assertEqual(classify_tool("mcp__server__read"), "mcp")
        self.assertEqual(classify_tool("Skill"), "skill")
        self.assertEqual(classify_tool("Bash"), "builtin")
        self.assertEqual(classify_error({"type": "RateLimitError"}, status_code=429), "rate_limit")
        self.assertEqual(classify_error("socket timeout"), "timeout")
        self.assertEqual(classify_error(None), "other")

    def test_nonzero_structured_tool_result_overrides_hermes_ok_wrapper(self) -> None:
        event = CanonicalNormalizer().normalize("post_tool_call", {
            "telemetry_schema_version": "hermes.observer.v1",
            "session_id": "session-1",
            "turn_id": "turn-1",
            "tool_name": "terminal",
            "status": "ok",
            "result": json.dumps({
                "output": "cat: missing: No such file or directory",
                "exit_code": 1,
                "error": None,
            }),
        }, timestamp=1_750_000_000.0)
        self.assertEqual(event.payload["status"], "error")
        self.assertEqual(event.payload["error_type"], "tool_error")
        self.assertIn("No such file", event.payload["error_message"])

    def test_zero_structured_tool_result_preserves_hermes_status(self) -> None:
        event = CanonicalNormalizer().normalize("post_tool_call", {
            "telemetry_schema_version": "hermes.observer.v1",
            "session_id": "session-1",
            "tool_name": "terminal",
            "status": "ok",
            "result": {"output": "done", "exit_code": 0, "error": None},
        }, timestamp=1_750_000_000.0)
        self.assertEqual(event.payload["status"], "ok")
        self.assertNotIn("error_type", event.payload)


if __name__ == "__main__":
    unittest.main()

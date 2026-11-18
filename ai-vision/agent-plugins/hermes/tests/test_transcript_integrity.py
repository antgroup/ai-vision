from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from hermes_replay.capture import (
    GENESIS_CHECKSUM,
    RawEnvelopeWriter,
    RawTranscriptError,
    _checksum,
    read_raw_transcript,
)
from hermes_replay.replay import replay_transcript

from tests.replay_test_support import representative_hooks, write_raw_transcript


class TranscriptIntegrityTests(unittest.TestCase):
    def write_valid(self, root: Path, session_id: str = "session/similar") -> Path:
        return write_raw_transcript(
            root, session_id, representative_hooks(session_id)[:2]
        )

    @staticmethod
    def rewrite(path: Path, records: list[dict]) -> None:
        previous = GENESIS_CHECKSUM
        for sequence, record in enumerate(records, 1):
            record["integrity"] = {
                "sequence": sequence,
                "previousChecksum": previous,
            }
            checksum = _checksum(record)
            record["integrity"]["checksum"] = checksum
            previous = checksum
        path.write_text(
            "".join(json.dumps(record, sort_keys=True) + "\n" for record in records)
        )

    def test_filename_chain_manifest_and_permissions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "transcripts"
            path = self.write_valid(root)
            self.assertRegex(
                path.name, r"^session_session_similar_[0-9a-f]{12}\.jsonl$"
            )
            verified = read_raw_transcript(path)
            self.assertEqual(
                [event["integrity"]["sequence"] for event in verified], [1, 2]
            )
            if os.name == "posix":
                self.assertEqual(root.stat().st_mode & 0o777, 0o700)
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_similar_ids_do_not_collide(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = RawEnvelopeWriter(root, "session/a", collection_mode="full")
            second = RawEnvelopeWriter(root, "session_a", collection_mode="full")
            self.assertNotEqual(first.path, second.path)
            first.close()
            second.close()

    def test_missing_duplicate_reorder_mutation_and_manifest_tampering_are_rejected(
        self,
    ) -> None:
        mutations = ("missing", "duplicate", "reorder", "payload")
        for mutation in mutations:
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as tmp:
                path = self.write_valid(Path(tmp))
                records = [json.loads(line) for line in path.read_text().splitlines()]
                if mutation == "missing":
                    records = records[1:]
                elif mutation == "duplicate":
                    records.append(records[-1])
                elif mutation == "reorder":
                    records.reverse()
                elif mutation == "payload":
                    records[0]["rawInput"]["model"] = "changed"
                path.write_text(
                    "".join(
                        json.dumps(record, sort_keys=True) + "\n" for record in records
                    )
                )
                with self.assertRaises(RawTranscriptError):
                    read_raw_transcript(path)

    def test_unknown_schema_and_partial_final_line_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = self.write_valid(Path(tmp))
            path.write_text(path.read_text().rstrip("\n"))
            with self.assertRaisesRegex(RawTranscriptError, "partial final"):
                read_raw_transcript(path)

        with tempfile.TemporaryDirectory() as tmp:
            path = self.write_valid(Path(tmp))
            records = [json.loads(line) for line in path.read_text().splitlines()]
            records[0]["schemaVersion"] = "hermes.replay.raw.v2"
            path.write_text(json.dumps(records[0]) + "\n")
            with self.assertRaisesRegex(RawTranscriptError, "schema"):
                read_raw_transcript(path)

    def test_strict_raw_schema_and_cross_envelope_consistency_are_required(
        self,
    ) -> None:
        mutations = {
            "identity": lambda records: records[0].__setitem__("plugin", "other"),
            "unknown field": lambda records: records[0].__setitem__("extra", True),
            "hook": lambda records: records[0].__setitem__("hook", "unknown_hook"),
            "timestamp": lambda records: records[0].__setitem__(
                "capturedAt", "not-a-time"
            ),
            "timestamp timezone": lambda records: records[0].__setitem__(
                "capturedAt", "2026-08-10T12:00:00"
            ),
            "session": lambda records: records[1].__setitem__(
                "sessionId", "other-session"
            ),
            "mode": lambda records: records[1]["redaction"].__setitem__(
                "collectionMode", "privacy"
            ),
            "observer schema": lambda records: records[0]["rawInput"].__setitem__(
                "telemetry_schema_version", "other.schema"
            ),
            "redaction": lambda records: records[0]["redaction"].pop("contentRedacted"),
        }
        for label, mutate in mutations.items():
            with self.subTest(label=label), tempfile.TemporaryDirectory() as tmp:
                path = self.write_valid(Path(tmp))
                records = [json.loads(line) for line in path.read_text().splitlines()]
                mutate(records)
                self.rewrite(path, records)
                with self.assertRaises(RawTranscriptError):
                    read_raw_transcript(path)

    def test_recomputed_checksum_cannot_bypass_raw_input_bindings(self) -> None:
        mutations = {
            "raw session": lambda record: record["rawInput"].__setitem__(
                "session_id", "other-session"
            ),
            "correlation": lambda record: record["correlation"].__setitem__(
                "session_id", "other-session"
            ),
            "context": lambda record: record.__setitem__(
                "rawContext", {"unexpected": True}
            ),
            "output": lambda record: record.__setitem__("rawOutput", "fabricated"),
            "error": lambda record: record.__setitem__("rawError", "fabricated"),
            "nested field": lambda record: record["rawInput"].__setitem__(
                "future_unpinned_field", True
            ),
            "wrong-hook correlation": lambda record: record["rawInput"].__setitem__(
                "parent_session_id", record["sessionId"]
            ),
        }
        for label, mutate in mutations.items():
            with self.subTest(label=label), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                path = self.write_valid(root)
                records = [json.loads(line) for line in path.read_text().splitlines()]
                mutate(records[0])
                self.rewrite(path, records)
                output = root / "replays"
                with self.assertRaises(RawTranscriptError):
                    replay_transcript(path, output)
                self.assertFalse(output.exists())

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = self.write_valid(root)
            records = [json.loads(line) for line in path.read_text().splitlines()]
            records[-1]["rawInput"]["future_unpinned_field"] = True
            self.rewrite(path, records)
            output = root / "replays"
            with self.assertRaises(RawTranscriptError):
                replay_transcript(path, output)
            self.assertFalse(output.exists())

    def test_integrity_metadata_types_are_required_before_output_creation(self) -> None:
        mutations = {
            "boolean sequence": lambda records: records[0]["integrity"].__setitem__(
                "sequence", True
            ),
            "zero sequence": lambda records: records[0]["integrity"].__setitem__(
                "sequence", 0
            ),
            "uppercase checksum": lambda records: records[0]["integrity"].__setitem__(
                "checksum", "A" * 64
            ),
            "non-hex checksum": lambda records: records[0]["integrity"].__setitem__(
                "checksum", "g" * 64
            ),
            "short checksum": lambda records: records[0]["integrity"].__setitem__(
                "checksum", "0" * 63
            ),
            "invalid previous checksum": lambda records: records[0][
                "integrity"
            ].__setitem__("previousChecksum", "G" * 64),
        }
        for label, mutate in mutations.items():
            with self.subTest(label=label), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                path = self.write_valid(root)
                records = [json.loads(line) for line in path.read_text().splitlines()]
                mutate(records)
                if label == "boolean sequence":
                    records[0]["integrity"].pop("checksum")
                    records[0]["integrity"]["checksum"] = _checksum(records[0])
                path.write_text(
                    "".join(
                        json.dumps(record, sort_keys=True) + "\n" for record in records
                    )
                )
                output = root / "replays"
                with self.assertRaises(RawTranscriptError):
                    replay_transcript(path, output)
                self.assertFalse(output.exists())

    def test_privacy_markers_must_exactly_match_canonical_shape_before_output_creation(
        self,
    ) -> None:
        mutations = {
            "trailing plaintext": "[REDACTED:sha256:0123456789ab] leaked",
            "uppercase digest": "[REDACTED:sha256:0123456789AB]",
            "malformed digest": "[REDACTED:sha256:0123456789ag]",
            "short digest": "[REDACTED:sha256:0123456789a]",
            "long digest": "[REDACTED:sha256:0123456789abc]",
        }
        for label, marker in mutations.items():
            with self.subTest(label=label), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                session_id = "privacy-marker-session"
                path = write_raw_transcript(
                    root,
                    session_id,
                    [
                        (
                            "pre_llm_call",
                            {
                                "telemetry_schema_version": "hermes.observer.v1",
                                "session_id": session_id,
                                "turn_id": "turn-1",
                                "user_message": "private prompt",
                            },
                        )
                    ],
                    collection_mode="privacy",
                )
                records = [json.loads(line) for line in path.read_text().splitlines()]
                records[0]["rawInput"]["user_message"] = marker
                self.rewrite(path, records)
                output = root / "replays"
                with self.assertRaisesRegex(
                    RawTranscriptError, "privacy redaction marker"
                ):
                    replay_transcript(path, output)
                self.assertFalse(output.exists())

    def test_pinned_hook_field_types_are_required_before_output_creation(self) -> None:
        mutations = {
            "string": lambda record: record["rawInput"].__setitem__(
                "model", {"bad": True}
            ),
            "boolean": lambda record: record["rawInput"].__setitem__(
                "completed", "true"
            ),
            "number": lambda record: record["rawInput"].__setitem__(
                "duration_ms", True
            ),
            "array": lambda record: record["rawInput"].__setitem__(
                "conversation_history", {"role": "user"}
            ),
            "object": lambda record: record["rawInput"].__setitem__("request", []),
        }
        hooks = {
            "string": ("on_session_start", {"model": "model"}),
            "boolean": ("on_session_end", {"completed": True}),
            "number": ("post_tool_call", {"tool_name": "read", "duration_ms": 1}),
            "array": ("pre_llm_call", {"conversation_history": []}),
            "object": ("pre_api_request", {"request": {"method": "POST"}}),
        }
        for label, mutate in mutations.items():
            with self.subTest(label=label), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                hook, payload = hooks[label]
                path = write_raw_transcript(
                    root,
                    "typed-session",
                    [
                        (
                            hook,
                            {
                                "telemetry_schema_version": "hermes.observer.v1",
                                "session_id": "typed-session",
                                **payload,
                            },
                        )
                    ],
                )
                records = [json.loads(line) for line in path.read_text().splitlines()]
                mutate(records[0])
                self.rewrite(path, records)
                output = root / "replays"
                with self.assertRaises(RawTranscriptError):
                    replay_transcript(path, output)
                self.assertFalse(output.exists())

    def test_v0182_optional_raw_fields_are_accepted_and_bound(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            session_id = "fixture-session"
            path = write_raw_transcript(
                root,
                session_id,
                [
                    (
                        "pre_api_request",
                        {
                            "telemetry_schema_version": "hermes.observer.v1",
                            "session_id": session_id,
                            "turn_id": "turn-1",
                            "api_request_id": "api-1",
                            "conversation_history": [],
                            "middleware_trace": {"source": "v0.18.2"},
                        },
                    )
                ],
            )
            record = read_raw_transcript(path)[0]
            self.assertEqual(record["rawInput"]["conversation_history"], [])
            self.assertEqual(
                record["rawInput"]["middleware_trace"], {"source": "v0.18.2"}
            )

    def test_hook_specific_output_and_error_are_copied_from_raw_input(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            session_id = "binding-session"
            schema = {
                "telemetry_schema_version": "hermes.observer.v1",
                "session_id": session_id,
                "turn_id": "turn-1",
                "api_request_id": "api-1",
            }
            path = write_raw_transcript(
                Path(tmp),
                session_id,
                [
                    ("post_api_request", {**schema, "response": {"id": "response-1"}}),
                    ("api_request_error", {**schema, "error": {"message": "failed"}}),
                ],
            )
            response, error = read_raw_transcript(path)
            self.assertEqual(response["rawOutput"], response["rawInput"]["response"])
            self.assertEqual(error["rawError"], error["rawInput"]["error"])


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from hermes_replay.replay import replay_suffix, replay_transcript
from hermes_replay.report import TIMESTAMP_FIELDS, post_files, read_jsonl, write_shifted

from tests.replay_test_support import (
    HERMES_ROOT,
    representative_hooks,
    write_raw_transcript,
)


def create_transcript(directory: Path, session_id: str) -> Path:
    return write_raw_transcript(directory, session_id, representative_hooks(session_id))


def create_reset_transcript(directory: Path, session_id: str) -> Path:
    schema = {
        "telemetry_schema_version": "hermes.observer.v1",
        "session_id": session_id,
    }
    return write_raw_transcript(
        directory,
        session_id,
        [
            (
                "on_session_reset",
                {**schema, "platform": "cli", "reason": "new_session"},
            ),
            (
                "on_session_finalize",
                {**schema, "platform": "cli", "reason": "shutdown"},
            ),
        ],
    )


def without_timestamps(value):
    if isinstance(value, dict):
        return {
            key: without_timestamps(child)
            for key, child in value.items()
            if key not in TIMESTAMP_FIELDS
        }
    if isinstance(value, list):
        return [without_timestamps(child) for child in value]
    return value


class ReplayCliTests(unittest.TestCase):
    def test_replay_suffix_is_stable_when_the_same_fixture_is_copied(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first_dir = root / "first"
            second_dir = root / "second"
            first_dir.mkdir()
            second_dir.mkdir()
            first = create_transcript(first_dir, "stable-suffix")
            second = second_dir / first.name
            second.write_bytes(first.read_bytes())
            self.assertEqual(replay_suffix(first), replay_suffix(second))

    def test_valid_replay_is_transactional_and_directory_sessions_stay_separate(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            transcripts = root / "transcripts"
            transcripts.mkdir()
            first = create_transcript(transcripts, "session-a")
            second = create_transcript(transcripts, "session-b")
            replay = root / "replays"
            first_outputs = replay_transcript(first, replay)
            second_outputs = replay_transcript(second, replay)
            self.assertEqual(set(first_outputs), {"span", "event", "metric"})
            self.assertEqual(set(second_outputs), {"span", "event", "metric"})
            self.assertEqual(len(list(replay.glob("spans_*.jsonl"))), 2)
            self.assertEqual(len(list(replay.glob("events_*.jsonl"))), 2)
            self.assertEqual(len(list(replay.glob("metrics_*.jsonl"))), 2)
            self.assertFalse(
                {path.name for path in first_outputs.values()}
                & {path.name for path in second_outputs.values()}
            )

    def test_replay_otlp_uses_the_shared_nine_field_resource(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            outputs = replay_transcript(
                create_transcript(root, "resource"), root / "replays"
            )
            expected = {
                "agentic.collection.mode",
                "ant.agent.env",
                "ant.agent.instance",
                "ant.agent.product",
                "ant.agent.scene",
                "ant.agent.type",
                "deployment.environment",
                "service.name",
                "service.version",
            }
            observed: list[set[str]] = []
            for output in outputs.values():
                for line in output.read_text(encoding="utf-8").splitlines():
                    request = json.loads(line)
                    groups = next(
                        request[key]
                        for key in ("resourceSpans", "resourceLogs", "resourceMetrics")
                        if key in request
                    )
                    observed.extend(
                        {item["key"] for item in group["resource"]["attributes"]}
                        for group in groups
                    )
            self.assertTrue(observed)
            self.assertTrue(all(attributes == expected for attributes in observed))

    def test_corrupt_transcript_exits_nonzero_before_output(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            transcript = create_transcript(root, "corrupt")
            lines = transcript.read_text().splitlines()
            record = json.loads(lines[1])
            record["rawInput"]["user_message"] = "mutated"
            lines[1] = json.dumps(record)
            transcript.write_text("\n".join(lines) + "\n")
            replay = root / "replays"
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "hermes_replay.replay",
                    str(transcript),
                    "--otel",
                    str(replay),
                ],
                cwd=HERMES_ROOT,
                env={
                    **os.environ,
                    "PYTHONPATH": os.pathsep.join(
                        [
                            str(HERMES_ROOT / "hermes-replay"),
                            str(HERMES_ROOT / "hermes-core"),
                        ]
                    ),
                },
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("checksum", result.stderr.casefold())
            self.assertFalse(list(replay.glob("*.jsonl")) if replay.exists() else [])

    def test_reset_only_transcript_is_diagnostic_without_fabricated_event(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            transcript = create_reset_transcript(root, "reset-only")
            outputs = replay_transcript(
                transcript,
                root / "replays",
            )
            self.assertEqual(set(outputs), {"diagnostic"})

    def test_report_posts_each_signal_and_shared_shift_changes_only_timestamps(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            transcript = create_transcript(root, "report")
            replay = root / "replays"
            outputs = replay_transcript(
                transcript,
                replay,
            )
            files = sorted(outputs.values())
            shifted_dir = root / "shifted"
            shifted = write_shifted(
                files, shifted_dir, target_unix_nano=1_900_000_000_000_000_000
            )
            for original, updated in zip(files, shifted):
                before = read_jsonl(original)
                after = read_jsonl(updated)
                self.assertEqual(without_timestamps(before), without_timestamps(after))
                self.assertNotEqual(before, after)
            requests = []

            class Response:
                status = 200

                def __enter__(self):
                    return self

                def __exit__(self, *_args):
                    return None

                def read(self):
                    return b""

            def capture(request, **_kwargs):
                requests.append(request)
                return Response()

            with patch(
                "hermes_replay.report.urllib.request.urlopen", side_effect=capture
            ):
                posted, failed = post_files(
                    files, base_url="http://127.0.0.1:4319", shift=True
                )
            self.assertEqual(failed, 0)
            self.assertEqual(posted, sum(len(read_jsonl(path)) for path in files))
            self.assertEqual(
                {request.full_url for request in requests},
                {
                    "http://127.0.0.1:4319/v1/traces",
                    "http://127.0.0.1:4319/v1/logs",
                    "http://127.0.0.1:4319/v1/metrics",
                },
            )
            self.assertTrue(
                all(set(request.headers) == {"Content-type"} for request in requests)
            )

    def test_report_rejects_remote_or_credential_bearing_destinations(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "spans_test.jsonl"
            output.write_text('{"resourceSpans": []}\n')
            for endpoint in (
                "https://127.0.0.1:4319",
                "http://localhost:4319",
                "http://127.0.0.1:4318",
                "http://user:secret@127.0.0.1:4319",
                "http://127.0.0.1:4319/v1/traces",
                "http://127.0.0.1:4319?authorization=secret",
            ):
                with self.subTest(endpoint=endpoint), self.assertRaises(ValueError):
                    post_files([output], base_url=endpoint)


if __name__ == "__main__":
    unittest.main()

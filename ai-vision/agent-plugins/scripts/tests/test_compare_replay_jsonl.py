from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPO = Path(__file__).resolve().parents[2]
COMPARATOR = REPO / "scripts" / "compare_replay_jsonl.py"


def write_jsonl(path: Path, records: list[object], *, trailing_blank: bool = False) -> None:
    content = "".join(json.dumps(record, sort_keys=True) + "\n" for record in records)
    if trailing_blank:
        content += "\n"
    path.write_text(content, encoding="utf-8")


def run_comparator(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(COMPARATOR), *args],
        cwd=REPO,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


class CompareReplayJsonlTests(unittest.TestCase):
    def test_empty_replay_directories_are_equal(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            baseline = root / "baseline"
            candidate = root / "candidate"
            baseline.mkdir()
            candidate.mkdir()

            result = run_comparator(
                "--baseline",
                str(baseline),
                "--candidate",
                str(candidate),
            )

            self.assertEqual(result.returncode, 0, result.stderr)

    def test_replay_directory_file_names_are_part_of_the_contract(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            baseline = root / "baseline"
            candidate = root / "candidate"
            baseline.mkdir()
            candidate.mkdir()
            write_jsonl(baseline / "spans_a.jsonl", [{"record": 1}])
            write_jsonl(candidate / "spans_b.jsonl", [{"record": 1}])

            result = run_comparator(
                "--baseline",
                str(baseline),
                "--candidate",
                str(candidate),
            )

            self.assertEqual(result.returncode, 1)
            self.assertIn("missing candidate", result.stderr)
            self.assertIn("extra candidate", result.stderr)

    def test_compares_each_jsonl_root_and_ignores_object_order(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            baseline = root / "baseline.jsonl"
            candidate = root / "candidate.jsonl"
            write_jsonl(baseline, [{"a": 1, "b": {"x": True}}, ["one", 2]])
            candidate.write_text(
                '{"b":{"x":true},"a":1}\n["one",2]\n\n', encoding="utf-8"
            )

            result = run_comparator(
                "--baseline",
                str(baseline),
                "--candidate",
                str(candidate),
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("JSONL records match", result.stdout)

    def test_returns_one_for_semantic_difference(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            baseline = root / "baseline.jsonl"
            candidate = root / "candidate.jsonl"
            write_jsonl(baseline, [{"metadata": {"clock": 1}, "name": "same"}])
            write_jsonl(candidate, [{"metadata": {"clock": 2}, "name": "same"}])

            result = run_comparator(
                "--baseline",
                str(baseline),
                "--candidate",
                str(candidate),
            )

            self.assertEqual(result.returncode, 1)
            self.assertIn("/metadata/clock", result.stderr)

    def test_record_order_is_significant(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            baseline = root / "baseline"
            candidate = root / "candidate"
            baseline.mkdir()
            candidate.mkdir()
            write_jsonl(baseline / "spans_a.jsonl", [{"record": 1}, {"record": 2}])
            write_jsonl(candidate / "spans_a.jsonl", [{"record": 2}, {"record": 1}])

            result = run_comparator(
                "--baseline",
                str(baseline),
                "--candidate",
                str(candidate),
            )

            self.assertEqual(result.returncode, 1)
            self.assertIn("record 1/1", result.stderr)

    def test_ignore_pointer_missing_on_both_sides_is_a_noop(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            baseline = root / "baseline"
            candidate = root / "candidate"
            baseline.mkdir()
            candidate.mkdir()
            record = {"metadata": {"stable": True}}
            write_jsonl(baseline / "spans_a.jsonl", [record])
            write_jsonl(candidate / "spans_a.jsonl", [record])

            result = run_comparator(
                "--baseline",
                str(baseline),
                "--candidate",
                str(candidate),
                "--ignore-pointer",
                "/metadata/optional",
            )

            self.assertEqual(result.returncode, 0, result.stderr)

    def test_repeatable_rfc6901_ignores_support_escaped_tokens(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            baseline = root / "baseline.jsonl"
            candidate = root / "candidate.jsonl"
            write_jsonl(
                baseline,
                [
                    {
                        "metadata": {
                            "a/b~c": "old",
                            "timestamp": 1,
                            "nested": {"value": "old"},
                        }
                    }
                ],
            )
            write_jsonl(
                candidate,
                [
                    {
                        "metadata": {
                            "a/b~c": "new",
                            "timestamp": 2,
                            "nested": {"value": "new"},
                        }
                    }
                ],
            )

            result = run_comparator(
                "--baseline",
                str(baseline),
                "--candidate",
                str(candidate),
                "--ignore-pointer",
                "/metadata/a~1b~0c",
                "--ignore-pointer",
                "/metadata/timestamp",
                "--ignore-pointer",
                "/metadata/nested/value",
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("EQUAL", result.stdout)

    def test_ignore_is_not_allowed_to_hide_one_sided_field_presence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            baseline = root / "baseline"
            candidate = root / "candidate"
            baseline.mkdir()
            candidate.mkdir()
            write_jsonl(baseline / "spans_a.jsonl", [{"metadata": {"clock": 1}}])
            write_jsonl(candidate / "spans_a.jsonl", [{"metadata": {}}])

            result = run_comparator(
                "--baseline",
                str(baseline),
                "--candidate",
                str(candidate),
                "--ignore-pointer",
                "/metadata/clock",
            )

            self.assertEqual(result.returncode, 1)
            self.assertIn("metadata", result.stderr)

    def test_array_ignore_masks_value_without_shifting_array(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            baseline = root / "baseline.jsonl"
            candidate = root / "candidate.jsonl"
            write_jsonl(baseline, [{"metadata": [{"clock": 1}, {"stable": True}]}])
            write_jsonl(candidate, [{"metadata": [{"clock": 2}, {"stable": True}]}])

            result = run_comparator(
                "--baseline",
                str(baseline),
                "--candidate",
                str(candidate),
                "--ignore-pointer",
                "/metadata/0/clock",
            )

            self.assertEqual(result.returncode, 0, result.stderr)

    def test_protected_segments_cannot_be_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            baseline = root / "baseline.jsonl"
            candidate = root / "candidate.jsonl"
            write_jsonl(baseline, [{"spans": [{"attributes": [{"value": 1}]}]}])
            write_jsonl(candidate, [{"spans": [{"attributes": [{"value": 2}]}]}])

            protected_segments = (
                "resourceSpans",
                "scopeSpans",
                "spans",
                "traceId",
                "spanId",
                "parentSpanId",
                "name",
                "kind",
                "status",
                "startTimeUnixNano",
                "endTimeUnixNano",
                "attributes",
                "events",
                "links",
                "messages",
                "token",
            )
            for pointer in tuple(f"/{segment}" for segment in protected_segments) + (
                "/spans/0/attributes/0/value",
            ):
                with self.subTest(pointer=pointer):
                    result = run_comparator(
                        "--baseline",
                        str(baseline),
                        "--candidate",
                        str(candidate),
                        "--ignore-pointer",
                        pointer,
                    )
                    self.assertEqual(result.returncode, 2)
                    self.assertIn("protected", result.stderr)

    def test_protected_values_still_compare_strictly(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            baseline = root / "baseline.jsonl"
            candidate = root / "candidate.jsonl"
            write_jsonl(baseline, [{"traceId": "a", "metadata": {"clock": 1}}])
            write_jsonl(candidate, [{"traceId": "b", "metadata": {"clock": 1}}])

            result = run_comparator(
                "--baseline",
                str(baseline),
                "--candidate",
                str(candidate),
                "--ignore-pointer",
                "/metadata/clock",
            )

            self.assertEqual(result.returncode, 1)
            self.assertIn("/traceId", result.stderr)

    def test_invalid_pointer_and_input_have_distinct_exit_codes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            baseline = root / "baseline.jsonl"
            candidate = root / "candidate.jsonl"
            write_jsonl(baseline, [{}])
            write_jsonl(candidate, [{}])

            invalid_pointer = run_comparator(
                "--baseline",
                str(baseline),
                "--candidate",
                str(candidate),
                "--ignore-pointer",
                "/metadata/bad~2escape",
            )
            self.assertEqual(invalid_pointer.returncode, 2)

            malformed = root / "malformed.jsonl"
            malformed.write_text('{"ok": true}\nnot-json\n', encoding="utf-8")
            input_error = run_comparator(
                "--baseline",
                str(malformed),
                "--candidate",
                str(candidate),
            )
            self.assertEqual(input_error.returncode, 2)
            self.assertIn("line 2", input_error.stderr)

    def test_missing_replay_path_has_input_error_exit_code(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            candidate = root / "candidate"
            candidate.mkdir()

            result = run_comparator(
                "--baseline",
                str(root / "missing"),
                "--candidate",
                str(candidate),
            )

            self.assertEqual(result.returncode, 3)
            self.assertIn("does not exist", result.stderr)

    def test_record_count_mismatch_is_content_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            baseline = root / "baseline.jsonl"
            candidate = root / "candidate.jsonl"
            write_jsonl(baseline, [{"record": 1}])
            write_jsonl(candidate, [{"record": 1}, {"record": 2}])

            result = run_comparator(
                "--baseline",
                str(baseline),
                "--candidate",
                str(candidate),
            )

            self.assertEqual(result.returncode, 1)
            self.assertIn("record count", result.stderr)


if __name__ == "__main__":
    unittest.main()

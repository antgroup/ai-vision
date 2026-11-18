from __future__ import annotations

import json
import importlib
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
finalize_manifest = importlib.import_module("golden_finalize").finalize_manifest


class GoldenManifestTests(unittest.TestCase):
    def test_finalizer_uses_artifacts_and_preserves_honest_skips(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            transcripts = root / "transcripts"
            replays = root / "replays"
            transcripts.mkdir()
            replays.mkdir()
            scenarios = []
            transcript_records = []
            validation_files = []
            for index, name in enumerate(("basic-llm", "tool-success", "tool-error", "multi-tool", "subagent"), 1):
                transcript = transcripts / f"session_{index}.jsonl"
                record = {
                    "hook": "post_tool_call",
                    "correlation": {
                        "tool_call_id": f"native-{index}",
                        **({"tool_call_id_synthetic": True} if index == 2 else {}),
                    },
                }
                transcript.write_text(json.dumps(record) + "\n", encoding="utf-8")
                replay = replays / f"spans_{transcript.stem}.jsonl"
                replay.write_text("{}\n", encoding="utf-8")
                validation_files.append({"file_path": str(replay), "signals": []})
                scenarios.append({"scenario": name, "status": "passed", "transcripts": [str(transcript)]})
                transcript_records.append(transcript)
            lifecycle_session = transcripts / "session_6.jsonl"
            lifecycle_session.write_text(
                "\n".join(json.dumps({"hook": hook, "correlation": {}}) for hook in (
                    "on_session_start", "on_session_finalize",
                )) + "\n",
                encoding="utf-8",
            )
            lifecycle = transcripts / "session_7.jsonl"
            lifecycle.write_text(json.dumps({"hook": "on_session_reset", "correlation": {}}) + "\n", encoding="utf-8")
            diagnostic = replays / f"diagnostics_{lifecycle.stem}.ndjson"
            diagnostic.write_text("{}\n", encoding="utf-8")
            (root / "provenance.json").write_text(json.dumps({
                "hermes_version": "Hermes Agent v0.18.2", "scenarios": scenarios,
            }), encoding="utf-8")
            validation = root / "validate.json"
            validation.write_text(json.dumps({
                "files": validation_files,
                "summary": {"violations": 0, "semconv_issues": 0, "pass": True},
            }), encoding="utf-8")
            plugin = root / "plugin.yaml"
            plugin.write_text('name: observ-hermes\nversion: "0.2.0"\n', encoding="utf-8")

            manifest = finalize_manifest(root, validation, plugin)
            self.assertTrue(manifest["complete"])
            self.assertEqual(manifest["plugin_version"], "0.2.0")
            self.assertEqual(manifest["tool_call_ids"], {"native": 4, "synthetic": 1})
            indexed = {entry["scenario"]: entry for entry in manifest["scenarios"]}
            self.assertEqual(indexed["reset/finalize"]["status"], "passed")
            self.assertEqual(
                indexed["reset/finalize"]["transcripts"],
                [str(lifecycle_session), str(lifecycle)],
            )
            self.assertTrue(indexed["reset/finalize"]["validation"]["pass"])
            self.assertEqual(indexed["api-error"]["status"], "skipped")
            self.assertIn("not authorized", indexed["api-error"]["skip_reason"])
            self.assertEqual(indexed["compaction"]["status"], "skipped")
            self.assertIn("no documented compaction hook", indexed["compaction"]["skip_reason"])


if __name__ == "__main__":
    unittest.main()

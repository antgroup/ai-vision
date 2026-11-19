"""Check the artifact and readback identities that qualify personal edition smoke."""

import gzip
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

from sample_trace import prepare_demo, trace_complete
from smoke import QualificationError, inspect, read_candidate, read_diagnosis, require_archive_parity


SOURCE = "a" * 40
BUILD = "123.2"
TAG = "v0.6.0-daily.20261119.2"


class CandidateTests(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.manifest_path = self.root / "all-in-one-manifest.json"
        self.raw = b"one synthetic Docker tar fixture"
        self.manifest = {
            "profile": "all-in-one", "schema_version": 1, "platform": "linux/amd64",
            "release_tag": TAG, "base_release": TAG, "image_id": "sha256:" + "b" * 64,
            "source_candidate": {
                "source_repository": "antgroup/ai-vision", "source_sha": SOURCE,
                "release_tag": TAG, "build_id": BUILD,
            },
            "tar_sha256": self.digest(self.raw),
            "agent_cli": {"archive": "aiv-linux-x64.tar.gz", "version": TAG[1:]},
        }
        self.manifest.update(self.archive("aiv-all-in-one-linux-amd64.tar.gz", gzip.compress(self.raw)))
        self.manifest.update({
            "zstd_" + key: value
            for key, value in self.archive("aiv-all-in-one-linux-amd64.tar.zst", b"zstd fixture").items()
        })
        self.manifest["agent_cli"].update(self.archive("aiv-linux-x64.tar.gz", b"cli fixture"))
        (self.root / "install-all-in-one.py").write_text("# installer fixture")
        (self.root / "release-manifest.json").write_text("{}")
        shutil.copyfile(Path(__file__).with_name("sample_trace.py"), self.root / "sample-trace.py")
        self.write_inventory()

    @staticmethod
    def digest(data):
        return hashlib.sha256(data).hexdigest()

    def archive(self, name, data):
        (self.root / name).write_bytes(data)
        return {"archive": name, "archive_sha256": self.digest(data), "archive_bytes": len(data)}

    def write_inventory(self):
        self.manifest_path.write_text(json.dumps(self.manifest))
        sums = self.root / "SHA256SUMS"
        sums.write_text("".join(
            f"{self.digest(path.read_bytes())}  {path.name}\n"
            for path in sorted(self.root.iterdir()) if path != sums
        ))

    def test_candidate_binds_exact_source_and_attempt(self):
        manifest, _ = read_candidate(self.root, "amd64", SOURCE, BUILD)
        self.assertEqual(manifest, self.manifest)
        for source, build in (("c" * 40, BUILD), (SOURCE, "123.1")):
            with self.subTest(source=source, build=build), self.assertRaisesRegex(
                QualificationError, "source, attempt and native architecture"
            ):
                read_candidate(self.root, "amd64", source, build)

    def test_candidate_cannot_use_the_other_platforms_cli(self):
        self.manifest["agent_cli"]["archive"] = "aiv-linux-arm64.tar.gz"
        self.write_inventory()
        with self.assertRaisesRegex(QualificationError, "version or architecture"):
            read_candidate(self.root, "amd64", SOURCE, BUILD)

    def test_candidate_rejects_corrupt_sample_bytes(self):
        (self.root / "sample-trace.py").write_text("# edited after freeze")
        with self.assertRaisesRegex(QualificationError, "checksum mismatch"):
            read_candidate(self.root, "amd64", SOURCE, BUILD)

    @unittest.skipUnless(shutil.which("zstd"), "zstd is required for compression parity")
    def test_both_compressions_restore_the_same_tar(self):
        archive = self.root / self.manifest["zstd_archive"]
        archive.write_bytes(subprocess.run(
            ["zstd", "-qc"], input=self.raw, capture_output=True, check=True
        ).stdout)
        require_archive_parity(self.root, self.manifest)
        archive.write_bytes(subprocess.run(
            ["zstd", "-qc"], input=b"different contents", capture_output=True, check=True
        ).stdout)
        with self.assertRaisesRegex(QualificationError, "zstd archive contains different"):
            require_archive_parity(self.root, self.manifest)


class DockerResourceTests(unittest.TestCase):
    @patch("smoke.docker", return_value="some-other-network\naiv-test-network-old")
    def test_missing_network_is_confirmed_without_inspecting_an_absent_name(self, docker):
        self.assertIsNone(inspect("network", "aiv-test-network", ".Labels", missing_ok=True))
        docker.assert_called_once_with(
            "network", "ls", "--format", "{{.Name}}", name="List Docker networks"
        )

    @patch("smoke.docker", side_effect=("aiv-test-container", '{"owner": "fixture"}'))
    def test_resource_found_in_inventory_is_inspected(self, docker):
        self.assertEqual(inspect("container", "aiv-test-container", ".Config.Labels", missing_ok=True),
                         {"owner": "fixture"})
        self.assertEqual(docker.call_args_list[0].args, ("container", "ls", "--all", "--format", "{{.Names}}"))
        self.assertEqual(docker.call_args_list[1].args[1], "inspect")

    @patch("smoke.docker", side_effect=QualificationError("Docker daemon is unavailable"))
    def test_network_inventory_failure_is_not_resource_absence(self, _docker):
        with self.assertRaisesRegex(QualificationError, "daemon is unavailable"):
            inspect("network", "aiv-test-network", ".Labels", missing_ok=True)


class ReadbackTests(unittest.TestCase):
    def test_demo_accepts_the_product_rule_identity(self):
        client = Mock()
        receipt = {
            "agent_uuid": "12345678-1234-1234-1234-1234567890ab",
            "diagnoser_uuid": "dg-a1b2c3d4", "trace_id": "a" * 32,
            "comparison_trace_id": "b" * 32,
        }
        client.api.return_value = receipt
        self.assertEqual(prepare_demo(client, "c" * 32), receipt)

    def test_trace_cannot_pass_on_partial_or_duplicate_spans(self):
        trace_id = "a" * 32
        spans = [{"trace_id": trace_id, "span_id": f"{index:016x}"} for index in (1, 2)]
        detail = {"traceId": trace_id, "meta": {"complete": True, "expectedSpanCount": 2}, "spans": spans}
        self.assertTrue(trace_complete(detail, trace_id, 2))
        self.assertFalse(trace_complete({**detail, "spans": spans[:1]}, trace_id, 2))
        self.assertFalse(trace_complete({**detail, "spans": spans[:1] * 2}, trace_id, 2))

    def test_diagnosis_must_persist_the_expected_tool_failure(self):
        client = Mock()
        job_id, task_id, record_id = "a" * 32, "b" * 32, "r-" + "c" * 16
        demo = {"trace_id": "d" * 32, "diagnoser_uuid": "e" * 32}
        receipt = {"job_uuid": job_id, "task_uuid": task_id}
        task = {"uuid": task_id, "job_uuid": job_id, "status": "succeeded"}
        listing = {"items": [{"uuid": record_id}], "total": 1}
        failure = {"name": "tool_success_rate", "verdict": "bad", "evidence": {"counts": {"failure": 1}}}
        record = {
            **receipt, "uuid": record_id, "target_id": demo["trace_id"], "diagnoser_uuid": demo["diagnoser_uuid"],
            "verdict": "bad", "execution": {"status": "succeeded"}, "metrics": [failure],
        }
        client.api.side_effect = [task, listing, record]
        self.assertEqual(read_diagnosis(client, "space", demo, receipt), record_id)
        client.api.side_effect = [task, listing, {**record, "target_id": "different trace"}]
        with self.assertRaisesRegex(QualificationError, "expected failing sample"):
            read_diagnosis(client, "space", demo, receipt)


if __name__ == "__main__":
    unittest.main()

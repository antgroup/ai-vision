"""Check the artifact and readback identities that qualify personal edition smoke."""

from contextlib import redirect_stdout
import gzip
import hashlib
import importlib.util
from io import BytesIO, StringIO
import json
from pathlib import Path
import shutil
import subprocess
import tarfile
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError

from sample_trace import Platform, main as sample_main, prepare_demo, trace_complete
from smoke import QualificationError, inspect, memory_request, read_candidate, read_diagnosis, require_archive_parity


SOURCE = "a" * 40
BUILD = "123.2"
TAG = "v0.6.0-daily.20261119.2"
INSTALLER_PATH = Path(__file__).with_name("install-all-in-one.py")
INSTALLER_SPEC = importlib.util.spec_from_file_location("personal_installer", INSTALLER_PATH)
installer = importlib.util.module_from_spec(INSTALLER_SPEC)
INSTALLER_SPEC.loader.exec_module(installer)


class InstallerImageIdentityTests(unittest.TestCase):
    target = "linux/arm64"
    runtime_id = "sha256:" + "d" * 64
    config = b'{"os":"linux","architecture":"arm64"}'
    digest = hashlib.sha256(config).hexdigest()
    config_path = f"blobs/sha256/{digest}"
    manifest = {"image_tag": "aiv-all-in-one:fixture", "image_id": "sha256:" + digest, "platform": target}

    def docker_save(self, manifest_path=None, content=None):
        data = BytesIO()
        layout = json.dumps([{"Config": manifest_path or self.config_path}]).encode()
        with tarfile.open(fileobj=data, mode="w") as archive:
            for name, raw in ((self.config_path, content or self.config), ("manifest.json", layout)):
                entry = tarfile.TarInfo(name)
                entry.size = len(raw)
                archive.addfile(entry, BytesIO(raw))
        data.seek(0)
        return data

    def test_config_id_uses_classic_fast_path(self):
        inspected = f"{self.manifest['image_id']} {self.target}"
        with patch.object(installer, "output", return_value=inspected), patch.object(installer.subprocess, "Popen") as save:
            self.assertEqual(installer.verified_image(self.manifest), self.manifest["image_id"])
            save.assert_not_called()

    def test_reload_can_reuse_a_previously_verified_immutable_id(self):
        inspected = f"{self.runtime_id} {self.target}"
        with patch.object(installer, "output", return_value=inspected), patch.object(installer.subprocess, "Popen") as save:
            self.assertEqual(installer.verified_image(self.manifest, known_id=self.runtime_id), self.runtime_id)
            save.assert_not_called()

    def test_containerd_verifies_the_config_and_uses_the_immutable_engine_id(self):
        with patch.object(installer, "output", return_value=f"{self.runtime_id} {self.target}"), \
             patch.object(installer.subprocess, "Popen") as save:
            process = save.return_value.__enter__.return_value
            process.stdout = self.docker_save()
            process.wait.return_value = 0
            self.assertEqual(installer.verified_image(self.manifest), self.runtime_id)
            save.assert_called_once_with(["docker", "image", "save", self.runtime_id], stdout=subprocess.PIPE)

    def test_rejects_export_with_wrong_config_reference_or_contents(self):
        for stream in (self.docker_save(manifest_path="other.json"), self.docker_save(content=b"other config")):
            with self.subTest(stream=stream):
                self.assertFalse(installer.saved_image_matches(stream, self.manifest["image_id"], self.target))
        self.assertFalse(installer.saved_image_matches(self.docker_save(), self.manifest["image_id"], "linux/amd64"))


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
            "image_tag": f"aiv-all-in-one:{TAG[1:]}-amd64",
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

    def test_stable_and_rc_candidate_use_the_exact_matching_cli_version(self):
        for tag in ("v0.6.0", "v0.6.0-rc.1"):
            with self.subTest(tag=tag):
                self.manifest.update(release_tag=tag, base_release=tag)
                self.manifest["source_candidate"]["release_tag"] = tag
                self.manifest["agent_cli"]["version"] = tag[1:]
                self.write_inventory()
                self.assertEqual(read_candidate(self.root, "amd64", SOURCE, BUILD)[0], self.manifest)

    def test_installer_binds_stable_and_rc_online_and_offline_bundles(self):
        for channel, tag in (("stable", "v0.6.0"), ("rc", "v0.6.0-rc.1")):
            with self.subTest(channel=channel):
                self.manifest.update(release_tag=tag, base_release=tag)
                self.manifest["source_candidate"]["release_tag"] = tag
                self.manifest["agent_cli"].update(version=tag[1:], binary_sha256="c" * 64)
                product = {**self.manifest["source_candidate"], "product_version": tag[1:], "channel": channel}
                (self.root / "release-manifest.json").write_text(json.dumps(product))
                self.write_inventory()

                def download(folder, selected, *names):
                    self.assertEqual(selected, tag)
                    for name in names:
                        shutil.copyfile(self.root / name, folder / name)

                with patch.object(installer, "download", side_effect=download), patch.object(
                    installer, "release_pages", side_effect=AssertionError("exact tags do not require discovery")
                ):
                    self.assertEqual(installer.resolve_release(tag, "linux/amd64"), tag)
                manifest, assets = installer.preview_manifest(self.root, None, "linux/amd64")
                selection = installer.select_cli(self.root, manifest, "linux/amd64", assets, online=False)
                self.assertEqual(selection["version"], tag[1:])

                (self.root / "release-manifest.json").write_text(json.dumps({**product, "channel": "daily"}))
                self.write_inventory()
                with self.assertRaisesRegex(ValueError, "source candidate differs"):
                    installer.preview_manifest(self.root, None, "linux/amd64")

    def test_installer_still_accepts_a_trusted_legacy_offline_bundle(self):
        self.manifest.update(profile="all-in-one-preview", release_tag=f"all-in-one-preview-{TAG}",
                             base_release=f"delivery-{TAG}")
        self.manifest["agent_cli"]["binary_sha256"] = "c" * 64
        (self.root / "release-manifest.json").unlink()
        self.write_inventory()
        manifest, assets = installer.preview_manifest(self.root, None, "linux/amd64")
        selection = installer.select_cli(self.root, manifest, "linux/amd64", assets, online=False)
        self.assertEqual(selection["version"], TAG[1:])

    def test_automatic_discovery_prefers_compatible_stable_over_newer_previews(self):
        target = "linux/amd64"
        uploaded = [{"name": item, "state": "uploaded"}
                    for item in (*installer.ASSETS[target], "install-all-in-one.py", "SHA256SUMS", "release-manifest.json")]
        releases = [
            {"tag_name": tag, "draft": False, "prerelease": prerelease, "published_at": f"2026-11-{day:02}T00:00:00Z",
             "id": day, "assets": uploaded}
            for tag, prerelease, day in (("v0.6.0", False, 21), ("v0.6.1-rc.1", True, 24), (TAG, True, 23))
        ]
        with patch.object(installer, "release_pages", return_value=[releases]):
            self.assertEqual(installer.resolve_release(None, target), "v0.6.0")
        with patch.object(installer, "release_pages", return_value=[releases[1:]]):
            self.assertEqual(installer.resolve_release(None, target), TAG)

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
    def test_memory_query_preserves_native_capability_error(self):
        client = Platform(5174)
        path = "/api/v2/spaces/fixture/labs/memory/observations"
        error = HTTPError(client.origin + path, 400, "Bad Request", {}, BytesIO(b'{"code":3011}'))
        with patch.object(client.request, "open", side_effect=error) as send:
            self.assertEqual(memory_request(client, path), (400, {"code": 3011}))
        self.assertEqual(send.call_args.args[0].full_url, client.origin + path)

    @patch("sample_trace.Platform")
    @patch("sample_trace.getpass.getpass", return_value="sample password")
    @patch("sample_trace.sys.stdin.isatty", return_value=True)
    @patch("builtins.input", side_effect=("y", "sample@example.invalid"))
    def test_sample_command_uses_the_personal_space_without_team_ownership(
        self, _input, _isatty, _password, platform,
    ):
        space_id, trace_id = "a" * 32, "b" * 32
        agent_id = "12345678-1234-1234-1234-1234567890ab"
        demo = {
            "agent_uuid": agent_id, "agent_product": "custom",
            "diagnoser_uuid": "dg-a1b2c3d4", "trace_id": trace_id,
            "comparison_trace_id": "c" * 32, "span_count": 1,
        }
        client = platform.return_value
        client.origin = Platform(5174).origin
        client.api.side_effect = [
            {"items": [
                {"type": 1, "space_id": space_id, "is_owner": False},
                {"type": 2, "space_id": "d" * 32, "is_owner": True},
            ]},
            demo,
            {"traceId": trace_id, "meta": {"complete": True, "expectedSpanCount": 1},
             "spans": [{"trace_id": trace_id, "span_id": "e" * 16}]},
        ]
        output = StringIO()
        with redirect_stdout(output):
            self.assertEqual(sample_main([]), 0)
        self.assertEqual(client.api.call_args_list[1].args, (f"/spaces/{space_id}/onboarding/demo",))
        self.assertIn(f"/trace?spaceId={space_id}&agent=custom&agent_uuid={agent_id}&traceId={trace_id}",
                      output.getvalue())

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

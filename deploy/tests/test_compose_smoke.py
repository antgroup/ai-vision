import argparse
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import MagicMock, patch
import urllib.error

import compose_smoke

from compose_smoke import (
    ComposeSmoke, IMAGE_SERVICES, image_overrides, organization_output, snapshot_assets, write_env,
)
from seekdb_deploy_smoke import SECRET_VARS


class ComposeSmokeTests(unittest.TestCase):
    def test_dependency_recovery_rejects_liveness_without_storage_readiness(self):
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            values = {key: "private" for key in (*SECRET_VARS, "SIGNOZ_USER_ROOT_PASSWORD")}
            values["SIGNOZ_PORT"] = "18091"
            smoke = ComposeSmoke(directory, directory, values,
                                 directory / "compose.yaml", directory / "private.env", external=False)
            smoke.containers = {"ingest-gateway": "gateway-fixture"}
            smoke.stop_services = lambda *args: None
            smoke.run = lambda *args: subprocess.CompletedProcess([], 0, '{"Running":true,"OOMKilled":false}')
            client = MagicMock()

            def response(url, **kwargs):
                if url.endswith("/api/v1/ready"):
                    raise urllib.error.HTTPError(url, 503, "storage unavailable", {}, None)
                result = MagicMock()
                result.__enter__.return_value.status = 200
                return result

            client.open.side_effect = response
            with patch.object(compose_smoke.urllib.request, "build_opener", return_value=client), \
                    patch.object(compose_smoke.time, "monotonic", side_effect=[0, 181]), \
                    patch.object(compose_smoke, "TraceCanary"):
                with self.assertRaisesRegex(AssertionError, "did not recover"):
                    smoke.recover_dependency(MagicMock(), "fixture")

    def test_bind_volume_preserves_compose_ownership_and_uses_private_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            values = {key: "private" for key in (*SECRET_VARS, "SIGNOZ_USER_ROOT_PASSWORD")}
            values.update(NAMESPACE="test-namespace", AIV_STATE_DIR=str(directory / "state"))
            smoke = ComposeSmoke(directory, directory, values, directory / "compose.yaml",
                                 directory / "private.env", external=False)
            smoke.volume_data = directory / "volumes"
            smoke.volume_data.mkdir()
            calls = []
            def run(label, command, **kwargs):
                calls.append(command)
                return subprocess.CompletedProcess(command, 1 if "inspect" in command else 0, "")
            smoke.run = run
            smoke.create_volume("test-data", {"com.docker.compose.volume": "database-data-0"})
            created = calls[-1]
            self.assertIn("com.docker.compose.volume=database-data-0", created)
            self.assertIn("com.docker.compose.project=test-namespace", created)
            self.assertIn("io.aivision.test-run=test-namespace", created)
            self.assertIn("type=none", created)
            device = next(value[len("device="):] for value in created if value.startswith("device="))
            self.assertEqual(Path(device).parent, smoke.volume_data)
            self.assertTrue(Path(device).is_dir())
            self.assertEqual(smoke.owned_volumes, ["test-data"])
            smoke.run = lambda *args, **kwargs: subprocess.CompletedProcess([], 0, "existing")
            with self.assertRaisesRegex(RuntimeError, "refusing to reuse"):
                smoke.create_volume("already-existing")
            self.assertEqual(smoke.owned_volumes, ["test-data"])

    def test_snapshot_preserves_relative_assets_when_source_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            source, runtime = directory / "source", directory / "runtime"
            for name, content in (("docker/compose.yaml", "../bootstrap"),
                                  ("bootstrap/seekdb.sh", "original bootstrap")):
                path = source / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content)
            compose = snapshot_assets(runtime, "compose.yaml", source)
            (source / "bootstrap/seekdb.sh").write_text("changed")
            self.assertEqual(compose.read_text(), "../bootstrap")
            self.assertFalse(compose.with_name("compose.py").exists())
            self.assertEqual((compose.parent.parent / "bootstrap/seekdb.sh").read_text(),
                             "original bootstrap")

    def test_lifecycle_uses_aiv_and_native_compose_without_host_python(self):
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            values = {key: "private" for key in (*SECRET_VARS, "SIGNOZ_USER_ROOT_PASSWORD")}
            compose, env = directory / "compose.yaml", directory / "private.env"
            smoke = ComposeSmoke(directory, directory, values, compose, env, external=False,
                                 cli=Path("/fixture/aiv"))
            self.assertEqual(smoke.lifecycle_command("up"), [
                "/fixture/aiv", "cast", "--target", "compose", "--compose-file", str(compose),
                "--compose-env-file", str(env)])
            self.assertEqual(smoke.lifecycle_command("down", "--purge"), [
                "/fixture/aiv", "destroy", "--target", "compose", "--compose-file", str(compose),
                "--compose-env-file", str(env), "-y", "--purge"])

    def test_org_output_accepts_django_stderr_before_bare_identifier(self):
        org = "12345678-1234-1234-1234-123456789abc"
        self.assertEqual(organization_output("INFO Django initialized\n" + org + "\n"), org)
        for output in ("", "INFO Django initialized\n", org + "\nnot an org"):
            with self.subTest(output=output), self.assertRaises(AssertionError):
                organization_output(output)

    def test_images_reject_unknown_roles_and_empty_refs(self):
        for value in ('{"typo": "local/test:dev"}', '{"backend": ""}', '{"backend": 42}', '[]'):
            with self.subTest(value=value), self.assertRaises(argparse.ArgumentTypeError):
                image_overrides(value)

    def test_image_files_and_inline_json_have_same_contract(self):
        images = {"backend": "local/backend:next"}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "images.json"
            path.write_text(json.dumps(images))
            self.assertEqual(image_overrides(str(path)), images)
            self.assertEqual(image_overrides(json.dumps(images)), images)

    def test_env_rewrite_preserves_private_permissions(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "private.env"
            write_env(path, {"SECRET_KEY": "private-value", "BE_IMAGE": "local/backend:first"})
            write_env(path, {"SECRET_KEY": "private-value", "BE_IMAGE": "local/backend:second"})
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertIn("second", path.read_text())
            self.assertNotIn("first", path.read_text())

    def test_container_discovery_does_not_rewrite_persistent_configuration(self):
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            values = {key: "private" for key in (*SECRET_VARS, "SIGNOZ_USER_ROOT_PASSWORD")}
            values.update(AGENTLENS_CONTAINER="stable-diagnosis", BE_CONTAINER="stable-backend")
            smoke = ComposeSmoke(directory, directory, values, directory / "compose.yaml",
                                 directory / "private.env", external=True)
            smoke.run = lambda *args, **kwargs: subprocess.CompletedProcess([], 0, "a" * 64 + "\n")
            smoke.discover("initial")
            self.assertEqual(values["AGENTLENS_CONTAINER"], "stable-diagnosis")
            self.assertEqual(values["BE_CONTAINER"], "stable-backend")
            self.assertEqual(set(smoke.containers), set(IMAGE_SERVICES.values()))


if __name__ == "__main__":
    unittest.main()

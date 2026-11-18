"""Offline checks for explicit CLI Compose acceptance assertions."""
import argparse
from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import compose_cli_smoke as smoke


def service(name, namespace="fixture"):
    return {"Id": name + "-id", "Config": {"Labels": {
        "com.docker.compose.project": namespace, "com.docker.compose.service": name,
    }}, "State": {"Running": True, "OOMKilled": False, "Health": {"Status": "healthy"}},
        "RestartCount": 0, "Mounts": []}


class ComposeCLISmokeTests(unittest.TestCase):
    def fixture(self, directory):
        values = {key: "private" for key in (*smoke.SECRET_VARS, "SIGNOZ_USER_ROOT_PASSWORD")}
        values.update(NAMESPACE="fixture", DOCKER_NETWORK="fixture-net")
        return smoke.ComposeCLISmoke(directory, directory, values, Path("/aiv"), False)

    def test_cleanup_does_not_claim_a_project_when_collision_validation_failed(self):
        with tempfile.TemporaryDirectory() as directory:
            instance = self.fixture(Path(directory))
            calls = []

            def run(label, command, **unused):
                calls.append(command)
                return subprocess.CompletedProcess(command, 0, "[]")

            instance.run = run
            self.assertEqual(instance.cleanup(), [])
            self.assertEqual(calls, [])

    def test_health_requires_compose_identity_and_native_health_for_every_service(self):
        valid = [service("backend"), service("frontend")]
        self.assertEqual(set(smoke.checked_services(valid, "fixture", ("backend", "frontend"))),
                         {"backend", "frontend"})
        variants = []
        for mutate in (lambda row: row["State"].update(Running=False),
                       lambda row: row["State"].update(OOMKilled=True),
                       lambda row: row["State"].pop("Health"),
                       lambda row: row["State"]["Health"].update(Status="unhealthy"),
                       lambda row: row.update(RestartCount=1),
                       lambda row: row["Config"]["Labels"].update({"com.docker.compose.project": "foreign"})):
            rows = deepcopy(valid)
            mutate(rows[0])
            variants.append(rows)
        variants.extend([valid[:1], [valid[0], valid[0], valid[1]]])
        for rows in variants:
            with self.subTest(rows=rows), self.assertRaises(AssertionError):
                smoke.checked_services(rows, "fixture", ("backend", "frontend"))

    def test_retention_identity_ignores_bootstrap_code_binds_and_tracks_metadata(self):
        containers = {"database": service("database"), "data-backend": service("data-backend")}
        containers["database"]["Mounts"] = [{"Type": "volume", "Destination": "/var/lib/seekdb", "Name": "data"}]
        containers["data-backend"]["Mounts"] = [
            {"Type": "bind", "Destination": "/var/lib/signoz", "Source": "/private/metadata"}]
        expected = smoke.persistent_mounts(containers)
        containers["database"]["Mounts"].append({"Type": "bind", "Destination": "/opt/aivision/bootstrap",
                                                "Source": "/private/replacement-bundle/bootstrap"})
        self.assertEqual(smoke.persistent_mounts(containers), expected)
        containers["data-backend"]["Mounts"][0]["Source"] = "/replacement/metadata"
        self.assertNotEqual(smoke.persistent_mounts(containers), expected)

    def test_saved_project_requires_private_matching_vars_and_bundle_ownership(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory).resolve()
            bundle = state / "compose/bundles" / ("a" * 64)
            bundle.mkdir(parents=True)
            values = {"SECRET_KEY": "literal-$TOKEN", "FE_IMAGE": "candidate/frontend"}
            for path, value in ((state / "vars.json", values),
                                (state / ".compose-cli.json", {"schema": 1, "bundle": str(bundle), "status": "ready"}),
                                (state / ".compose-state.json", {"namespace": "fixture", "state_dir": str(state)}),
                                (bundle / ".cli-vars.json", {"schema": 1, "namespace": "fixture", "state_dir": str(state), "vars": values})):
                path.write_text(json.dumps(value))
                path.chmod(0o600)
            (bundle / "compose.yaml").write_text("# fixture")
            self.assertEqual(smoke.saved_project(state, "fixture", "ready"), bundle)
            (state / "vars.json").write_text(json.dumps({"SECRET_KEY": "different"}))
            with self.assertRaisesRegex(AssertionError, "variables"):
                smoke.saved_project(state, "fixture", "ready")
            (state / "vars.json").write_text(json.dumps(values))
            (state / "vars.json").chmod(0o644)
            with self.assertRaisesRegex(AssertionError, "private"):
                smoke.saved_project(state, "fixture", "ready")

    def test_host_python_sentinels_fail_and_record_actual_interpreter_attempts(self):
        with tempfile.TemporaryDirectory() as directory:
            instance = self.fixture(Path(directory))
            self.assertEqual(instance.no_host_python()["host_interpreter_invocations"], 0)
            result = subprocess.run(["python3", "--version"], env=instance.env, capture_output=True)
            self.assertEqual(result.returncode, 99)
            with self.assertRaisesRegex(AssertionError, "host Python"):
                instance.no_host_python()

    def test_image_mapping_accepts_files_and_rejects_empty_unknown_values(self):
        expected = {"frontend": "local/frontend:next"}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "images.json"
            path.write_text(json.dumps(expected))
            self.assertEqual(smoke.image_mapping(str(path)), expected)
            self.assertEqual(smoke.image_mapping(json.dumps(expected)), expected)
        for value in ('[]', '{"unknown":"image"}', '{"frontend":""}', '{"frontend":12}'):
            with self.subTest(value=value), self.assertRaises(argparse.ArgumentTypeError):
                smoke.image_mapping(value)

    def test_runtime_cleanup_mounts_only_the_exact_owned_random_directory(self):
        namespace = "aiv-compose-cli-seekdb-0123456789ab"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            runtime = root / (namespace + "-random")
            runtime.mkdir(mode=0o700)
            command = smoke.private_runtime_cleanup_command(runtime, namespace, "trusted/diagnosis:candidate")
            self.assertEqual(command[:7], ["docker", "run", "--rm", "--network", "none", "--user", "0:0"])
            self.assertEqual(command.count("--mount"), 1)
            self.assertIn(f"type=bind,source={runtime},target=/cleanup-runtime", command)
            self.assertEqual(command[-3:], ["-c", smoke.RUNTIME_CLEANUP, "/cleanup-runtime"])
            for path in (root, root / "unowned-runtime"):
                with self.subTest(path=path), self.assertRaises(ValueError):
                    smoke.private_runtime_cleanup_command(path, namespace, "trusted/diagnosis:candidate")
            link = root / (namespace + "-linked")
            link.symlink_to(runtime, target_is_directory=True)
            with self.assertRaises(ValueError):
                smoke.private_runtime_cleanup_command(link, namespace, "trusted/diagnosis:candidate")

    def test_container_cleanup_unlinks_symlinks_without_following_external_targets(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runtime, outside = root / "runtime", root / "outside"
            (runtime / "nested").mkdir(parents=True)
            outside.mkdir()
            retained = outside / "retained.txt"
            retained.write_text("outside this test runtime")
            (runtime / "nested/scheduler.log").write_text("private task log")
            (runtime / "nested/external-link").symlink_to(outside, target_is_directory=True)
            (runtime / "file-link").symlink_to(retained)
            result = subprocess.run([sys.executable, "-c", smoke.RUNTIME_CLEANUP, str(runtime)],
                                    text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(list(runtime.iterdir()), [])
            self.assertEqual(retained.read_text(), "outside this test runtime")


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tests.replay_test_support import representative_hooks, write_raw_transcript

HERMES_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = HERMES_ROOT.parent


class MakeInstallTests(unittest.TestCase):
    @staticmethod
    def golden_fixture(root: Path) -> Path:
        """Build lifecycle inputs privately; ignored developer artifacts are not fixtures."""
        from hermes_replay.replay import replay_transcript
        from tests.test_opencode_signal_parity import SCENARIO_PATHS, load_scenario, transcript_text

        transcripts = root / "transcripts"
        transcripts.mkdir(parents=True)
        for source in SCENARIO_PATHS:
            scenario = load_scenario(source)
            transcript = transcripts / f"session_{scenario['session_id']}_v1.jsonl"
            transcript.write_text(transcript_text(scenario), encoding="utf-8")
            replay_transcript(transcript, root / "baselines")
        return root

    @staticmethod
    def snapshot_tree(root: Path) -> tuple[tuple[str, ...], dict[str, bytes]]:
        directories = tuple(
            sorted(
                str(path.relative_to(root)) for path in root.rglob("*") if path.is_dir()
            )
        )
        files = {
            str(path.relative_to(root)): path.read_bytes()
            for path in root.rglob("*")
            if path.is_file()
        }
        return directories, files

    @staticmethod
    def create_venv(root: Path, *, seed: bool = True) -> Path:
        environment = root / "venv"
        command = ["uv", "venv", "--python", sys.executable]
        if seed:
            command.append("--seed")
        command.append(str(environment))
        result = subprocess.run(
            command,
            text=True,
            capture_output=True,
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr)
        return environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")

    def test_all_standard_targets_are_resolvable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            variables = [
                f"TRANSCRIPT={root / 'session.jsonl'}",
                f"REPLAY_DIR={root / 'replays'}",
                f"TRANSCRIPT_DIR={root / 'transcripts'}",
                f"GOLDEN_DIR={root / 'golden'}",
                f"FILE={root / 'replays/spans.jsonl'}",
            ]
            for target in (
                "build",
                "replay",
                "replay-all",
                "validate",
                "validate-one",
                "report",
                "report-shift",
                "clean",
            ):
                result = subprocess.run(
                    ["make", "-n", target, *variables],
                    cwd=HERMES_ROOT,
                    text=True,
                    capture_output=True,
                )
                self.assertEqual(result.returncode, 0, f"{target}: {result.stderr}")

    def test_golden_check_rejects_stale_replays_when_fresh_generation_is_incomplete(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = self.golden_fixture(Path(tmp) / "fixture")
            golden = Path(tmp) / "golden"
            transcripts = golden / "transcripts"
            baselines = golden / "baselines"
            stale_replays = golden / "replays"
            transcripts.mkdir(parents=True)
            baselines.mkdir()
            stale_replays.mkdir()

            success_transcript = next(
                (fixture / "transcripts").glob("*parity-success*.jsonl")
            )
            shutil.copy2(success_transcript, transcripts / success_transcript.name)
            for baseline in (fixture / "baselines").glob("*.jsonl"):
                shutil.copy2(baseline, baselines / baseline.name)
                shutil.copy2(baseline, stale_replays / baseline.name)

            result = subprocess.run(
                [
                    "make",
                    "golden-check",
                    f"GOLDEN_DIR={golden}",
                    f"PYTHON={sys.executable}",
                ],
                cwd=HERMES_ROOT,
                env={
                    **os.environ,
                    "PYTHONPATH": os.pathsep.join(
                        [
                            str(HERMES_ROOT / "hermes-replay"),
                            str(HERMES_ROOT / "hermes-core"),
                            str(HERMES_ROOT),
                        ]
                    ),
                    "PYTHONDONTWRITEBYTECODE": "1",
                },
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("Missing fresh replay", result.stderr)

    @unittest.skipUnless(os.name == "posix", "POSIX directory modes are required")
    def test_golden_check_preserves_existing_directory_modes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = self.golden_fixture(Path(tmp) / "fixture")
            # This test covers Make's filesystem lifecycle. Compare every
            # generated byte independently without requiring a Rust build in
            # a Python unit-test checkout; normative validation has its own gate.
            comparator = Path(tmp) / "compare.py"
            comparator.write_text(
                "from pathlib import Path\nimport sys\n"
                "assert sys.argv[1] == 'compare' and sys.argv[3] == '--replay'\n"
                "def contents(root):\n"
                "    return {p.name: p.read_bytes() for p in Path(root).glob('*.jsonl')}\n"
                "expected = contents(sys.argv[2])\n"
                "assert expected and expected == contents(sys.argv[4])\n"
            )
            golden = Path(tmp) / "golden"
            shutil.copytree(fixture, golden)
            replays = golden / "replays"
            replays.mkdir(exist_ok=True)
            golden.chmod(0o755)
            replays.chmod(0o751)

            result = subprocess.run(
                [
                    "make",
                    "golden-check",
                    f"GOLDEN_DIR={golden}",
                    f"PYTHON={sys.executable}",
                    f"WEAVER_GUARD={sys.executable} {comparator}",
                ],
                cwd=HERMES_ROOT,
                env={
                    **os.environ,
                    "PYTHONPATH": os.pathsep.join(
                        [
                            str(HERMES_ROOT / "hermes-replay"),
                            str(HERMES_ROOT / "hermes-core"),
                            str(HERMES_ROOT),
                        ]
                    ),
                    "PYTHONDONTWRITEBYTECODE": "1",
                },
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(golden.stat().st_mode & 0o777, 0o755)
            self.assertEqual(replays.stat().st_mode & 0o777, 0o751)

    def test_replay_build_preserves_non_wheel_content_in_overridden_directory(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            build_dir = Path(tmp) / "operator-selected"
            sentinels = (
                build_dir / "keep.txt",
                build_dir / "core/keep.txt",
                build_dir / "replay/keep.txt",
            )
            for sentinel in sentinels:
                sentinel.parent.mkdir(parents=True, exist_ok=True)
                sentinel.write_text("preserve\n")
            if os.name == "posix":
                build_dir.chmod(0o755)
                (build_dir / "core").chmod(0o751)
                (build_dir / "replay").chmod(0o750)

            result = subprocess.run(
                ["make", "replay-plugin-build", f"REPLAY_BUILD_DIR={build_dir}"],
                cwd=HERMES_ROOT,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(all(path.read_text() == "preserve\n" for path in sentinels))
            self.assertTrue(list((build_dir / "core").glob("*.whl")))
            self.assertTrue(list((build_dir / "replay").glob("*.whl")))
            if os.name == "posix":
                self.assertEqual(build_dir.stat().st_mode & 0o777, 0o755)
                self.assertEqual((build_dir / "core").stat().st_mode & 0o777, 0o751)
                self.assertEqual((build_dir / "replay").stat().st_mode & 0o777, 0o750)

    def test_replay_build_normalizes_empty_override_and_rejects_unsafe_root(
        self,
    ) -> None:
        dry_run = subprocess.run(
            ["make", "-n", "replay-plugin-build", "REPLAY_BUILD_DIR="],
            cwd=HERMES_ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(dry_run.returncode, 0, dry_run.stderr)
        self.assertIn(str(HERMES_ROOT / "dist/hermes-replay/core"), dry_run.stdout)
        self.assertNotIn('install -d -m 700 "/core"', dry_run.stdout)

        rejected = subprocess.run(
            ["make", "replay-plugin-build", "REPLAY_BUILD_DIR=/"],
            cwd=HERMES_ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertNotEqual(rejected.returncode, 0)
        self.assertIn("unsafe REPLAY_BUILD_DIR", rejected.stderr)

    def test_install_is_atomic_retains_backup_and_rolls_back(self) -> None:
        script = HERMES_ROOT / "scripts/install_plugin.py"
        source = HERMES_ROOT / "observ_hermes"
        with tempfile.TemporaryDirectory() as tmp:
            destination = Path(tmp) / "plugins/observ_hermes"
            destination.mkdir(parents=True)
            marker = destination / "old-version.txt"
            marker.write_text("old")
            legacy_backup = destination.with_name("observ_hermes.previous")
            legacy_backup.mkdir()
            (legacy_backup / "plugin.yaml").write_text("name: stale\n")

            failed = subprocess.run(
                [
                    sys.executable,
                    str(script),
                    "install",
                    "--source",
                    str(source),
                    "--destination",
                    str(destination),
                    "--fail-phase",
                    "copy",
                ],
                text=True,
                capture_output=True,
            )
            self.assertNotEqual(failed.returncode, 0)
            self.assertEqual(marker.read_text(), "old")

            installed = subprocess.run(
                [
                    sys.executable,
                    str(script),
                    "install",
                    "--source",
                    str(source),
                    "--destination",
                    str(destination),
                ],
                text=True,
                capture_output=True,
            )
            self.assertEqual(installed.returncode, 0, installed.stderr)
            backup = destination.parent.parent / "plugin-backups/observ_hermes.previous"
            self.assertTrue((destination / "semantic.py").is_file())
            self.assertEqual((backup / "old-version.txt").read_text(), "old")
            self.assertFalse((destination.parent / "observ_hermes.previous").exists())
            self.assertTrue(
                (
                    destination.parent.parent
                    / "plugin-backups/observ_hermes.legacy-previous/plugin.yaml"
                ).is_file()
            )

            rolled_back = subprocess.run(
                [
                    sys.executable,
                    str(script),
                    "rollback",
                    "--destination",
                    str(destination),
                ],
                text=True,
                capture_output=True,
            )
            self.assertEqual(rolled_back.returncode, 0, rolled_back.stderr)
            self.assertEqual((destination / "old-version.txt").read_text(), "old")

    def test_standalone_failures_preserve_all_plugin_state(self) -> None:
        script = HERMES_ROOT / "scripts/install_plugin.py"
        source = HERMES_ROOT / "observ_hermes"
        for fail_phase in ("copy", "validate", "swap"):
            with (
                self.subTest(fail_phase=fail_phase),
                tempfile.TemporaryDirectory() as tmp,
            ):
                root = Path(tmp)
                destination = root / "plugins/observ_hermes"
                backup = root / "plugin-backups/observ_hermes.previous"
                legacy_backup = root / "plugins/observ_hermes.previous"
                legacy_archive = root / "plugin-backups/observ_hermes.legacy-previous"
                expected = {
                    destination / "current.bin": b"current\x00plugin",
                    backup / "backup.bin": b"retained\x00backup",
                    legacy_backup / "legacy.bin": b"legacy\x00backup",
                    legacy_archive / "archive.bin": b"prior\x00archive",
                }
                for path, content in expected.items():
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(content)

                failed = subprocess.run(
                    [
                        sys.executable,
                        str(script),
                        "install",
                        "--source",
                        str(source),
                        "--destination",
                        str(destination),
                        "--fail-phase",
                        fail_phase,
                    ],
                    text=True,
                    capture_output=True,
                )

                self.assertNotEqual(failed.returncode, 0)
                for path, content in expected.items():
                    self.assertEqual(path.read_bytes(), content, path)
                self.assertFalse(
                    any((root / "plugin-backups").glob(".observ_hermes-install-*"))
                )

    def test_standalone_rollback_failures_restore_every_managed_surface(self) -> None:
        script = HERMES_ROOT / "scripts/install_plugin.py"
        source = HERMES_ROOT / "observ_hermes"
        template = source / "configs/aivision.json"
        for fail_phase in ("config", "legacy"):
            with (
                self.subTest(fail_phase=fail_phase),
                tempfile.TemporaryDirectory() as tmp,
            ):
                root = Path(tmp)
                destination = root / "plugins/observ_hermes"
                destination.mkdir(parents=True)
                (destination / "old.bin").write_bytes(b"old\x00plugin")
                legacy = root / "observ-hermes.env"
                legacy.write_bytes(b"OBSERV_HERMES_SERVICE_NAME=legacy\n")
                config = root / "observ-hermes.json"
                installed = subprocess.run(
                    [
                        sys.executable,
                        str(script),
                        "install",
                        "--source",
                        str(source),
                        "--destination",
                        str(destination),
                        "--config-template",
                        str(template),
                        "--config-destination",
                        str(config),
                        "--legacy-env",
                        str(legacy),
                    ],
                    text=True,
                    capture_output=True,
                )
                self.assertEqual(installed.returncode, 0, installed.stderr)
                (destination / "installed.bin").write_bytes(b"new\x00plugin")
                before = self.snapshot_tree(root)

                failed = subprocess.run(
                    [
                        sys.executable,
                        str(script),
                        "rollback",
                        "--destination",
                        str(destination),
                        "--fail-phase",
                        fail_phase,
                    ],
                    text=True,
                    capture_output=True,
                )

                self.assertNotEqual(failed.returncode, 0)
                self.assertEqual(self.snapshot_tree(root), before)

    def test_standalone_undo_failures_restore_every_managed_surface(self) -> None:
        script = HERMES_ROOT / "scripts/install_plugin.py"
        source = HERMES_ROOT / "observ_hermes"
        template = source / "configs/aivision.json"
        for fail_phase in ("config", "legacy"):
            with (
                self.subTest(fail_phase=fail_phase),
                tempfile.TemporaryDirectory() as tmp,
            ):
                root = Path(tmp)
                destination = root / "plugins/observ_hermes"
                legacy = root / "observ-hermes.env"
                legacy.write_bytes(b"OBSERV_HERMES_SERVICE_NAME=legacy\n")
                config = root / "observ-hermes.json"
                installed = subprocess.run(
                    [
                        sys.executable,
                        str(script),
                        "install",
                        "--source",
                        str(source),
                        "--destination",
                        str(destination),
                        "--config-template",
                        str(template),
                        "--config-destination",
                        str(config),
                        "--legacy-env",
                        str(legacy),
                    ],
                    text=True,
                    capture_output=True,
                )
                self.assertEqual(installed.returncode, 0, installed.stderr)
                (destination / "installed.bin").write_bytes(b"new\x00plugin")
                before = self.snapshot_tree(root)
                env = dict(os.environ)
                env["PYTHONPATH"] = str(HERMES_ROOT)

                failed = subprocess.run(
                    [
                        sys.executable,
                        "-c",
                        "from pathlib import Path; "
                        "from scripts.install_plugin import undo_install; "
                        "import sys; "
                        "undo_install(Path(sys.argv[1]), fail_phase=sys.argv[2])",
                        str(destination),
                        fail_phase,
                    ],
                    env=env,
                    text=True,
                    capture_output=True,
                )

                self.assertNotEqual(failed.returncode, 0)
                self.assertEqual(self.snapshot_tree(root), before)

    def test_make_install_only_installs_the_production_plugin(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = subprocess.run(
                [
                    "make",
                    "-n",
                    "install",
                    f"PYTHON={sys.executable}",
                    f"INSTALL_DIR={root / 'plugins/observ_hermes'}",
                    f"INSTALL_CONFIG={root / 'observ-hermes.json'}",
                    f"LEGACY_ENV={root / 'observ-hermes.env'}",
                    "MAKE=/usr/bin/true",
                ],
                cwd=HERMES_ROOT,
                text=True,
                capture_output=True,
            )
            output = f"{result.stdout}\n{result.stderr}"
            self.assertEqual(result.returncode, 0, output)
            self.assertIn("hermes_lifecycle.py", output)
            self.assertNotIn("install-cli", output)
            self.assertIn("hermes-core", output)
            self.assertNotIn("hermes-replay", output)
            self.assertNotIn("observ-hermes-replay", output)
            self.assertNotIn("observ-hermes-report", output)

    def test_make_lifecycle_failures_restore_every_managed_surface(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            python = self.create_venv(root)
            hermes_home = root / "hermes-home"
            build_dir = root / "replay-build"
            plugin = hermes_home / "plugins/observ_hermes"
            config = hermes_home / "observ-hermes.json"
            legacy = hermes_home / "observ-hermes.env"
            cohort = (
                hermes_home
                / "plugin-backups/aivision-hermes-cohort/current/cohort.json"
            )
            commands = ("hermes-replay", "observ-hermes-replay", "observ-hermes-report")
            variables = [
                f"PYTHON={python}",
                f"HERMES_HOME={hermes_home}",
                f"REPLAY_BUILD_DIR={build_dir}",
            ]

            def run(target: str, *, fail_phase: str = "", install_phase: str = ""):
                env = dict(os.environ)
                if fail_phase:
                    env["OBSERV_HERMES_LIFECYCLE_FAIL_PHASE"] = fail_phase
                if install_phase:
                    env["OBSERV_HERMES_INSTALL_FAIL_PHASE"] = install_phase
                return subprocess.run(
                    ["make", target, *variables],
                    cwd=HERMES_ROOT,
                    env=env,
                    text=True,
                    capture_output=True,
                )

            def probe(*, core: bool, replay: bool) -> None:
                result = subprocess.run(
                    [
                        python,
                        "-c",
                        "import importlib.util; "
                        "assert (importlib.util.find_spec('aivision_hermes_core') "
                        f"is not None) is {core!r}; "
                        "assert (importlib.util.find_spec('hermes_replay') "
                        f"is not None) is {replay!r}",
                    ],
                    cwd=root,
                    text=True,
                    capture_output=True,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                for command in commands:
                    self.assertEqual(
                        (python.parent / command).exists(), replay, command
                    )

            legacy.parent.mkdir(parents=True)
            legacy.write_text("OBSERV_HERMES_SERVICE_NAME=before-failure\n")
            before_copy = run("install", install_phase="copy")
            self.assertNotEqual(before_copy.returncode, 0)
            self.assertFalse(plugin.exists())
            self.assertFalse(config.exists())
            self.assertFalse(cohort.exists())
            self.assertTrue(legacy.is_file())
            self.assertIn("before-failure", legacy.read_text())
            probe(core=False, replay=False)

            after_plugin = run("install", fail_phase="after-plugin")
            self.assertNotEqual(after_plugin.returncode, 0)
            self.assertFalse(plugin.exists())
            self.assertFalse(config.exists())
            self.assertFalse(cohort.exists())
            self.assertTrue(legacy.is_file())
            probe(core=False, replay=False)

            installed = run("install")
            self.assertEqual(installed.returncode, 0, installed.stderr)
            production_config = config.read_bytes()
            self.assertTrue((plugin / "plugin.yaml").is_file())
            self.assertFalse(legacy.exists())
            probe(core=True, replay=False)

            first_rollback = run("rollback")
            self.assertEqual(first_rollback.returncode, 0, first_rollback.stderr)
            self.assertFalse(plugin.exists())
            self.assertFalse(config.exists())
            self.assertTrue(legacy.is_file())
            self.assertIn("before-failure", legacy.read_text())
            self.assertEqual(json.loads(cohort.read_text())["products"], {})
            probe(core=False, replay=False)

            shutil.rmtree(hermes_home / "plugin-backups/aivision-hermes-cohort")
            first_cohort = run("install-cohort")
            self.assertEqual(first_cohort.returncode, 0, first_cohort.stderr)
            self.assertTrue((plugin / "plugin.yaml").is_file())
            probe(core=True, replay=True)
            first_cohort_rollback = run("rollback-cohort")
            self.assertEqual(
                first_cohort_rollback.returncode, 0, first_cohort_rollback.stderr
            )
            self.assertFalse(plugin.exists())
            self.assertFalse(config.exists())
            self.assertTrue(legacy.is_file())
            self.assertEqual(json.loads(cohort.read_text())["products"], {})
            probe(core=False, replay=False)

            shutil.rmtree(hermes_home / "plugin-backups/aivision-hermes-cohort")
            installed = run("install")
            self.assertEqual(installed.returncode, 0, installed.stderr)
            production_config = config.read_bytes()
            self.assertFalse(legacy.exists())
            probe(core=True, replay=False)

            replay_failure = run(
                "replay-plugin-install", fail_phase="after-distribution"
            )
            self.assertNotEqual(replay_failure.returncode, 0)
            self.assertTrue((plugin / "plugin.yaml").is_file())
            self.assertEqual(config.read_bytes(), production_config)
            self.assertEqual(
                set(json.loads(cohort.read_text())["products"]), {"production"}
            )
            probe(core=True, replay=False)

            cohort_failure = run("install-cohort", fail_phase="after-cohort")
            self.assertNotEqual(cohort_failure.returncode, 0)
            self.assertEqual(
                set(json.loads(cohort.read_text())["products"]), {"production"}
            )
            self.assertEqual(config.read_bytes(), production_config)
            probe(core=True, replay=False)

            full = run("install-cohort")
            self.assertEqual(full.returncode, 0, full.stderr)
            full_config = config.read_bytes()
            self.assertEqual(
                set(json.loads(cohort.read_text())["products"]),
                {"production", "replay"},
            )
            probe(core=True, replay=True)

            uninstall_failure = run("uninstall", fail_phase="after-plugin")
            self.assertNotEqual(uninstall_failure.returncode, 0)
            self.assertTrue((plugin / "plugin.yaml").is_file())
            self.assertEqual(config.read_bytes(), full_config)
            self.assertEqual(
                set(json.loads(cohort.read_text())["products"]),
                {"production", "replay"},
            )
            probe(core=True, replay=True)

            rollback_failure = run("rollback-cohort", fail_phase="after-plugin")
            self.assertNotEqual(rollback_failure.returncode, 0)
            self.assertTrue((plugin / "plugin.yaml").is_file())
            self.assertEqual(config.read_bytes(), full_config)
            self.assertEqual(
                set(json.loads(cohort.read_text())["products"]),
                {"production", "replay"},
            )
            probe(core=True, replay=True)

            uninstalled = run("uninstall")
            self.assertEqual(uninstalled.returncode, 0, uninstalled.stderr)
            self.assertFalse(plugin.exists())
            self.assertEqual(
                set(json.loads(cohort.read_text())["products"]), {"replay"}
            )
            probe(core=True, replay=True)

            restored = run("rollback")
            self.assertEqual(restored.returncode, 0, restored.stderr)
            self.assertTrue((plugin / "plugin.yaml").is_file())
            self.assertEqual(config.read_bytes(), full_config)
            self.assertEqual(
                set(json.loads(cohort.read_text())["products"]),
                {"production", "replay"},
            )
            probe(core=True, replay=True)

    def test_root_make_install_accepts_hms_alias(self) -> None:
        result = subprocess.run(
            ["make", "-n", "install", "hms", "MAKE=/usr/bin/true"],
            cwd=REPO_ROOT,
            text=True,
            capture_output=True,
        )
        output = f"{result.stdout}\n{result.stderr}"
        self.assertEqual(result.returncode, 0, output)
        self.assertIn("hermes", output)
        self.assertNotIn("Unknown agent", output)

    def test_root_replay_and_cohort_aliases_route_only_to_explicit_targets(
        self,
    ) -> None:
        cases = {
            "hms": "install",
            "hms-replay": "replay-plugin-install",
            "hms-cohort": "install-cohort",
        }
        for alias, expected in cases.items():
            with self.subTest(alias=alias):
                result = subprocess.run(
                    ["make", "-n", "install", alias, "MAKE=/usr/bin/true"],
                    cwd=REPO_ROOT,
                    text=True,
                    capture_output=True,
                )
                output = f"{result.stdout}\n{result.stderr}"
                self.assertEqual(result.returncode, 0, output)
                self.assertIn(f"-C $dir {expected}", output)
        production = subprocess.run(
            ["make", "-n", "install", "hms", "MAKE=/usr/bin/true"],
            cwd=REPO_ROOT,
            text=True,
            capture_output=True,
        )
        self.assertNotIn("replay-plugin-install", production.stdout)
        self.assertNotIn("install-cohort", production.stdout)

    def test_replay_targets_fail_before_artifact_creation_when_replay_is_absent(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            transcript = root / "session.jsonl"
            transcript.write_text("{}\n")
            replay = root / "replays"
            result = subprocess.run(
                [
                    "make",
                    "replay",
                    f"PYTHON={sys.executable} -I",
                    f"TRANSCRIPT={transcript}",
                    f"REPLAY_DIR={replay}",
                ],
                cwd=HERMES_ROOT,
                text=True,
                capture_output=True,
            )
            output = f"{result.stdout}\n{result.stderr}"
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("replay-plugin-install", output)
            self.assertFalse(replay.exists())

    def test_all_standard_targets_execute_with_replay_owned_and_validator_owned_tools(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            transcripts = root / "transcripts"
            transcript = write_raw_transcript(
                transcripts,
                "make-functional",
                representative_hooks("make-functional"),
            )
            replay = root / "replays"
            replay_all = root / "replays-all"
            tool_log = root / "tools.log"
            stub = root / "tool-stub"
            stub.write_text(f"#!/bin/sh\nprintf '%s\\n' \"$*\" >> {tool_log!s}\n")
            stub.chmod(0o755)
            env = {
                **os.environ,
                "PYTHONPATH": os.pathsep.join(
                    [
                        str(HERMES_ROOT / "hermes-replay"),
                        str(HERMES_ROOT / "hermes-core"),
                        str(HERMES_ROOT),
                    ]
                ),
                "PYTHONDONTWRITEBYTECODE": "1",
            }

            def run(target: str, *variables: str) -> subprocess.CompletedProcess[str]:
                result = subprocess.run(
                    ["make", target, f"PYTHON={sys.executable}", *variables],
                    cwd=HERMES_ROOT,
                    env=env,
                    text=True,
                    capture_output=True,
                )
                self.assertEqual(
                    result.returncode,
                    0,
                    f"{target}: {result.stdout}\n{result.stderr}",
                )
                return result

            build = run("build", f"REPLAY_PKG_DIR={root / 'missing-replay-source'}")
            self.assertNotIn("hermes-replay", build.stdout)

            run("replay", f"TRANSCRIPT={transcript}", f"REPLAY_DIR={replay}")
            self.assertTrue(list(replay.glob("spans_*.jsonl")))
            run(
                "replay-all",
                f"TRANSCRIPT_DIR={transcripts}",
                f"REPLAY_DIR={replay_all}",
            )
            self.assertTrue(list(replay_all.glob("spans_*.jsonl")))

            run("validate", f"REPLAY_DIR={replay}", f"WEAVER_GUARD={stub}")
            span = next(replay.glob("spans_*.jsonl"))
            run("validate-one", f"FILE={span}", f"WEAVER_GUARD={stub}")
            run("report", f"REPLAY_DIR={replay}", f"REPORT_CLI={stub}")
            run("report-shift", f"REPLAY_DIR={replay}", f"REPORT_CLI={stub}")
            log = tool_log.read_text()
            self.assertIn("validate", log)
            self.assertIn("--base-url http://127.0.0.1:4319", log)
            self.assertIn("--shift", log)

            transcript_guard = root / "transcript-guard.jsonl"
            golden_guard = root / "golden-guard.jsonl"
            config_guard = root / "observ-hermes.json"
            for path in (transcript_guard, golden_guard, config_guard):
                path.write_text("preserve\n")
            run("clean", f"REPLAY_DIR={replay}")
            self.assertFalse(list(replay.glob("spans_*.jsonl")))
            self.assertFalse(list(replay.glob("events_*.jsonl")))
            self.assertFalse(list(replay.glob("metrics_*.jsonl")))
            self.assertTrue(transcript_guard.is_file())
            self.assertTrue(golden_guard.is_file())
            self.assertTrue(config_guard.is_file())

    def test_real_isolated_production_and_replay_installs_remain_disjoint(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            python = self.create_venv(root)
            hermes_home = root / "hermes-home"
            build_dir = root / "replay-build"

            def run(target: str) -> subprocess.CompletedProcess[str]:
                result = subprocess.run(
                    [
                        "make",
                        target,
                        f"PYTHON={python}",
                        f"HERMES_HOME={hermes_home}",
                        f"REPLAY_BUILD_DIR={build_dir}",
                    ],
                    cwd=HERMES_ROOT,
                    text=True,
                    capture_output=True,
                )
                self.assertEqual(
                    result.returncode, 0, f"{target}: {result.stdout}\n{result.stderr}"
                )
                return result

            run("install")
            self.assertTrue(
                (hermes_home / "plugins/observ_hermes/plugin.yaml").is_file()
            )
            self.assertTrue((hermes_home / "observ-hermes.json").is_file())
            bin_dir = python.parent
            for command in (
                "hermes-replay",
                "observ-hermes-replay",
                "observ-hermes-report",
            ):
                self.assertFalse((bin_dir / command).exists())
            production_probe = subprocess.run(
                [
                    python,
                    "-c",
                    "import importlib.util; "
                    "assert importlib.util.find_spec('aivision_hermes_core'); "
                    "assert importlib.util.find_spec('hermes_replay') is None",
                ],
                text=True,
                capture_output=True,
                cwd=root,
            )
            self.assertEqual(production_probe.returncode, 0, production_probe.stderr)

            run("uninstall")
            self.assertFalse((hermes_home / "plugins/observ_hermes").exists())
            run("replay-plugin-install")
            self.assertFalse((hermes_home / "plugins/observ_hermes").exists())
            for command in (
                "hermes-replay",
                "observ-hermes-replay",
                "observ-hermes-report",
            ):
                self.assertTrue((bin_dir / command).is_file())
            replay_probe = subprocess.run(
                [
                    python,
                    "-c",
                    "import importlib.util; import hermes_replay; "
                    "assert importlib.util.find_spec('observ_hermes') is None",
                ],
                text=True,
                capture_output=True,
                cwd=root,
            )
            self.assertEqual(replay_probe.returncode, 0, replay_probe.stderr)

            transcript = root / "transcripts/session_guard.jsonl"
            transcript.parent.mkdir()
            transcript.write_text("preserve\n")
            run("replay-plugin-uninstall")
            self.assertTrue(transcript.is_file())
            for command in (
                "hermes-replay",
                "observ-hermes-replay",
                "observ-hermes-report",
            ):
                self.assertFalse((bin_dir / command).exists())
            last_consumer_probe = subprocess.run(
                [
                    python,
                    "-c",
                    "import importlib.util; "
                    "assert importlib.util.find_spec('hermes_replay') is None; "
                    "assert importlib.util.find_spec('aivision_hermes_core') is None",
                ],
                text=True,
                capture_output=True,
                cwd=root,
            )
            self.assertEqual(
                last_consumer_probe.returncode, 0, last_consumer_probe.stderr
            )

    def test_production_install_bootstraps_pip_in_unseeded_venv(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            python = self.create_venv(root, seed=False)
            hermes_home = root / "hermes-home"

            missing_pip = subprocess.run(
                [python, "-m", "pip", "--version"],
                text=True,
                capture_output=True,
            )
            self.assertNotEqual(missing_pip.returncode, 0)

            installed = subprocess.run(
                [
                    "make",
                    "install",
                    f"PYTHON={python}",
                    f"HERMES_HOME={hermes_home}",
                ],
                cwd=HERMES_ROOT,
                text=True,
                capture_output=True,
            )
            self.assertEqual(
                installed.returncode,
                0,
                f"install: {installed.stdout}\n{installed.stderr}",
            )

            pip_probe = subprocess.run(
                [python, "-m", "pip", "--version"],
                text=True,
                capture_output=True,
            )
            self.assertEqual(pip_probe.returncode, 0, pip_probe.stderr)
            self.assertTrue(
                (hermes_home / "plugins/observ_hermes/plugin.yaml").is_file()
            )
            production_probe = subprocess.run(
                [
                    python,
                    "-c",
                    "import importlib.util, sys; "
                    f"sys.path.insert(0, {str(hermes_home / 'plugins')!r}); "
                    "assert importlib.util.find_spec('aivision_hermes_core'); "
                    "assert importlib.util.find_spec('hermes_replay') is None; "
                    "assert importlib.util.find_spec('opentelemetry.sdk'); "
                    "from observ_hermes import register, _shutdown; "
                    "hooks = []; "
                    "context = type('Context', (), {"
                    "'register_hook': lambda self, name, callback: hooks.append(name)"
                    "})(); "
                    "register(context); "
                    "assert 'on_session_start' in hooks; "
                    "assert 'post_llm_call' in hooks; "
                    "_shutdown()",
                ],
                text=True,
                capture_output=True,
                cwd=root,
                env={
                    **os.environ,
                    "HERMES_HOME": str(hermes_home),
                    "OBSERV_HERMES_TRACES_ENABLED": "false",
                    "OBSERV_HERMES_METRICS_ENABLED": "false",
                    "OBSERV_HERMES_LOGS_ENABLED": "false",
                },
            )
            self.assertEqual(production_probe.returncode, 0, production_probe.stderr)
            for command in (
                "hermes-replay",
                "observ-hermes-replay",
                "observ-hermes-report",
            ):
                self.assertFalse((python.parent / command).exists())

    def test_real_isolated_cohort_install_and_rollback_restore_compatible_set(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            python = self.create_venv(root)
            hermes_home = root / "hermes-home"
            variables = [
                f"PYTHON={python}",
                f"HERMES_HOME={hermes_home}",
                f"REPLAY_BUILD_DIR={root / 'replay-build'}",
            ]
            for target in ("install", "install-cohort"):
                result = subprocess.run(
                    ["make", target, *variables],
                    cwd=HERMES_ROOT,
                    text=True,
                    capture_output=True,
                )
                self.assertEqual(
                    result.returncode, 0, f"{target}: {result.stdout}\n{result.stderr}"
                )
            self.assertTrue((python.parent / "hermes-replay").is_file())
            result = subprocess.run(
                ["make", "rollback-cohort", *variables],
                cwd=HERMES_ROOT,
                text=True,
                capture_output=True,
            )
            self.assertEqual(
                result.returncode,
                0,
                f"rollback-cohort: {result.stdout}\n{result.stderr}",
            )
            self.assertTrue(
                (hermes_home / "plugins/observ_hermes/plugin.yaml").is_file()
            )
            self.assertFalse((python.parent / "hermes-replay").exists())
            probe = subprocess.run(
                [
                    python,
                    "-c",
                    "import importlib.util; "
                    "assert importlib.util.find_spec('aivision_hermes_core'); "
                    "assert importlib.util.find_spec('hermes_replay') is None",
                ],
                text=True,
                capture_output=True,
                cwd=root,
            )
            self.assertEqual(probe.returncode, 0, probe.stderr)


if __name__ == "__main__":
    unittest.main()
